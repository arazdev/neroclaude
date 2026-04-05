"""Automated market maker — posts limit bids on both YES and NO to earn the spread.

Strategy: For each market with a wide enough spread, place:
  - A BUY limit order on YES at (best_bid + offset)
  - A BUY limit order on NO at (1 - best_ask - offset)

When both sides fill, we hold YES + NO = guaranteed $1 at resolution.
Our cost = bid_yes + bid_no, profit = $1 - cost.

Key risk: only one side fills → directional exposure. Mitigated by:
  - Only entering markets with high volume (likely to fill both sides)
  - Setting tight expiry / cancelling stale orders
  - Capping inventory per market
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from config import Config
from models import MarketSnapshot
from position_tracker import PositionTracker

logger = logging.getLogger("market_maker")


@dataclass
class MMQuote:
    """A two-sided quote for market making."""
    question: str
    condition_id: str
    token_id_yes: str
    token_id_no: str

    bid_yes_price: float  # our buy price for YES
    bid_no_price: float   # our buy price for NO
    total_cost: float     # bid_yes + bid_no
    spread_profit: float  # 1.0 - total_cost (per share)
    spread_pct: float

    shares: float
    estimated_profit_usdc: float

    original_spread: float  # market's native spread
    best_bid: float
    best_ask: float


class MarketMaker:
    """Posts two-sided quotes to earn the bid-ask spread."""

    # Minimum market spread to participate (otherwise not worth it)
    MIN_SPREAD = 0.03  # 3 cents
    # How much inside the spread to place our orders (from each side)
    SPREAD_OFFSET = 0.01  # 1 cent inside
    # Minimum profit per share after fees to bother
    MIN_PROFIT_PER_SHARE = 0.01  # 1 cent

    def __init__(
        self,
        cfg: Config,
        poly: PolymarketClient,
        tracker: PositionTracker,
    ) -> None:
        self.cfg = cfg
        self.poly = poly
        self.tracker = tracker
        self.mm_max_usdc = float(getattr(cfg, "mm_max_usdc", cfg.max_order_usdc))
        self.mm_max_markets = int(getattr(cfg, "mm_max_markets", 3))

    def find_opportunities(self, snapshots: list[MarketSnapshot] | None = None) -> list[MMQuote]:
        """Find markets suitable for market making."""
        if snapshots is None:
            snapshots = self.poly.get_snapshots(limit=20)

        quotes: list[MMQuote] = []

        for snap in snapshots:
            quote = self._build_quote(snap)
            if quote:
                quotes.append(quote)

        # Sort by profit potential
        quotes.sort(key=lambda q: q.spread_profit, reverse=True)
        return quotes[:self.mm_max_markets]

    def _build_quote(self, snap: MarketSnapshot) -> MMQuote | None:
        """Build a two-sided quote for a market."""
        if snap.spread < self.MIN_SPREAD:
            return None

        # Skip very illiquid markets
        if snap.liquidity < self.cfg.min_liquidity_usdc:
            return None

        # Skip markets where we already have exposure
        if self.tracker.exposure_for_token(snap.token_id_yes) > 0:
            return None
        if self.tracker.exposure_for_token(snap.token_id_no) > 0:
            return None

        # Place bids inside the spread
        # YES bid: slightly above best bid
        bid_yes = snap.best_bid + self.SPREAD_OFFSET
        # NO bid: derived from the ask side — we want to buy NO at a discount
        # If best_ask for YES = 0.60, then NO should be around 0.40
        # We bid slightly below: 1.0 - best_ask + offset = buying NO cheap
        bid_no = 1.0 - snap.best_ask + self.SPREAD_OFFSET

        # Sanity: both bids must be in (0, 1) and total < 1.0
        if bid_yes <= 0.01 or bid_yes >= 0.99:
            return None
        if bid_no <= 0.01 or bid_no >= 0.99:
            return None

        total_cost = bid_yes + bid_no
        profit_per_share = 1.0 - total_cost

        if profit_per_share < self.MIN_PROFIT_PER_SHARE:
            return None

        # Calculate shares to trade
        max_shares_by_budget = self.mm_max_usdc / total_cost
        # Cap at reasonable size
        shares = min(max_shares_by_budget, 100)

        if shares < 1.0:
            return None

        return MMQuote(
            question=snap.question,
            condition_id=snap.condition_id,
            token_id_yes=snap.token_id_yes,
            token_id_no=snap.token_id_no,
            bid_yes_price=round(bid_yes, 4),
            bid_no_price=round(bid_no, 4),
            total_cost=round(total_cost, 4),
            spread_profit=round(profit_per_share, 4),
            spread_pct=round((profit_per_share / total_cost) * 100, 2),
            shares=round(shares, 2),
            estimated_profit_usdc=round(profit_per_share * shares, 2),
            original_spread=snap.spread,
            best_bid=snap.best_bid,
            best_ask=snap.best_ask,
        )

    def execute_quote(self, quote: MMQuote, dry_run: bool) -> dict[str, Any]:
        """Post both sides of a market-making quote."""
        logger.info(
            "MM QUOTE: %s | bid_yes=%.4f bid_no=%.4f | cost=%.4f | profit=%.2f%% (~$%.2f)",
            quote.question[:50], quote.bid_yes_price, quote.bid_no_price,
            quote.total_cost, quote.spread_pct, quote.estimated_profit_usdc,
        )

        if dry_run:
            logger.info(
                "[DRY RUN] Would post: BUY YES @ %.4f + BUY NO @ %.4f × %.0f shares",
                quote.bid_yes_price, quote.bid_no_price, quote.shares,
            )
            self._record_mm(quote, dry_run=True)
            return {"dry_run": True, "quote": quote.__dict__}

        try:
            result_yes = self.poly.place_limit_order(
                token_id=quote.token_id_yes,
                side="BUY",
                price=quote.bid_yes_price,
                size=quote.shares,
            )
            result_no = self.poly.place_limit_order(
                token_id=quote.token_id_no,
                side="BUY",
                price=quote.bid_no_price,
                size=quote.shares,
            )
            self._record_mm(quote, dry_run=False)
            return {"yes_order": result_yes, "no_order": result_no}
        except Exception as exc:
            logger.error("MM execution failed: %s", exc)
            try:
                self.poly.cancel_all()
            except Exception:
                pass
            return {"error": str(exc)}

    def _record_mm(self, quote: MMQuote, dry_run: bool) -> None:
        prefix = "[DRY] " if dry_run else ""
        reasoning = (
            f"Market making: spread={quote.original_spread:.4f}, "
            f"bid_yes={quote.bid_yes_price:.4f}+bid_no={quote.bid_no_price:.4f}="
            f"{quote.total_cost:.4f}, profit={quote.spread_pct:.2f}%"
        )
        self.tracker.record_trade(
            token_id=quote.token_id_yes,
            market_question=f"{prefix}MM YES: {quote.question}",
            side="BUY",
            action="MM_BID_YES",
            price=quote.bid_yes_price,
            size_usdc=quote.bid_yes_price * quote.shares,
            confidence=0.8,
            reasoning=reasoning,
        )
        self.tracker.record_trade(
            token_id=quote.token_id_no,
            market_question=f"{prefix}MM NO: {quote.question}",
            side="BUY",
            action="MM_BID_NO",
            price=quote.bid_no_price,
            size_usdc=quote.bid_no_price * quote.shares,
            confidence=0.8,
            reasoning=reasoning,
        )

    def cancel_stale_orders(self) -> int:
        """Cancel all open orders (called before refreshing quotes)."""
        try:
            open_orders = self.poly.get_open_orders()
            cancelled = 0
            for order in open_orders:
                oid = order.get("id", "")
                if oid:
                    self.poly.cancel_order(oid)
                    cancelled += 1
            if cancelled:
                logger.info("Cancelled %d stale MM orders", cancelled)
            return cancelled
        except Exception as exc:
            logger.error("Failed to cancel stale orders: %s", exc)
            return 0

    def run_cycle(self, dry_run: bool) -> int:
        """Run one market-making cycle: cancel stale → quote → post."""
        if not dry_run:
            self.cancel_stale_orders()

        quotes = self.find_opportunities()
        executed = 0

        if not quotes:
            logger.debug("No market-making opportunities found")
            return 0

        for quote in quotes:
            current_exposure = self.tracker.total_exposure()
            cost = quote.total_cost * quote.shares
            if current_exposure + cost > self.cfg.max_position_usdc:
                logger.info("Skipping MM quote — would exceed exposure limit")
                continue

            result = self.execute_quote(quote, dry_run)
            if "error" not in result:
                executed += 1

        return executed
