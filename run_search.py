"""
Busca customizada standalone — usada pelo GitHub Actions (workflow_dispatch).

Variáveis de ambiente:
  SEARCH_ORIGIN       código IATA origem (ex: CGH)
  SEARCH_DEST         código IATA destino (ex: BSB)
  SEARCH_MONTHS       meses à frente (default: 3)
  SEARCH_WEEKDAY      0=Seg...6=Dom, -1=Qualquer (default: -1)
  SEARCH_TIME_PERIOD  manha/tarde/noite/qualquer (default: qualquer)

  TELEGRAM_BOT_TOKEN  token do bot
  TELEGRAM_CHAT_ID    chat id para enviar resultado
"""

import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_search")

# ---------------------------------------------------------------------------
# Parâmetros
# ---------------------------------------------------------------------------
ORIGIN = os.environ.get("SEARCH_ORIGIN", "").strip().upper()
DEST   = os.environ.get("SEARCH_DEST", "").strip().upper()

if not ORIGIN or not DEST:
    logger.error("SEARCH_ORIGIN e SEARCH_DEST são obrigatórios.")
    sys.exit(1)

MONTHS       = int(os.environ.get("SEARCH_MONTHS", "3"))
# 0 = qualquer, 1=Seg … 7=Dom  (converte para Python weekday: 0=Seg…6=Dom)
_WEEKDAY_INPUT = int(os.environ.get("SEARCH_WEEKDAY", "0"))
WEEKDAY_NUM  = _WEEKDAY_INPUT                        # 0–7 para exibição
WEEKDAY      = (_WEEKDAY_INPUT - 1) if _WEEKDAY_INPUT >= 1 else None  # Python
TIME_KEY     = os.environ.get("SEARCH_TIME_PERIOD", "qualquer").lower().strip()
_AIRLINES_INPUT = os.environ.get("SEARCH_AIRLINES", "LA").strip().upper()
AIRLINES_LIST = None if _AIRLINES_INPUT == "ALL" else [a.strip() for a in _AIRLINES_INPUT.split(",")]
MAX_STOPS = int(os.environ.get("SEARCH_MAX_STOPS", "0"))

WEEKDAY_SHORT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
WEEKDAY_LABEL = {
    0: "Qualquer dia",
    1: "Segunda-feira", 2: "Terça-feira", 3: "Quarta-feira",
    4: "Quinta-feira",  5: "Sexta-feira", 6: "Sábado", 7: "Domingo",
}

TIME_PERIODS = {
    "madrugada": ("Madrugada (00h–05h59)", "00:00", "05:59"),
    "manha":     ("Manhã (06h–11h59)",     "06:00", "11:59"),
    "tarde":     ("Tarde (12h–18h59)",     "12:00", "18:59"),
    "noite":     ("Noite (19h–23h59)",     "19:00", "23:59"),
    "qualquer":  ("Qualquer horário",      None,    None),
}
if TIME_KEY not in TIME_PERIODS:
    TIME_KEY = "qualquer"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
MAX_DATES = 45  # teto de datas para evitar buscas de 15-30 min


def get_dates(months: int, weekday) -> list[date]:
    """weekday: None = qualquer, 0–6 = Python weekday (Seg=0)."""
    today = date.today()
    end = today + relativedelta(months=months)
    out = []
    d = today + timedelta(days=1)
    while d <= end:
        if weekday is None or d.weekday() == weekday:
            out.append(d)
        d += timedelta(days=1)
    if len(out) > MAX_DATES:
        # Amostragem uniforme: espaça as datas para cobrir todo o período
        step = len(out) / MAX_DATES
        out = [out[int(i * step)] for i in range(MAX_DATES)]
    return out


def in_period(dep_time: str, time_key: str) -> bool:
    _, min_t, max_t = TIME_PERIODS.get(time_key, TIME_PERIODS["qualquer"])
    if not min_t:
        return True
    if dep_time < min_t:
        return False
    if max_t and dep_time > max_t:
        return False
    return True


def format_results(
    origin: str,
    dest: str,
    results: dict,
    time_key: str,
    failed_dates: list | None = None,
    airline_key: str = "LA",
) -> str:
    period_label = TIME_PERIODS[time_key][0]
    failed_dates = failed_dates or []
    multi_cia = (airline_key == "ALL" or airline_key is None)

    if not results:
        msg = f"❌ Nenhum voo encontrado\n<b>{origin}→{dest}</b> | {period_label}"
        if failed_dates:
            fmt = [datetime.strptime(ds, "%Y-%m-%d").strftime("%d/%m") for ds in failed_dates]
            msg += f"\n<i>⚠️ Erro ao consultar: {', '.join(fmt)}</i>"
        return msg

    lines = [f"✈️ <b>{origin}→{dest}</b> | {period_label}\n"]
    total = 0
    for date_str in sorted(results):
        d = datetime.strptime(date_str, "%Y-%m-%d")
        day = WEEKDAY_SHORT[d.weekday()]
        lines.append(f"📅 <b>{day} {d.strftime('%d/%m')}</b>")
        day_flights = results[date_str]
        if multi_cia:
            by_cia = {}
            for f in day_flights:
                cia = f.get("airline_name", "?")
                by_cia.setdefault(cia, []).append(f)
            for cia_name, cia_flights in sorted(by_cia.items()):
                lines.append(f"  <b>{cia_name}</b>")
                for f in cia_flights:
                    price = f.get("price_brl") or 0
                    dep = f.get("departure_time", "??:??")
                    arr = f.get("arrival_time", "??:??")
                    dur = f.get("duration", "")
                    dur_str = f" ({dur})" if dur else ""
                    stops_icon = " 🔄" if f.get("stops", 0) > 0 else ""
                    lines.append(f"    {dep}→{arr}{dur_str}{stops_icon} <b>R${price:.0f}</b>")
                    total += 1
        else:
            for f in day_flights:
                price = f.get("price_brl") or 0
                dep = f.get("departure_time", "??:??")
                arr = f.get("arrival_time", "??:??")
                dur = f.get("duration", "")
                dur_str = f" ({dur})" if dur else ""
                stops_icon = " 🔄" if f.get("stops", 0) > 0 else ""
                lines.append(f"  {dep}→{arr}{dur_str}{stops_icon} <b>R${price:.0f}</b>")
                total += 1
    summary = f"\n<i>{len(results)} datas com voos · {total} opções</i>"
    if failed_dates:
        fmt = [datetime.strptime(ds, "%Y-%m-%d").strftime("%d/%m") for ds in failed_dates]
        summary += f"\n<i>⚠️ Sem resposta em: {', '.join(fmt)}</i>"
    lines.append(summary)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    from src.google_flights_client import search_google_flights
    from src.telegram_notifier import send_message

    dates = get_dates(MONTHS, WEEKDAY)
    period_label  = TIME_PERIODS[TIME_KEY][0]
    weekday_label = WEEKDAY_LABEL.get(WEEKDAY_NUM, "Qualquer dia")
    total_raw = len(dates)
    sampled = total_raw == MAX_DATES and WEEKDAY is None  # só amostra quando qualquer dia

    logger.info(
        "Buscando %s→%s | %d datas | %s | %s | cias=%s | max_stops=%s",
        ORIGIN, DEST, total_raw, weekday_label, period_label, _AIRLINES_INPUT, MAX_STOPS,
    )

    sample_note = f"\n<i>⚡ Amostragem: {total_raw} datas espaçadas (máx {MAX_DATES})</i>" if sampled else ""
    cia_desc = "todas as cias" if _AIRLINES_INPUT == "ALL" else _AIRLINES_INPUT
    stops_desc = "diretos" if MAX_STOPS == 0 else f"até {MAX_STOPS} escala(s)"
    send_message(
        f"🔍 <b>Iniciando busca</b>\n"
        f"<b>{ORIGIN}→{DEST}</b> | {MONTHS} meses | {weekday_label} | {period_label}\n"
        f"🏢 {cia_desc} | {stops_desc}\n"
        f"📅 {total_raw} datas para verificar...{sample_note}"
    )

    results: dict[str, list] = {}
    failed_dates: list[str] = []
    for i, d in enumerate(dates):
        date_str = d.strftime("%Y-%m-%d")
        if i > 0:
            time.sleep(2)  # evita bloqueio por rate limit do Google
        try:
            flights = search_google_flights(
                ORIGIN, DEST, date_str, airlines=AIRLINES_LIST, max_stops=MAX_STOPS
            )
            flights = [f for f in flights if in_period(f.get("departure_time", ""), TIME_KEY)]
            if flights:
                results[date_str] = sorted(
                    flights, key=lambda f: (f.get("price_brl") or 9999, f.get("departure_time", ""))
                )
                logger.info("%s: %d voo(s)", date_str, len(results[date_str]))
            else:
                logger.info("%s: sem voos após filtro", date_str)
        except Exception as exc:
            logger.error("Erro %s→%s %s: %s", ORIGIN, DEST, date_str, exc)
            failed_dates.append(date_str)

    result_text = format_results(
        ORIGIN, DEST, results, TIME_KEY, failed_dates, airline_key=_AIRLINES_INPUT
    )

    # Telegram: máx 4096 chars por mensagem
    for i in range(0, len(result_text), 4000):
        send_message(result_text[i:i+4000])

    logger.info("Concluído. %d datas com resultados.", len(results))


if __name__ == "__main__":
    main()
