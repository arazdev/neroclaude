"""Polymarket data fetching and order execution via py-clob-client."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    BookParams,
    MarketOrderArgs,
    OpenOrderParams,
    OrderArgs,
    OrderType,
)
from py_clob_client.order_builder.constants import BUY, SELL

# py-clob-client's OrderType extends `enumerate` (not Enum), so Pylance
# cannot narrow the Literal["GTC"] ↔ OrderType relationship.  The string
# constants below keep the type-checker happy while remaining identical at
# runtime.
_GTC: OrderType = OrderType.GTC  # type: ignore[assignment]
_FOK: OrderType = OrderType.FOK  # type: ignore[assignment]

from config import Config

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


# ── Data models ──────────────────────────────────────────────────────────────


@dataclass
class MarketSnapshot:
    condition_id: str
    question: str
    token_id_yes: str
    token_id_no: str
    outcome_yes_price: float
    outcome_no_price: float
    volume_24h: float
    liquidity: float
    best_bid: float
    best_ask: float
    spread: float
    end_date: str


# ── Client ───────────────────────────────────────────────────────────────────


class PolymarketClient:
    """Wraps py-clob-client for data reads and authenticated trading."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

        # Read-only client for market data
        self._reader = ClobClient(cfg.poly_host)

        # Authenticated client for trading
        kwargs: dict[str, Any] = {
            "key": cfg.poly_private_key,
            "chain_id": cfg.poly_chain_id,
            "signature_type": cfg.poly_signature_type,
        }
        if cfg.poly_funder:
            kwargs["funder"] = cfg.poly_funder
        self._trader = ClobClient(cfg.poly_host, **kwargs)

        # Derive API creds from the private key (most reliable)
        self._trader.set_api_creds(self._trader.create_or_derive_api_creds())

        self._http = httpx.Client(timeout=15)

    # ── Market discovery ─────────────────────────────────────────────────

    def fetch_active_markets(
        self, limit: int = 30, order: str = "volume24hr"
    ) -> list[dict[str, Any]]:
        """Fetch top active individual markets from Gamma API.

        Uses /markets endpoint sorted by 24h volume so we get markets
        with *recent* trading activity (tight spreads) instead of the
        /events endpoint which returns all-time volume leaders.
        """
        resp = self._http.get(
            f"{GAMMA_API}/markets",
            params={
                "active": "true",
                "closed": "false",
                "order": order,
                "ascending": "false",
                "limit": str(limit),
            },
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_active_events(
        self, limit: int = 20, order: str = "volume"
    ) -> list[dict[str, Any]]:
        """Fetch top active events from Gamma API."""
        resp = self._http.get(
            f"{GAMMA_API}/events",
            params={
                "active": "true",
                "closed": "false",
                "order": order,
                "ascending": "false",
                "limit": str(limit),
            },
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_event_by_slug(self, slug: str) -> dict[str, Any]:
        resp = self._http.get(f"{GAMMA_API}/events/slug/{slug}")
        resp.raise_for_status()
        return resp.json()

    # ── Snapshot builder ─────────────────────────────────────────────────

    def build_snapshot(self, market: dict[str, Any]) -> MarketSnapshot | None:
        """Build a MarketSnapshot from a Gamma API market dict."""
        import json as _json

        raw_tokens = market.get("clobTokenIds")
        if not raw_tokens:
            return None
        # Gamma API returns clobTokenIds as a JSON-encoded string
        tokens = _json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
        if not tokens or len(tokens) < 2:
            return None

        token_yes, token_no = tokens[0], tokens[1]

        try:
            book = self._reader.get_order_book(token_yes)
        except Exception:
            logger.warning("Could not fetch order book for %s", token_yes)
            return None

        bids = book.bids or []
        asks = book.asks or []
        # Order book bids are sorted ascending, asks descending.
        # Best bid = highest bid price, best ask = lowest ask price.
        best_bid = max((float(b.price) for b in bids), default=0.0)
        best_ask = min((float(a.price) for a in asks), default=1.0)
        spread = best_ask - best_bid

        outcomes = market.get("outcomePrices", "")
        if isinstance(outcomes, str) and outcomes:
            import json as _json

            prices = _json.loads(outcomes)
        elif isinstance(outcomes, list):
            prices = outcomes
        else:
            prices = [best_bid, 1.0 - best_bid]

        return MarketSnapshot(
            condition_id=market.get("conditionId", ""),
            question=market.get("question", ""),
            token_id_yes=token_yes,
            token_id_no=token_no,
            outcome_yes_price=float(prices[0]),
            outcome_no_price=float(prices[1]) if len(prices) > 1 else 1.0 - float(prices[0]),
            volume_24h=float(market.get("volume24hr", 0)),
            liquidity=float(market.get("liquidity", 0)),
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            end_date=market.get("endDate", ""),
        )

    def get_snapshots(self, limit: int = 10) -> list[MarketSnapshot]:
        """Get snapshots for top active markets, sorted by tightest spread.

        Uses /markets?order=volume24hr to find markets with recent trading
        activity, pre-filters by outcomePrices to skip extremely one-sided
        markets, then fetches CLOB order books only for promising candidates.
        """
        import json as _json

        raw_markets = self.fetch_active_markets(limit=limit * 3)

        # Pre-filter: skip markets with extreme prices (likely illiquid)
        candidates: list[dict[str, Any]] = []
        for m in raw_markets:
            outcomes = m.get("outcomePrices", "")
            if isinstance(outcomes, str) and outcomes:
                try:
                    prices = _json.loads(outcomes)
                except Exception:
                    continue
            elif isinstance(outcomes, list):
                prices = outcomes
            else:
                continue

            if not prices:
                continue
            yes_price = float(prices[0])
            # Skip markets where YES is >95% or <5% — these have wide spreads
            if 0.05 <= yes_price <= 0.95:
                candidates.append(m)

        snapshots: list[MarketSnapshot] = []
        for m in candidates[:limit * 2]:  # cap CLOB calls
            snap = self.build_snapshot(m)
            if snap:
                snapshots.append(snap)

        # Sort by tightest spread first — give Claude the most tradeable markets
        snapshots.sort(key=lambda s: s.spread)
        return snapshots[:limit]

    # ── Trading ──────────────────────────────────────────────────────────

    def place_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> Any:
        """Sign and post a GTC limit order. Returns API response."""
        order_side = BUY if side.upper() == "BUY" else SELL
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=order_side,
        )
        signed = self._trader.create_order(order_args)
        resp = self._trader.post_order(signed, _GTC)
        logger.info("Limit order posted: %s %s @ %.4f x %.2f → %s", side, token_id[:12], price, size, resp)
        return resp

    def place_market_order(
        self,
        token_id: str,
        side: str,
        amount_usdc: float,
    ) -> Any:
        """Sign and post a FOK market order. Returns API response."""
        order_side = BUY if side.upper() == "BUY" else SELL
        mo = MarketOrderArgs(
            token_id=token_id,
            amount=amount_usdc,
            side=order_side,
        )
        signed = self._trader.create_market_order(mo)
        resp = self._trader.post_order(signed, _FOK)
        logger.info("Market order posted: %s %s $%.2f → %s", side, token_id[:12], amount_usdc, resp)
        return resp

    def get_open_orders(self) -> list[dict[str, Any]]:
        return self._trader.get_orders(OpenOrderParams())

    def cancel_order(self, order_id: str) -> Any:
        return self._trader.cancel(order_id)

    def cancel_all(self) -> Any:
        return self._trader.cancel_all()
