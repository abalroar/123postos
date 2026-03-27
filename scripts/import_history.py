#!/usr/bin/env python3
"""
Import user's historical flight price CSV into the standard data/history.csv format.

Usage
-----
    python scripts/import_history.py <input_csv> [--output data/history.csv]

The input CSV can have columns in Portuguese or English — the script will
auto-detect and normalize them to the standard schema:

    date, origin, dest, price_brl, points, fare_family, flight_number, notes

After import, a stats summary is printed per route.
"""

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("import_history")

# ---------------------------------------------------------------------------
# Column alias mapping (input col name → canonical name)
# ---------------------------------------------------------------------------
_ALIASES: dict[str, str] = {
    # date
    "date": "date",
    "data": "date",
    "data_voo": "date",
    "flight_date": "date",
    "departure_date": "date",
    # origin
    "origin": "origin",
    "origem": "origin",
    "from": "origin",
    "saida": "origin",
    # destination / dest
    "dest": "dest",
    "destination": "dest",
    "destino": "dest",
    "to": "dest",
    "chegada": "dest",
    # price
    "price_brl": "price_brl",
    "price": "price_brl",
    "preco": "price_brl",
    "preço": "price_brl",
    "valor": "price_brl",
    "valor_r$": "price_brl",
    "valor_rs": "price_brl",
    "r$": "price_brl",
    "preco_brl": "price_brl",
    # points
    "points": "points",
    "pontos": "points",
    "milhas": "points",
    "miles": "points",
    # fare family
    "fare_family": "fare_family",
    "fare": "fare_family",
    "tarifa": "fare_family",
    "classe_tarifa": "fare_family",
    "cabin": "fare_family",
    # flight number
    "flight_number": "flight_number",
    "flight": "flight_number",
    "voo": "flight_number",
    "numero_voo": "flight_number",
    "flight_no": "flight_number",
    # notes
    "notes": "notes",
    "notas": "notes",
    "obs": "notes",
    "observations": "notes",
    "observacoes": "notes",
}

_OUTPUT_COLUMNS = ["date", "origin", "dest", "price_brl", "points", "fare_family", "flight_number", "notes"]
_REQUIRED_COLUMNS = {"date", "origin", "dest"}


def _normalise_col(raw: str) -> str:
    key = raw.strip().lower().replace(" ", "_").replace("ç", "c").replace("ã", "a").replace("é", "e").replace("ê", "e")
    return _ALIASES.get(key, key)


def _parse_price(raw: str) -> float | None:
    """Parse a BRL price string, e.g. 'R$ 1.234,56' → 1234.56"""
    if not raw or raw.strip() == "":
        return None
    cleaned = (
        raw.strip()
        .replace("R$", "")
        .replace(" ", "")
        .replace("\xa0", "")
    )
    # Handle Brazilian number format: 1.234,56 → 1234.56
    if "," in cleaned and "." in cleaned:
        # Both separators: assume '.' = thousands, ',' = decimal
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        # Only comma: assume comma = decimal separator
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_points(raw: str) -> int | None:
    if not raw or raw.strip() == "":
        return None
    cleaned = raw.strip().replace(".", "").replace(",", "").replace(" ", "")
    try:
        return int(cleaned)
    except ValueError:
        return None


def import_csv(input_path: str, output_path: str) -> int:
    """
    Read *input_path*, normalize columns, and write to *output_path*.

    Returns the number of rows written.
    """
    if not os.path.exists(input_path):
        logger.error("Input file not found: %s", input_path)
        sys.exit(1)

    # Read raw CSV
    rows_in = []
    with open(input_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            logger.error("CSV has no headers.")
            sys.exit(1)

        # Build header map
        header_map: dict[str, str] = {}
        for raw_col in reader.fieldnames:
            canonical = _normalise_col(raw_col)
            header_map[raw_col] = canonical
            if canonical != raw_col.strip().lower():
                logger.debug("Column '%s' → '%s'", raw_col, canonical)

        detected = set(header_map.values())
        missing_required = _REQUIRED_COLUMNS - detected
        if missing_required:
            logger.error(
                "Input CSV is missing required columns (after alias mapping): %s\n"
                "Detected columns: %s",
                missing_required,
                list(header_map.values()),
            )
            sys.exit(1)

        for raw_row in reader:
            row = {header_map[k]: (v.strip() if v else "") for k, v in raw_row.items() if k}
            rows_in.append(row)

    logger.info("Read %d rows from %s", len(rows_in), input_path)

    # Normalize rows
    rows_out = []
    skipped = 0
    for i, row in enumerate(rows_in, start=2):  # line 1 = header
        origin = row.get("origin", "").upper().strip()
        dest = row.get("dest", "").upper().strip()
        date_raw = row.get("date", "").strip()

        if not origin or not dest or not date_raw:
            logger.debug("Row %d skipped: missing origin/dest/date.", i)
            skipped += 1
            continue

        # Normalize IATA codes (3 letters)
        if len(origin) != 3 or len(dest) != 3:
            logger.warning("Row %d: unusual IATA codes '%s'/'%s' — keeping as-is.", i, origin, dest)

        price_brl = _parse_price(row.get("price_brl", ""))
        points = _parse_points(row.get("points", ""))
        fare_family = row.get("fare_family", "").upper().strip()
        flight_number = row.get("flight_number", "").upper().strip()
        notes = row.get("notes", "").strip()

        rows_out.append({
            "date": date_raw,
            "origin": origin,
            "dest": dest,
            "price_brl": f"{price_brl:.2f}" if price_brl is not None else "",
            "points": str(points) if points is not None else "",
            "fare_family": fare_family,
            "flight_number": flight_number,
            "notes": notes,
        })

    if skipped:
        logger.warning("Skipped %d row(s) with missing required fields.", skipped)

    # Ensure output directory exists
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Write output CSV (append if exists, write header only if new)
    file_exists = os.path.exists(output_path)
    mode = "a" if file_exists else "w"
    with open(output_path, mode, newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_OUTPUT_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows_out)

    logger.info("Wrote %d rows to %s (%s mode).", len(rows_out), output_path, mode)
    return len(rows_out)


def print_stats(csv_path: str) -> None:
    """Read the output CSV and print per-route price stats."""
    from collections import defaultdict

    if not os.path.exists(csv_path):
        return

    route_prices: dict[str, list[float]] = defaultdict(list)

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            key = f"{row.get('origin', '?')}→{row.get('dest', '?')}"
            price_str = row.get("price_brl", "")
            try:
                route_prices[key].append(float(price_str))
            except (ValueError, TypeError):
                pass

    if not route_prices:
        print("\nNenhum dado de preço encontrado no CSV de saída.")
        return

    print("\n" + "=" * 60)
    print(f"{'Estatísticas por rota':^60}")
    print("=" * 60)

    for route, prices in sorted(route_prices.items()):
        prices_sorted = sorted(prices)
        n = len(prices_sorted)

        def pct(p: float) -> float:
            idx = (p / 100) * (n - 1)
            lo = int(idx)
            hi = min(lo + 1, n - 1)
            frac = idx - lo
            return prices_sorted[lo] + frac * (prices_sorted[hi] - prices_sorted[lo])

        mean = sum(prices_sorted) / n
        print(f"\nRota: {route}  ({n} registros)")
        print(f"  Média : R$ {mean:>8,.2f}")
        print(f"  Mínimo: R$ {prices_sorted[0]:>8,.2f}")
        print(f"  P25   : R$ {pct(25):>8,.2f}")
        print(f"  P50   : R$ {pct(50):>8,.2f}  ← mediana")
        print(f"  P75   : R$ {pct(75):>8,.2f}")
        print(f"  Máximo: R$ {prices_sorted[-1]:>8,.2f}")

    print("\n" + "=" * 60 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import historical flight prices into the standard CSV format."
    )
    parser.add_argument(
        "input",
        help="Path to the input CSV file (your own purchase history).",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).parent.parent / "data" / "history.csv"),
        help="Path to the output CSV file (default: data/history.csv).",
    )
    args = parser.parse_args()

    n = import_csv(args.input, args.output)
    print(f"\n✅ Importação concluída: {n} linhas gravadas em {args.output}")
    print_stats(args.output)


if __name__ == "__main__":
    main()
