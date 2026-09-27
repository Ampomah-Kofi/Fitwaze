FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Apply database migrations, then serve. Hosting platforms pass the port in
# $PORT. The caller's real address for rate limiting is read from the
# platform proxy's X-Forwarded-For entry (TRUSTED_PROXY_HOPS), not from
# uvicorn's --forwarded-allow-ips='*', which trusts caller-written entries.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
