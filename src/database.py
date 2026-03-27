"""
SQLite database module for storing and retrieving flight price history.
"""

import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def get_connection(db_path: str) -> sqlite3.Connection:
    """Return a SQLite connection with row_factory set."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    """Initialize the database and create tables if they don't exist."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS price_checks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                origin        TEXT    NOT NULL,
                destination   TEXT    NOT NULL,
                date          TEXT    NOT NULL,
                departure_time TEXT   NOT NULL,
                arrival_time   TEXT   NOT NULL,
                airline        TEXT   NOT NULL,
                flight_number  TEXT   NOT NULL,
                fare_family    TEXT   NOT NULL,
                booking_class  TEXT,
                price_brl      REAL   NOT NULL,
                is_refundable  INTEGER NOT NULL DEFAULT 0,
                duration       TEXT,
                checked_at     TEXT   NOT NULL
            )
            """
        )
        # Index for faster history lookups
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_route_date
            ON price_checks (origin, destination, date)
            """
        )
        conn.commit()
        logger.info("Database initialised at %s", db_path)
    finally:
        conn.close()


def save_price_check(
    db_path: str,
    origin: str,
    destination: str,
    date: str,
    departure_time: str,
    arrival_time: str,
    airline: str,
    flight_number: str,
    fare_family: str,
    booking_class: Optional[str],
    price_brl: float,
    is_refundable: bool,
    duration: Optional[str] = None,
) -> int:
    """
    Insert a price check record and return the new row id.

    Parameters
    ----------
    db_path       : path to the SQLite file
    origin        : IATA origin code  (e.g. "CGH")
    destination   : IATA dest code    (e.g. "BSB")
    date          : flight date        (YYYY-MM-DD)
    departure_time: departure HH:MM
    arrival_time  : arrival   HH:MM
    airline       : IATA airline code  (e.g. "LA")
    flight_number : full flight number (e.g. "LA3580")
    fare_family   : fare family label  (e.g. "FULL")
    booking_class : booking class code (e.g. "Y")
    price_brl     : total price in BRL
    is_refundable : whether the fare is fully refundable
    duration      : ISO 8601 duration  (e.g. "PT1H30M")
    """
    checked_at = datetime.utcnow().isoformat()
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO price_checks
                (origin, destination, date, departure_time, arrival_time,
                 airline, flight_number, fare_family, booking_class,
                 price_brl, is_refundable, duration, checked_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                origin.upper(),
                destination.upper(),
                date,
                departure_time,
                arrival_time,
                airline.upper(),
                flight_number.upper(),
                fare_family.upper(),
                booking_class,
                price_brl,
                1 if is_refundable else 0,
                duration,
                checked_at,
            ),
        )
        conn.commit()
        row_id = cursor.lastrowid
        logger.debug(
            "Saved price check id=%s  %s→%s  %s  R$%.2f",
            row_id,
            origin,
            destination,
            date,
            price_brl,
        )
        return row_id
    finally:
        conn.close()


def get_price_history(
    db_path: str,
    origin: str,
    destination: str,
    days: int = 90,
    fare_family: Optional[str] = None,
) -> list[dict]:
    """
    Return price_checks rows for the given route within the last *days* days.

    Each row is returned as a plain dict.
    """
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        if fare_family:
            cursor.execute(
                """
                SELECT * FROM price_checks
                WHERE origin = ?
                  AND destination = ?
                  AND fare_family = ?
                  AND checked_at >= ?
                ORDER BY checked_at DESC
                """,
                (
                    origin.upper(),
                    destination.upper(),
                    fare_family.upper(),
                    since,
                ),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM price_checks
                WHERE origin = ?
                  AND destination = ?
                  AND checked_at >= ?
                ORDER BY checked_at DESC
                """,
                (origin.upper(), destination.upper(), since),
            )
        rows = [dict(row) for row in cursor.fetchall()]
        logger.debug(
            "get_price_history: %d rows for %s→%s (last %d days)",
            len(rows),
            origin,
            destination,
            days,
        )
        return rows
    finally:
        conn.close()


def get_all_routes(db_path: str) -> list[dict]:
    """Return distinct (origin, destination) pairs stored in the DB."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT origin, destination FROM price_checks ORDER BY origin, destination"
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
