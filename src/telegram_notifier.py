"""
Telegram alert module for the LATAM flight price monitor.

Uses the Telegram Bot API directly via requests (no extra SDK needed).
"""

import logging
import os
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_TG_API = "https://api.telegram.org/bot{token}/sendMessage"

# Day-of-week in Portuguese
_DOW_PT = {0: "Seg", 1: "Ter", 2: "Qua", 3: "Qui", 4: "Sex", 5: "Sáb", 6: "Dom"}


# ---------------------------------------------------------------------------
# Core sender
# ---------------------------------------------------------------------------

def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    Send *text* to the configured Telegram chat.

    Returns True on success, False on failure.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.error(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set. Message not sent."
        )
        return False

    url = _TG_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info("Telegram message sent (chat_id=%s)", chat_id)
        return True
    except requests.RequestException as exc:
        logger.error("Failed to send Telegram message: %s", exc)
        return False


def test_connection() -> bool:
    """Send a test ping to verify Telegram credentials are working."""
    return send_message(
        "✅ <b>Monitor LATAM — conexão OK!</b>\n"
        "O monitor está configurado e funcionando corretamente."
    )


# ---------------------------------------------------------------------------
# Message formatters
# ---------------------------------------------------------------------------

def _format_price_line(price_brl: float, evaluation: dict) -> str:
    """Return a formatted price + evaluation line."""
    emoji = evaluation.get("verdict_emoji", "⚪")
    verdict = evaluation.get("verdict", "?")
    pct = evaluation.get("pct_vs_median")
    p50 = evaluation.get("p50_ref")

    price_str = f"R$ {price_brl:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    if pct is not None and p50 is not None:
        direction = "abaixo" if pct < 0 else "acima"
        p50_str = f"R$ {p50:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return (
            f"{emoji} <b>{price_str}</b> — {verdict} "
            f"({abs(pct):.0f}% {direction} da mediana histórica {p50_str})"
        )
    elif pct is not None:
        direction = "abaixo" if pct < 0 else "acima"
        return f"{emoji} <b>{price_str}</b> — {verdict} ({abs(pct):.0f}% {direction} da mediana)"
    else:
        return f"{emoji} <b>{price_str}</b> — {verdict} (sem histórico)"


def format_weekday_alert(
    flight: dict,
    evaluation: dict,
    stats: dict,
    date_str: str,
    route_config: dict,
) -> str:
    """
    Format an alert for the Wed/Fri CGH→BSB Full-fare monitor.

    Parameters
    ----------
    flight      : parsed flight dict from amadeus_client.parse_offer()
    evaluation  : result of price_analyzer.evaluate_deal()
    stats       : result of price_analyzer.get_route_stats()
    date_str    : flight date YYYY-MM-DD
    route_config: dict from config.yaml weekday_routes entry
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    dow = _DOW_PT[dt.weekday()]
    date_display = dt.strftime(f"{dow}, %d/%m/%Y")

    origin = flight["origin"]
    dest = flight["destination"]
    dep = flight["departure_time"]
    arr = flight["arrival_time"]
    fn = flight["flight_number"]
    price_brl = flight["price_brl"]
    fare = flight.get("fare_family") or "Full"

    evaluation["p50_ref"] = stats.get("p50")
    price_line = _format_price_line(price_brl, evaluation)

    cash_url = (
        f"https://www.latamairlines.com/br/pt/oferta-voos"
        f"?origin={origin}&destination={dest}"
        f"&outbound={date_str}&adt=1&chd=0&inf=0&trip=OW&cabin=Y&redemption=false"
    )
    points_url = cash_url.replace("redemption=false", "redemption=true")

    lines = [
        f"✈️ <b>LATAM {origin}→{dest} | {date_display}</b>",
        f"🕐 {dep} → {arr}  |  Voo {fn}  |  Tarifa: {fare}",
        "",
        price_line,
        "",
        f"🔗 <a href='{cash_url}'>Comprar em R$</a>  |  "
        f"<a href='{points_url}'>Ver em Pontos LATAM Pass</a>",
    ]

    if stats["count"] > 0:
        p25_str = f"R$ {stats['p25']:,.0f}".replace(",", ".")
        p50_str = f"R$ {stats['p50']:,.0f}".replace(",", ".")
        p75_str = f"R$ {stats['p75']:,.0f}".replace(",", ".")
        lines.append(
            f"\n📊 Histórico ({stats['count']} compras): "
            f"min {p25_str} · med {p50_str} · max {p75_str}"
        )

    return "\n".join(lines)


def format_sunday_alert(
    last_flight: Optional[dict],
    cheapest_flight: Optional[dict],
    evaluation_last: Optional[dict],
    evaluation_cheap: Optional[dict],
    stats: dict,
    date_str: str,
    route_config: dict,
) -> str:
    """
    Format an alert for the Sunday BSB↔CGH monitor (last OR cheapest).
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    date_display = dt.strftime("Dom, %d/%m/%Y")

    origin = route_config["origin"]
    dest = route_config["destination"]

    cash_url = (
        f"https://www.latamairlines.com/br/pt/oferta-voos"
        f"?origin={origin}&destination={dest}"
        f"&outbound={date_str}&adt=1&chd=0&inf=0&trip=OW&cabin=Y&redemption=false"
    )
    points_url = cash_url.replace("redemption=false", "redemption=true")

    lines = [f"✈️ <b>LATAM {origin}→{dest} | {date_display}</b>", ""]

    # Last flight block
    if last_flight:
        ev = evaluation_last or {}
        ev["p50_ref"] = stats.get("p50")
        lines.append(f"🌙 <b>Último voo do dia</b>")
        lines.append(
            f"  {last_flight['departure_time']} → {last_flight['arrival_time']}  "
            f"|  {last_flight['flight_number']}"
        )
        lines.append(f"  {_format_price_line(last_flight['price_brl'], ev)}")
    else:
        lines.append("🌙 <b>Último voo:</b> não encontrado")

    lines.append("")

    # Cheapest block (only show if different from last)
    if cheapest_flight and (
        last_flight is None
        or cheapest_flight.get("flight_number") != last_flight.get("flight_number")
    ):
        ev = evaluation_cheap or {}
        ev["p50_ref"] = stats.get("p50")
        lines.append(f"💰 <b>Mais barato do dia</b>")
        lines.append(
            f"  {cheapest_flight['departure_time']} → {cheapest_flight['arrival_time']}  "
            f"|  {cheapest_flight['flight_number']}"
        )
        lines.append(f"  {_format_price_line(cheapest_flight['price_brl'], ev)}")
        lines.append("")

    lines.append(
        f"🔗 <a href='{cash_url}'>Comprar em R$</a>  |  "
        f"<a href='{points_url}'>Ver em Pontos LATAM Pass</a>"
    )

    if stats["count"] > 0:
        p50_str = f"R$ {stats['p50']:,.0f}".replace(",", ".")
        lines.append(
            f"\n📊 Histórico ({stats['count']} compras) · mediana {p50_str}"
        )

    return "\n".join(lines)


def send_daily_summary(results: list[dict]) -> bool:
    """
    Send a summary message listing all checked routes and their status.

    Each item in *results* should have keys:
        route, date, status ('found'/'not_found'/'error'), price_brl, verdict
    """
    now = datetime.now().strftime("%d/%m %H:%M")
    lines = [f"📋 <b>Resumo do Monitor LATAM — {now}</b>", ""]

    for r in results:
        route = r.get("route", "?→?")
        date = r.get("date", "")
        status = r.get("status", "error")

        if status == "found":
            price = r.get("price_brl", 0)
            verdict = r.get("verdict", "?")
            emoji = r.get("verdict_emoji", "⚪")
            price_str = f"R$ {price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            lines.append(f"{emoji} {route} {date}: {price_str} — {verdict}")
        elif status == "not_found":
            lines.append(f"⚫ {route} {date}: sem resultados")
        else:
            lines.append(f"❌ {route} {date}: erro na consulta")

    return send_message("\n".join(lines))
