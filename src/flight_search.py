"""
Unified flight search — dispatches to scraper (primary) or Amadeus (fallback).

The scraper is the default data source. Amadeus is used as a fallback when:
  1. Scraper returns no results and Amadeus credentials are configured, OR
  2. data_source is explicitly set to "amadeus" in config.yaml
"""

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def search_flights(
    origin: str,
    dest: str,
    date_str: str,
    data_source: str = "scraper",
    fetch_points: bool = True,
    amadeus_fallback: bool = True,
    fare_family_filter: Optional[str] = None,
    min_dep_time: Optional[str] = None,
    airline: str = "LA",
    headless: bool = False,
    timeout_ms: int = 45000,
) -> list[dict]:
    """
    Search flights using the configured data source.

    Returns a list of flight dicts with keys:
        origin, destination, departure_time, arrival_time,
        price_brl, points, flight_number, fare_family,
        is_refundable, duration

    Applies fare_family_filter and min_dep_time if provided.
    """
    flights: list[dict] = []

    if data_source in ("scraper", "auto"):
        flights = _search_scraper(origin, dest, date_str, fetch_points, headless, timeout_ms)

    if not flights and data_source in ("amadeus", "auto"):
        flights = _search_amadeus(origin, dest, date_str, airline)

    if not flights and data_source == "scraper" and amadeus_fallback:
        if _amadeus_configured():
            logger.info("Scraper returned 0 results; trying Amadeus fallback.")
            flights = _search_amadeus(origin, dest, date_str, airline)

    # Apply filters
    if fare_family_filter and fare_family_filter.upper() == "FULL":
        flights = _filter_full(flights)
    if min_dep_time:
        flights = _filter_after(flights, min_dep_time)

    return flights


def search_flights_batch(
    searches: list[dict],
    data_source: str = "scraper",
    fetch_points: bool = True,
    delay_between: float = 3.0,
) -> dict[str, list[dict]]:
    """
    Batch search using the scraper (reuses one browser for efficiency).

    Each item in *searches* must have keys: origin, dest, date_str.
    Optionally: mode ("cash"), fare_family_filter, min_dep_time.

    Returns dict mapping "ORIGIN-DEST-DATE" -> list[dict].
    """
    if data_source in ("scraper", "auto"):
        return _batch_scraper(searches, fetch_points, delay_between)

    # Amadeus doesn't have a batch mode; search sequentially
    results = {}
    for s in searches:
        key = f"{s['origin']}-{s['dest']}-{s['date_str']}"
        flights = _search_amadeus(s["origin"], s["dest"], s["date_str"])
        results[key] = flights
    return results


# ---------------------------------------------------------------------------
# Scraper path
# ---------------------------------------------------------------------------

def _search_scraper(
    origin: str, dest: str, date_str: str, fetch_points: bool,
    headless: bool = False, timeout_ms: int = 45000,
) -> list[dict]:
    from src.latam_scraper import scrape_flights_sync, merge_cash_and_points

    cash = scrape_flights_sync(origin, dest, date_str, mode="cash",
                               headless=headless, timeout_ms=timeout_ms)
    if not cash:
        return []

    if fetch_points:
        pts = scrape_flights_sync(origin, dest, date_str, mode="points",
                                  headless=headless, timeout_ms=timeout_ms)
        if pts:
            cash = merge_cash_and_points(cash, pts)

    return cash


def _batch_scraper(
    searches: list[dict], fetch_points: bool, delay: float
) -> dict[str, list[dict]]:
    from src.latam_scraper import scrape_batch_sync, merge_cash_and_points

    # Build scraper search list: cash first, then points for same routes
    scraper_searches = []
    for s in searches:
        scraper_searches.append({
            "origin": s["origin"],
            "dest": s["dest"],
            "date_str": s["date_str"],
            "mode": "cash",
        })
    if fetch_points:
        for s in searches:
            scraper_searches.append({
                "origin": s["origin"],
                "dest": s["dest"],
                "date_str": s["date_str"],
                "mode": "points",
            })

    raw = scrape_batch_sync(scraper_searches, delay_between=delay)

    # Merge cash + points results
    results: dict[str, list[dict]] = {}
    for s in searches:
        key = f"{s['origin']}-{s['dest']}-{s['date_str']}"
        cash_key = f"{s['origin']}-{s['dest']}-{s['date_str']}-cash"
        pts_key = f"{s['origin']}-{s['dest']}-{s['date_str']}-points"

        cash = raw.get(cash_key, [])
        if fetch_points:
            pts = raw.get(pts_key, [])
            if pts:
                cash = merge_cash_and_points(cash, pts)
        results[key] = cash

    return results


# ---------------------------------------------------------------------------
# Amadeus path
# ---------------------------------------------------------------------------

def _amadeus_configured() -> bool:
    return bool(
        os.environ.get("AMADEUS_CLIENT_ID")
        and os.environ.get("AMADEUS_CLIENT_SECRET")
    )


def _search_amadeus(
    origin: str, dest: str, date_str: str, airline: str = "LA"
) -> list[dict]:
    if not _amadeus_configured():
        logger.debug("Amadeus not configured; skipping.")
        return []

    from src.amadeus_client import search_flights as amadeus_search, parse_offer

    raw_offers = amadeus_search(
        origin, dest, date_str, airline_codes=[airline]
    )
    flights = []
    for offer in raw_offers:
        parsed = parse_offer(offer, origin, dest)
        # Add points=None for compatibility
        parsed.setdefault("points", None)
        flights.append(parsed)
    return flights


# ---------------------------------------------------------------------------
# Shared filters
# ---------------------------------------------------------------------------

def _filter_full(flights: list[dict]) -> list[dict]:
    full = [
        f for f in flights
        if f.get("is_refundable")
        or "FULL" in (f.get("fare_family") or "").upper()
    ]
    if not full and flights:
        logger.warning(
            "No Full-fare results among %d flights. Returning all as fallback.",
            len(flights),
        )
        return flights
    return full


def _filter_after(flights: list[dict], min_time: str) -> list[dict]:
    min_h, min_m = (int(x) for x in min_time.split(":"))
    result = []
    for f in flights:
        dep = f.get("departure_time")
        if not dep:
            continue
        parts = dep.split(":")
        if len(parts) >= 2:
            h, m = int(parts[0]), int(parts[1])
            if h > min_h or (h == min_h and m >= min_m):
                result.append(f)
    return result


def filter_last_or_cheapest(
    flights: list[dict],
) -> tuple[Optional[dict], Optional[dict]]:
    """Return (last_flight, cheapest_flight). Both may be None."""
    if not flights:
        return None, None
    last = max(flights, key=lambda f: f.get("departure_time") or "")
    cheapest = min(
        flights,
        key=lambda f: f.get("price_brl") if f.get("price_brl") is not None else float("inf"),
    )
    return last, cheapest
