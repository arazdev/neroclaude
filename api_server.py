"""Lightweight API server exposing position data for the Vercel dashboard."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import Config
from position_tracker import PositionTracker

cfg = Config()
tracker = PositionTracker()

app = FastAPI(title="BOTCLAUDE API", version="1.0.0")

# Allow the Vercel frontend to call this API
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Simple bearer token auth — set API_SECRET in .env
API_SECRET = os.getenv("API_SECRET", "")


@app.middleware("http")
async def check_auth(request: Request, call_next):
    if API_SECRET:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if token != API_SECRET:
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return await call_next(request)


@app.get("/")
def health():
    return {"status": "ok", "bot": "BOTCLAUDE", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/positions")
def get_positions():
    """All positions (open + closed)."""
    tracker._positions = tracker._load()  # refresh from disk
    return {
        "open": [p.model_dump() for p in tracker.open_positions],
        "closed": [p.model_dump() for p in tracker.closed_positions],
    }


@app.get("/api/summary")
def get_summary():
    """Dashboard summary stats."""
    tracker._positions = tracker._load()
    open_pos = tracker.open_positions
    closed_pos = tracker.closed_positions
    return {
        "open_count": len(open_pos),
        "closed_count": len(closed_pos),
        "total_exposure": tracker.total_exposure(),
        "realized_pnl": tracker.total_realized_pnl(),
        "max_position_usdc": cfg.max_position_usdc,
        "max_order_usdc": cfg.max_order_usdc,
        "dry_run": cfg.dry_run,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/positions/open")
def get_open_positions():
    tracker._positions = tracker._load()
    return [p.model_dump() for p in tracker.open_positions]


@app.get("/api/positions/closed")
def get_closed_positions():
    tracker._positions = tracker._load()
    return [p.model_dump() for p in tracker.closed_positions]
