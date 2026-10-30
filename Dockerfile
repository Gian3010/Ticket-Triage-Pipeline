FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY sql ./sql

# Usuário sem privilégios
RUN useradd -m app && mkdir -p /app/data/inbox /app/data/processed && chown -R app /app
USER app

CMD ["python", "-m", "triage", "run"]
