"""Kalshi exchange client for trading and cross-platform arbitrage.

Uses the official kalshi-python SDK for authenticated trading.
Kalshi API v2 docs: https://docs.kalshi.com/
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Literal

import httpx

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
        self._sdk_client = None
        self._init_sdk()
        
        # Public endpoint client (no auth needed)
        self._http = httpx.Client(
            base_url=KALSHI_API_BASE,
            timeout=15,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
    
    def _init_sdk(self) -> None:
        """Initialize the official Kalshi SDK for authenticated requests."""
        key_file = os.getenv("KALSHI_PRIVATE_KEY_FILE", "")
        if not self._api_key or not key_file:
            return
        
        try:
            from kalshi_python import Configuration, KalshiClient as SDKClient
            
            with open(key_file, "r") as f:
                private_key = f.read()
            
            config = Configuration(host=KALSHI_API_BASE)
            config.api_key_id = self._api_key
            config.private_key_pem = private_key
            
            self._sdk_client = SDKClient(config)
            logger.info("Kalshi SDK initialized successfully")
        except FileNotFoundError:
            logger.warning("Kalshi private key file not found: %s", key_file)
        except ImportError:
            logger.warning("kalshi-python package not installed")
        except Exception as e:
            logger.warning("Failed to initialize Kalshi SDK: %s", e)
    
    @property
    def is_authenticated(self) -> bool:
        """Check if client has valid credentials for trading."""
        return self._sdk_client is not None

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

    def get_snapshots(self, limit: int = 10) -> list[dict]:
        """Get market snapshots in a format compatible with Claude analysis.
        
        Returns list of dicts with:
            - ticker: Market ticker (used as token_id for orders)
            - question: Market question/title
            - yes_price: Current YES price (0-1)
            - no_price: Current NO price (0-1)
            - spread: Price spread
            - volume: 24h volume
            - platform: "kalshi"
        """
        markets = self.get_active_markets(limit=limit * 2)  # Fetch more, filter to best
        
        # Sort by volume and filter for tradeable markets
        tradeable = [m for m in markets if m.yes_price > 0 and m.yes_price < 1]
        tradeable.sort(key=lambda m: m.volume, reverse=True)
        
        snapshots = []
        for m in tradeable[:limit]:
            spread = abs(1.0 - m.yes_price - m.no_price) if m.no_price > 0 else 0.02
            snapshots.append({
                "ticker": m.ticker,
                "question": f"{m.title}: {m.subtitle}" if m.subtitle else m.title,
                "token_id_yes": m.ticker,  # Kalshi uses ticker for orders
                "token_id_no": m.ticker,
                "yes_price": m.yes_price,
                "no_price": m.no_price,
                "spread": spread,
                "volume": m.volume,
                "platform": "kalshi",
            })
        
        return snapshots

    # ─────────────────────────────────────────────────────────────────────
    # Authenticated endpoints (trading) - using official SDK
    # ─────────────────────────────────────────────────────────────────────
    
    def get_balance(self) -> dict[str, float]:
        """Get account balance. Returns {'balance': float, 'available': float}."""
        if not self._sdk_client:
            raise RuntimeError("Kalshi trading requires authentication. Set KALSHI_API_KEY and KALSHI_PRIVATE_KEY_FILE.")
        
        balance = self._sdk_client.get_balance()
        return {
            "balance": float(getattr(balance, 'balance', 0)) / 100,
            "available": float(getattr(balance, 'available_balance', getattr(balance, 'balance', 0))) / 100,
        }
    
    def get_positions(self) -> list[KalshiPosition]:
        """Get all open positions."""
        if not self._sdk_client:
            return []
        
        try:
            response = self._sdk_client.get_positions()
            positions = []
            for p in getattr(response, 'market_positions', []):
                pos = int(getattr(p, 'position', 0))
                positions.append(KalshiPosition(
                    ticker=getattr(p, 'ticker', ""),
                    market_exposure=float(getattr(p, 'market_exposure', 0)),
                    realized_pnl=float(getattr(p, 'realized_pnl', 0)),
                    resting_order_count=int(getattr(p, 'resting_orders_count', 0)),
                    total_traded=float(getattr(p, 'total_traded', 0)),
                    side="yes" if pos > 0 else "no",
                    quantity=abs(pos),
                ))
            return positions
        except Exception as e:
            logger.error("Failed to get Kalshi positions: %s", e)
            return []
    
    def get_orders(self, status: str = "resting") -> list[KalshiOrder]:
        """Get orders. Status can be 'resting', 'pending', 'executed', 'canceled'."""
        if not self._sdk_client:
            return []
        
        try:
            response = self._sdk_client.get_orders(status=status)
            orders = []
            for o in getattr(response, 'orders', []):
                orders.append(KalshiOrder(
                    order_id=getattr(o, 'order_id', ""),
                    ticker=getattr(o, 'ticker', ""),
                    side=getattr(o, 'side', ""),
                    type=getattr(o, 'type', ""),
                    status=getattr(o, 'status', ""),
                    price=float(getattr(o, 'yes_price', getattr(o, 'no_price', 0))) / 100,
                    size=int(getattr(o, 'count', 0)),
                    filled=int(getattr(o, 'filled_count', 0)),
                    remaining=int(getattr(o, 'remaining_count', 0)),
                    created_time=str(getattr(o, 'created_time', "")),
                ))
            return orders
        except Exception as e:
            logger.error("Failed to get Kalshi orders: %s", e)
            return []
    
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
        if not self._sdk_client:
            raise RuntimeError("Kalshi trading requires authentication.")
        
        # Kalshi prices are in cents (1-99)
        price_cents = int(price * 100)
        
        logger.info("Kalshi order: %s %s %s x%d @ $%.2f", action, side, ticker, size, price)
        
        response = self._sdk_client.create_order(
            ticker=ticker,
            action=action,
            side=side,
            count=size,
            type=order_type,
            yes_price=price_cents if side == "yes" else None,
            no_price=price_cents if side == "no" else None,
        )
        
        o = getattr(response, 'order', response)
        
        return KalshiOrder(
            order_id=getattr(o, 'order_id', ""),
            ticker=getattr(o, 'ticker', ticker),
            side=getattr(o, 'side', side),
            type=getattr(o, 'type', order_type),
            status=getattr(o, 'status', "pending"),
            price=price,
            size=size,
            filled=int(getattr(o, 'filled_count', 0)),
            remaining=int(getattr(o, 'remaining_count', size)),
            created_time=str(getattr(o, 'created_time', "")),
        )
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID."""
        if not self._sdk_client:
            return False
        
        try:
            self._sdk_client.cancel_order(order_id=order_id)
            logger.info("Kalshi order canceled: %s", order_id)
            return True
        except Exception as e:
            logger.error("Failed to cancel Kalshi order %s: %s", order_id, e)
            return False
    
    def cancel_all_orders(self, ticker: str | None = None) -> int:
        """Cancel all resting orders, optionally filtered by ticker."""
        if not self._sdk_client:
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
