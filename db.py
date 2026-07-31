"""
Database connection for the Tojibox Oracle API — Postgres via psycopg2.

Ported from ZoneProof's oracle/api/db.py. In the source repo this pulled
DB_HOST/PORT/USER/PASSWORD/NAME from oracle/scrapers/config.py (shared with
the scraper). tojibox-api is a standalone repo without that module, so the
env vars are read directly here instead — same names, same defaults-less
behavior (no hardcoded secrets; set them in .env).
"""
import os

import psycopg2
import psycopg2.extras

DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "5432"))
DB_USER     = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME     = os.getenv("DB_NAME", "postgres")


def get_conn():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD,
        dbname=DB_NAME, sslmode="require",
        connect_timeout=10,
    )
    conn.autocommit = True
    return conn


def query(sql: str, params=None) -> list:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
