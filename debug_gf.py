"""Debug script para testar fast-flights e ver o que está retornando."""

from src.google_flights_client import search_google_flights

results = search_google_flights("CGH", "BSB", "2026-06-04")
print(f"Total voos LATAM: {len(results)}")
for f in results[:10]:
    print(f"  dep='{f['departure_time']}' arr='{f['arrival_time']}' price={f['price_brl']} stops={f['stops']}")

if not results:
    # Mostra todos os voos sem filtro LATAM para diagnóstico
    print("\n--- SEM FILTRO LATAM (diagnóstico) ---")
    from fast_flights import FlightData, Passengers
    from fast_flights.core import fetch, parse_response
    from fast_flights.filter import TFSData

    filter_data = TFSData.from_interface(
        flight_data=[FlightData(date="2026-06-04", from_airport="CGH", to_airport="BSB")],
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
    result = parse_response(res)
    print(f"Total voos (todos): {len(result.flights)}")
    for f in result.flights[:10]:
        print(f"  name='{f.name}' dep='{f.departure}' arr='{f.arrival}' price='{f.price}' stops={f.stops}")
