"""
LATAM Monitor Bot — serviço unificado: Telegram polling + agendador local.

Comandos:
  /run      — busca interativa (escolhe rota, datas, período)
  /status   — estado atual do bot e uptime
  /last     — resultado da última execução agendada
  /help     — lista de comandos
"""

import asyncio
import logging
import logging.handlers
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_RUNNING
from apscheduler.triggers.cron import CronTrigger
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")

# ---------------------------------------------------------------------------
# Logging rotativo
# ---------------------------------------------------------------------------
LOG_DIR = _ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "bot.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("latam_bot")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN não configurado no .env")

_raw_ids = os.environ.get("ALLOWED_CHAT_IDS") or os.environ.get("TELEGRAM_CHAT_ID", "")
ALLOWED_IDS: set[int] = {int(x.strip()) for x in _raw_ids.split(",") if x.strip()}
if not ALLOWED_IDS:
    raise RuntimeError("Defina ALLOWED_CHAT_IDS ou TELEGRAM_CHAT_ID no .env")

# ---------------------------------------------------------------------------
# Constantes da busca interativa
# ---------------------------------------------------------------------------
AIRPORTS = ["CGH", "GRU", "BSB", "SDU", "GIG", "CNF", "FOR", "REC", "SSA", "POA"]

WEEKDAYS = {
    "Seg": 0, "Ter": 1, "Qua": 2, "Qui": 3,
    "Sex": 4, "Sáb": 5, "Dom": 6, "Qualquer": None,
}
WEEKDAY_NAMES_PT = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
WEEKDAY_SHORT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

# Períodos: (label_botão, min_dep, max_dep ou None)
TIME_PERIODS = {
    "manha":    ("🌅 Manhã (06-12)",  "06:00", "11:59"),
    "tarde":    ("🌆 Tarde (12-19)",  "12:00", "18:59"),
    "noite":    ("🌙 Noite (19+)",    "19:00", None),
    "qualquer": ("⏰ Qualquer",        None,    None),
}

# Estados do ConversationHandler
(PICK_ORIGIN, TYPE_ORIGIN, PICK_DEST, TYPE_DEST,
 PICK_MONTHS, PICK_WEEKDAY, PICK_TIME) = range(7)

# ---------------------------------------------------------------------------
# Estado global
# ---------------------------------------------------------------------------
_STARTED_AT = datetime.now()
_run_lock = threading.Lock()
_running = False
_last_run: dict = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_allowed(update: Update) -> bool:
    uid = update.effective_chat.id
    if uid not in ALLOWED_IDS:
        logger.warning("Acesso negado: chat_id=%s", uid)
        return False
    return True


def _load_schedule_config() -> tuple[list[str], str]:
    with open(_ROOT / "config.yaml", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    sched = cfg.get("schedule", {})
    return sched.get("run_times", ["08:00", "20:00"]), sched.get("timezone", "America/Sao_Paulo")


def _airport_keyboard(exclude: Optional[str] = None) -> InlineKeyboardMarkup:
    airports = [a for a in AIRPORTS if a != exclude]
    rows = [airports[i:i+3] for i in range(0, len(airports), 3)]
    keyboard = [
        [InlineKeyboardButton(a, callback_data=a) for a in row]
        for row in rows
    ]
    keyboard.append([InlineKeyboardButton("✏️ Outro (digitar)", callback_data="__custom__")])
    return InlineKeyboardMarkup(keyboard)


# ---------------------------------------------------------------------------
# Busca customizada
# ---------------------------------------------------------------------------
def _get_search_dates(months_ahead: int, weekday: Optional[int]) -> list[date]:
    today = date.today()
    end = today + relativedelta(months=months_ahead)
    dates = []
    current = today + timedelta(days=1)
    while current <= end:
        if weekday is None or current.weekday() == weekday:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def _in_period(dep_time: str, time_key: str) -> bool:
    _, min_t, max_t = TIME_PERIODS[time_key]
    if min_t is None:
        return True
    if dep_time < min_t:
        return False
    if max_t and dep_time > max_t:
        return False
    return True


def _run_custom_search_sync(
    origin: str, dest: str, months: int,
    weekday: Optional[int], time_key: str,
    notify_fn,
) -> dict:
    """Roda busca customizada em thread. Chama notify_fn(msg) com progresso."""
    from src.google_flights_client import search_google_flights

    dates = _get_search_dates(months, weekday)
    if not dates:
        return {"dates_searched": 0, "results": {}}

    results: dict[str, list] = {}
    notify_fn(
        f"🔍 Buscando <b>LATAM {origin}→{dest}</b>\n"
        f"📅 {len(dates)} datas | ⏳ aguarde..."
    )

    for d in dates:
        date_str = d.strftime("%Y-%m-%d")
        try:
            flights = search_google_flights(origin, dest, date_str)
            flights = [f for f in flights if _in_period(f.get("departure_time", ""), time_key)]
            if flights:
                results[date_str] = sorted(flights, key=lambda f: (f["price_brl"], f["departure_time"]))
        except Exception as exc:
            logger.error("Erro buscando %s->%s %s: %s", origin, dest, date_str, exc)

    return {"dates_searched": len(dates), "results": results}


def _format_results(origin: str, dest: str, results: dict, time_key: str) -> str:
    _, min_t, _ = TIME_PERIODS[time_key]
    period_label = TIME_PERIODS[time_key][0]

    if not results:
        return (
            f"❌ Nenhum voo LATAM encontrado\n"
            f"<b>{origin}→{dest}</b> | {period_label}"
        )

    lines = [f"✈️ <b>LATAM {origin}→{dest}</b> | {period_label}\n"]
    total_flights = 0

    for date_str in sorted(results):
        d = datetime.strptime(date_str, "%Y-%m-%d")
        day_name = WEEKDAY_SHORT[d.weekday()]
        lines.append(f"📅 <b>{day_name} {d.strftime('%d/%m')}</b>")

        flights = results[date_str]
        total_flights += len(flights)
        for f in flights[:5]:
            price = f.get("price_brl") or 0
            dep = f.get("departure_time", "??:??")
            arr = f.get("arrival_time", "??:??")
            dur = f.get("duration", "")
            dur_str = f" ({dur})" if dur else ""
            lines.append(f"  {dep}→{arr}{dur_str} <b>R${price:.0f}</b>")

    lines.append(f"\n<i>{len(results)} datas com voos · {total_flights} opções</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ConversationHandler — /run interativo
# ---------------------------------------------------------------------------
async def cmd_run_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        "✈️ <b>Busca LATAM</b>\n\nEscolha o aeroporto de <b>origem</b>:",
        reply_markup=_airport_keyboard(),
        parse_mode="HTML",
    )
    return PICK_ORIGIN


async def handle_origin_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "__custom__":
        await query.edit_message_text("✏️ Digite o código IATA da <b>origem</b> (ex: GRU):", parse_mode="HTML")
        return TYPE_ORIGIN
    context.user_data["origin"] = query.data.upper()
    await query.edit_message_text(
        f"Origem: <b>{query.data}</b>\n\nEscolha o aeroporto de <b>destino</b>:",
        reply_markup=_airport_keyboard(exclude=query.data),
        parse_mode="HTML",
    )
    return PICK_DEST


async def handle_origin_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    origin = update.message.text.strip().upper()[:3]
    context.user_data["origin"] = origin
    await update.message.reply_text(
        f"Origem: <b>{origin}</b>\n\nEscolha o aeroporto de <b>destino</b>:",
        reply_markup=_airport_keyboard(exclude=origin),
        parse_mode="HTML",
    )
    return PICK_DEST


async def handle_dest_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "__custom__":
        await query.edit_message_text("✏️ Digite o código IATA do <b>destino</b> (ex: BSB):", parse_mode="HTML")
        return TYPE_DEST
    context.user_data["dest"] = query.data.upper()
    origin = context.user_data["origin"]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("1 mês", callback_data="1"),
         InlineKeyboardButton("2 meses", callback_data="2"),
         InlineKeyboardButton("3 meses", callback_data="3")],
        [InlineKeyboardButton("6 meses", callback_data="6"),
         InlineKeyboardButton("12 meses", callback_data="12")],
    ])
    await query.edit_message_text(
        f"Origem: <b>{origin}</b> → Destino: <b>{query.data}</b>\n\n"
        f"Buscar até quantos <b>meses à frente</b>?",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    return PICK_MONTHS


async def handle_dest_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dest = update.message.text.strip().upper()[:3]
    context.user_data["dest"] = dest
    origin = context.user_data["origin"]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("1 mês", callback_data="1"),
         InlineKeyboardButton("2 meses", callback_data="2"),
         InlineKeyboardButton("3 meses", callback_data="3")],
        [InlineKeyboardButton("6 meses", callback_data="6"),
         InlineKeyboardButton("12 meses", callback_data="12")],
    ])
    await update.message.reply_text(
        f"Origem: <b>{origin}</b> → Destino: <b>{dest}</b>\n\n"
        f"Buscar até quantos <b>meses à frente</b>?",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    return PICK_MONTHS


async def handle_months(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["months"] = int(query.data)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(d, callback_data=d) for d in ["Seg", "Ter", "Qua", "Qui"]],
        [InlineKeyboardButton(d, callback_data=d) for d in ["Sex", "Sáb", "Dom"]],
        [InlineKeyboardButton("📅 Qualquer dia", callback_data="Qualquer")],
    ])
    await query.edit_message_text(
        f"Período: <b>{query.data} meses</b>\n\nFiltrar por <b>dia da semana</b>?",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    return PICK_WEEKDAY


async def handle_weekday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["weekday_label"] = query.data
    context.user_data["weekday"] = WEEKDAYS[query.data]

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(TIME_PERIODS["manha"][0],    callback_data="manha"),
         InlineKeyboardButton(TIME_PERIODS["tarde"][0],    callback_data="tarde")],
        [InlineKeyboardButton(TIME_PERIODS["noite"][0],    callback_data="noite"),
         InlineKeyboardButton(TIME_PERIODS["qualquer"][0], callback_data="qualquer")],
    ])
    await query.edit_message_text(
        f"Dia: <b>{query.data}</b>\n\nQual <b>período do dia</b>?\n"
        f"<i>(Noite = após 19h, após o trabalho)</i>",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    return PICK_TIME


async def handle_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    ud = context.user_data
    ud["time_key"] = query.data

    origin   = ud["origin"]
    dest     = ud["dest"]
    months   = ud["months"]
    weekday  = ud["weekday"]
    weekday_label = ud["weekday_label"]
    time_key = ud["time_key"]
    period_label = TIME_PERIODS[time_key][0]

    summary = (
        f"✈️ <b>{origin}→{dest}</b> | {months} meses | "
        f"{weekday_label} | {period_label}\n\n"
        f"🔍 Iniciando busca..."
    )
    msg = await query.edit_message_text(summary, parse_mode="HTML")

    chat_id = query.message.chat_id
    bot = context.bot

    def notify(text: str):
        asyncio.run_coroutine_threadsafe(
            bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML"),
            asyncio.get_event_loop(),
        )

    def run():
        return _run_custom_search_sync(origin, dest, months, weekday, time_key, notify)

    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, run)

    result_text = _format_results(origin, dest, data["results"], time_key)

    # Telegram tem limite de 4096 chars; divide se necessário
    if len(result_text) <= 4096:
        await bot.send_message(chat_id=chat_id, text=result_text, parse_mode="HTML")
    else:
        chunks = [result_text[i:i+4000] for i in range(0, len(result_text), 4000)]
        for chunk in chunks:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")

    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Busca cancelada.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Monitor padrão (agendado / config.yaml)
# ---------------------------------------------------------------------------
def _run_monitor_sync() -> dict:
    global _running, _last_run
    if not _run_lock.acquire(blocking=False):
        logger.info("Monitor já em execução — disparo ignorado.")
        return {"error": "already_running"}
    _running = True
    start = datetime.now()
    logger.info("=== Monitor iniciado (%s) ===", start.strftime("%d/%m %H:%M"))
    try:
        from main import run_monitor
        run_monitor()
        elapsed = int((datetime.now() - start).total_seconds())
        result = {"started_at": start.strftime("%d/%m %H:%M"), "elapsed_s": elapsed, "status": "ok"}
        logger.info("=== Monitor concluído em %ds ===", elapsed)
    except Exception as exc:
        elapsed = int((datetime.now() - start).total_seconds())
        logger.error("Erro no monitor: %s", exc, exc_info=True)
        result = {
            "started_at": start.strftime("%d/%m %H:%M"),
            "elapsed_s": elapsed,
            "status": "error",
            "error": str(exc)[:300],
        }
    finally:
        _running = False
        _run_lock.release()
    _last_run = result
    return result


def _scheduled_run():
    from src.telegram_notifier import send_message
    result = _run_monitor_sync()
    if result.get("error") == "already_running":
        return
    if result["status"] == "error":
        send_message(f"❌ <b>Execução agendada falhou</b>\n\n<code>{result['error']}</code>")


# ---------------------------------------------------------------------------
# Handlers simples
# ---------------------------------------------------------------------------
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    run_times, tz = _load_schedule_config()
    await update.message.reply_text(
        "<b>LATAM Monitor</b>\n\n"
        "/run — busca interativa (rota, datas, período)\n"
        "/status — estado do bot\n"
        "/last — última execução agendada\n"
        "/help — esta mensagem\n\n"
        f"⏰ Automático: {' e '.join(run_times)} ({tz})",
        parse_mode="HTML",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    uptime = datetime.now() - _STARTED_AT
    h, resto = divmod(int(uptime.total_seconds()), 3600)
    m = resto // 60
    estado = "🔄 Buscando..." if _running else "✅ Aguardando"
    run_times, tz = _load_schedule_config()
    await update.message.reply_text(
        f"<b>Status</b>\n\n{estado}\nUptime: {h}h {m}min\n"
        f"Horários: {', '.join(run_times)} ({tz})\n"
        f"Iniciado: {_STARTED_AT.strftime('%d/%m %H:%M')}",
        parse_mode="HTML",
    )


async def cmd_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    if not _last_run:
        await update.message.reply_text("Nenhuma execução agendada registrada nesta sessão.")
        return
    emoji = "✅" if _last_run["status"] == "ok" else "❌"
    msg = (
        f"<b>Última execução agendada</b>\n\n"
        f"{emoji} {_last_run['status'].upper()}\n"
        f"Início: {_last_run['started_at']}\n"
        f"Duração: {_last_run['elapsed_s']}s"
    )
    if _last_run.get("error"):
        msg += f"\n\nErro:\n<code>{_last_run['error']}</code>"
    await update.message.reply_text(msg, parse_mode="HTML")


# ---------------------------------------------------------------------------
# Agendador
# ---------------------------------------------------------------------------
def _start_scheduler() -> BackgroundScheduler:
    run_times, timezone = _load_schedule_config()
    scheduler = BackgroundScheduler(timezone=timezone)
    for time_str in run_times:
        h, m = (int(x) for x in time_str.split(":"))
        scheduler.add_job(
            _scheduled_run,
            CronTrigger(hour=h, minute=m, timezone=timezone),
            id=f"monitor_{h:02d}{m:02d}",
            misfire_grace_time=600,
            coalesce=True,
        )
        logger.info("Agendado: %02d:%02d %s", h, m, timezone)
    scheduler.start()
    return scheduler


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    logger.info("Iniciando LATAM Monitor Bot...")
    logger.info("Chat IDs autorizados: %s", ALLOWED_IDS)

    scheduler = _start_scheduler()

    app = ApplicationBuilder().token(TOKEN).build()

    # Conversa interativa /run
    conv = ConversationHandler(
        entry_points=[CommandHandler("run", cmd_run_start)],
        states={
            PICK_ORIGIN: [CallbackQueryHandler(handle_origin_pick)],
            TYPE_ORIGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_origin_type)],
            PICK_DEST:   [CallbackQueryHandler(handle_dest_pick)],
            TYPE_DEST:   [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_dest_type)],
            PICK_MONTHS: [CallbackQueryHandler(handle_months)],
            PICK_WEEKDAY:[CallbackQueryHandler(handle_weekday)],
            PICK_TIME:   [CallbackQueryHandler(handle_time)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_user=True,
        per_chat=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler(["help", "start"], cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("last", cmd_last))

    logger.info("Bot ativo. Aguardando comandos...")
    try:
        app.run_polling(drop_pending_updates=True)
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Bot encerrado.")


if __name__ == "__main__":
    main()
