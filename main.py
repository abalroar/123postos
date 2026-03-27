"""
LATAM Flight Price Monitor — main entry point.

Usage
-----
    python main.py              # run monitor once, immediately
    python main.py --schedule   # run on schedule (08:00 and 20:00 BRT)
    python main.py --test       # test Telegram connection only

Configuration
-------------
    config.yaml   — routes, schedule, thresholds
    .env          — API credentials (AMADEUS_CLIENT_ID, AMADEUS_CLIENT_SECRET,
                    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
"""

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap: load .env before importing anything that reads env vars
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("latam_monitor")

# ---------------------------------------------------------------------------
# Project imports (after env is loaded)
# ---------------------------------------------------------------------------
from src.amadeus_client import (  # noqa: E402
    search_flights,
    filter_full_fare,
    filter_after_time,
    filter_last_or_cheapest,
    parse_offer,
)
from src.database import init_db, save_price_check  # noqa: E402
from src.price_analyzer import (  # noqa: E402
    load_history,
    get_route_stats,
    evaluate_deal,
)
from src.telegram_notifier import (  # noqa: E402
    format_weekday_alert,
    format_sunday_alert,
    send_daily_summary,
    send_message,
    test_connection,
)
from src.scheduler import start_scheduler  # noqa: E402


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config(path: str = "config.yaml") -> dict:
    cfg_path = _ROOT / path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _next_n_dates_for_weekday(weekday: int, weeks_ahead: int) -> list[str]:
    """
    Return the next *weeks_ahead* occurrences of *weekday* (0=Mon … 6=Sun)
    starting from tomorrow.

    Returns dates as YYYY-MM-DD strings.
    """
    today = date.today()
    results = []
    d = today + timedelta(days=1)
    while len(results) < weeks_ahead:
        if d.weekday() == weekday:
            results.append(d.isoformat())
        d += timedelta(days=1)
    return results


def _get_dates_for_route(days_of_week: list[int], weeks_ahead: int) -> list[str]:
    """Return upcoming dates matching any day-of-week in *days_of_week*."""
    all_dates = []
    for dow in days_of_week:
        all_dates.extend(_next_n_dates_for_weekday(dow, weeks_ahead))
    return sorted(set(all_dates))


# ---------------------------------------------------------------------------
# Weekday monitor (CGH→BSB Wed/Fri after 20:00, Full fare)
# ---------------------------------------------------------------------------

def check_weekday_routes(config: dict, history: list[dict], db_path: str) -> list[dict]:
    """
    Search for CGH→BSB flights on the configured weekdays.

    Returns a list of result summary dicts for the daily summary.
    """
    results = []
    weekday_routes = config.get("monitoring", {}).get("weekday_routes", [])
    alert_cfg = config.get("alerts", {})
    alert_without_history = alert_cfg.get("alert_without_history", True)

    for route in weekday_routes:
        origin = route["origin"]
        dest = route["destination"]
        days_of_week = route.get("days_of_week", [2, 4])
        min_dep_time = route.get("min_departure_time", "20:00")
        airline = route.get("airline", "LA")
        fare_family = route.get("fare_family", "FULL")
        currency = route.get("currency", "BRL")
        weeks_ahead = route.get("weeks_ahead", 3)

        dates = _get_dates_for_route(days_of_week, weeks_ahead)
        stats = get_route_stats(history, origin, dest, fare_family)

        for date_str in dates:
            logger.info("Checking weekday route %s→%s on %s", origin, dest, date_str)

            offers = search_flights(
                origin, dest, date_str,
                currency=currency,
                airline_codes=[airline],
            )

            if not offers:
                results.append({
                    "route": f"{origin}→{dest}",
                    "date": date_str,
                    "status": "not_found",
                    "price_brl": None,
                    "verdict": "—",
                    "verdict_emoji": "⚫",
                })
                continue

            # Filter: Full fare
            full_offers = filter_full_fare(offers)
            if not full_offers:
                logger.warning(
                    "No Full-fare offers for %s→%s on %s — using all offers as fallback.",
                    origin, dest, date_str,
                )
                full_offers = offers  # fallback: use all for inspection

            # Filter: after min_departure_time
            late_offers = filter_after_time(full_offers, min_time=min_dep_time)

            if not late_offers:
                logger.info(
                    "No flights after %s for %s→%s on %s.",
                    min_dep_time, origin, dest, date_str,
                )
                results.append({
                    "route": f"{origin}→{dest}",
                    "date": date_str,
                    "status": "not_found",
                    "price_brl": None,
                    "verdict": f"nenhum após {min_dep_time}",
                    "verdict_emoji": "⚫",
                })
                continue

            # Pick the cheapest among qualifying offers
            best_offer = min(
                late_offers,
                key=lambda o: float(o.get("price", {}).get("grandTotal", float("inf")))
                if "price" in o else float("inf"),
            )
            flight = parse_offer(best_offer, origin, dest)

            # Save to DB
            try:
                save_price_check(
                    db_path=db_path,
                    origin=origin,
                    destination=dest,
                    date=date_str,
                    departure_time=flight["departure_time"],
                    arrival_time=flight["arrival_time"],
                    airline=airline,
                    flight_number=flight["flight_number"],
                    fare_family=flight.get("fare_family") or fare_family,
                    booking_class=flight.get("booking_class"),
                    price_brl=flight["price_brl"],
                    is_refundable=flight.get("is_refundable", False),
                    duration=flight.get("duration"),
                )
            except Exception as exc:
                logger.error("DB save error: %s", exc)

            # Evaluate deal
            evaluation = evaluate_deal(flight["price_brl"], stats)

            # Decide whether to send alert
            should_alert = evaluation["is_good_deal"] or (
                alert_without_history and stats["count"] == 0
            )

            result_entry = {
                "route": f"{origin}→{dest}",
                "date": date_str,
                "status": "found",
                "price_brl": flight["price_brl"],
                "verdict": evaluation["verdict"],
                "verdict_emoji": evaluation.get("verdict_emoji", "⚪"),
                "alert_sent": False,
            }

            if should_alert:
                msg = format_weekday_alert(
                    flight=flight,
                    evaluation=evaluation,
                    stats=stats,
                    date_str=date_str,
                    route_config=route,
                )
                ok = send_message(msg)
                result_entry["alert_sent"] = ok
                if ok:
                    logger.info(
                        "Alert sent for %s→%s on %s: R$ %.2f (%s)",
                        origin, dest, date_str, flight["price_brl"], evaluation["verdict"],
                    )

            results.append(result_entry)

    return results


# ---------------------------------------------------------------------------
# Sunday monitor (BSB↔CGH — last flight OR cheapest)
# ---------------------------------------------------------------------------

def check_sunday_routes(config: dict, history: list[dict], db_path: str) -> list[dict]:
    """
    Search for Sunday BSB↔CGH and CGH↔BSB flights.

    Returns a list of result summary dicts for the daily summary.
    """
    results = []
    sunday_routes = config.get("monitoring", {}).get("sunday_routes", [])
    alert_cfg = config.get("alerts", {})
    alert_without_history = alert_cfg.get("alert_without_history", True)

    for route in sunday_routes:
        origin = route["origin"]
        dest = route["destination"]
        days_of_week = route.get("days_of_week", [6])
        currency = route.get("currency", "BRL")
        weeks_ahead = route.get("weeks_ahead", 3)

        dates = _get_dates_for_route(days_of_week, weeks_ahead)
        stats = get_route_stats(history, origin, dest)

        for date_str in dates:
            logger.info("Checking Sunday route %s→%s on %s", origin, dest, date_str)

            offers = search_flights(
                origin, dest, date_str,
                currency=currency,
                airline_codes=["LA"],
            )

            if not offers:
                results.append({
                    "route": f"{origin}→{dest}",
                    "date": date_str,
                    "status": "not_found",
                    "price_brl": None,
                    "verdict": "—",
                    "verdict_emoji": "⚫",
                })
                continue

            last_flight_raw, cheapest_raw = filter_last_or_cheapest(offers)

            last_flight = parse_offer(last_flight_raw, origin, dest) if last_flight_raw else None
            cheapest_flight = parse_offer(cheapest_raw, origin, dest) if cheapest_raw else None

            # Save both to DB (skip duplicates if they're the same flight)
            saved_flight_numbers = set()
            for flt, label in [(last_flight, "last"), (cheapest_flight, "cheapest")]:
                if flt is None:
                    continue
                fn_key = f"{flt['flight_number']}_{flt['departure_time']}"
                if fn_key in saved_flight_numbers:
                    continue
                saved_flight_numbers.add(fn_key)
                try:
                    save_price_check(
                        db_path=db_path,
                        origin=origin,
                        destination=dest,
                        date=date_str,
                        departure_time=flt["departure_time"],
                        arrival_time=flt["arrival_time"],
                        airline="LA",
                        flight_number=flt["flight_number"],
                        fare_family=flt.get("fare_family") or "STANDARD",
                        booking_class=flt.get("booking_class"),
                        price_brl=flt["price_brl"],
                        is_refundable=flt.get("is_refundable", False),
                        duration=flt.get("duration"),
                    )
                except Exception as exc:
                    logger.error("DB save error (%s flight): %s", label, exc)

            # Evaluate
            eval_last = evaluate_deal(last_flight["price_brl"], stats) if last_flight else None
            eval_cheap = evaluate_deal(cheapest_flight["price_brl"], stats) if cheapest_flight else None

            # Always send Sunday alert (user wants to plan returns)
            primary = last_flight or cheapest_flight
            primary_eval = eval_last or eval_cheap

            should_alert = primary is not None and (
                (primary_eval and primary_eval["is_good_deal"])
                or alert_without_history
            )

            result_entry = {
                "route": f"{origin}→{dest}",
                "date": date_str,
                "status": "found",
                "price_brl": primary["price_brl"] if primary else None,
                "verdict": primary_eval["verdict"] if primary_eval else "—",
                "verdict_emoji": primary_eval.get("verdict_emoji", "⚪") if primary_eval else "⚪",
                "alert_sent": False,
            }

            if should_alert:
                msg = format_sunday_alert(
                    last_flight=last_flight,
                    cheapest_flight=cheapest_flight,
                    evaluation_last=eval_last,
                    evaluation_cheap=eval_cheap,
                    stats=stats,
                    date_str=date_str,
                    route_config=route,
                )
                ok = send_message(msg)
                result_entry["alert_sent"] = ok

            results.append(result_entry)

    return results


# ---------------------------------------------------------------------------
# Main monitor orchestrator
# ---------------------------------------------------------------------------

def run_monitor() -> None:
    """Run a full monitoring cycle: weekday routes + Sunday routes + summary."""
    logger.info("=== LATAM Monitor starting ===")

    config = load_config()
    db_path = str(_ROOT / config["data"]["database"])
    history_csv = str(_ROOT / config["data"]["history_csv"])

    # Initialize DB
    init_db(db_path)

    # Load historical prices
    history = load_history(history_csv)

    all_results = []

    # Check weekday routes (CGH→BSB Wed/Fri after 20:00, Full fare)
    try:
        wd_results = check_weekday_routes(config, history, db_path)
        all_results.extend(wd_results)
        logger.info("Weekday routes: %d results.", len(wd_results))
    except Exception as exc:
        logger.error("Error in check_weekday_routes: %s", exc, exc_info=True)

    # Check Sunday routes (BSB↔CGH last/cheapest)
    try:
        sun_results = check_sunday_routes(config, history, db_path)
        all_results.extend(sun_results)
        logger.info("Sunday routes: %d results.", len(sun_results))
    except Exception as exc:
        logger.error("Error in check_sunday_routes: %s", exc, exc_info=True)

    # Send daily summary
    try:
        send_daily_summary(all_results)
    except Exception as exc:
        logger.error("Error sending daily summary: %s", exc, exc_info=True)

    logger.info("=== LATAM Monitor finished (%d route-dates checked) ===", len(all_results))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="LATAM Flight Price Monitor — CGH↔BSB"
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run on schedule (default: 08:00 and 20:00 BRT). Blocks until Ctrl+C.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Send a Telegram test message and exit.",
    )
    args = parser.parse_args()

    if args.test:
        logger.info("Testing Telegram connection...")
        ok = test_connection()
        sys.exit(0 if ok else 1)

    if args.schedule:
        config = load_config()
        sched_cfg = config.get("schedule", {})
        run_times = sched_cfg.get("run_times", ["08:00", "20:00"])
        timezone = sched_cfg.get("timezone", "America/Sao_Paulo")

        logger.info(
            "Starting scheduled monitor at %s (%s timezone).",
            ", ".join(run_times),
            timezone,
        )
        start_scheduler(run_monitor, run_times, timezone)
    else:
        run_monitor()


if __name__ == "__main__":
    main()
