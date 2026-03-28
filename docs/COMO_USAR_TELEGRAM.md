# Como usar o bot no Telegram (URA)

Este guia explica, em linguagem direta, como usar o monitor LATAM sem precisar abrir código.

## 1) Comandos principais

- `/start` → abre o menu inicial e envia este guia.
- `/run` → inicia a busca interativa (URA em 5 passos).
- `/status` → mostra se o bot está online e o uptime.
- `/last` → mostra o resultado da última execução agendada.
- `/cancel` → cancela a busca interativa em andamento.
- `/help` → resumo rápido dos comandos.
- `/ajuda` → envia novamente este guia.

## 2) Passo a passo do `/run`

Quando você envia `/run`, o bot faz perguntas em sequência:

1. **Origem** (aeroporto de saída)
2. **Destino** (aeroporto de chegada)
3. **Meses à frente** para pesquisar
4. **Dia da semana** (ou qualquer dia)
5. **Período do dia** (madrugada/manhã/tarde/noite/qualquer)

Ao final, o bot monta o resumo da busca e executa:

- na **nuvem** (GitHub Actions), quando `GITHUB_TOKEN` está configurado; ou
- localmente como **fallback**, se o token não estiver disponível.

## 3) Dicas de uso

- Se quiser pesquisar todas as datas possíveis, selecione **0 · Qualquer dia**.
- Se quiser apenas voos noturnos, selecione **Noite (19h–23h59)**.
- Se errar durante o fluxo, envie `/cancel` e comece de novo.

## 4) Segurança e acesso

Somente chats autorizados em `ALLOWED_CHAT_IDS` conseguem usar os comandos.
Se um chat não autorizado enviar comando, o bot ignora a ação.

## 5) Execução automática

Além do uso manual no Telegram, o monitor também roda automaticamente nos horários definidos em `config.yaml` (`schedule.run_times`).

