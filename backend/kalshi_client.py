"""Kalshi exchange client for trading and cross-platform arbitrage.

Kalshi API v2 docs: https://docs.kalshi.com/
Base URL: https://api.elections.kalshi.com/trade-api/v2  (production)

Supports both read-only market data and authenticated trading.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

logger = logging.getLogger("kalshi")

KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"


@dataclass
class KalshiMarket:
    """Slim representation of a Kalshi market for comparison."""
    ticker: str
    title: str
    subtitle: str
    yes_price: float  # 0-1.00 (dollars)
    no_price: float
    volume: int
    open_interest: int
    status: str
    category: str
    event_ticker: str


@dataclass
class KalshiPosition:
    """Kalshi position in a market."""
    ticker: str
    market_exposure: float
    realized_pnl: float
    resting_order_count: int
    total_traded: float
    side: str  # "yes" or "no"
    quantity: int


@dataclass
class KalshiOrder:
    """Kalshi order."""
    order_id: str
    ticker: str
    side: str
    type: str
    status: str
    price: float
    size: int
    filled: int
    remaining: int
    created_time: str


class KalshiClient:
    """Kalshi client with optional authentication for trading.
    
    For read-only market data, no auth needed.
    For trading, requires KALSHI_API_KEY and KALSHI_PRIVATE_KEY_FILE env vars.
    """

    def __init__(self) -> None:
        self._api_key = os.getenv("KALSHI_API_KEY", "")
        self._private_key = self._load_private_key()
        self._member_id: str | None = None
        
        self._http = httpx.Client(
            base_url=KALSHI_API_BASE,
            timeout=15,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
    
    def _load_private_key(self) -> rsa.RSAPrivateKey | None:
        """Load RSA private key for API authentication."""
        key_file = os.getenv("KALSHI_PRIVATE_KEY_FILE", "")
        if not key_file or not os.path.exists(key_file):
            # Try inline key from env
            key_data = os.getenv("KALSHI_PRIVATE_KEY", "")
            if not key_data:
                return None
            key_bytes = key_data.encode()
        else:
            with open(key_file, "rb") as f:
                key_bytes = f.read()
        
        try:
            return serialization.load_pem_private_key(key_bytes, password=None)
        except Exception as e:
            logger.warning("Failed to load Kalshi private key: %s", e)
            return None
    
    @property
    def is_authenticated(self) -> bool:
        """Check if client has valid credentials for trading."""
        return bool(self._api_key and self._private_key)
    
    def _sign_request(self, method: str, path: str, body: str = "") -> dict[str, str]:
        """Generate authentication headers with RSA-PSS signature."""
        if not self._private_key or not self._api_key:
            return {}
        
        timestamp = str(int(time.time() * 1000))
        # Message to sign: timestamp + method + path + body
        message = f"{timestamp}{method.upper()}{path}{body}"
        
        signature = self._private_key.sign(
            message.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        
        return {
            "KALSHI-ACCESS-KEY": self._api_key,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }
    
    def _auth_get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        """Authenticated GET request."""
        headers = self._sign_request("GET", path)
        resp = self._http.get(path, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()
    
    def _auth_post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        """Authenticated POST request."""
        import json
        body = json.dumps(data)
        headers = self._sign_request("POST", path, body)
        resp = self._http.post(path, content=body, headers=headers)
        resp.raise_for_status()
        return resp.json()
    
    def _auth_delete(self, path: str) -> dict[str, Any]:
        """Authenticated DELETE request."""
        headers = self._sign_request("DELETE", path)
        resp = self._http.delete(path, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # ─────────────────────────────────────────────────────────────────────
    # Public endpoints (no auth required)
    # ─────────────────────────────────────────────────────────────────────

    def get_events(self, limit: int = 50, status: str = "open") -> list[dict[str, Any]]:
        """Fetch active events."""
        resp = self._http.get(
            "/events",
            params={"limit": limit, "status": status},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("events", [])

    def get_markets(
        self,
        limit: int = 100,
        status: str = "open",
        event_ticker: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch active markets."""
        params: dict[str, Any] = {"limit": limit, "status": status}
        if event_ticker:
            params["event_ticker"] = event_ticker
        resp = self._http.get("/markets", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("markets", [])

    def get_market(self, ticker: str) -> dict[str, Any]:
        """Fetch a single market by ticker."""
        resp = self._http.get(f"/markets/{ticker}")
        resp.raise_for_status()
        return resp.json().get("market", {})

    def get_orderbook(self, ticker: str) -> dict[str, Any]:
        """Fetch order book for a market."""
        resp = self._http.get(f"/markets/{ticker}/orderbook")
        resp.raise_for_status()
        return resp.json().get("orderbook", {})

    def parse_market(self, raw: dict[str, Any]) -> KalshiMarket | None:
        """Parse a raw Kalshi market dict into our model."""
        try:
            # Kalshi API v2 uses dollar prices (0-1.00) in "*_dollars" fields
            yes_price = float(raw.get("yes_ask_dollars", raw.get("last_price_dollars", 0)))
            no_price = float(raw.get("no_ask_dollars", 0))

            # Fallback: if no_ask missing, derive from yes
            if no_price == 0 and yes_price > 0:
                no_price = 1.0 - yes_price

            return KalshiMarket(
                ticker=raw.get("ticker", ""),
                title=raw.get("title", ""),
                subtitle=raw.get("yes_sub_title", ""),
                yes_price=yes_price,
                no_price=no_price,
                volume=int(float(raw.get("volume_fp", 0))),
                open_interest=int(float(raw.get("open_interest_fp", 0))),
                status=raw.get("status", ""),
                category=raw.get("market_type", ""),
                event_ticker=raw.get("event_ticker", ""),
            )
        except Exception as exc:
            logger.debug("Failed to parse Kalshi market: %s", exc)
            return None

    def get_active_markets(self, limit: int = 100) -> list[KalshiMarket]:
        """Get parsed active markets."""
        raw = self.get_markets(limit=limit, status="open")
        markets = []
        for r in raw:
            m = self.parse_market(r)
            # Include markets with any price data (yes or no)
            if m and (m.yes_price > 0 or m.no_price > 0):
                markets.append(m)
        return markets

    # ─────────────────────────────────────────────────────────────────────
    # Authenticated endpoints (trading)
    # ─────────────────────────────────────────────────────────────────────
    
    def get_balance(self) -> dict[str, float]:
        """Get account balance. Returns {'balance': float, 'available': float}."""
        if not self.is_authenticated:
            raise RuntimeError("Kalshi trading requires authentication. Set KALSHI_API_KEY and KALSHI_PRIVATE_KEY_FILE.")
        
        data = self._auth_get("/portfolio/balance")
        return {
            "balance": float(data.get("balance", 0)) / 100,  # Convert cents to dollars
            "available": float(data.get("available_balance", data.get("balance", 0))) / 100,
        }
    
    def get_positions(self) -> list[KalshiPosition]:
        """Get all open positions."""
        if not self.is_authenticated:
            return []
        
        data = self._auth_get("/portfolio/positions")
        positions = []
        for p in data.get("market_positions", []):
            positions.append(KalshiPosition(
                ticker=p.get("ticker", ""),
                market_exposure=float(p.get("market_exposure", 0)),
                realized_pnl=float(p.get("realized_pnl", 0)),
                resting_order_count=int(p.get("resting_orders_count", 0)),
                total_traded=float(p.get("total_traded", 0)),
                side="yes" if p.get("position", 0) > 0 else "no",
                quantity=abs(int(p.get("position", 0))),
            ))
        return positions
    
    def get_orders(self, status: str = "resting") -> list[KalshiOrder]:
        """Get orders. Status can be 'resting', 'pending', 'executed', 'canceled'."""
        if not self.is_authenticated:
            return []
        
        data = self._auth_get("/portfolio/orders", params={"status": status})
        orders = []
        for o in data.get("orders", []):
            orders.append(KalshiOrder(
                order_id=o.get("order_id", ""),
                ticker=o.get("ticker", ""),
                side=o.get("side", ""),
                type=o.get("type", ""),
                status=o.get("status", ""),
                price=float(o.get("yes_price", o.get("no_price", 0))) / 100,
                size=int(o.get("count", 0)),
                filled=int(o.get("filled_count", 0)),
                remaining=int(o.get("remaining_count", 0)),
                created_time=o.get("created_time", ""),
            ))
        return orders
    
    def create_order(
        self,
        ticker: str,
        side: Literal["yes", "no"],
        action: Literal["buy", "sell"],
        size: int,
        price: float,
        order_type: Literal["limit", "market"] = "limit",
    ) -> KalshiOrder:
        """Create a new order.
        
        Args:
            ticker: Market ticker
            side: "yes" or "no"
            action: "buy" or "sell"
            size: Number of contracts
            price: Price in dollars (0.01 to 0.99)
            order_type: "limit" or "market"
        
        Returns:
            Created order
        """
        if not self.is_authenticated:
            raise RuntimeError("Kalshi trading requires authentication.")
        
        # Kalshi prices are in cents (1-99)
        price_cents = int(price * 100)
        
        order_data = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "count": size,
            "type": order_type,
        }
        
        if order_type == "limit":
            if side == "yes":
                order_data["yes_price"] = price_cents
            else:
                order_data["no_price"] = price_cents
        
        logger.info("Kalshi order: %s %s %s x%d @ $%.2f", action, side, ticker, size, price)
        
        data = self._auth_post("/portfolio/orders", order_data)
        o = data.get("order", {})
        
        return KalshiOrder(
            order_id=o.get("order_id", ""),
            ticker=o.get("ticker", ticker),
            side=o.get("side", side),
            type=o.get("type", order_type),
            status=o.get("status", "pending"),
            price=price,
            size=size,
            filled=int(o.get("filled_count", 0)),
            remaining=int(o.get("remaining_count", size)),
            created_time=o.get("created_time", ""),
        )
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID."""
        if not self.is_authenticated:
            return False
        
        try:
            self._auth_delete(f"/portfolio/orders/{order_id}")
            logger.info("Kalshi order canceled: %s", order_id)
            return True
        except Exception as e:
            logger.error("Failed to cancel Kalshi order %s: %s", order_id, e)
            return False
    
    def cancel_all_orders(self, ticker: str | None = None) -> int:
        """Cancel all resting orders, optionally filtered by ticker."""
        if not self.is_authenticated:
            return 0
        
        orders = self.get_orders(status="resting")
        canceled = 0
        for order in orders:
            if ticker and order.ticker != ticker:
                continue
            if self.cancel_order(order.order_id):
                canceled += 1
        return canceled
    
    def buy_yes(self, ticker: str, size: int, price: float) -> KalshiOrder:
        """Buy YES contracts at given price."""
        return self.create_order(ticker, "yes", "buy", size, price)
    
    def buy_no(self, ticker: str, size: int, price: float) -> KalshiOrder:
        """Buy NO contracts at given price."""
        return self.create_order(ticker, "no", "buy", size, price)
    
    def sell_yes(self, ticker: str, size: int, price: float) -> KalshiOrder:
        """Sell YES contracts at given price."""
        return self.create_order(ticker, "yes", "sell", size, price)
    
    def sell_no(self, ticker: str, size: int, price: float) -> KalshiOrder:
        """Sell NO contracts at given price."""
        return self.create_order(ticker, "no", "sell", size, price)
