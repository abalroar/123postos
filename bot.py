"""
LATAM Monitor Bot

Comandos:
  /start  — menu inicial + envio do guia "Como usar"
  /run    — busca interativa URA (5 passos) → dispara GitHub Actions
  /ajuda  — envia o arquivo "Como usar" (Markdown)
  /status — estado do bot e uptime
  /last   — última execução agendada
  /help   — lista de comandos
  /cancel — cancela busca em andamento
"""

import asyncio
import logging
import logging.handlers
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests as http_requests
import yaml
from apscheduler.schedulers.background import BackgroundScheduler
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
GUIDE_PATH = _ROOT / "docs" / "COMO_USAR_TELEGRAM.md"
GUIDE_FALLBACK_TEXT = """# Como usar o bot no Telegram (URA)

Comandos:
- /start: menu inicial e envio deste guia
- /run: busca interativa em 5 passos
- /status: estado do bot
- /last: última execução agendada
- /cancel: cancela busca atual
- /help: ajuda rápida
- /ajuda: reenvia este guia
"""

# ---------------------------------------------------------------------------
# Logging
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
# Credenciais
# ---------------------------------------------------------------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN não configurado no .env")

_raw_ids = os.environ.get("ALLOWED_CHAT_IDS") or os.environ.get("TELEGRAM_CHAT_ID", "")
ALLOWED_IDS: set[int] = {int(x.strip()) for x in _raw_ids.split(",") if x.strip()}
if not ALLOWED_IDS:
    raise RuntimeError("Defina ALLOWED_CHAT_IDS ou TELEGRAM_CHAT_ID no .env")

# GitHub Actions dispatch (opcional — se não configurado, busca roda localmente)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "abalroar/biuro")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")

# ---------------------------------------------------------------------------
# Constantes URA
# ---------------------------------------------------------------------------
AIRPORTS = ["CGH", "GRU", "BSB", "SDU", "GIG", "CNF", "FOR", "REC", "SSA", "POA"]

AIRPORT_NAMES = {
    "CGH": "Congonhas · SP",    "GRU": "Guarulhos · SP",
    "BSB": "Brasília · DF",     "SDU": "Santos Dumont · RJ",
    "GIG": "Galeão · RJ",       "CNF": "Confins · MG",
    "FOR": "Fortaleza · CE",    "REC": "Recife · PE",
    "SSA": "Salvador · BA",     "POA": "Porto Alegre · RS",
}

WEEKDAY_BUTTONS = {
    "1 · Seg": 1, "2 · Ter": 2, "3 · Qua": 3, "4 · Qui": 4,
    "5 · Sex": 5, "6 · Sáb": 6, "7 · Dom": 7, "0 · Qualquer": 0,
}
WEEKDAY_LABEL = {
    0: "Qualquer dia",
    1: "Segunda-feira", 2: "Terça-feira", 3: "Quarta-feira",
    4: "Quinta-feira",  5: "Sexta-feira", 6: "Sábado", 7: "Domingo",
}
WEEKDAY_SHORT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

TIME_PERIODS = {
    "madrugada": ("🌙 Madrugada  00h–05h59", "00:00", "05:59"),
    "manha":     ("🌅 Manhã      06h–11h59", "06:00", "11:59"),
    "tarde":     ("🌆 Tarde      12h–18h59", "12:00", "18:59"),
    "noite":     ("🌃 Noite      19h–23h59", "19:00", "23:59"),
    "qualquer":  ("⏰ Qualquer horário",      None,    None),
}

(PICK_ORIGIN, TYPE_ORIGIN, PICK_DEST, TYPE_DEST,
 PICK_MONTHS, PICK_WEEKDAY, PICK_TIME) = range(7)

_STEP_HEADER = "✈️ <b>LATAM Monitor</b> — Nova busca\n─────────────────────\n"

# ---------------------------------------------------------------------------
# Estado global
# ---------------------------------------------------------------------------
_STARTED_AT = datetime.now()
_run_lock   = threading.Lock()
_running    = False
_last_run: dict = {}


# ---------------------------------------------------------------------------
# Helpers gerais
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


def _months_end_date(months: int) -> str:
    end = date.today() + relativedelta(months=months)
    months_pt = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"]
    return f"{end.day:02d}/{months_pt[end.month-1]}/{end.year}"


def _get_search_dates(months_ahead: int, weekday: Optional[int]) -> list[date]:
    today = date.today()
    end   = today + relativedelta(months=months_ahead)
    dates, current = [], today + timedelta(days=1)
    while current <= end:
        if weekday is None or current.weekday() == weekday:
            dates.append(current)
        current += timedelta(days=1)
    return dates


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------
def _airport_keyboard(exclude: Optional[str] = None) -> InlineKeyboardMarkup:
    airports = [a for a in AIRPORTS if a != exclude]
    rows = [airports[i:i+3] for i in range(0, len(airports), 3)]
    kb = [[InlineKeyboardButton(a, callback_data=a) for a in row] for row in rows]
    kb.append([InlineKeyboardButton("✏️ Outro — digitar código IATA", callback_data="__custom__")])
    return InlineKeyboardMarkup(kb)


def _airport_list_text() -> str:
    return "\n".join(f"  <code>{k}</code> = {v}" for k, v in AIRPORT_NAMES.items())


def _months_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1 mês",   callback_data="1"),
         InlineKeyboardButton("2 meses", callback_data="2"),
         InlineKeyboardButton("3 meses", callback_data="3")],
        [InlineKeyboardButton("6 meses", callback_data="6"),
         InlineKeyboardButton("12 meses",callback_data="12")],
    ])


def _weekday_keyboard() -> InlineKeyboardMarkup:
    items = list(WEEKDAY_BUTTONS.items())
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(l, callback_data=str(n)) for l, n in items[:4]],
        [InlineKeyboardButton(l, callback_data=str(n)) for l, n in items[4:]],
    ])


def _time_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(TIME_PERIODS["madrugada"][0], callback_data="madrugada"),
         InlineKeyboardButton(TIME_PERIODS["manha"][0],     callback_data="manha")],
        [InlineKeyboardButton(TIME_PERIODS["tarde"][0],     callback_data="tarde"),
         InlineKeyboardButton(TIME_PERIODS["noite"][0],     callback_data="noite")],
        [InlineKeyboardButton(TIME_PERIODS["qualquer"][0],  callback_data="qualquer")],
    ])


# ---------------------------------------------------------------------------
# GitHub Actions dispatch
# ---------------------------------------------------------------------------
def _trigger_github_actions(origin: str, dest: str, months: int,
                             weekday_num: int, time_key: str,
                             chat_id: int) -> bool:
    """Dispara workflow_dispatch no GitHub Actions. Retorna True se sucesso."""
    if not GITHUB_TOKEN:
        return False
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/monitor.yml/dispatches"
    resp = http_requests.post(
        url,
        json={
            "ref": GITHUB_BRANCH,
            "inputs": {
                "origin":      origin,
                "dest":        dest,
                "months":      str(months),
                "weekday":     str(weekday_num),
                "time_period": time_key,
                "chat_id":     str(chat_id),
            },
        },
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=10,
    )
    ok = resp.status_code == 204
    if not ok:
        logger.error("GitHub Actions dispatch falhou: %s %s", resp.status_code, resp.text[:200])
    return ok


# ---------------------------------------------------------------------------
# Busca local (fallback quando GITHUB_TOKEN não está configurado)
# ---------------------------------------------------------------------------
def _in_period(dep_time: str, time_key: str) -> bool:
    _, min_t, max_t = TIME_PERIODS[time_key]
    if not min_t:
        return True
    if dep_time < min_t:
        return False
    if max_t and dep_time > max_t:
        return False
    return True


def _run_custom_search_sync(origin, dest, months, weekday, time_key, notify_fn) -> dict:
    from src.google_flights_client import search_google_flights
    dates = _get_search_dates(months, weekday)
    if not dates:
        return {"results": {}}
    notify_fn(f"🔍 Buscando <b>LATAM {origin}→{dest}</b>\n📅 {len(dates)} datas | ⏳ aguarde...")
    results: dict[str, list] = {}
    for d in dates:
        date_str = d.strftime("%Y-%m-%d")
        try:
            flights = search_google_flights(origin, dest, date_str)
            flights = [f for f in flights if _in_period(f.get("departure_time", ""), time_key)]
            if flights:
                results[date_str] = sorted(flights, key=lambda f: (f["price_brl"], f["departure_time"]))
        except Exception as exc:
            logger.error("Erro %s->%s %s: %s", origin, dest, date_str, exc)
    return {"results": results}


def _format_results(origin, dest, results, time_key) -> str:
    period_label = TIME_PERIODS[time_key][0]
    if not results:
        return f"❌ Nenhum voo LATAM encontrado\n<b>{origin}→{dest}</b> | {period_label}"
    lines = [f"✈️ <b>LATAM {origin}→{dest}</b> | {period_label}\n"]
    total = 0
    for date_str in sorted(results):
        d = datetime.strptime(date_str, "%Y-%m-%d")
        lines.append(f"📅 <b>{WEEKDAY_SHORT[d.weekday()]} {d.strftime('%d/%m')}</b>")
        for f in results[date_str]:
            dep = f.get("departure_time","??:??"); arr = f.get("arrival_time","??:??")
            dur = f.get("duration",""); price = f.get("price_brl") or 0
            lines.append(f"  {dep}→{arr}{' ('+dur+')' if dur else ''} <b>R${price:.0f}</b>")
            total += 1
    lines.append(f"\n<i>{len(results)} datas com voos · {total} opções</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# URA — ConversationHandler /run
# ---------------------------------------------------------------------------
async def cmd_run_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        _STEP_HEADER
        + "<b>Passo 1 de 5 — AEROPORTO DE ORIGEM</b>\n\n"
        "De onde você vai partir?\n\n"
        + _airport_list_text()
        + "\n\n<i>Não encontrou? Use ✏️ Outro para digitar qualquer código IATA.</i>",
        reply_markup=_airport_keyboard(), parse_mode="HTML",
    )
    return PICK_ORIGIN


async def handle_origin_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "__custom__":
        await q.edit_message_text(
            _STEP_HEADER + "<b>Passo 1 de 5 — ORIGEM</b>\n\nDigite o código IATA (3 letras):\n<i>Ex: GRU, CGH, VCP…</i>",
            parse_mode="HTML")
        return TYPE_ORIGIN
    origin = q.data.upper(); context.user_data["origin"] = origin
    await q.edit_message_text(
        _STEP_HEADER + f"✅ Origem: <b>{origin}</b> ({AIRPORT_NAMES.get(origin, origin)})\n\n"
        "<b>Passo 2 de 5 — AEROPORTO DE DESTINO</b>\n\nPara onde você vai?\n\n"
        + _airport_list_text() + "\n\n<i>Não encontrou? Use ✏️ Outro.</i>",
        reply_markup=_airport_keyboard(exclude=origin), parse_mode="HTML")
    return PICK_DEST


async def handle_origin_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    origin = update.message.text.strip().upper()[:3]; context.user_data["origin"] = origin
    await update.message.reply_text(
        _STEP_HEADER + f"✅ Origem: <b>{origin}</b> ({AIRPORT_NAMES.get(origin,'aeroporto informado')})\n\n"
        "<b>Passo 2 de 5 — AEROPORTO DE DESTINO</b>\n\nPara onde você vai?\n\n"
        + _airport_list_text() + "\n\n<i>Não encontrou? Use ✏️ Outro.</i>",
        reply_markup=_airport_keyboard(exclude=origin), parse_mode="HTML")
    return PICK_DEST


async def handle_dest_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "__custom__":
        await q.edit_message_text(
            _STEP_HEADER + f"✅ Origem: <b>{context.user_data['origin']}</b>\n\n"
            "<b>Passo 2 de 5 — DESTINO</b>\n\nDigite o código IATA (3 letras):\n<i>Ex: BSB, GIG, SDU…</i>",
            parse_mode="HTML")
        return TYPE_DEST
    dest = q.data.upper(); context.user_data["dest"] = dest
    origin = context.user_data["origin"]
    await q.edit_message_text(
        _STEP_HEADER + f"✅ Rota: <b>{origin} → {dest}</b> ({AIRPORT_NAMES.get(dest, dest)})\n\n"
        "<b>Passo 3 de 5 — PERÍODO DE BUSCA</b>\n\n"
        f"Hoje é <b>{date.today().strftime('%d/%m/%Y')}</b>. Até quantos meses à frente?\n\n"
        f"  • 1 mês   → até {_months_end_date(1)}\n"
        f"  • 2 meses → até {_months_end_date(2)}\n"
        f"  • 3 meses → até {_months_end_date(3)}\n"
        f"  • 6 meses → até {_months_end_date(6)}\n"
        f"  • 12 meses → até {_months_end_date(12)}\n\n"
        "<i>Quanto mais meses, mais datas verificadas.</i>",
        reply_markup=_months_keyboard(), parse_mode="HTML")
    return PICK_MONTHS


async def handle_dest_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dest = update.message.text.strip().upper()[:3]; context.user_data["dest"] = dest
    origin = context.user_data["origin"]
    await update.message.reply_text(
        _STEP_HEADER + f"✅ Rota: <b>{origin} → {dest}</b> ({AIRPORT_NAMES.get(dest,'aeroporto informado')})\n\n"
        "<b>Passo 3 de 5 — PERÍODO DE BUSCA</b>\n\n"
        f"Hoje é <b>{date.today().strftime('%d/%m/%Y')}</b>. Até quantos meses à frente?\n\n"
        f"  • 1 mês   → até {_months_end_date(1)}\n"
        f"  • 2 meses → até {_months_end_date(2)}\n"
        f"  • 3 meses → até {_months_end_date(3)}\n"
        f"  • 6 meses → até {_months_end_date(6)}\n"
        f"  • 12 meses → até {_months_end_date(12)}\n\n"
        "<i>Quanto mais meses, mais datas verificadas.</i>",
        reply_markup=_months_keyboard(), parse_mode="HTML")
    return PICK_MONTHS


async def handle_months(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    context.user_data["months"] = int(q.data)
    origin = context.user_data["origin"]; dest = context.user_data["dest"]
    await q.edit_message_text(
        _STEP_HEADER + f"✅ Rota: <b>{origin} → {dest}</b>  |  Período: <b>{q.data} meses</b>\n\n"
        "<b>Passo 4 de 5 — DIA DA SEMANA</b>\n\n"
        "Quer filtrar por um dia específico?\n\n"
        "  <b>1</b> = Segunda  <b>2</b> = Terça  <b>3</b> = Quarta  <b>4</b> = Quinta\n"
        "  <b>5</b> = Sexta    <b>6</b> = Sábado  <b>7</b> = Domingo\n"
        "  <b>0</b> = Qualquer dia (busca todos)\n\n"
        "<i>Dica: para viajar toda quarta, escolha 3.</i>",
        reply_markup=_weekday_keyboard(), parse_mode="HTML")
    return PICK_WEEKDAY


async def handle_weekday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    weekday_num = int(q.data); context.user_data["weekday_num"] = weekday_num
    origin = context.user_data["origin"]; dest = context.user_data["dest"]
    months = context.user_data["months"]
    await q.edit_message_text(
        _STEP_HEADER + f"✅ <b>{origin} → {dest}</b>  |  {months} meses  |  <b>{WEEKDAY_LABEL[weekday_num]}</b>\n\n"
        "<b>Passo 5 de 5 — PERÍODO DO DIA</b>\n\n"
        "Em qual horário você prefere voar?\n\n"
        "  🌙 <b>Madrugada</b>  00h00 – 05h59\n"
        "  🌅 <b>Manhã</b>      06h00 – 11h59\n"
        "  🌆 <b>Tarde</b>      12h00 – 18h59\n"
        "  🌃 <b>Noite</b>      19h00 – 23h59  ← ideal pós-trabalho\n"
        "  ⏰ <b>Qualquer</b>   sem filtro de horário",
        reply_markup=_time_keyboard(), parse_mode="HTML")
    return PICK_TIME


async def handle_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ud = context.user_data; ud["time_key"] = q.data

    origin      = ud["origin"];      dest        = ud["dest"]
    months      = ud["months"];      weekday_num = ud["weekday_num"]
    time_key    = ud["time_key"]
    day_label   = WEEKDAY_LABEL[weekday_num]
    period_label, min_t, max_t = TIME_PERIODS[time_key]
    horario_desc = f"{min_t}–{max_t}" if min_t and max_t else ("19h+" if min_t else "sem filtro")
    py_weekday  = (weekday_num - 1) if weekday_num >= 1 else None
    n_dates     = len(_get_search_dates(months, py_weekday))
    chat_id     = q.message.chat_id
    bot         = context.bot

    # ── Tenta disparar no GitHub Actions (nuvem) ──────────────────────────
    if GITHUB_TOKEN:
        triggered = _trigger_github_actions(origin, dest, months, weekday_num, time_key, chat_id)
        if triggered:
            await q.edit_message_text(
                _STEP_HEADER
                + "✅ <b>Busca enviada para a nuvem!</b>\n\n"
                f"  ✈️  Rota:    <b>{origin} → {dest}</b>\n"
                f"  📅  Período: <b>{months} meses</b> (até {_months_end_date(months)})\n"
                f"  📆  Dias:    <b>{day_label}</b>\n"
                f"  🕐  Horário: <b>{period_label.split()[1]}</b> ({horario_desc})\n"
                f"  🔢  Datas:   <b>{n_dates}</b>\n\n"
                "☁️ GitHub Actions está rodando a busca.\n"
                "Os resultados chegam aqui em <b>~2 minutos</b>.",
                parse_mode="HTML",
            )
            return ConversationHandler.END
        else:
            await bot.send_message(chat_id=chat_id,
                text="⚠️ Não consegui acionar o GitHub Actions. Rodando localmente...",
                parse_mode="HTML")

    # ── Fallback: roda localmente no Mac ──────────────────────────────────
    await q.edit_message_text(
        _STEP_HEADER
        + "✅ <b>Resumo da busca</b>\n\n"
        f"  ✈️  Rota:    <b>{origin} → {dest}</b>\n"
        f"  📅  Período: <b>{months} meses</b> (até {_months_end_date(months)})\n"
        f"  📆  Dias:    <b>{day_label}</b>\n"
        f"  🕐  Horário: <b>{period_label.split()[1]}</b> ({horario_desc})\n"
        f"  🔢  Datas a verificar: <b>{n_dates}</b>\n\n"
        "🔍 Buscando localmente... aguarde.",
        parse_mode="HTML",
    )

    loop = asyncio.get_running_loop()

    def notify(text: str):
        asyncio.run_coroutine_threadsafe(
            bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML"),
            loop,
        )

    try:
        data = await loop.run_in_executor(
            None, lambda: _run_custom_search_sync(origin, dest, months, py_weekday, time_key, notify)
        )
        result_text = _format_results(origin, dest, data["results"], time_key)
        for chunk in [result_text[i:i+4000] for i in range(0, len(result_text), 4000)]:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")
    except Exception as exc:
        logger.error("Erro na busca local: %s", exc, exc_info=True)
        await bot.send_message(
            chat_id=chat_id,
            text="❌ Erro durante a busca local. Tente novamente com /run.",
            parse_mode="HTML",
        )

    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Busca cancelada. Digite /run para começar de novo.")
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Monitor padrão (agendado automaticamente)
# ---------------------------------------------------------------------------
def _run_monitor_sync() -> dict:
    global _running, _last_run
    if not _run_lock.acquire(blocking=False):
        return {"error": "already_running"}
    _running = True
    start = datetime.now()
    logger.info("=== Monitor iniciado (%s) ===", start.strftime("%d/%m %H:%M"))
    try:
        from main import run_monitor
        run_monitor()
        elapsed = int((datetime.now() - start).total_seconds())
        result = {"started_at": start.strftime("%d/%m %H:%M"), "elapsed_s": elapsed, "status": "ok"}
    except Exception as exc:
        elapsed = int((datetime.now() - start).total_seconds())
        logger.error("Erro no monitor: %s", exc, exc_info=True)
        result = {"started_at": start.strftime("%d/%m %H:%M"), "elapsed_s": elapsed,
                  "status": "error", "error": str(exc)[:300]}
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
# Error handler global
# ---------------------------------------------------------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exceção no handler:", exc_info=context.error)
    if not isinstance(update, Update):
        return
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Ocorreu um erro interno. Tente novamente com /run ou /start.",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Comandos simples
# ---------------------------------------------------------------------------
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    run_times, tz = _load_schedule_config()
    cloud = "☁️ Nuvem (GitHub Actions)" if GITHUB_TOKEN else "💻 Local (Mac)"
    await update.message.reply_text(
        "<b>LATAM Monitor</b>\n\n"
        f"/run — busca interativa URA → {cloud}\n"
        "/start — menu inicial + guia\n"
        "/ajuda — envia o guia 'Como usar'\n"
        "/status — estado do bot\n"
        "/last — última execução agendada\n"
        "/cancel — cancela busca\n"
        "/help — esta mensagem\n\n"
        f"⏰ Automático: {' e '.join(run_times)} ({tz})",
        parse_mode="HTML",
    )


async def _send_how_to_use_file(update: Update):
    message = update.effective_message
    if message is None:
        logger.warning("Não foi possível enviar guia: update sem effective_message")
        return
    if not GUIDE_PATH.exists():
        logger.warning("Guia ausente em %s; enviando fallback em texto.", GUIDE_PATH)
        await message.reply_text(GUIDE_FALLBACK_TEXT)
        return
    with open(GUIDE_PATH, "rb") as guide_file:
        await message.reply_document(
            document=guide_file,
            filename="Como_usar_Telegram.md",
            caption="📘 Guia completo: <b>Como usar o bot no Telegram</b>",
            parse_mode="HTML",
        )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    run_times, tz = _load_schedule_config()
    cloud = "☁️ Nuvem (GitHub Actions)" if GITHUB_TOKEN else "💻 Local (Mac)"
    await update.message.reply_text(
        "<b>Bem-vindo ao LATAM Monitor</b>\n\n"
        "Escolha como começar:\n"
        "• /run — iniciar busca URA (5 passos)\n"
        "• /ajuda — receber o guia 'Como usar'\n"
        "• /status — ver estado atual do bot\n"
        "• /last — ver última execução agendada\n"
        "• /help — resumo rápido dos comandos\n\n"
        f"Execução: {cloud}\n"
        f"⏰ Agendado: {' e '.join(run_times)} ({tz})",
        parse_mode="HTML",
    )
    await _send_how_to_use_file(update)


async def cmd_ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    await _send_how_to_use_file(update)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    uptime = datetime.now() - _STARTED_AT
    h, m = divmod(int(uptime.total_seconds()), 3600)
    m //= 60
    estado = "🔄 Buscando..." if _running else "✅ Aguardando"
    cloud  = "☁️ GitHub Actions configurado" if GITHUB_TOKEN else "💻 Busca local (sem GITHUB_TOKEN)"
    run_times, tz = _load_schedule_config()
    await update.message.reply_text(
        f"<b>Status</b>\n\n{estado}\n{cloud}\n"
        f"Uptime: {h}h {m}min\n"
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
    msg = (f"<b>Última execução agendada</b>\n\n{emoji} {_last_run['status'].upper()}\n"
           f"Início: {_last_run['started_at']}\nDuração: {_last_run['elapsed_s']}s")
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
        scheduler.add_job(_scheduled_run, CronTrigger(hour=h, minute=m, timezone=timezone),
                          id=f"monitor_{h:02d}{m:02d}", misfire_grace_time=600, coalesce=True)
        logger.info("Agendado: %02d:%02d %s", h, m, timezone)
    scheduler.start()
    return scheduler


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    logger.info("Iniciando LATAM Monitor Bot...")
    logger.info("Chat IDs: %s | GitHub Actions: %s", ALLOWED_IDS, "SIM" if GITHUB_TOKEN else "NÃO")
    scheduler = _start_scheduler()
    app = ApplicationBuilder().token(TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("run", cmd_run_start)],
        states={
            PICK_ORIGIN:  [CallbackQueryHandler(handle_origin_pick)],
            TYPE_ORIGIN:  [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_origin_type)],
            PICK_DEST:    [CallbackQueryHandler(handle_dest_pick)],
            TYPE_DEST:    [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_dest_type)],
            PICK_MONTHS:  [CallbackQueryHandler(handle_months)],
            PICK_WEEKDAY: [CallbackQueryHandler(handle_weekday)],
            PICK_TIME:    [CallbackQueryHandler(handle_time)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_user=True, per_chat=True,
        allow_reentry=True,
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ajuda", cmd_ajuda))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("last", cmd_last))
    app.add_error_handler(error_handler)
    logger.info("Bot ativo. Aguardando comandos...")
    try:
        app.run_polling(drop_pending_updates=True)
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Bot encerrado.")


if __name__ == "__main__":
    main()
