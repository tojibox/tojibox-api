"""
Togibox Oracle API
-------------------
Lightweight FastAPI server exposing oracle data for:
  - the pipeline / committer (togibox-scraper) → GET /api/oracle/pending-events
  - Frontend / external consumers               → parcel history, petition details

Ported from ZoneProof's oracle/api/main.py — same shape, rebranded.

Run:
  python -m uvicorn main:app --reload --port 8001
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

from routes import events, parcels, petitions, health
from middleware.x402 import X402Middleware

app = FastAPI(
    title="Togibox Oracle API",
    description="Oracle data layer for Togibox (GIWA L2) — change events, parcel history, petition registry",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS must be added before x402 so preflight OPTIONS requests pass through
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*", "X-Payment"],
    expose_headers=["X-402-Version"],
)

# x402 payment gate — sits inside CORS, only runs on matched protected routes
app.add_middleware(X402Middleware)

app.include_router(health.router,    prefix="/api/oracle")
app.include_router(events.router,    prefix="/api/oracle")
app.include_router(parcels.router,   prefix="/api/oracle")
app.include_router(petitions.router, prefix="/api/oracle")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("API_PORT", "8001")), reload=True)
