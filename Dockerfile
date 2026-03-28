FROM python:3.11-slim

WORKDIR /app

# Só as dependências do bot (sem Playwright, sem Amadeus)
COPY requirements-bot.txt .
RUN pip install --no-cache-dir -r requirements-bot.txt

# Código
COPY bot.py config.yaml ./
COPY docs/ docs/
COPY src/telegram_notifier.py src/

RUN mkdir -p logs data

CMD ["python", "bot.py"]
