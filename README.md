# biuro — Monitor de preços LATAM com controle por Telegram

O **biuro** monitora preços de voos LATAM, compara com histórico e envia alertas no Telegram.
O uso diário é feito pelo bot, com uma **URA de 5 passos** no comando `/run`.

## Visão geral do funcionamento atual

```text
Telegram (usuário)
   │
   ├─ /run  → URA interativa (origem, destino, meses, dia, período)
   ├─ /start → menu inicial + arquivo “Como usar”
   ├─ /ajuda → arquivo “Como usar”
   └─ /status, /last, /help
   │
   ▼
bot.py (python-telegram-bot)
   │
   ├─ Se GITHUB_TOKEN existir:
   │     dispara workflow_dispatch no GitHub Actions
   │
   └─ Senão:
         executa busca local de fallback
   │
   ▼
Busca de voos + análise
   ├─ src/google_flights_client.py
   ├─ src/flight_search.py
   ├─ src/price_analyzer.py
   └─ src/database.py (histórico)
   │
   ▼
Notificação Telegram
   └─ src/telegram_notifier.py
```

Além do uso manual via comandos, há execução agendada com horários definidos em `config.yaml`.

---

## Comandos do bot

| Comando | Função |
|---|---|
| `/start` | Inicia o uso com menu e envia o guia **Como usar** em arquivo Markdown |
| `/run` | Inicia a URA de busca (5 passos) |
| `/ajuda` | Reenvia o guia **Como usar** |
| `/status` | Mostra estado do bot, modo de execução e uptime |
| `/last` | Mostra a última execução agendada registrada |
| `/help` | Mostra resumo rápido dos comandos |
| `/cancel` | Cancela fluxo `/run` em andamento |

---

## URA do `/run` (5 passos)

1. **Origem** (IATA)
2. **Destino** (IATA)
3. **Janela de busca** (`1`, `2`, `3`, `6` ou `12` meses)
4. **Dia da semana** (`1..7` ou `0` para qualquer)
5. **Período do dia** (`madrugada`, `manhã`, `tarde`, `noite`, `qualquer`)

Ao final, o bot envia um resumo da busca e executa na nuvem (quando possível), retornando resultados no chat.

---

## Arquivo “Como usar” no Telegram

O guia fica versionado em:

- `docs/COMO_USAR_TELEGRAM.md`

Disponibilidade no bot:

- enviado automaticamente no `/start`
- disponível sob demanda via `/ajuda`

---

## Execução agendada

O bot agenda execuções automáticas usando `APScheduler` com horários em `config.yaml`:

- `schedule.run_times`
- `schedule.timezone`

O resultado da última execução fica disponível em `/last` durante a sessão atual do processo.

---

## Estrutura de arquivos (resumo)

```text
biuro/
├── bot.py                     # Bot Telegram + URA + agendamento
├── main.py                    # Execução principal do monitor
├── run_search.py              # Busca customizada para workflow_dispatch
├── config.yaml                # Configuração de rotas e horários
├── requirements.txt           # Dependências gerais
├── requirements-bot.txt       # Dependências do bot
├── docs/
│   └── COMO_USAR_TELEGRAM.md  # Guia enviado no /start e /ajuda
└── src/
    ├── google_flights_client.py
    ├── flight_search.py
    ├── price_analyzer.py
    ├── database.py
    └── telegram_notifier.py
```

---

## Configuração rápida

### 1) Ambiente

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Para rodar apenas o bot, também pode usar:

```bash
pip install -r requirements-bot.txt
```

### 2) Variáveis `.env`

Defina no mínimo:

- `TELEGRAM_BOT_TOKEN`
- `ALLOWED_CHAT_IDS` (ou `TELEGRAM_CHAT_ID`)

Opcional (para execução na nuvem via dispatch):

- `GITHUB_TOKEN`
- `GITHUB_REPO`
- `GITHUB_BRANCH`

### 3) Rodar bot

```bash
python bot.py
```

No Telegram, envie `/start`.

---

## Operação e manutenção

- Logs locais do bot: pasta `logs/` (arquivo `bot.log`).
- Deploy cloud: `fly.toml` + `Dockerfile`.
- Workflow de execução em nuvem: `.github/workflows/monitor.yml`.

---

## Observações importantes

- O bot valida `chat_id` antes de executar comandos.
- Se o dispatch no GitHub Actions falhar, o fluxo `/run` tenta fallback local.
- O guia de uso Telegram é entregue como arquivo para facilitar consulta no próprio chat.

