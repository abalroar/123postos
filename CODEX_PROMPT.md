# Prompt Executável: Expandir Bot de Voos para Multi-Companhia e Escalas

## Contexto do Projeto

Este é um bot Telegram (`biuro`) que monitora preços de voos domésticos brasileiros via Google Flights (`fast-flights`). Atualmente busca **apenas voos LATAM diretos**. O objetivo é expandir para suportar GOL, Azul e outras companhias, além de permitir voos com escala.

**Stack**: Python 3.11, fast-flights, python-telegram-bot, Fly.io, GitHub Actions.

**Restrição importante**: Google Flights (via `fast-flights`) **não retorna** número do voo, tarifa (Light/Full/Flex), nem código IATA da companhia. Retorna: `f.name` (string livre como "LATAM Airlines", "GOL"), `f.stops` (int), `f.price` (string "R$1.150"), `f.departure`, `f.arrival`, `f.duration`.

---

## Tarefa 1: Multi-companhia em `src/google_flights_client.py`

### O que existe hoje (linhas 107-118):
```python
for f in result.flights:
    if "LATAM" not in (f.name or "").upper():
        continue  # hardcoded LATAM-only
    stops_val = f.stops
    if isinstance(stops_val, int) and stops_val > 0:
        continue  # hardcoded diretos-only
```

### O que fazer:

1. **Adicionar constante `AIRLINE_PATTERNS`** no topo do arquivo (após os imports):
```python
# Mapeamento: substring no f.name (upper) → (código IATA, nome curto)
AIRLINE_PATTERNS: list[tuple[str, str, str]] = [
    ("LATAM", "LA", "LATAM"),
    ("GOL",   "G3", "GOL"),
    ("AZUL",  "AD", "Azul"),
    ("VOEPASS","2Z", "Voepass"),
    ("MAP",   "7M", "MAP"),
]
```

2. **Adicionar função `_identify_airline`**:
```python
def _identify_airline(name: str) -> tuple[str, str]:
    """Retorna (código IATA, nome curto) a partir do f.name do Google Flights."""
    upper = (name or "").upper()
    for pattern, code, display in AIRLINE_PATTERNS:
        if pattern in upper:
            return code, display
    return "??", name or "Desconhecida"
```

3. **Alterar assinatura de `search_google_flights`**:
```python
def search_google_flights(
    origin: str,
    dest: str,
    date_str: str,
    min_dep_time: Optional[str] = None,
    airlines: Optional[list[str]] = None,  # NOVO: None = ["LA"] (backward-compat)
    max_stops: Optional[int] = 0,          # NOVO: 0 = só direto, None = qualquer
) -> list[dict]:
```

4. **Substituir filtro de companhia** (linha 109):
```python
# ANTES:
if "LATAM" not in (f.name or "").upper():
    continue

# DEPOIS:
airline_code, airline_name = _identify_airline(f.name)
effective_airlines = airlines if airlines is not None else ["LA"]
if airline_code not in effective_airlines:
    continue
```

5. **Substituir filtro de escalas** (linhas 116-118):
```python
# ANTES:
stops_val = f.stops
if isinstance(stops_val, int) and stops_val > 0:
    continue

# DEPOIS:
stops_val = f.stops
if max_stops is not None and isinstance(stops_val, int) and stops_val > max_stops:
    continue
```

6. **Incluir cia no dict de retorno** (linha 139+):
```python
flights.append({
    "airline": airline_code,        # NOVO
    "airline_name": airline_name,   # NOVO
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
```

7. **Atualizar mensagem de log** (linha 153):
```python
# ANTES:
logger.info("Google Flights %s->%s %s: %d voo(s) LATAM diretos (únicos)", ...)

# DEPOIS:
cias = ",".join(effective_airlines) if airlines else "LA"
logger.info("Google Flights %s->%s %s: %d voo(s) [%s] (únicos)", origin, dest, date_str, len(flights), cias)
```

8. **Atualizar dedup key** para incluir cia (evitar confundir voo LATAM e GOL no mesmo horário):
```python
# ANTES:
key = (dep_time, arr_time, price)

# DEPOIS:
key = (airline_code, dep_time, arr_time, price)
```

### Comportamento backward-compatible:
- `search_google_flights("CGH", "BSB", "2026-05-15")` → só LATAM, só direto (igual hoje)
- `search_google_flights("CGH", "BSB", "2026-05-15", airlines=["LA", "G3", "AD"], max_stops=1)` → LATAM+GOL+Azul, diretos e 1 escala

---

## Tarefa 2: URA expandida em `bot.py` (5→7 passos)

### Novos estados da conversa:

Alterar a linha 123-124:
```python
# ANTES:
(PICK_ORIGIN, TYPE_ORIGIN, PICK_DEST, TYPE_DEST,
 PICK_MONTHS, PICK_WEEKDAY, PICK_TIME) = range(7)

# DEPOIS:
(PICK_ORIGIN, TYPE_ORIGIN, PICK_DEST, TYPE_DEST,
 PICK_MONTHS, PICK_WEEKDAY, PICK_AIRLINE, PICK_STOPS, PICK_TIME) = range(9)
```

### Novas constantes (adicionar após `WEEKDAY_SHORT`, ~linha 113):

```python
AIRLINE_BUTTONS = {
    "✈️ LATAM":  "LA",
    "🟠 GOL":    "G3",
    "🔵 Azul":   "AD",
    "📋 Todas":  "all",
}

AIRLINE_LABEL = {
    "LA": "LATAM", "G3": "GOL", "AD": "Azul", "all": "Todas as cias",
}

STOPS_BUTTONS = {
    "Só diretos": "0",
    "Diretos + com escala": "1",
}
```

### Novos teclados:

```python
def _airline_keyboard() -> InlineKeyboardMarkup:
    items = list(AIRLINE_BUTTONS.items())
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(l, callback_data=v) for l, v in items[:2]],
        [InlineKeyboardButton(l, callback_data=v) for l, v in items[2:]],
    ])


def _stops_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(l, callback_data=v) for l, v in STOPS_BUTTONS.items()],
    ])
```

### Novos handlers (inserir entre `handle_weekday` e `handle_time`):

```python
async def handle_airline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    airline_key = q.data; context.user_data["airline_key"] = airline_key
    origin = context.user_data["origin"]; dest = context.user_data["dest"]
    months = context.user_data["months"]; weekday_num = context.user_data["weekday_num"]
    cia_label = AIRLINE_LABEL.get(airline_key, airline_key)
    await q.edit_message_text(
        _STEP_HEADER + f"✅ <b>{origin} → {dest}</b>  |  {months} meses  |  {WEEKDAY_LABEL[weekday_num]}  |  <b>{cia_label}</b>\n\n"
        "<b>Passo 6 de 7 — ESCALAS</b>\n\n"
        "Incluir voos com escala nos resultados?\n\n"
        "  <b>Só diretos</b> — mais rápido, menos opções\n"
        "  <b>Diretos + com escala</b> — mais opções, pode ser mais barato",
        reply_markup=_stops_keyboard(), parse_mode="HTML")
    return PICK_STOPS


async def handle_stops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    max_stops = int(q.data); context.user_data["max_stops"] = max_stops
    origin = context.user_data["origin"]; dest = context.user_data["dest"]
    months = context.user_data["months"]; weekday_num = context.user_data["weekday_num"]
    airline_key = context.user_data["airline_key"]
    cia_label = AIRLINE_LABEL.get(airline_key, airline_key)
    stops_label = "Só diretos" if max_stops == 0 else "Diretos + escalas"
    await q.edit_message_text(
        _STEP_HEADER + f"✅ <b>{origin} → {dest}</b>  |  {months}m  |  {WEEKDAY_LABEL[weekday_num]}  |  {cia_label}  |  {stops_label}\n\n"
        "<b>Passo 7 de 7 — PERÍODO DO DIA</b>\n\n"
        "Em qual horário você prefere voar?\n\n"
        "  🌙 <b>Madrugada</b>  00h00 – 05h59\n"
        "  🌅 <b>Manhã</b>      06h00 – 11h59\n"
        "  🌆 <b>Tarde</b>      12h00 – 18h59\n"
        "  🌃 <b>Noite</b>      19h00 – 23h59\n"
        "  ⏰ <b>Qualquer</b>   sem filtro de horário",
        reply_markup=_time_keyboard(), parse_mode="HTML")
    return PICK_TIME
```

### Atualizar `handle_weekday` para ir para PICK_AIRLINE em vez de PICK_TIME:

```python
# No final de handle_weekday, mudar:
#   return PICK_TIME
# para:
    await q.edit_message_text(
        _STEP_HEADER + f"✅ <b>{origin} → {dest}</b>  |  {months} meses  |  <b>{WEEKDAY_LABEL[weekday_num]}</b>\n\n"
        "<b>Passo 5 de 7 — COMPANHIA AÉREA</b>\n\n"
        "Qual companhia deseja buscar?\n\n"
        "  ✈️ <b>LATAM</b> — voos LA\n"
        "  🟠 <b>GOL</b> — voos G3\n"
        "  🔵 <b>Azul</b> — voos AD\n"
        "  📋 <b>Todas</b> — todas as companhias",
        reply_markup=_airline_keyboard(), parse_mode="HTML")
    return PICK_AIRLINE
```

### Atualizar `handle_time` para ler airline_key e max_stops:

No `handle_time`, adicionar após coletar os dados existentes:
```python
airline_key = ud.get("airline_key", "LA")
max_stops = ud.get("max_stops", 0)
airlines_list = None if airline_key == "all" else [airline_key]
```

E passar nos dispatches (GitHub Actions e local).

### Atualizar `_trigger_github_actions`:

Adicionar `"airlines"` e `"max_stops"` no payload `inputs`:
```python
"inputs": {
    "origin": origin,
    "dest": dest,
    "months": str(months),
    "weekday": str(weekday_num),
    "time_period": time_key,
    "chat_id": str(chat_id),
    "airlines": airline_key,          # NOVO
    "max_stops": str(max_stops),      # NOVO
},
```

### Atualizar `_run_custom_search_sync`:

Passar `airlines` e `max_stops` para `search_google_flights`:
```python
flights = search_google_flights(origin, dest, date_str,
                                airlines=airlines_list,
                                max_stops=max_stops)
```

### Atualizar `_format_results` para incluir cia e escala:

```python
# Na formatação de cada voo, mudar:
#   lines.append(f"  {dep}→{arr}{dur_str} <b>R${price:.0f}</b>")
# para:
cia = f.get("airline_name", "")
stops_icon = " 🔄" if f.get("stops", 0) > 0 else ""
cia_prefix = f"[{cia}] " if airline_key == "all" else ""
lines.append(f"  {cia_prefix}{dep}→{arr}{dur_str}{stops_icon} <b>R${price:.0f}</b>")
```

### Atualizar ConversationHandler (no final do arquivo):

Adicionar os novos estados:
```python
states={
    PICK_ORIGIN:  [...],
    TYPE_ORIGIN:  [...],
    PICK_DEST:    [...],
    TYPE_DEST:    [...],
    PICK_MONTHS:  [...],
    PICK_WEEKDAY: [...],
    PICK_AIRLINE: [CallbackQueryHandler(handle_airline)],   # NOVO
    PICK_STOPS:   [CallbackQueryHandler(handle_stops)],     # NOVO
    PICK_TIME:    [...],
},
```

### Atualizar _STEP_HEADER:

```python
# ANTES:
_STEP_HEADER = "✈️ <b>LATAM Monitor</b> — Nova busca\n─────────────────────\n"

# DEPOIS:
_STEP_HEADER = "✈️ <b>Flight Monitor</b> — Nova busca\n─────────────────────\n"
```

---

## Tarefa 3: `run_search.py` — Novos parâmetros e formatação

### Novos env vars (adicionar após linha 50):

```python
_AIRLINES_INPUT = os.environ.get("SEARCH_AIRLINES", "LA").strip().upper()
AIRLINES_LIST = None if _AIRLINES_INPUT == "ALL" else [a.strip() for a in _AIRLINES_INPUT.split(",")]
MAX_STOPS = int(os.environ.get("SEARCH_MAX_STOPS", "0"))
```

### Alterar chamada a `search_google_flights` (linha 169):

```python
# ANTES:
flights = search_google_flights(ORIGIN, DEST, date_str)

# DEPOIS:
flights = search_google_flights(ORIGIN, DEST, date_str,
                                airlines=AIRLINES_LIST,
                                max_stops=MAX_STOPS)
```

### Atualizar `format_results` para agrupar por cia:

```python
def format_results(origin, dest, results, time_key, failed_dates=None, airline_key="LA"):
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
            # Agrupa por companhia
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
```

### Atualizar chamada a `format_results` (linha 182):

```python
result_text = format_results(ORIGIN, DEST, results, TIME_KEY, failed_dates, airline_key=_AIRLINES_INPUT)
```

### Atualizar mensagem de início (linha 156):

```python
cia_desc = "todas as cias" if _AIRLINES_INPUT == "ALL" else _AIRLINES_INPUT
stops_desc = "diretos" if MAX_STOPS == 0 else f"até {MAX_STOPS} escala(s)"
send_message(
    f"🔍 <b>Iniciando busca</b>\n"
    f"<b>{ORIGIN}→{DEST}</b> | {MONTHS} meses | {weekday_label} | {period_label}\n"
    f"🏢 {cia_desc} | {stops_desc}\n"
    f"📅 {total_raw} datas para verificar...{sample_note}"
)
```

---

## Tarefa 4: `.github/workflows/monitor.yml` — Novos inputs

Adicionar após o input `time_period` (linha 28):

```yaml
      airlines:
        description: 'Cia aérea: LA, G3, AD, ALL'
        required: false
        default: 'LA'
      max_stops:
        description: 'Escalas: 0=só direto, 1=até 1 escala'
        required: false
        default: '0'
```

Adicionar env vars no step "Busca customizada" (após `SEARCH_TIME_PERIOD`):

```yaml
          SEARCH_AIRLINES: ${{ inputs.airlines }}
          SEARCH_MAX_STOPS: ${{ inputs.max_stops }}
```

---

## Checklist de Verificação

Após implementar tudo:

1. [ ] `search_google_flights("CGH", "BSB", "2026-05-15")` retorna só LATAM direto (backward-compat)
2. [ ] `search_google_flights("CGH", "BSB", "2026-05-15", airlines=["LA","G3","AD"], max_stops=1)` retorna múltiplas cias e escalas
3. [ ] Cada voo no retorno tem campos `airline` e `airline_name`
4. [ ] URA do Telegram funciona com 7 passos
5. [ ] Escolher "LATAM" na URA resulta nos mesmos resultados de antes
6. [ ] Escolher "Todas" agrupa resultados por companhia
7. [ ] Indicador 🔄 aparece em voos com escala
8. [ ] GitHub Actions dispatch aceita `airlines` e `max_stops`
9. [ ] `run_search.py` lê `SEARCH_AIRLINES` e `SEARCH_MAX_STOPS` corretamente
10. [ ] Nenhuma feature existente (monitor agendado, /status, /last) quebrou
