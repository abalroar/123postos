"""
Historical price analysis for the LATAM flight monitor.

Loads the user's purchase history CSV and computes per-route statistics
(p25 / p50 / p75) used to evaluate whether a current fare is a good deal.
"""

import csv
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# History loading
# ---------------------------------------------------------------------------

# Accepted column aliases → canonical name
_COL_ALIASES: dict[str, str] = {
    # date
    "data": "date", "date": "date", "data_voo": "date", "flight_date": "date",
    # origin
    "origem": "origin", "origin": "origin", "from": "origin",
    # destination
    "destino": "destination", "destination": "destination", "dest": "destination", "to": "destination",
    # price brl
    "preco": "price_brl", "preço": "price_brl", "valor": "price_brl",
    "price_brl": "price_brl", "price": "price_brl", "valor_r$": "price_brl",
    "valor_rs": "price_brl", "r$": "price_brl",
    # points
    "pontos": "points", "milhas": "points", "points": "points", "miles": "points",
    # fare family
    "tarifa": "fare_family", "fare_family": "fare_family", "fare": "fare_family",
    "classe_tarifa": "fare_family",
    # flight number
    "voo": "flight_number", "flight_number": "flight_number", "flight": "flight_number",
    "numero_voo": "flight_number",
    # notes
    "notas": "notes", "notes": "notes", "obs": "notes",
}


def _normalise_header(raw: str) -> str:
    return _COL_ALIASES.get(raw.strip().lower().replace(" ", "_"), raw.strip().lower())


def load_history(csv_path: str) -> list[dict]:
    """
    Load the user's flight purchase history from *csv_path*.

    Returns a list of dicts with canonical keys:
        date, origin, destination, price_brl, points, fare_family, flight_number, notes

    Missing optional columns are filled with None.
    Rows with missing origin/destination/price are skipped.
    """
    if not os.path.exists(csv_path):
        logger.warning("History CSV not found at %s — starting with no history.", csv_path)
        return []

    records: list[dict] = []
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            logger.warning("Empty CSV at %s", csv_path)
            return []

        # Map raw headers → canonical
        header_map = {h: _normalise_header(h) for h in reader.fieldnames}

        for raw_row in reader:
            row = {header_map[k]: v.strip() for k, v in raw_row.items() if k}

            origin = (row.get("origin") or "").upper().strip()
            destination = (row.get("destination") or "").upper().strip()
            price_raw = row.get("price_brl") or ""

            if not origin or not destination:
                continue
            # Try to parse price — handle both BRL formats:
            #   Brazilian: "1.234,56" (dot=thousands, comma=decimal)
            #   Standard:  "1234.56"  (dot=decimal)
            price_brl: Optional[float] = None
            try:
                cleaned = price_raw.replace("R$", "").replace("\xa0", "").replace(" ", "").strip()
                if "," in cleaned and "." in cleaned:
                    # Both separators: dot=thousands, comma=decimal  → "1.234,56"
                    cleaned = cleaned.replace(".", "").replace(",", ".")
                elif "," in cleaned:
                    # Only comma: comma=decimal → "489,90"
                    cleaned = cleaned.replace(",", ".")
                # else: standard dot-decimal "489.90" — use as-is
                price_brl = float(cleaned)
            except ValueError:
                pass  # no price — still useful for points-only rows

            points_raw = row.get("points") or ""
            points: Optional[int] = None
            try:
                points = int(points_raw.replace(".", "").replace(",", "").strip())
            except ValueError:
                pass

            records.append(
                {
                    "date": row.get("date", ""),
                    "origin": origin,
                    "destination": destination,
                    "price_brl": price_brl,
                    "points": points,
                    "fare_family": (row.get("fare_family") or "").upper(),
                    "flight_number": row.get("flight_number", ""),
                    "notes": row.get("notes", ""),
                }
            )

    logger.info("Loaded %d records from history CSV.", len(records))
    return records


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def get_route_stats(
    history: list[dict],
    origin: str,
    destination: str,
    fare_family: Optional[str] = None,
) -> dict:
    """
    Compute price statistics for a given route (and optionally fare family).

    Returns
    -------
    dict with keys:
        count, mean, p25, p50, p75, min, max
    All price values in BRL.  Returns None values if count == 0.
    """
    prices = [
        r["price_brl"]
        for r in history
        if r["origin"] == origin.upper()
        and r["destination"] == destination.upper()
        and r["price_brl"] is not None
        and (
            fare_family is None
            or r["fare_family"] == fare_family.upper()
            or not r["fare_family"]  # include rows with no fare info
        )
    ]

    if not prices:
        return {
            "count": 0,
            "mean": None,
            "p25": None,
            "p50": None,
            "p75": None,
            "min": None,
            "max": None,
        }

    prices_sorted = sorted(prices)
    n = len(prices_sorted)

    def percentile(data: list[float], pct: float) -> float:
        """Simple linear-interpolation percentile (no numpy needed)."""
        if len(data) == 1:
            return data[0]
        idx = (pct / 100) * (len(data) - 1)
        lo = int(idx)
        hi = lo + 1
        frac = idx - lo
        if hi >= len(data):
            return data[-1]
        return data[lo] + frac * (data[hi] - data[lo])

    return {
        "count": n,
        "mean": sum(prices_sorted) / n,
        "p25": percentile(prices_sorted, 25),
        "p50": percentile(prices_sorted, 50),
        "p75": percentile(prices_sorted, 75),
        "min": prices_sorted[0],
        "max": prices_sorted[-1],
    }


# ---------------------------------------------------------------------------
# Deal evaluation
# ---------------------------------------------------------------------------

def evaluate_deal(price_brl: float, stats: dict) -> dict:
    """
    Evaluate whether *price_brl* is a good deal compared to *stats*.

    Returns
    -------
    dict with keys:
        is_good_deal   : bool
        percentile_pos : float (0-100, estimated position in historical distribution)
        pct_vs_median  : float (% above/below p50; negative = cheaper)
        verdict        : str  ('ÓTIMO' / 'BOM' / 'NORMAL' / 'CARO')
        verdict_emoji  : str  (🟢 / 🟡 / 🟠 / 🔴)
    """
    if stats["count"] == 0 or stats["p50"] is None:
        return {
            "is_good_deal": True,  # no history → always alert
            "percentile_pos": None,
            "pct_vs_median": None,
            "verdict": "SEM HISTÓRICO",
            "verdict_emoji": "⚪",
        }

    p50: float = stats["p50"]
    p25: float = stats["p25"]
    p75: float = stats["p75"]

    pct_vs_median = ((price_brl - p50) / p50) * 100

    # Rough percentile position using p25/p50/p75 as anchors
    if price_brl <= p25:
        percentile_pos = 12.5 * (price_brl / p25) if p25 > 0 else 0
    elif price_brl <= p50:
        span = p50 - p25
        percentile_pos = 25 + 25 * ((price_brl - p25) / span) if span > 0 else 25
    elif price_brl <= p75:
        span = p75 - p50
        percentile_pos = 50 + 25 * ((price_brl - p50) / span) if span > 0 else 50
    else:
        percentile_pos = min(100, 75 + 25 * ((price_brl - p75) / max(p75, 1)))

    if pct_vs_median <= -15:
        verdict, emoji, good = "ÓTIMO", "🟢", True
    elif pct_vs_median <= 0:
        verdict, emoji, good = "BOM", "🟡", True
    elif pct_vs_median <= 20:
        verdict, emoji, good = "NORMAL", "🟠", False
    else:
        verdict, emoji, good = "CARO", "🔴", False

    return {
        "is_good_deal": good,
        "percentile_pos": round(percentile_pos, 1),
        "pct_vs_median": round(pct_vs_median, 1),
        "verdict": verdict,
        "verdict_emoji": emoji,
    }


# ---------------------------------------------------------------------------
# Cash vs Points comparison
# ---------------------------------------------------------------------------

def compare_cash_vs_points(
    price_brl: Optional[float],
    points: Optional[int],
    cost_per_thousand_pts: float,
) -> Optional[dict]:
    """
    Compare paying in R$ vs redeeming LATAM Pass points.

    Parameters
    ----------
    price_brl             : cash price in BRL (Full fare)
    points                : points needed for the same flight (Full fare)
    cost_per_thousand_pts : user's average acquisition cost per 1 000 points in BRL
                            (e.g. 24.50 means each 1 000 pts cost R$ 24,50)

    Returns
    -------
    dict with keys:
        points_cost_brl : equivalent BRL cost of using points
        savings_brl     : how much cheaper the winner is (always positive)
        savings_pct     : savings as % of the more expensive option
        winner          : "cash" | "points" | "equal"
        recommendation  : human-readable string (Portuguese)
    Returns None if either price_brl or points is missing.
    """
    if not price_brl or not points or points <= 0:
        return None

    points_cost_brl = (points / 1000) * cost_per_thousand_pts

    diff = price_brl - points_cost_brl
    if abs(diff) < 1.0:  # less than R$1 difference — effectively equal
        return {
            "points_cost_brl": round(points_cost_brl, 2),
            "savings_brl": 0.0,
            "savings_pct": 0.0,
            "winner": "equal",
            "recommendation": "Tanto faz — R$ e pontos custam praticamente o mesmo.",
        }

    if diff > 0:
        # Points are cheaper
        pct = (diff / price_brl) * 100
        return {
            "points_cost_brl": round(points_cost_brl, 2),
            "savings_brl": round(diff, 2),
            "savings_pct": round(pct, 1),
            "winner": "points",
            "recommendation": (
                f"USE PONTOS — economia de R$ {diff:,.2f} ({pct:.0f}%) "
                f"vs pagar em R$"
            ),
        }
    else:
        # Cash is cheaper
        savings = abs(diff)
        pct = (savings / points_cost_brl) * 100
        return {
            "points_cost_brl": round(points_cost_brl, 2),
            "savings_brl": round(savings, 2),
            "savings_pct": round(pct, 1),
            "winner": "cash",
            "recommendation": (
                f"PAGUE EM R$ — economia de R$ {savings:,.2f} ({pct:.0f}%) "
                f"vs usar pontos"
            ),
        }


# ---------------------------------------------------------------------------
# LATAM URL generators
# ---------------------------------------------------------------------------

def generate_latam_cash_url(origin: str, dest: str, date_str: str) -> str:
    """Generate direct LATAM website URL for cash fare search."""
    return (
        f"https://www.latamairlines.com/br/pt/oferta-voos"
        f"?origin={origin.upper()}&destination={dest.upper()}"
        f"&outbound={date_str}&adt=1&chd=0&inf=0&trip=OW&cabin=Y&redemption=false"
    )


def generate_latam_points_url(origin: str, dest: str, date_str: str) -> str:
    """Generate direct LATAM Pass URL for points/miles fare search."""
    return (
        f"https://www.latamairlines.com/br/pt/oferta-voos"
        f"?origin={origin.upper()}&destination={dest.upper()}"
        f"&outbound={date_str}&adt=1&chd=0&inf=0&trip=OW&cabin=Y&redemption=true"
    )
