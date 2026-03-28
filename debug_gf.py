"""Debug script para testar fast-flights e ver o que está retornando."""

from fast_flights import FlightData, Passengers
from fast_flights.core import get_flights_from_filter
from fast_flights.filter import TFSData

filter_data = TFSData.from_interface(
    flight_data=[FlightData(date="2026-06-04", from_airport="CGH", to_airport="BSB")],
    trip="one-way",
    passengers=Passengers(adults=1),
    seat="economy",
    max_stops=None,
)

try:
    result = get_flights_from_filter(filter_data, currency="BRL")
    print(f"current_price: {result.current_price}")
    print(f"total flights: {len(result.flights)}")
    for f in result.flights[:10]:
        print(f"  name='{f.name}' dep='{f.departure}' arr='{f.arrival}' price='{f.price}' stops={f.stops}")
except Exception as e:
    print(f"ERRO: {type(e).__name__}: {e}")
