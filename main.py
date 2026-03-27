"""
LATAM Flight Price Monitor — main entry point.

Usage
-----
    python main.py              # run monitor once, immediately
    python main.py --schedule   # run on schedule (08:00 and 20:00 BRT)
    python main.py --test       # test Telegram connection only

Data sources
------------
    scraper  (default) — Playwright scraping of latamairlines.com (R$ + pontos)
    amadeus            — Amadeus Flight Offers API (R$ only, free tier)
    auto               — scraper first, Amadeus fallback

Configuration
-------------
    config.yaml   — routes, schedule, thresholds, data source
    .env          — credentials (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                    optionally AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET)
"""

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("latam_monitor")

from src.flight_search import search_flights, filter_last_or_cheapest  # noqa: E402
from src.database import init_db, save_price_check  # noqa: E402
from src.price_analyzer import load_history, get_route_stats, evaluate_deal  # noqa: E402
from src.telegram_notifier import (  # noqa: E402
    format_weekday_alert,
    format_sunday_alert,
    send_daily_summary,
    send_message,
    test_connection,
)
from src.scheduler import start_scheduler  # noqa: E402


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str = "config.yaml") -> dict:
    with open(_ROOT / path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _next_n_dates_for_weekday(weekday: int, weeks_ahead: int) -> list[str]:
    today = date.today()
    results = []
    d = today + timedelta(days=1)
    while len(results) < weeks_ahead:
        if d.weekday() == weekday:
            results.append(d.isoformat())
        d += timedelta(days=1)
    return results


def _get_dates_for_route(days_of_week: list[int], weeks_ahead: int) -> list[str]:
    all_dates = []
    for dow in days_of_week:
        all_dates.extend(_next_n_dates_for_weekday(dow, weeks_ahead))
    return sorted(set(all_dates))


# ---------------------------------------------------------------------------
# Weekday monitor (CGH→BSB Wed/Fri after 20:00, Full fare)
# ---------------------------------------------------------------------------

def check_weekday_routes(config: dict, history: list[dict], db_path: str) -> list[dict]:
    results = []
    weekday_routes = config.get("monitoring", {}).get("weekday_routes", [])
    alert_cfg = config.get("alerts", {})
    alert_without_history = alert_cfg.get("alert_without_history", True)
    data_source = config.get("data_source", "scraper")
    fetch_points = config.get("fetch_points", True)

    for route in weekday_routes:
        origin = route["origin"]
        dest = route["destination"]
        days_of_week = route.get("days_of_week", [2, 4])
        min_dep_time = route.get("min_departure_time", "20:00")
        fare_family = route.get("fare_family", "FULL")
        weeks_ahead = route.get("weeks_ahead", 3)

        dates = _get_dates_for_route(days_of_week, weeks_ahead)
        stats = get_route_stats(history, origin, dest, fare_family)

        for date_str in dates:
            logger.info("Checking weekday %s->%s on %s", origin, dest, date_str)

            flights = search_flights(
                origin, dest, date_str,
                data_source=data_source,
                fetch_points=fetch_points,
                fare_family_filter=fare_family,
                min_dep_time=min_dep_time,
            )

            if not flights:
                results.append({
                    "route": f"{origin}->{dest}",
                    "date": date_str,
                    "status": "not_found",
                    "price_brl": None,
                    "verdict": f"nenhum Full apos {min_dep_time}",
                    "verdict_emoji": "⚫",
                })
                continue

            # Pick cheapest qualifying flight
            best = min(
                flights,
                key=lambda f: f.get("price_brl") if f.get("price_brl") is not None else float("inf"),
            )

            # Save to DB
            try:
                save_price_check(
                    db_path=db_path,
                    origin=origin,
                    destination=dest,
                    date=date_str,
                    departure_time=best.get("departure_time", ""),
                    arrival_time=best.get("arrival_time", ""),
                    airline="LA",
                    flight_number=best.get("flight_number", ""),
                    fare_family=best.get("fare_family") or fare_family,
                    booking_class=best.get("booking_class"),
                    price_brl=best.get("price_brl", 0),
                    is_refundable=best.get("is_refundable", False),
                    duration=best.get("duration"),
                )
            except Exception as exc:
                logger.error("DB save error: %s", exc)

            evaluation = evaluate_deal(best.get("price_brl", 0), stats)

            should_alert = evaluation["is_good_deal"] or (
                alert_without_history and stats["count"] == 0
            )

            result_entry = {
                "route": f"{origin}->{dest}",
                "date": date_str,
                "status": "found",
                "price_brl": best.get("price_brl"),
                "points": best.get("points"),
                "verdict": evaluation["verdict"],
                "verdict_emoji": evaluation.get("verdict_emoji", "⚪"),
                "alert_sent": False,
            }

            if should_alert:
                msg = format_weekday_alert(
                    flight=best,
                    evaluation=evaluation,
                    stats=stats,
                    date_str=date_str,
                    route_config=route,
                )
                ok = send_message(msg)
                result_entry["alert_sent"] = ok
                if ok:
                    logger.info(
                        "Alert sent %s->%s %s: R$ %.2f (%s)",
                        origin, dest, date_str,
                        best.get("price_brl", 0), evaluation["verdict"],
                    )

            results.append(result_entry)

    return results


# ---------------------------------------------------------------------------
# Sunday monitor (BSB↔CGH — last flight OR cheapest)
# ---------------------------------------------------------------------------

def check_sunday_routes(config: dict, history: list[dict], db_path: str) -> list[dict]:
    results = []
    sunday_routes = config.get("monitoring", {}).get("sunday_routes", [])
    alert_cfg = config.get("alerts", {})
    alert_without_history = alert_cfg.get("alert_without_history", True)
    data_source = config.get("data_source", "scraper")
    fetch_points = config.get("fetch_points", True)

    for route in sunday_routes:
        origin = route["origin"]
        dest = route["destination"]
        days_of_week = route.get("days_of_week", [6])
        weeks_ahead = route.get("weeks_ahead", 3)

        dates = _get_dates_for_route(days_of_week, weeks_ahead)
        stats = get_route_stats(history, origin, dest)

        for date_str in dates:
            logger.info("Checking Sunday %s->%s on %s", origin, dest, date_str)

            flights = search_flights(
                origin, dest, date_str,
                data_source=data_source,
                fetch_points=fetch_points,
            )

            if not flights:
                results.append({
                    "route": f"{origin}->{dest}",
                    "date": date_str,
                    "status": "not_found",
                    "price_brl": None,
                    "verdict": "---",
                    "verdict_emoji": "⚫",
                })
                continue

            last_flight, cheapest_flight = filter_last_or_cheapest(flights)

            # Save to DB (deduplicate if same flight)
            saved = set()
            for flt, label in [(last_flight, "last"), (cheapest_flight, "cheapest")]:
                if flt is None:
                    continue
                fn_key = f"{flt.get('flight_number')}_{flt.get('departure_time')}"
                if fn_key in saved:
                    continue
                saved.add(fn_key)
                try:
                    save_price_check(
                        db_path=db_path,
                        origin=origin,
                        destination=dest,
                        date=date_str,
                        departure_time=flt.get("departure_time", ""),
                        arrival_time=flt.get("arrival_time", ""),
                        airline="LA",
                        flight_number=flt.get("flight_number", ""),
                        fare_family=flt.get("fare_family") or "STANDARD",
                        booking_class=flt.get("booking_class"),
                        price_brl=flt.get("price_brl", 0),
                        is_refundable=flt.get("is_refundable", False),
                        duration=flt.get("duration"),
                    )
                except Exception as exc:
                    logger.error("DB save error (%s): %s", label, exc)

            eval_last = evaluate_deal(last_flight["price_brl"], stats) if last_flight and last_flight.get("price_brl") else None
            eval_cheap = evaluate_deal(cheapest_flight["price_brl"], stats) if cheapest_flight and cheapest_flight.get("price_brl") else None

            primary = last_flight or cheapest_flight
            primary_eval = eval_last or eval_cheap
            should_alert = primary is not None and (
                (primary_eval and primary_eval["is_good_deal"])
                or alert_without_history
            )

            result_entry = {
                "route": f"{origin}->{dest}",
                "date": date_str,
                "status": "found",
                "price_brl": primary.get("price_brl") if primary else None,
                "points": primary.get("points") if primary else None,
                "verdict": primary_eval["verdict"] if primary_eval else "---",
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
# Orchestrator
# ---------------------------------------------------------------------------

def run_monitor() -> None:
    logger.info("=== LATAM Monitor starting ===")

    config = load_config()
    db_path = str(_ROOT / config["data"]["database"])
    history_csv = str(_ROOT / config["data"]["history_csv"])

    init_db(db_path)
    history = load_history(history_csv)

    all_results = []

    try:
        wd = check_weekday_routes(config, history, db_path)
        all_results.extend(wd)
        logger.info("Weekday routes: %d results.", len(wd))
    except Exception as exc:
        logger.error("Error in weekday routes: %s", exc, exc_info=True)

    try:
        sun = check_sunday_routes(config, history, db_path)
        all_results.extend(sun)
        logger.info("Sunday routes: %d results.", len(sun))
    except Exception as exc:
        logger.error("Error in sunday routes: %s", exc, exc_info=True)

    try:
        send_daily_summary(all_results)
    except Exception as exc:
        logger.error("Error sending summary: %s", exc, exc_info=True)

    logger.info("=== Monitor finished (%d route-dates checked) ===", len(all_results))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="LATAM Flight Price Monitor")
    parser.add_argument("--schedule", action="store_true", help="Run on schedule (blocks)")
    parser.add_argument("--test", action="store_true", help="Test Telegram and exit")
    parser.add_argument(
        "--source", choices=["scraper", "amadeus", "auto"], default=None,
        help="Override data source (default: from config.yaml)",
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
        logger.info("Starting scheduler at %s (%s).", ", ".join(run_times), timezone)
        start_scheduler(run_monitor, run_times, timezone)
    else:
        run_monitor()


if __name__ == "__main__":
    main()
