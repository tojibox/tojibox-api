FROM python:3.13-slim

# libpq5: psycopg2-binary's wheel dynamically links against it at runtime
# (python:3.13-slim doesn't include it, causing "ImportError: libpq.so.5").
# build-essential + libpq-dev: in case pip falls back to compiling any
# dependency from source instead of using a prebuilt wheel for this
# platform/Python version, which the base image otherwise has no
# toolchain for at all.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 libpq-dev build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
