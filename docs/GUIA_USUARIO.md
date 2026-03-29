# Como usar o LATAM Monitor Bot 🛫

Bem-vindo! Este bot monitora passagens da LATAM e avisa quando encontrar voos.

---

## Comandos disponíveis

| Comando | O que faz |
|---------|-----------|
| `/start` | Abre o menu principal |
| `/run` | Inicia uma busca de passagens (5 passos) |
| `/status` | Mostra se o bot está ativo e o uptime |
| `/last` | Resultado da última busca automática |
| `/cancel` | Cancela uma busca em andamento |
| `/help` | Lista rápida de comandos |

---

## Como fazer uma busca com `/run`

O bot vai guiar você em 5 passos:

**Passo 1 — Origem**
Toque no aeroporto de onde você vai sair.
Não está na lista? Toque em **✏️ Outro** e digite o código IATA (3 letras).
> Exemplos: `CGH` = Congonhas, `GRU` = Guarulhos, `BSB` = Brasília

**Passo 2 — Destino**
Mesmo processo: toque no destino ou use **✏️ Outro**.

**Passo 3 — Período**
Quantos meses à frente quer buscar?
> Dica: 2 ou 3 meses é um bom equilíbrio entre opções e velocidade.

**Passo 4 — Dia da semana**
Quer viajar em um dia específico? Escolha qual.
Ou toque **Qualquer** para buscar todos os dias.

**Passo 5 — Horário**
Filtre pelo período do dia que prefere voar, ou escolha **Qualquer**.

Pronto! O bot dispara a busca na nuvem e envia os resultados em ~2 minutos.

---

## Buscas automáticas

O bot roda automaticamente **todos os dias às 08h e às 20h** (horário de Brasília)
e envia os resultados diretamente aqui no Telegram.

---

## Dicas

- Se o bot não responder, mande `/start` para reativar
- Use `/cancel` se quiser recomeçar uma busca no meio
- Os preços são aproximados — confirme sempre no site da LATAM antes de comprar
- Voos com escala já são filtrados — o bot mostra apenas **voos diretos**
