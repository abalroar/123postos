"""
Amadeus API client for LATAM flight searches.

Uses the official amadeus-python SDK (amadeus==9.0.0).
Free tier: 2 000 flight-offer requests per month.
"""

import logging
import os
from datetime import datetime
from typing import Optional

from amadeus import Client, ResponseError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singleton so we authenticate only once per process.
# ---------------------------------------------------------------------------
_amadeus_client: Optional[Client] = None


def _get_client() -> Client:
    """Return (and lazily create) the authenticated Amadeus client."""
    global _amadeus_client
    if _amadeus_client is None:
        client_id = os.environ.get("AMADEUS_CLIENT_ID")
        client_secret = os.environ.get("AMADEUS_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise EnvironmentError(
                "AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET must be set in the environment."
            )
        _amadeus_client = Client(
            client_id=client_id,
            client_secret=client_secret,
            # Remove 'hostname' arg to use production; keep 'test' for sandbox:
            hostname="production",
            log_level="silent",
        )
        logger.info("Amadeus client authenticated.")
    return _amadeus_client


# ---------------------------------------------------------------------------
# Main search
# ---------------------------------------------------------------------------

def search_flights(
    origin: str,
    dest: str,
    date_str: str,
    currency: str = "BRL",
    airline_codes: Optional[list[str]] = None,
    max_results: int = 50,
) -> list[dict]:
    """
    Search for one-way flight offers via Amadeus Flight Offers Search v2.

    Parameters
    ----------
    origin       : IATA departure airport (e.g. "CGH")
    dest         : IATA arrival airport   (e.g. "BSB")
    date_str     : departure date YYYY-MM-DD
    currency     : price currency (default BRL)
    airline_codes: list of IATA airline codes to restrict search (e.g. ["LA"])
    max_results  : maximum number of offers to request (max 250)

    Returns a list of raw Amadeus offer dicts (already parsed from JSON).
    Returns an empty list on any API error.
    """
    client = _get_client()

    params: dict = {
        "originLocationCode": origin.upper(),
        "destinationLocationCode": dest.upper(),
        "departureDate": date_str,
        "adults": 1,
        "max": max_results,
        "currencyCode": currency,
        "nonStop": False,
    }
    if airline_codes:
        params["includedAirlineCodes"] = ",".join(airline_codes)

    try:
        response = client.shopping.flight_offers_search.get(**params)
        offers = response.data
        logger.info(
            "search_flights %s→%s %s: %d offer(s) returned",
            origin,
            dest,
            date_str,
            len(offers),
        )
        return offers
    except ResponseError as exc:
        # 429 = rate limit; 404 = no results; others = misc errors
        status = getattr(exc.response, "status_code", "?")
        logger.warning(
            "Amadeus API error (HTTP %s) for %s→%s on %s: %s",
            status,
            origin,
            dest,
            date_str,
            exc,
        )
        if str(status) == "429":
            logger.error(
                "Rate limit reached. You may have exhausted the free-tier quota (2 000 req/month)."
            )
        return []
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected error in search_flights: %s", exc, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Fare-family filtering
# ---------------------------------------------------------------------------

def _is_full_fare(offer: dict) -> bool:
    """
    Return True if the offer appears to be a Full / fully-refundable economy fare.

    LATAM fare hierarchy (domestic Brazil):
      Light → Standard → Full → Premium Economy

    Detection strategy (multiple fallbacks):
    1. brandedFare field on the offer
    2. fareBasis code in travelerPricings (LATAM Full uses codes starting with "Y")
    3. fareDetailsBySegment amenities / farePolicies
    4. pricingOptions.refundableFare flag (not always present)
    """
    # -- 1. Top-level brandedFare -----------------------------------------
    branded = (offer.get("brandedFare") or "").upper()
    if branded:
        if any(kw in branded for kw in ("FULL", "FLEX", "PREMIUM")):
            return True
        # explicitly exclude cheaper families
        if any(kw in branded for kw in ("LIGHT", "BASIC", "ECO", "STANDARD")):
            return False

    # -- 2. travelerPricings / fareDetailsBySegment -----------------------
    for tp in offer.get("travelerPricings", []):
        for seg in tp.get("fareDetailsBySegment", []):
            fare_basis = (seg.get("fareBasis") or "").upper()
            # LATAM Full-fare basis codes typically start with Y or contain FULL
            if fare_basis.startswith("Y") and len(fare_basis) >= 1:
                return True
            if "FULL" in fare_basis:
                return True

            # amenities: look for CHANGEABLE + REFUNDABLE
            amenities = seg.get("amenities", [])
            has_refund = False
            has_change = False
            for amenity in amenities:
                desc = (amenity.get("description") or "").upper()
                amenity_type = (amenity.get("amenityType") or "").upper()
                is_chargeable = amenity.get("isChargeable", True)
                if not is_chargeable:
                    if "REFUND" in desc or "REFUND" in amenity_type:
                        has_refund = True
                    if "CHANGE" in desc or "REISSUE" in desc:
                        has_change = True
            if has_refund and has_change:
                return True

    # -- 3. pricingOptions ------------------------------------------------
    pricing_opts = offer.get("pricingOptions", {})
    if pricing_opts.get("refundableFare") is True:
        return True

    # -- 4. brandedFareLabel (alternate field name) -----------------------
    label = (offer.get("brandedFareLabel") or "").upper()
    if "FULL" in label or "FLEX" in label:
        return True

    return False


def filter_full_fare(offers: list[dict]) -> list[dict]:
    """Return only the offers that match the Full / refundable fare family."""
    filtered = [o for o in offers if _is_full_fare(o)]
    logger.debug(
        "filter_full_fare: %d/%d offers passed", len(filtered), len(offers)
    )
    if not filtered and offers:
        logger.warning(
            "No Full-fare offers found among %d offer(s). "
            "The Amadeus test environment may not carry LATAM branded fares. "
            "Returning all offers as fallback for inspection.",
            len(offers),
        )
    return filtered


# ---------------------------------------------------------------------------
# Time-based filtering
# ---------------------------------------------------------------------------

def filter_after_time(
    offers: list[dict], min_time: str = "20:00"
) -> list[dict]:
    """
    Return offers whose first segment departs at or after *min_time* (HH:MM).

    Parameters
    ----------
    offers   : list of Amadeus offer dicts
    min_time : minimum departure time in "HH:MM" format
    """
    min_h, min_m = (int(x) for x in min_time.split(":"))
    result = []
    for offer in offers:
        dep_str = _get_first_departure(offer)
        if dep_str is None:
            continue
        try:
            dep_dt = datetime.fromisoformat(dep_str)
            if dep_dt.hour > min_h or (dep_dt.hour == min_h and dep_dt.minute >= min_m):
                result.append(offer)
        except ValueError:
            logger.debug("Could not parse departure time: %s", dep_str)
    logger.debug(
        "filter_after_time(%s): %d/%d offers passed", min_time, len(result), len(offers)
    )
    return result


# ---------------------------------------------------------------------------
# Last-or-cheapest selection
# ---------------------------------------------------------------------------

def filter_last_or_cheapest(
    offers: list[dict],
) -> tuple[Optional[dict], Optional[dict]]:
    """
    Given a list of offers, return (last_flight, cheapest_flight).

    last_flight    : offer with the latest departure time
    cheapest_flight: offer with the lowest total price

    Both may be None if *offers* is empty.
    Both may point to the same offer if only one offer exists.
    """
    if not offers:
        return None, None

    # Sort by departure time ascending to find last
    def dep_key(o: dict) -> str:
        return _get_first_departure(o) or ""

    def price_key(o: dict) -> float:
        return _get_total_price(o)

    last_flight = max(offers, key=dep_key)
    cheapest_flight = min(offers, key=price_key)
    return last_flight, cheapest_flight


# ---------------------------------------------------------------------------
# Offer → structured dict
# ---------------------------------------------------------------------------

def parse_offer(offer: dict, origin: str, dest: str) -> dict:
    """
    Convert a raw Amadeus offer dict into a clean structured dict.

    Returns
    -------
    dict with keys:
        departure_time, arrival_time, price_brl, duration,
        fare_family, flight_number, booking_class,
        is_refundable, origin, destination, raw_offer
    """
    # Price
    price_brl = _get_total_price(offer)

    # Itinerary / first segment
    itineraries = offer.get("itineraries", [])
    first_itin = itineraries[0] if itineraries else {}
    segments = first_itin.get("segments", [])
    first_seg = segments[0] if segments else {}
    last_seg = segments[-1] if segments else {}

    dep_at = first_seg.get("departure", {}).get("at", "")
    arr_at = last_seg.get("arrival", {}).get("at", "")
    duration = first_itin.get("duration", "")

    # Flight number
    carrier = first_seg.get("carrierCode", "")
    flight_no = first_seg.get("number", "")
    flight_number = f"{carrier}{flight_no}".upper()

    # Fare details
    branded_fare = (offer.get("brandedFare") or offer.get("brandedFareLabel") or "").upper()
    booking_class = None
    fare_basis = None
    for tp in offer.get("travelerPricings", []):
        for seg in tp.get("fareDetailsBySegment", []):
            booking_class = seg.get("class") or booking_class
            fare_basis = seg.get("fareBasis") or fare_basis

    is_refundable = _is_full_fare(offer)

    # Friendly time strings (strip date part)
    def _time_only(iso: str) -> str:
        try:
            return datetime.fromisoformat(iso).strftime("%H:%M")
        except ValueError:
            return iso

    return {
        "origin": origin.upper(),
        "destination": dest.upper(),
        "departure_time": _time_only(dep_at),
        "departure_datetime": dep_at,
        "arrival_time": _time_only(arr_at),
        "arrival_datetime": arr_at,
        "price_brl": price_brl,
        "duration": duration,
        "fare_family": branded_fare or (fare_basis or ""),
        "flight_number": flight_number,
        "booking_class": booking_class,
        "is_refundable": is_refundable,
        "raw_offer": offer,
    }


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _get_first_departure(offer: dict) -> Optional[str]:
    """Extract the ISO departure datetime string of the first segment."""
    try:
        return offer["itineraries"][0]["segments"][0]["departure"]["at"]
    except (KeyError, IndexError):
        return None


def _get_total_price(offer: dict) -> float:
    """Extract the grand total price as a float."""
    try:
        return float(offer["price"]["grandTotal"])
    except (KeyError, TypeError, ValueError):
        return float("inf")
