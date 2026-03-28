"""
LATAM Monitor Bot — serviço unificado: Telegram polling + agendador local.

Roda como processo persistente no Mac via launchd.
O scraping usa o IP residencial do Mac — sem bloqueio da LATAM.

Comandos disponíveis:
  /run    — dispara busca imediata
  /status — estado atual do bot e uptime
  /last   — resultado da última execução
  /help   — lista de comandos

Setup:
  1. Copie .env.example para .env e preencha as variáveis
  2. python bot.py                    (teste manual)
  3. Siga SETUP_MAC.md para instalar via launchd
"""

import asyncio
import logging
import logging.handlers
import os
import threading
from datetime import datetime
from pathlib import Path

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")

# ---------------------------------------------------------------------------
# Logging rotativo (5 MB × 5 arquivos = 25 MB máximo)
# ---------------------------------------------------------------------------
LOG_DIR = _ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "bot.log",
            maxBytes=5_000_000,
            backupCount=5,
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("latam_bot")

# ---------------------------------------------------------------------------
# Configuração — lida do .env
# ---------------------------------------------------------------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN não configurado no .env")

# IDs permitidos: ALLOWED_CHAT_IDS (vírgula) ou TELEGRAM_CHAT_ID como fallback
_raw_ids = os.environ.get("ALLOWED_CHAT_IDS") or os.environ.get("TELEGRAM_CHAT_ID", "")
ALLOWED_IDS: set[int] = {int(x.strip()) for x in _raw_ids.split(",") if x.strip()}
if not ALLOWED_IDS:
    raise RuntimeError("Defina ALLOWED_CHAT_IDS ou TELEGRAM_CHAT_ID no .env")

# ---------------------------------------------------------------------------
# Estado global (simples — processo único)
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


# ---------------------------------------------------------------------------
# Monitor runner — síncrono, roda em thread separada
# ---------------------------------------------------------------------------
def _run_monitor_sync() -> dict:
    """
    Executa run_monitor() com lock para evitar concorrência.
    Thread-safe. Retorna dict com resultado.
    """
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
        result = {
            "started_at": start.strftime("%d/%m %H:%M"),
            "elapsed_s": elapsed,
            "status": "ok",
        }
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
    """Chamado pelo APScheduler. Roda e notifica falhas."""
    from src.telegram_notifier import send_message
    result = _run_monitor_sync()
    if result.get("error") == "already_running":
        return
    if result["status"] == "error":
        send_message(
            f"❌ <b>Execução agendada falhou</b>\n\n"
            f"<code>{result['error']}</code>"
        )


# ---------------------------------------------------------------------------
# Handlers de comandos Telegram
# ---------------------------------------------------------------------------
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    run_times, tz = _load_schedule_config()
    await update.message.reply_text(
        "<b>LATAM Monitor</b>\n\n"
        "/run — busca imediata\n"
        "/status — estado do bot\n"
        "/last — última execução\n"
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
        f"<b>Status</b>\n\n"
        f"{estado}\n"
        f"Uptime: {h}h {m}min\n"
        f"Horários: {', '.join(run_times)} ({tz})\n"
        f"Iniciado: {_STARTED_AT.strftime('%d/%m %H:%M')}",
        parse_mode="HTML",
    )


async def cmd_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    if not _last_run:
        await update.message.reply_text("Nenhuma execução registrada ainda nesta sessão.")
        return
    emoji = "✅" if _last_run["status"] == "ok" else "❌"
    msg = (
        f"<b>Última execução</b>\n\n"
        f"{emoji} {_last_run['status'].upper()}\n"
        f"Início: {_last_run['started_at']}\n"
        f"Duração: {_last_run['elapsed_s']}s"
    )
    if _last_run.get("error"):
        msg += f"\n\nErro:\n<code>{_last_run['error']}</code>"
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    if _running:
        await update.message.reply_text("⏳ Já tem uma busca em andamento.")
        return
    await update.message.reply_text("🔍 Buscando... pode demorar 10–15 min.")
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _run_monitor_sync)
    if result.get("error") == "already_running":
        await update.message.reply_text("⏳ Já tem uma busca em andamento.")
    elif result["status"] == "error":
        await update.message.reply_text(
            f"❌ Erro:\n<code>{result['error']}</code>",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"✅ Concluído em {result['elapsed_s']}s.\n"
            "Verifique as mensagens acima com os resultados."
        )


# ---------------------------------------------------------------------------
# Agendador (roda em background thread)
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
            misfire_grace_time=600,   # aceita até 10 min de atraso (Mac saiu do sono)
            coalesce=True,            # se perdeu múltiplos, roda só uma vez
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
    app.add_handler(CommandHandler(["help", "start"], cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("last", cmd_last))
    app.add_handler(CommandHandler("run", cmd_run))

    logger.info("Bot ativo. Aguardando comandos...")
    try:
        app.run_polling(drop_pending_updates=True)
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Bot encerrado.")


if __name__ == "__main__":
    main()
