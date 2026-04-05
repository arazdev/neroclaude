"""Kalshi exchange client for cross-platform arbitrage.

Kalshi API v3 docs: https://docs.kalshi.com/
Base URL: https://api.elections.kalshi.com/trade-api/v2  (production)

This is a read-only client for price comparison. We don't trade on Kalshi,
we just look for price mismatches vs Polymarket and trade on Polymarket
where the edge exists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("kalshi")

KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"


@dataclass
class KalshiMarket:
    """Slim representation of a Kalshi market for comparison."""
    ticker: str
    title: str
    subtitle: str
    yes_price: float  # 0–100 cents, we normalize to 0–1
    no_price: float
    volume: int
    open_interest: int
    status: str
    category: str
    event_ticker: str


class KalshiClient:
    """Read-only Kalshi client — fetches market prices for cross-platform arb.

    No authentication needed for public market data.
    """

    def __init__(self) -> None:
        self._http = httpx.Client(
            base_url=KALSHI_API_BASE,
            timeout=15,
            headers={"Accept": "application/json"},
        )

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
