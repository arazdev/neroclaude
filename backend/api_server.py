"""Lightweight API server exposing position data for the Vercel dashboard."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import Config
from position_tracker import PositionTracker

cfg = Config()
tracker = PositionTracker()

app = FastAPI(title="NEROCLAUDE API", version="1.0.0")

# Allow the Vercel frontend to call this API
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    allow_credentials=True,
)

# Settings model
class SettingsUpdate(BaseModel):
    dry_run: bool | None = None
    bot_mode: str | None = None
    max_order_usdc: float | None = None
    max_position_usdc: float | None = None
    poll_interval: int | None = None

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
    return {"status": "ok", "bot": "NEROCLAUDE", "time": datetime.now(timezone.utc).isoformat()}


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

    # Categorize by strategy
    arb_trades = [p for p in open_pos if p.action in ("ARB_YES", "ARB_NO")]
    cross_trades = [p for p in open_pos if p.action == "CROSS_ARB"]
    mm_trades = [p for p in open_pos if p.action in ("MM_BID_YES", "MM_BID_NO")]
    claude_trades = [p for p in open_pos if p.action not in ("ARB_YES", "ARB_NO", "CROSS_ARB", "MM_BID_YES", "MM_BID_NO")]

    return {
        "open_count": len(open_pos),
        "closed_count": len(closed_pos),
        "total_exposure": tracker.total_exposure(),
        "realized_pnl": tracker.total_realized_pnl(),
        "max_position_usdc": cfg.max_position_usdc,
        "max_order_usdc": cfg.max_order_usdc,
        "dry_run": cfg.dry_run,
        "bot_mode": cfg.bot_mode,
        "strategies": {
            "claude": {"open": len(claude_trades), "exposure": sum(p.size_usdc for p in claude_trades)},
            "arb": {"open": len(arb_trades), "exposure": sum(p.size_usdc for p in arb_trades)},
            "cross": {"open": len(cross_trades), "exposure": sum(p.size_usdc for p in cross_trades)},
            "mm": {"open": len(mm_trades), "exposure": sum(p.size_usdc for p in mm_trades)},
        },
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


# ─────────────────────────────────────────────────────────────────────────────
# Settings API
# ─────────────────────────────────────────────────────────────────────────────

ENV_FILE = Path(__file__).parent / ".env"


def read_env() -> dict[str, str]:
    """Read .env file into dict."""
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def write_env(updates: dict[str, str]) -> None:
    """Update specific keys in .env file while preserving comments."""
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    new_lines = []
    updated_keys = set()

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                updated_keys.add(key)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    # Add any new keys not already in file
    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(new_lines) + "\n")


@app.get("/api/settings")
def get_settings():
    """Get current bot settings."""
    env = read_env()
    return {
        "dry_run": env.get("DRY_RUN", "true").lower() == "true",
        "bot_mode": env.get("BOT_MODE", "claude"),
        "max_order_usdc": float(env.get("MAX_ORDER_USDC", "25.0")),
        "max_position_usdc": float(env.get("MAX_POSITION_USDC", "100.0")),
        "poll_interval": int(env.get("POLL_INTERVAL_SECONDS", "300")),
    }


@app.post("/api/settings")
def update_settings(settings: SettingsUpdate):
    """Update bot settings in .env file."""
    updates = {}
    if settings.dry_run is not None:
        updates["DRY_RUN"] = "true" if settings.dry_run else "false"
    if settings.bot_mode is not None:
        if settings.bot_mode not in ("claude", "arb", "cross", "mm", "all"):
            return JSONResponse(status_code=400, content={"error": "Invalid bot_mode"})
        updates["BOT_MODE"] = settings.bot_mode
    if settings.max_order_usdc is not None:
        updates["MAX_ORDER_USDC"] = str(settings.max_order_usdc)
    if settings.max_position_usdc is not None:
        updates["MAX_POSITION_USDC"] = str(settings.max_position_usdc)
    if settings.poll_interval is not None:
        updates["POLL_INTERVAL_SECONDS"] = str(settings.poll_interval)

    if updates:
        write_env(updates)

    return {"status": "ok", "updated": list(updates.keys())}


@app.post("/api/restart")
def restart_bot():
    """Restart the bot service (requires systemd)."""
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", "neroclaude"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        return {"status": "ok", "message": "Bot restarting..."}
    except subprocess.CalledProcessError as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to restart: {e.stderr.decode()[:200]}"},
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
