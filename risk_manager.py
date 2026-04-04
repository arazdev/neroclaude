"""Pre-trade risk checks and position limits."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from claude_engine import TradeDecision
from config import Config
from polymarket_client import MarketSnapshot, PolymarketClient
from position_tracker import PositionTracker

logger = logging.getLogger(__name__)


@dataclass
class RiskVerdict:
    approved: bool
    reason: str


class RiskManager:
    def __init__(self, cfg: Config, poly: PolymarketClient, tracker: PositionTracker) -> None:
        self.cfg = cfg
        self.poly = poly
        self.tracker = tracker

    def check(self, decision: TradeDecision, snapshot: MarketSnapshot) -> RiskVerdict:
        """Run all risk checks. Returns approved=True if every check passes."""
        checks = [
            self._check_hold,
            self._check_order_size,
            self._check_total_exposure,
            self._check_duplicate_token,
            self._check_open_orders,
            self._check_liquidity,
            self._check_price_bounds,
            self._check_confidence,
        ]
        for fn in checks:
            verdict = fn(decision, snapshot)
            if not verdict.approved:
                logger.warning("Risk REJECTED: %s", verdict.reason)
                return verdict

        logger.info("Risk APPROVED: %s $%.2f", decision.action, decision.size_usdc)
        return RiskVerdict(approved=True, reason="All checks passed")

    # ── Individual checks ────────────────────────────────────────────────

    def _check_hold(self, d: TradeDecision, _s: MarketSnapshot) -> RiskVerdict:
        if d.action.upper() == "HOLD":
            return RiskVerdict(False, "Decision is HOLD — no trade needed")
        return RiskVerdict(True, "")

    def _check_order_size(self, d: TradeDecision, _s: MarketSnapshot) -> RiskVerdict:
        if d.size_usdc <= 0:
            return RiskVerdict(False, "Order size is zero or negative")
        if d.size_usdc > self.cfg.max_order_usdc:
            return RiskVerdict(
                False,
                f"Order ${d.size_usdc:.2f} exceeds max ${self.cfg.max_order_usdc:.2f}",
            )
        return RiskVerdict(True, "")

    def _check_open_orders(self, _d: TradeDecision, _s: MarketSnapshot) -> RiskVerdict:
        try:
            open_orders = self.poly.get_open_orders()
            if len(open_orders) >= self.cfg.max_open_orders:
                return RiskVerdict(
                    False,
                    f"Already {len(open_orders)} open orders (max {self.cfg.max_open_orders})",
                )
        except Exception as exc:
            logger.error("Could not fetch open orders: %s", exc)
            return RiskVerdict(False, f"Failed to fetch open orders: {exc}")
        return RiskVerdict(True, "")

    def _check_liquidity(self, _d: TradeDecision, s: MarketSnapshot) -> RiskVerdict:
        if s.liquidity < self.cfg.min_liquidity_usdc:
            return RiskVerdict(
                False,
                f"Liquidity ${s.liquidity:.0f} below minimum ${self.cfg.min_liquidity_usdc:.0f}",
            )
        return RiskVerdict(True, "")

    def _check_price_bounds(self, d: TradeDecision, _s: MarketSnapshot) -> RiskVerdict:
        if d.price < 0.0 or d.price > 1.0:
            return RiskVerdict(False, f"Price {d.price} out of [0, 1] range")
        return RiskVerdict(True, "")

    def _check_confidence(self, d: TradeDecision, _s: MarketSnapshot) -> RiskVerdict:
        if d.confidence < 0.5:
            return RiskVerdict(
                False,
                f"Confidence {d.confidence:.2f} below 0.50 threshold",
            )
        return RiskVerdict(True, "")

    def _check_total_exposure(self, d: TradeDecision, _s: MarketSnapshot) -> RiskVerdict:
        current = self.tracker.total_exposure()
        if current + d.size_usdc > self.cfg.max_position_usdc:
            return RiskVerdict(
                False,
                f"Total exposure ${current:.2f} + ${d.size_usdc:.2f} "
                f"exceeds max ${self.cfg.max_position_usdc:.2f}",
            )
        return RiskVerdict(True, "")

    def _check_duplicate_token(self, d: TradeDecision, _s: MarketSnapshot) -> RiskVerdict:
        existing = self.tracker.exposure_for_token(d.token_id)
        if existing > 0:
            return RiskVerdict(
                False,
                f"Already have ${existing:.2f} exposure on token {d.token_id[:16]}",
            )
        return RiskVerdict(True, "")
