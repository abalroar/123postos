"""
Simple APScheduler wrapper for the LATAM flight monitor.
Schedules the monitor to run at specific times every day.
"""

import logging
from typing import Callable

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


def start_scheduler(
    run_function: Callable,
    run_times: list[str],
    timezone: str = "America/Sao_Paulo",
) -> None:
    """
    Start a blocking scheduler that calls *run_function* at each time in *run_times*.

    Parameters
    ----------
    run_function : callable to invoke (no arguments)
    run_times    : list of "HH:MM" strings (e.g. ["08:00", "20:00"])
    timezone     : timezone string (default "America/Sao_Paulo")
    """
    scheduler = BlockingScheduler(timezone=timezone)

    for time_str in run_times:
        parts = time_str.strip().split(":")
        if len(parts) != 2:
            logger.warning("Invalid time format '%s' — skipping.", time_str)
            continue
        hour, minute = int(parts[0]), int(parts[1])
        scheduler.add_job(
            run_function,
            CronTrigger(hour=hour, minute=minute, timezone=timezone),
            id=f"monitor_{hour:02d}{minute:02d}",
            name=f"LATAM Monitor {hour:02d}:{minute:02d}",
            misfire_grace_time=300,  # 5 minutes grace period
        )
        logger.info("Scheduled monitor at %02d:%02d %s", hour, minute, timezone)

    logger.info("Scheduler started. Waiting for scheduled runs... (Ctrl+C to stop)")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
