FROM python:3.13-slim

# psycopg2-binary's wheel dynamically links against libpq at runtime.
# python:3.13-slim doesn't include it, hence "ImportError: libpq.so.5:
# cannot open shared object file" without this.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
