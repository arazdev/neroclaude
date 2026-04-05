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
from polymarket_client import PolymarketClient

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
    """Update bot settings in .env file and auto-restart services."""
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
        # Auto-restart services to apply new settings
        try:
            subprocess.run(
                ["sudo", "systemctl", "restart", "botclaude"],
                check=True,
                capture_output=True,
                timeout=10,
            )
            # Restart API service (Popen so we don't block on our own restart)
            subprocess.Popen(
                ["sudo", "systemctl", "restart", "botclaude-api"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {"status": "ok", "updated": list(updates.keys()), "restarted": True}
        except Exception as e:
            # Settings saved but restart failed
            return {"status": "partial", "updated": list(updates.keys()), "restarted": False, "error": str(e)}

    return {"status": "ok", "updated": [], "restarted": False}


@app.post("/api/restart")
def restart_bot():
    """Restart both bot and API services to apply new settings."""
    try:
        # Restart bot service
        subprocess.run(
            ["sudo", "systemctl", "restart", "botclaude"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        # Restart API service (this will kill current process, systemd restarts it)
        subprocess.Popen(
            ["sudo", "systemctl", "restart", "botclaude-api"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"status": "ok", "message": "Bot and API restarting..."}
    except subprocess.CalledProcessError as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to restart: {e.stderr.decode()[:200]}"},
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ─────────────────────────────────────────────────────────────────────────────
# Wallet & Portfolio API
# ─────────────────────────────────────────────────────────────────────────────

# Lazy-load polymarket client to avoid import errors if credentials missing
_poly_client = None

def get_poly_client():
    global _poly_client
    if _poly_client is None:
        try:
            _poly_client = PolymarketClient(cfg)
        except Exception:
            return None
    return _poly_client


@app.get("/api/wallet")
def get_wallet():
    """Get wallet balance and portfolio value for both platforms."""
    tracker._positions = tracker._load()
    open_pos = tracker.open_positions
    total_exposure = tracker.total_exposure()
    realized_pnl = tracker.total_realized_pnl()
    
    # Polymarket balance
    poly_balance = 0.0
    client = get_poly_client()
    if client:
        try:
            balance_info = client._trader.get_balance_allowance()
            if balance_info and "balance" in balance_info:
                poly_balance = float(balance_info.get("balance", 0)) / 1e6
        except Exception:
            pass
    
    # Kalshi balance
    kalshi_balance = 0.0
    kalshi_available = 0.0
    kalshi_client = get_kalshi_client()
    if kalshi_client and kalshi_client.is_authenticated:
        try:
            bal = kalshi_client.get_balance()
            kalshi_balance = bal.get("balance", 0.0)
            kalshi_available = bal.get("available", 0.0)
        except Exception:
            pass
    
    total_balance = poly_balance + kalshi_balance
    
    return {
        "polymarket": {
            "balance": round(poly_balance, 2),
            "positions_value": round(total_exposure, 2),
            "total": round(poly_balance + total_exposure, 2),
        },
        "kalshi": {
            "balance": round(kalshi_balance, 2),
            "available": round(kalshi_available, 2),
        },
        "combined": {
            "total_balance": round(total_balance, 2),
            "total_portfolio": round(total_balance + total_exposure, 2),
        },
        "realized_pnl": round(realized_pnl, 2),
        "open_positions": len(open_pos),
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/orders")
def get_orders():
    """Get open orders on Polymarket."""
    client = get_poly_client()
    if not client:
        return {"orders": [], "count": 0, "error": "Client not available"}
    try:
        orders = client.get_open_orders()
        return {"orders": orders, "count": len(orders)}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Failed to fetch orders: {str(e)}"},
        )


# ─────────────────────────────────────────────────────────────────────────────
# Kalshi API
# ─────────────────────────────────────────────────────────────────────────────

_kalshi_client = None


def get_kalshi_client():
    global _kalshi_client
    if _kalshi_client is None:
        try:
            from kalshi_client import KalshiClient
            _kalshi_client = KalshiClient()
        except Exception:
            return None
    return _kalshi_client


class KalshiOrderRequest(BaseModel):
    ticker: str
    side: str  # "yes" or "no"
    action: str  # "buy" or "sell"
    size: int
    price: float


@app.get("/api/kalshi/status")
def get_kalshi_status():
    """Check Kalshi connection and authentication status."""
    client = get_kalshi_client()
    if not client:
        return {"connected": False, "authenticated": False, "error": "Client not available"}
    
    try:
        # Test public endpoint
        markets = client.get_markets(limit=1)
        is_authenticated = client.is_authenticated
        
        # Test auth if available
        balance = None
        if is_authenticated:
            try:
                balance = client.get_balance()
            except Exception as e:
                is_authenticated = False
        
        return {
            "connected": True,
            "authenticated": is_authenticated,
            "balance": balance,
            "market_count": len(markets),
        }
    except Exception as e:
        return {"connected": False, "authenticated": False, "error": str(e)}


@app.get("/api/kalshi/balance")
def get_kalshi_balance():
    """Get Kalshi account balance."""
    client = get_kalshi_client()
    if not client or not client.is_authenticated:
        return JSONResponse(
            status_code=400,
            content={"error": "Kalshi not authenticated. Check KALSHI_API_KEY and KALSHI_PRIVATE_KEY_FILE."},
        )
    try:
        balance = client.get_balance()
        return balance
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/kalshi/positions")
def get_kalshi_positions():
    """Get Kalshi positions."""
    client = get_kalshi_client()
    if not client or not client.is_authenticated:
        return {"positions": [], "error": "Not authenticated"}
    try:
        positions = client.get_positions()
        return {"positions": [p.__dict__ for p in positions], "count": len(positions)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/kalshi/orders")
def get_kalshi_orders():
    """Get Kalshi open orders."""
    client = get_kalshi_client()
    if not client or not client.is_authenticated:
        return {"orders": [], "error": "Not authenticated"}
    try:
        orders = client.get_orders(status="resting")
        return {"orders": [o.__dict__ for o in orders], "count": len(orders)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/kalshi/markets")
def get_kalshi_markets(limit: int = 20):
    """Get active Kalshi markets."""
    client = get_kalshi_client()
    if not client:
        return {"markets": [], "error": "Client not available"}
    try:
        markets = client.get_active_markets(limit=limit)
        return {"markets": [m.__dict__ for m in markets], "count": len(markets)}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/kalshi/order")
def create_kalshi_order(order: KalshiOrderRequest):
    """Create a new order on Kalshi."""
    client = get_kalshi_client()
    if not client or not client.is_authenticated:
        return JSONResponse(
            status_code=400,
            content={"error": "Kalshi not authenticated"},
        )
    
    # Validate inputs
    if order.side not in ("yes", "no"):
        return JSONResponse(status_code=400, content={"error": "side must be 'yes' or 'no'"})
    if order.action not in ("buy", "sell"):
        return JSONResponse(status_code=400, content={"error": "action must be 'buy' or 'sell'"})
    if order.size < 1:
        return JSONResponse(status_code=400, content={"error": "size must be at least 1"})
    if not (0.01 <= order.price <= 0.99):
        return JSONResponse(status_code=400, content={"error": "price must be between 0.01 and 0.99"})
    
    # Check dry run
    env = read_env()
    if env.get("DRY_RUN", "true").lower() == "true":
        return {
            "status": "dry_run",
            "order": {
                "ticker": order.ticker,
                "side": order.side,
                "action": order.action,
                "size": order.size,
                "price": order.price,
            },
            "message": "DRY_RUN enabled - order not placed",
        }
    
    try:
        result = client.create_order(
            ticker=order.ticker,
            side=order.side,
            action=order.action,
            size=order.size,
            price=order.price,
        )
        return {"status": "ok", "order": result.__dict__}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.delete("/api/kalshi/order/{order_id}")
def cancel_kalshi_order(order_id: str):
    """Cancel a Kalshi order."""
    client = get_kalshi_client()
    if not client or not client.is_authenticated:
        return JSONResponse(status_code=400, content={"error": "Kalshi not authenticated"})
    
    try:
        success = client.cancel_order(order_id)
        if success:
            return {"status": "ok", "canceled": order_id}
        return JSONResponse(status_code=400, content={"error": "Failed to cancel order"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
