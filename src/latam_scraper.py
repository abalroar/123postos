"""
LATAM Airlines website scraper using Playwright.

Scrapes flight prices (R$ and LATAM Pass points) directly from latamairlines.com.
Uses network interception to capture internal API responses, with DOM parsing as
fallback.

Primary data source — replaces Amadeus after self-service shutdown (July 2026).
"""

import asyncio
import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_LATAM_SEARCH_URL = (
    "https://www.latamairlines.com/br/pt/oferta-voos"
    "?origin={origin}&destination={dest}"
    "&outbound={date}&adt=1&chd=0&inf=0&trip=OW&cabin=Y&redemption={redemption}"
)

# Keywords that hint a JSON response contains flight data
_URL_HINTS = [
    "flight", "offer", "availability", "air-offer", "search",
    "itinerar", "fare", "bff", "shopping", "booking",
]
_JSON_HINTS = [
    "flight", "itinerar", "segment", "fare", "offer",
    "departure", "arrival", "cabin",
]


# ---------------------------------------------------------------------------
# Network interception
# ---------------------------------------------------------------------------

def _setup_interception(page, captured: list[dict]):
    """Register a response listener that captures flight-related JSON."""

    async def _on_response(response):
        if response.status != 200:
            return
        ct = response.headers.get("content-type", "")
        if "json" not in ct:
            return
        try:
            body = await response.json()
            if _looks_like_flight_data(body, response.url):
                captured.append({"url": response.url, "data": body})
                logger.debug("Captured flight data from %s", response.url)
        except Exception:
            pass

    page.on("response", _on_response)


def _looks_like_flight_data(data, url: str) -> bool:
    url_lower = url.lower()
    if any(h in url_lower for h in _URL_HINTS):
        return True
    if isinstance(data, dict):
        keys_lower = " ".join(str(k).lower() for k in data.keys())
        return any(h in keys_lower for h in _JSON_HINTS)
    return False


# ---------------------------------------------------------------------------
# JSON extraction (from intercepted responses)
# ---------------------------------------------------------------------------

def _parse_intercepted(captured: list[dict], mode: str) -> list[dict]:
    flights: list[dict] = []
    for item in captured:
        flights.extend(_extract_from_json(item["data"], mode))
    # Deduplicate by (flight_number, departure_time)
    seen = set()
    unique = []
    for f in flights:
        key = (f.get("flight_number"), f.get("departure_time"))
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def _extract_from_json(data, mode: str, depth: int = 0) -> list[dict]:
    if depth > 6:
        return []
    flights = []
    if isinstance(data, dict):
        for key in ("flights", "itineraries", "offers", "flightOffers",
                     "data", "results", "items", "legs", "journeys"):
            val = data.get(key)
            if isinstance(val, list) and val:
                for item in val:
                    f = _try_parse_item(item, mode)
                    if f:
                        flights.append(f)
                if flights:
                    return flights
        # Recurse
        for v in data.values():
            if isinstance(v, (dict, list)):
                flights.extend(_extract_from_json(v, mode, depth + 1))
    elif isinstance(data, list):
        for item in data:
            f = _try_parse_item(item, mode)
            if f:
                flights.append(f)
            elif isinstance(item, (dict, list)):
                flights.extend(_extract_from_json(item, mode, depth + 1))
    return flights


def _try_parse_item(item, mode: str) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    keys_lower = {k.lower(): v for k, v in item.items()}
    keys_str = " ".join(keys_lower.keys())
    has_time = any(k in keys_str for k in ("departure", "depart", "arrival", "arrive", "time", "schedule"))
    has_price = any(k in keys_str for k in ("price", "fare", "amount", "valor", "miles", "point"))
    if not has_time and not has_price:
        return None

    dep = _find(item, ["departureTime", "departure", "departAt", "departureDatetime", "dep",
                        "departureDate", "std", "scheduledDepartureTime"])
    arr = _find(item, ["arrivalTime", "arrival", "arriveAt", "arrivalDatetime", "arr",
                        "arrivalDate", "sta", "scheduledArrivalTime"])
    if isinstance(dep, dict):
        dep = dep.get("dateTime") or dep.get("at") or dep.get("time") or dep.get("date")
    if isinstance(arr, dict):
        arr = arr.get("dateTime") or arr.get("at") or arr.get("time") or arr.get("date")

    fn = _find(item, ["flightNumber", "flight", "number", "marketingFlightNumber",
                       "flightCode", "flightNo"])
    carrier = _find(item, ["carrier", "airline", "carrierCode", "marketingCarrier",
                            "airlineCode", "operatingCarrier"])
    if isinstance(carrier, dict):
        carrier = carrier.get("code") or carrier.get("iata") or carrier.get("carrierCode")

    price_brl = None
    points = None
    if mode == "cash":
        price_brl = _find(item, ["price", "amount", "total", "totalPrice", "valor",
                                  "grandTotal", "totalAmount", "value"])
        if isinstance(price_brl, dict):
            price_brl = price_brl.get("total") or price_brl.get("amount") or price_brl.get("value")
        if price_brl is not None:
            try:
                price_brl = float(price_brl)
            except (ValueError, TypeError):
                price_brl = None
    else:
        points = _find(item, ["miles", "points", "milesAmount", "milesTotal",
                               "pointsAmount", "redemptionMiles"])
        if isinstance(points, dict):
            points = points.get("total") or points.get("amount")
        if points is not None:
            try:
                points = int(float(str(points)))
            except (ValueError, TypeError):
                points = None

    fare = _find(item, ["fareFamily", "brandedFare", "cabin", "fareType", "brand",
                         "tarifa", "fareBrand", "brandName", "cabinClass"])
    if isinstance(fare, dict):
        fare = fare.get("name") or fare.get("label") or fare.get("type")

    if not dep and not price_brl and not points:
        return None

    flight_number = ""
    if carrier and fn:
        flight_number = f"{carrier}{fn}".upper()
    elif fn:
        flight_number = str(fn).upper()

    fare_str = (str(fare) if fare else "").upper()
    return {
        "departure_time": _extract_time(dep),
        "departure_datetime": str(dep) if dep else None,
        "arrival_time": _extract_time(arr),
        "arrival_datetime": str(arr) if arr else None,
        "price_brl": price_brl,
        "points": points,
        "flight_number": flight_number,
        "fare_family": fare_str,
        "is_refundable": any(kw in fare_str for kw in ("FULL", "FLEX", "REFUND")),
        "duration": None,
        "origin": None,
        "destination": None,
    }


def _find(d: dict, keys: list[str]):
    d_lower = {k.lower(): v for k, v in d.items()}
    for key in keys:
        val = d_lower.get(key.lower())
        if val is not None:
            return val
    return None


def _extract_time(val) -> Optional[str]:
    if not val:
        return None
    m = re.search(r"(\d{2}):(\d{2})", str(val))
    return f"{m.group(1)}:{m.group(2)}" if m else None


# ---------------------------------------------------------------------------
# DOM parsing fallback
# ---------------------------------------------------------------------------

async def _parse_dom(page, mode: str) -> list[dict]:
    flights = []
    try:
        await page.wait_for_selector(
            "[class*='flight'], [class*='itinerary'], [data-test*='flight'], "
            "[class*='sc-'], [class*='MuiCard'], ol li, [class*='offer']",
            timeout=5000,
        )
    except Exception:
        logger.debug("No flight elements found in DOM for fallback.")
        return flights

    for selector in [
        "li[class*='flight']",
        "div[class*='flight-card']",
        "div[class*='itinerary']",
        "[data-test*='result']",
        "[class*='offer']",
        "ol > li",
    ]:
        elements = await page.query_selector_all(selector)
        if len(elements) >= 2:
            for el in elements:
                text = await el.inner_text()
                f = _parse_card_text(text, mode)
                if f:
                    flights.append(f)
            if flights:
                break
    return flights


def _parse_card_text(text: str, mode: str) -> Optional[dict]:
    if not text or len(text) < 10:
        return None
    times = re.findall(r"\b(\d{1,2}:\d{2})\b", text)
    dep_time = times[0] if len(times) >= 1 else None
    arr_time = times[1] if len(times) >= 2 else None

    fn_match = re.search(r"\b(LA|JJ)\s*(\d{3,5})\b", text, re.IGNORECASE)
    flight_number = f"{fn_match.group(1)}{fn_match.group(2)}".upper() if fn_match else ""

    price_brl = None
    points = None
    if mode == "cash":
        pm = re.search(r"R\$\s*([\d.,]+)", text)
        if pm:
            raw = pm.group(1)
            if "," in raw and "." in raw:
                price_brl = float(raw.replace(".", "").replace(",", "."))
            elif "," in raw:
                price_brl = float(raw.replace(",", "."))
            else:
                price_brl = float(raw)
    else:
        pm = re.search(r"([\d.]+)\s*(pts|pontos|milhas|miles)", text, re.IGNORECASE)
        if pm:
            points = int(pm.group(1).replace(".", ""))

    fare = ""
    for name in ("FULL", "STANDARD", "LIGHT", "PREMIUM"):
        if name.lower() in text.lower():
            fare = name
            break

    if not dep_time and not price_brl and not points:
        return None

    return {
        "departure_time": dep_time,
        "departure_datetime": None,
        "arrival_time": arr_time,
        "arrival_datetime": None,
        "price_brl": price_brl,
        "points": points,
        "flight_number": flight_number,
        "fare_family": fare,
        "is_refundable": fare == "FULL",
        "duration": None,
        "origin": None,
        "destination": None,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def scrape_flights(
    origin: str,
    dest: str,
    date_str: str,
    mode: str = "cash",
    headless: bool = True,
    timeout_ms: int = 45000,
) -> list[dict]:
    """
    Scrape LATAM website for flight offers.

    Parameters
    ----------
    origin     : IATA origin  (e.g. "CGH")
    dest       : IATA dest    (e.g. "BSB")
    date_str   : departure date YYYY-MM-DD
    mode       : "cash" for BRL prices, "points" for LATAM Pass points
    headless   : run browser headless
    timeout_ms : max wait for page load (ms)

    Returns list of dicts with keys:
        departure_time, arrival_time, price_brl, points,
        flight_number, fare_family, is_refundable, origin, destination
    """
    from playwright.async_api import async_playwright

    redemption = "true" if mode == "points" else "false"
    url = _LATAM_SEARCH_URL.format(
        origin=origin.upper(),
        dest=dest.upper(),
        date=date_str,
        redemption=redemption,
    )

    logger.info("Scraping LATAM: %s->%s %s (mode=%s)", origin, dest, date_str, mode)
    captured: list[dict] = []
    flights: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1366, "height": 768},
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            window.chrome = { runtime: {} };
        """)
        page = await context.new_page()
        _setup_interception(page, captured)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                await page.wait_for_selector(
                    "[class*='flight'], [class*='itinerary'], "
                    "[data-test*='flight'], ol li, [class*='offer']",
                    timeout=timeout_ms,
                )
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                logger.debug("Timeout waiting for flight elements; checking captured data...")
                await asyncio.sleep(3)

            # Strategy 1: intercepted API data
            if captured:
                flights = _parse_intercepted(captured, mode)
                logger.info(
                    "Intercepted %d response(s) -> %d flight(s)",
                    len(captured), len(flights),
                )

            # Strategy 2: DOM fallback
            if not flights:
                flights = await _parse_dom(page, mode)
                logger.info("DOM fallback -> %d flight(s)", len(flights))

            # Save raw captured JSON for debugging (first run)
            if captured and logger.isEnabledFor(logging.DEBUG):
                for i, c in enumerate(captured[:3]):
                    logger.debug(
                        "Raw captured [%d] url=%s data=%s",
                        i, c["url"], json.dumps(c["data"], ensure_ascii=False)[:500],
                    )

            for f in flights:
                f["origin"] = f["origin"] or origin.upper()
                f["destination"] = f["destination"] or dest.upper()

        except Exception as exc:
            logger.error("Scraper error %s->%s %s: %s", origin, dest, date_str, exc)
        finally:
            await browser.close()

    logger.info("scrape_flights %s->%s %s: %d result(s)", origin, dest, date_str, len(flights))
    return flights


async def scrape_flights_batch(
    searches: list[dict],
    headless: bool = True,
    timeout_ms: int = 45000,
    delay_between: float = 3.0,
) -> dict[str, list[dict]]:
    """
    Run multiple searches reusing one browser instance.

    Parameters
    ----------
    searches : list of dicts with keys: origin, dest, date_str, mode
    delay_between : seconds to wait between page loads (anti-bot)

    Returns dict mapping "{origin}-{dest}-{date}-{mode}" -> list[dict]
    """
    from playwright.async_api import async_playwright

    results: dict[str, list[dict]] = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1366, "height": 768},
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            window.chrome = { runtime: {} };
        """)

        for i, search in enumerate(searches):
            origin = search["origin"]
            dest = search["dest"]
            date_str = search["date_str"]
            mode = search.get("mode", "cash")
            key = f"{origin}-{dest}-{date_str}-{mode}"

            redemption = "true" if mode == "points" else "false"
            url = _LATAM_SEARCH_URL.format(
                origin=origin.upper(),
                dest=dest.upper(),
                date=date_str,
                redemption=redemption,
            )

            captured: list[dict] = []
            flights: list[dict] = []
            page = await context.new_page()
            _setup_interception(page, captured)

            try:
                logger.info("[%d/%d] Scraping %s", i + 1, len(searches), key)
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    await page.wait_for_selector(
                        "[class*='flight'], [class*='itinerary'], "
                        "[data-test*='flight'], ol li, [class*='offer']",
                        timeout=timeout_ms,
                    )
                    await page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    await asyncio.sleep(3)

                if captured:
                    flights = _parse_intercepted(captured, mode)
                if not flights:
                    flights = await _parse_dom(page, mode)

                for f in flights:
                    f["origin"] = f["origin"] or origin.upper()
                    f["destination"] = f["destination"] or dest.upper()

                results[key] = flights
                logger.info("[%d/%d] %s -> %d flight(s)", i + 1, len(searches), key, len(flights))

            except Exception as exc:
                logger.error("[%d/%d] Error scraping %s: %s", i + 1, len(searches), key, exc)
                results[key] = []
            finally:
                await page.close()

            if i < len(searches) - 1:
                await asyncio.sleep(delay_between)

        await browser.close()

    return results


def scrape_flights_sync(
    origin: str,
    dest: str,
    date_str: str,
    mode: str = "cash",
    headless: bool = True,
    timeout_ms: int = 45000,
) -> list[dict]:
    """Synchronous wrapper for scrape_flights()."""
    return asyncio.run(scrape_flights(origin, dest, date_str, mode, headless, timeout_ms))


def scrape_batch_sync(
    searches: list[dict],
    headless: bool = True,
    timeout_ms: int = 45000,
    delay_between: float = 3.0,
) -> dict[str, list[dict]]:
    """Synchronous wrapper for scrape_flights_batch()."""
    return asyncio.run(
        scrape_flights_batch(searches, headless, timeout_ms, delay_between)
    )


# ---------------------------------------------------------------------------
# Filtering (same interface as amadeus_client)
# ---------------------------------------------------------------------------

def filter_full_fare(flights: list[dict]) -> list[dict]:
    """Return only Full (refundable) fare flights."""
    full = [
        f for f in flights
        if f.get("is_refundable")
        or "FULL" in (f.get("fare_family") or "").upper()
    ]
    if not full and flights:
        logger.warning("No Full-fare flights among %d scraped results.", len(flights))
    return full


def filter_after_time(flights: list[dict], min_time: str = "20:00") -> list[dict]:
    """Return flights departing at or after min_time (HH:MM)."""
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
    """Return (last_flight, cheapest_flight)."""
    if not flights:
        return None, None
    last = max(flights, key=lambda f: f.get("departure_time") or "")
    cheapest = min(
        flights,
        key=lambda f: f.get("price_brl") if f.get("price_brl") is not None else float("inf"),
    )
    return last, cheapest


def merge_cash_and_points(
    cash_flights: list[dict], points_flights: list[dict]
) -> list[dict]:
    """Merge points prices into cash results by matching flight_number + departure_time."""
    pts_map: dict[tuple, int] = {}
    for pf in points_flights:
        key = (pf.get("flight_number"), pf.get("departure_time"))
        if key[0] and pf.get("points"):
            pts_map[key] = pf["points"]

    for cf in cash_flights:
        key = (cf.get("flight_number"), cf.get("departure_time"))
        if key in pts_map:
            cf["points"] = pts_map[key]

    return cash_flights
