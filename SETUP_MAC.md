# Setup no Mac — do zero

## O que você vai ter ao final

- Bot Telegram respondendo a `/run`, `/status`, `/last`, `/help`
- Execuções automáticas às 08:00 e 20:00 (horário de Brasília)
- Serviço que reinicia sozinho se cair
- Logs em `logs/bot.log`

---

## Pré-requisitos

- macOS com Python 3.11+ instalado
  ```bash
  python3 --version   # deve mostrar 3.11 ou maior
  ```
- Git instalado (`xcode-select --install` se necessário)
- Mac conectado à internet (IP residencial = sem bloqueio da LATAM)

---

## Passo 1 — Clonar o repositório

```bash
cd ~
git clone https://github.com/abalroar/biuro.git
cd biuro
```

---

## Passo 2 — Criar o ambiente virtual

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
```

---

## Passo 3 — Configurar o .env

```bash
cp .env.example .env
nano .env      # ou abra com qualquer editor de texto
```

Preencha:
```
TELEGRAM_BOT_TOKEN=8798069891:AAFkFRVb...   (seu token do BotFather)
TELEGRAM_CHAT_ID=1624082321                 (seu chat_id)
ALLOWED_CHAT_IDS=1624082321                 (mesmo valor, ou múltiplos separados por vírgula)
```

---

## Passo 4 — Testar manualmente

```bash
source .venv/bin/activate
python bot.py
```

Vá no Telegram e mande `/help` para o bot. Deve responder.
Mande `/run` para testar uma busca real (demora ~15 min).

`Ctrl+C` para parar após o teste.

---

## Passo 5 — Instalar como serviço (launchd)

### 5.1 Descobrir o caminho exato do projeto

```bash
pwd   # dentro da pasta biuro — copie o resultado
# Exemplo: /Users/matheusjprates/biuro
```

### 5.2 Substituir o caminho no plist

```bash
PROJETO=/Users/matheusjprates/biuro   # coloque o seu caminho aqui

sed -i '' "s|SUBSTITUIR_PELO_CAMINHO_DO_PROJETO|$PROJETO|g" \
  launchd/com.user.latam-monitor.plist
```

### 5.3 Instalar e iniciar

```bash
cp launchd/com.user.latam-monitor.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.latam-monitor.plist
```

### 5.4 Verificar se está rodando

```bash
launchctl list | grep latam
# Deve aparecer com PID (número > 0 na primeira coluna)

tail -f logs/bot.log
# Deve mostrar "Bot ativo. Aguardando comandos..."
```

---

## Passo 6 — Confirmar no Telegram

Mande `/status` para o bot. Deve responder com uptime e horários agendados.

---

## Operação diária

| O que fazer | Como |
|---|---|
| Rodar busca agora | `/run` no Telegram |
| Ver estado | `/status` no Telegram |
| Ver última execução | `/last` no Telegram |
| Ver logs | `tail -f ~/biuro/logs/bot.log` |
| Reiniciar bot | `launchctl unload ~/Library/LaunchAgents/com.user.latam-monitor.plist && launchctl load ~/Library/LaunchAgents/com.user.latam-monitor.plist` |
| Parar bot | `launchctl unload ~/Library/LaunchAgents/com.user.latam-monitor.plist` |
| Atualizar código | `cd ~/biuro && git pull && launchctl unload ... && launchctl load ...` |

---

## Troubleshooting

**Bot não responde no Telegram:**
```bash
launchctl list | grep latam   # PID deve ser > 0
tail -50 logs/bot.log          # ver o que aconteceu
```

**"TELEGRAM_BOT_TOKEN não configurado":**
```bash
cat .env   # verificar se o arquivo existe e está correto
```

**"sem resultados" nas buscas:**
- Normal nas primeiras execuções (site pode demorar para carregar)
- Verifique os logs com `tail -100 logs/bot.log`
- Tente rodar `/run` manualmente para ver se funciona

**Mac foi reiniciado e bot não voltou:**
- O launchd reinicia automaticamente com o login
- Faça login no Mac (não basta ligar — precisa estar logado)

**Alterar horários das buscas:**
- Edite `config.yaml` → `schedule.run_times`
- Reinicie o bot (comandos de reinício acima)

---

## Ajustando o que monitorar

Edite `config.yaml`:

```yaml
date_range:
  start_months_ahead: 3   # pular os próximos N meses (você já tem passagens)
  end_months_ahead: 5     # buscar até M meses à frente

schedule:
  run_times: ["08:00", "20:00"]   # horários de execução automática
  timezone: "America/Sao_Paulo"
```

---

## Segurança

- O `.env` nunca vai para o git (está no `.gitignore`)
- Só o seu `chat_id` está em `ALLOWED_CHAT_IDS` — ninguém mais consegue controlar o bot
- O bot usa polling (sem porta aberta no Mac) — sem exposição de rede
