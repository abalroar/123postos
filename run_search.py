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


def format_results(origin: str, dest: str, results: dict, time_key: str) -> str:
    period_label = TIME_PERIODS[time_key][0]
    if not results:
        return f"❌ Nenhum voo LATAM encontrado\n<b>{origin}→{dest}</b> | {period_label}"

    lines = [f"✈️ <b>LATAM {origin}→{dest}</b> | {period_label}\n"]
    total = 0
    for date_str in sorted(results):
        d = datetime.strptime(date_str, "%Y-%m-%d")
        day = WEEKDAY_SHORT[d.weekday()]
        lines.append(f"📅 <b>{day} {d.strftime('%d/%m')}</b>")
        for f in results[date_str]:
            price = f.get("price_brl") or 0
            dep = f.get("departure_time", "??:??")
            arr = f.get("arrival_time", "??:??")
            dur = f.get("duration", "")
            dur_str = f" ({dur})" if dur else ""
            lines.append(f"  {dep}→{arr}{dur_str} <b>R${price:.0f}</b>")
            total += 1
    lines.append(f"\n<i>{len(results)} datas com voos · {total} opções</i>")
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

    logger.info(
        "Buscando LATAM %s→%s | %d datas | %s | %s",
        ORIGIN, DEST, len(dates), weekday_label, period_label,
    )

    send_message(
        f"🔍 <b>Iniciando busca</b>\n"
        f"LATAM <b>{ORIGIN}→{DEST}</b> | {MONTHS} meses | {weekday_label} | {period_label}\n"
        f"📅 {len(dates)} datas para verificar..."
    )

    results: dict[str, list] = {}
    for d in dates:
        date_str = d.strftime("%Y-%m-%d")
        try:
            flights = search_google_flights(ORIGIN, DEST, date_str)
            flights = [f for f in flights if in_period(f.get("departure_time", ""), TIME_KEY)]
            if flights:
                results[date_str] = sorted(
                    flights, key=lambda f: (f.get("price_brl") or 9999, f.get("departure_time", ""))
                )
                logger.info("%s: %d voo(s)", date_str, len(results[date_str]))
        except Exception as exc:
            logger.error("Erro %s→%s %s: %s", ORIGIN, DEST, date_str, exc)

    result_text = format_results(ORIGIN, DEST, results, TIME_KEY)

    # Telegram: máx 4096 chars por mensagem
    for i in range(0, len(result_text), 4000):
        send_message(result_text[i:i+4000])

    logger.info("Concluído. %d datas com resultados.", len(results))


if __name__ == "__main__":
    main()
