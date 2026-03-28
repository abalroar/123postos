# 123postos — Guia rápido (didático)

Este projeto monitora preços de voos LATAM e envia alertas no Telegram.

## 1) Pré-requisitos

- Python 3.10+ instalado
- Conta no Telegram (app no celular)
- Terminal (Mac/Linux) ou PowerShell (Windows)

## 2) Criar e ativar o ambiente virtual (venv)

> O ambiente virtual isola as bibliotecas do projeto.

### Mac/Linux

```bash
cd /Users/matheusjprates/123postos
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Windows (PowerShell)

```powershell
cd C:\caminho\para\123postos
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Quando ativado, normalmente aparece `(.venv)` no começo da linha do terminal.

## 3) Criar o bot no Telegram (BotFather)

1. Abra o Telegram e procure por **@BotFather**.
2. Envie `/start`.
3. Envie `/newbot`.
4. Escolha um nome para o bot (ex: `Monitor Voos LATAM`).
5. Escolha um username terminando com `bot` (ex: `monitor_voos_latam_bot`).
6. O BotFather vai retornar o **token** (algo como `123456:ABC...`). Guarde esse valor.

## 4) Descobrir seu `TELEGRAM_CHAT_ID`

1. No Telegram, abra conversa com seu bot recém-criado.
2. Clique em **Start** (ou envie qualquer mensagem, ex: `oi`).
3. No navegador, acesse:

```text
https://api.telegram.org/botSEU_TOKEN/getUpdates
```

4. Procure no JSON por `"chat":{"id": ...}`.
5. O número em `id` é seu `TELEGRAM_CHAT_ID`.

## 5) Configurar variáveis de ambiente (`.env`)

Crie um arquivo `.env` na raiz do projeto copiando o exemplo:

```bash
cp .env.example .env
```

Edite o `.env` e preencha:

```env
TELEGRAM_BOT_TOKEN=seu_token_aqui
TELEGRAM_CHAT_ID=seu_chat_id_aqui
AMADEUS_CLIENT_ID=
AMADEUS_CLIENT_SECRET=
```

> `AMADEUS_*` é opcional para fallback.

## 6) Testar conexão com Telegram

Com o ambiente virtual ativo:

```bash
python main.py --test
```

Se estiver tudo certo, você recebe mensagem de teste no Telegram.

## 7) Executar o monitor

### Rodar uma vez (imediato)

```bash
python main.py
```

### Rodar agendado

```bash
python main.py --schedule
```

## 8) Problemas comuns

- **`ModuleNotFoundError`**: venv não está ativo ou dependências não foram instaladas.
- **Sem mensagem no Telegram**: token/chat_id incorretos, ou você não enviou `/start` para o bot.
- **`getUpdates` vazio**: envie uma nova mensagem para o bot e consulte de novo.

## 9) Checklist rápido

- [ ] venv criado e ativado
- [ ] `pip install -r requirements.txt` executado
- [ ] bot criado no BotFather
- [ ] `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` no `.env`
- [ ] `python main.py --test` enviado com sucesso

