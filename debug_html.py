"""Salva o HTML do Google Flights para análise dos seletores."""

from fast_flights import FlightData, Passengers
from fast_flights.core import fetch
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
print("Status:", res.status_code)

with open("logs/google_flights_raw.html", "w", encoding="utf-8") as f:
    f.write(res.text)

print("HTML salvo em logs/google_flights_raw.html")
print("Tamanho:", len(res.text), "chars")

# Mostra um trecho em volta do primeiro preço encontrado
idx = res.text.find("R$")
if idx > 0:
    print("\nTrecho ao redor do primeiro R$:")
    print(res.text[max(0, idx-500):idx+500])
