"""
Google Flights client usando a biblioteca fast-flights.

Busca dados sem browser, via HTTP + protobuf encoding do Google.
Filtra apenas voos operados pela LATAM, diretos (sem escala).
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Mapeamento: substring no f.name (upper) → (código IATA, nome curto)
AIRLINE_PATTERNS: list[tuple[str, str, str]] = [
    ("LATAM", "LA", "LATAM"),
    ("GOL", "G3", "GOL"),
    ("AZUL", "AD", "Azul"),
    ("VOEPASS", "2Z", "Voepass"),
    ("MAP", "7M", "MAP"),
]


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
    """Extrai número de strings no formato brasileiro: 'R$1.150', 'R$321', 'R$1.150,50'."""
    if not price_str or price_str.strip() in ("0", "", "—"):
        return None
    # Remove símbolo de moeda e espaços não-quebráveis
    s = price_str.replace("R$", "").replace("\xa0", "").replace(" ", "").strip()
    # Formato BR: ponto = separador de milhar, vírgula = decimal
    s = re.sub(r'\.(?=\d{3}(\D|$))', '', s)
    s = s.replace(',', '.')
    s = re.sub(r'[^\d.]', '', s)
    try:
        val = float(s)
        return val if val > 0 else None
    except ValueError:
        return None


def _after_min_time(dep_time: str, min_dep_time: str) -> bool:
    """Retorna True se dep_time >= min_dep_time (ambos HH:MM)."""
    dep_h, dep_m = (int(x) for x in dep_time.split(":"))
    min_h, min_m = (int(x) for x in min_dep_time.split(":"))
    return (dep_h, dep_m) >= (min_h, min_m)


def _identify_airline(name: str) -> tuple[str, str]:
    """Retorna (código IATA, nome curto) a partir do f.name do Google Flights."""
    upper = (name or "").upper()
    for pattern, code, display in AIRLINE_PATTERNS:
        if pattern in upper:
            return code, display
    return "??", name or "Desconhecida"


def search_google_flights(
    origin: str,
    dest: str,
    date_str: str,
    min_dep_time: Optional[str] = None,
    airlines: Optional[list[str]] = None,
    max_stops: Optional[int] = 0,
) -> list[dict]:
    """
    Busca voos LATAM diretos no Google Flights para a rota e data informadas.

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
        from fast_flights.core import fetch, parse_response
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
        params = {
            "tfs": filter_data.as_b64().decode("utf-8"),
            "hl": "pt-BR",
            "tfu": "EgQIABABIgA",
            "curr": "BRL",
        }
        res = fetch(params)
        result = parse_response(res, dangerously_allow_looping_last_item=True)
    except Exception as exc:
        logger.error("Erro Google Flights %s->%s %s: %s", origin, dest, date_str, exc)
        return []

    flights = []
    seen: set[tuple] = set()
    effective_airlines = airlines if airlines is not None else ["LA"]

    for f in result.flights:
        airline_code, airline_name = _identify_airline(f.name)
        if airline_code not in effective_airlines:
            continue

        stops_val = f.stops
        if max_stops is not None and isinstance(stops_val, int) and stops_val > max_stops:
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

        # Deduplica por (cia, partida, chegada, preço) — a API retorna o mesmo voo
        # múltiplas vezes em resultados diferentes (melhor preço, mais rápido, etc.)
        key = (airline_code, dep_time, arr_time, price)
        if key in seen:
            continue
        seen.add(key)

        flights.append({
            "airline": airline_code,
            "airline_name": airline_name,
            "departure_time": dep_time,
            "arrival_time": arr_time,
            "price_brl": price,
            "points": None,
            "flight_number": "",
            "fare_family": "",
            "is_refundable": False,
            "duration": f.duration or "",
            "stops": stops_val if isinstance(stops_val, int) else 0,
            "origin": origin.upper(),
            "destination": dest.upper(),
        })

    cias = ",".join(effective_airlines) if airlines else "LA"
    logger.info(
        "Google Flights %s->%s %s: %d voo(s) [%s] (únicos)",
        origin, dest, date_str, len(flights), cias,
    )
    return flights
