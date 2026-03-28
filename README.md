# biuro — Monitor de Voos LATAM

Monitora preços de voos LATAM automaticamente e envia alertas no Telegram.  
Controlado 100% pelo Telegram, roda na nuvem (Fly.io + GitHub Actions), **sem precisar do computador ligado**.

---

## Como funciona

```
Telegram (/run)
     │
     ▼
Bot no Fly.io        ← sempre online, gratuito
     │  coleta origem, destino, datas, horário via URA (5 passos)
     │
     ▼
GitHub Actions       ← busca os voos na nuvem
     │  usa Google Flights via HTTP (sem browser)
     │
     ▼
Telegram             ← resultados chegam em ~2 minutos
```

### Execuções automáticas
Todo dia às **08:00** e **20:00 BRT**, o GitHub Actions busca automaticamente as rotas configuradas em `config.yaml` e envia o resumo no Telegram.

---

## Comandos do Telegram

| Comando | O que faz |
|---|---|
| `/run` | Inicia busca interativa (URA 5 passos) |
| `/ajuda` | Guia completo de uso |
| `/status` | Estado do bot e uptime |
| `/last` | Resultado da última execução agendada |
| `/cancel` | Cancela uma busca em andamento |
| `/help` | Lista resumida de comandos |

### Fluxo do `/run` (URA)

```
Passo 1 — Aeroporto de ORIGEM
  [CGH] [GRU] [BSB] [SDU] [GIG] ... [✏️ Outro]

Passo 2 — Aeroporto de DESTINO
  (mesmos botões, exceto origem)

Passo 3 — Período de busca
  [1 mês] [2 meses] [3 meses] [6 meses] [12 meses]

Passo 4 — Dia da semana
  [1·Seg] [2·Ter] [3·Qua] [4·Qui]
  [5·Sex] [6·Sáb] [7·Dom] [0·Qualquer]

Passo 5 — Horário
  [🌙 Madrugada 00-06] [🌅 Manhã 06-12]
  [🌆 Tarde 12-19]     [🌃 Noite 19-24]
  [⏰ Qualquer]

→ Busca disparada no GitHub Actions
→ Resultado no Telegram em ~2 minutos
```

---

## Rotas monitoradas automaticamente

Configuradas em `config.yaml`:

| Rota | Dias | Horário | Tipo |
|---|---|---|---|
| CGH → BSB | Quarta e Sexta | após 20h | Tarifa FULL |
| BSB → CGH | Domingo | qualquer | Último ou mais barato |
| CGH → BSB | Domingo | qualquer | Último ou mais barato |

Para alterar, edite `config.yaml` (seção `monitoring`).

---

## Infraestrutura

| Componente | Onde roda | Custo |
|---|---|---|
| Bot Telegram | Fly.io (São Paulo) | Gratuito |
| Busca de voos | GitHub Actions | Gratuito |
| Dados de voos | Google Flights (via HTTP) | Gratuito |

---

## Setup inicial (feito uma vez)

### Pré-requisitos

- Conta no GitHub: `github.com`
- Conta no Fly.io: `fly.io` (sem cartão de crédito)
- Mac ou Linux com `brew` instalado
- Bot do Telegram criado via `@BotFather`

### 1. Clone o repositório

```bash
git clone https://github.com/abalroar/biuro.git
cd biuro
```

### 2. Crie o bot no Telegram

1. Abra o Telegram → procure `@BotFather`
2. Envie `/newbot` → siga as instruções
3. Guarde o **token** (ex: `123456:ABC...`)
4. Envie uma mensagem para o bot, depois acesse:
   `https://api.telegram.org/botSEU_TOKEN/getUpdates`
5. Copie o número em `"chat":{"id": ...}` — é seu `CHAT_ID`

### 3. Crie um token do GitHub

1. Acesse `github.com/settings/tokens/new`
2. Note: `LATAM Monitor Bot` | Scope: apenas `workflow`
3. Clique **Generate token** e copie (começa com `ghp_...`)

### 4. Deploy no Fly.io

```bash
# Instala flyctl
brew install flyctl

# Login (abre browser)
flyctl auth signup

# Deploy
flyctl launch --no-deploy --name latam-monitor-bot --region gru
flyctl secrets set \
  TELEGRAM_BOT_TOKEN=SEU_TOKEN \
  TELEGRAM_CHAT_ID=SEU_CHAT_ID \
  ALLOWED_CHAT_IDS=SEU_CHAT_ID \
  GITHUB_TOKEN=SEU_GITHUB_TOKEN \
  GITHUB_REPO=abalroar/biuro \
  GITHUB_BRANCH=claude/flight-price-monitor-B7iNh
flyctl deploy
```

### 5. Verifique

```bash
flyctl status       # deve mostrar "running"
flyctl logs         # logs em tempo real
```

Abra o Telegram e envie `/help` para o bot.

---

## Estrutura do projeto

```
biuro/
├── bot.py                  # Bot Telegram + dispatcher GitHub Actions
├── main.py                 # Monitor principal (roda no GitHub Actions)
├── run_search.py           # Busca customizada (roda no GitHub Actions)
├── config.yaml             # Rotas, horários, alertas
├── Dockerfile              # Imagem do bot para Fly.io
├── fly.toml                # Config do Fly.io
├── requirements-bot.txt    # Deps do bot (leve, sem Playwright)
├── requirements.txt        # Deps completas (para GitHub Actions)
├── src/
│   ├── google_flights_client.py  # Busca no Google Flights
│   ├── flight_search.py          # Roteador de fontes de dados
│   ├── telegram_notifier.py      # Envio de mensagens
│   ├── price_analyzer.py         # Comparação com histórico
│   └── database.py               # SQLite com histórico de preços
└── .github/workflows/
    └── monitor.yml         # GitHub Actions (busca agendada + manual)
```

---

## Ajustes comuns

### Mudar rotas monitoradas

Edite `config.yaml`, seção `monitoring.weekday_routes` e `monitoring.sunday_routes`.

### Mudar horários automáticos

Edite `config.yaml`, seção `schedule.run_times`.  
E também `.github/workflows/monitor.yml`, seção `cron`.

### Adicionar aeroportos no /run

Edite `bot.py`, lista `AIRPORTS` e dicionário `AIRPORT_NAMES`.

### Ver logs do bot

```bash
flyctl logs
```
