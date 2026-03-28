"""
Google Flights client usando a biblioteca fast-flights.

Busca dados sem browser, via HTTP + protobuf encoding do Google.
Filtra apenas voos operados pela LATAM.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def _parse_time(time_str: str) -> Optional[str]:
    """Converte '10:30 AM' ou '10:30' para 'HH:MM' (24h)."""
    if not time_str:
        return None
    m = re.match(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", time_str.strip(), re.IGNORECASE)
    if not m:
        return None
    h, minute, period = int(m.group(1)), int(m.group(2)), m.group(3)
    if period:
        period = period.upper()
        if period == "PM" and h != 12:
            h += 12
        elif period == "AM" and h == 12:
            h = 0
    return f"{h:02d}:{minute:02d}"


def _parse_price(price_str: str) -> Optional[float]:
    """Extrai número de strings como 'R$450', 'BRL 1234', '450'."""
    if not price_str or price_str.strip() in ("0", "", "—"):
        return None
    # Remove letras de moeda e espaços, mantém dígitos e ponto decimal
    cleaned = re.sub(r"[^\d.]", "", price_str.replace(",", ""))
    try:
        val = float(cleaned)
        return val if val > 0 else None
    except ValueError:
        return None


def _after_min_time(dep_time: str, min_dep_time: str) -> bool:
    """Retorna True se dep_time >= min_dep_time (ambos HH:MM)."""
    dep_h, dep_m = (int(x) for x in dep_time.split(":"))
    min_h, min_m = (int(x) for x in min_dep_time.split(":"))
    return (dep_h, dep_m) >= (min_h, min_m)


def search_google_flights(
    origin: str,
    dest: str,
    date_str: str,
    min_dep_time: Optional[str] = None,
) -> list[dict]:
    """
    Busca voos LATAM no Google Flights para a rota e data informadas.

    Parâmetros
    ----------
    origin       : código IATA de origem (ex: "CGH")
    dest         : código IATA de destino (ex: "BSB")
    date_str     : data no formato YYYY-MM-DD
    min_dep_time : filtro de horário mínimo de partida "HH:MM" (opcional)

    Retorna lista de dicts no formato padrão do projeto.
    """
    try:
        from fast_flights import FlightData, Passengers
        from fast_flights.core import get_flights_from_filter
        from fast_flights.filter import TFSData
    except ImportError:
        logger.error("fast-flights não instalado. Execute: pip install fast-flights")
        return []

    logger.info("Google Flights: %s->%s %s", origin, dest, date_str)

    try:
        filter_data = TFSData.from_interface(
            flight_data=[FlightData(date=date_str, from_airport=origin, to_airport=dest)],
            trip="one-way",
            passengers=Passengers(adults=1),
            seat="economy",
            max_stops=None,
        )
        result = get_flights_from_filter(filter_data, currency="BRL")
    except Exception as exc:
        logger.error("Erro Google Flights %s->%s %s: %s", origin, dest, date_str, exc)
        return []

    flights = []
    for f in result.flights:
        # Filtra apenas voos LATAM
        if "LATAM" not in (f.name or "").upper():
            continue

        dep_time = _parse_time(f.departure)
        arr_time = _parse_time(f.arrival)
        price = _parse_price(f.price)

        # Sem horário ou sem preço: pula
        if not dep_time or price is None:
            continue

        # Filtra por horário mínimo de partida
        if min_dep_time and not _after_min_time(dep_time, min_dep_time):
            continue

        flights.append({
            "departure_time": dep_time,
            "arrival_time": arr_time,
            "price_brl": price,
            "points": None,           # Google Flights não exibe pontos LATAM Pass
            "flight_number": "",      # fast-flights não expõe número de voo
            "fare_family": "",        # não disponível via Google Flights
            "is_refundable": False,
            "duration": f.duration or "",
            "stops": getattr(f, "stops", 0),
            "origin": origin.upper(),
            "destination": dest.upper(),
        })

    logger.info(
        "Google Flights %s->%s %s: %d voo(s) LATAM",
        origin, dest, date_str, len(flights),
    )
    return flights
