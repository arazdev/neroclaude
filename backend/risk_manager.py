"""Pre-trade risk checks and position limits with game theory validation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from claude_engine import TradeDecision
from config import Config
from models import MarketSnapshot
from position_tracker import PositionTracker
from strategy import (
    calculate_ev,
    detect_mispricing,
    is_longshot_trap,
    KellyCalculator,
)

logger = logging.getLogger(__name__)


@dataclass
class RiskVerdict:
    approved: bool
    reason: str


class RiskManager:
    """Risk manager with game theory validation (from 72M trade analysis)."""
    
    def __init__(self, cfg: Config, tracker: PositionTracker, client: Any = None) -> None:
        self.cfg = cfg
        self.client = client  # Trading client (optional)
        self.tracker = tracker
        
        # Kelly calculator for position sizing validation
        self.kelly = KellyCalculator(
            bankroll=cfg.max_position_usdc,
            kelly_fraction=0.25,
            max_bet_pct=0.05
        )

    def check(self, decision: TradeDecision, snapshot: MarketSnapshot) -> RiskVerdict:
        """Run all risk checks including game theory validation."""
        checks = [
            self._check_hold,
            self._check_order_size,
            self._check_total_exposure,
            self._check_duplicate_token,
            self._check_liquidity,
            self._check_price_bounds,
            self._check_confidence,
            # Game theory checks (from 72M trade analysis)
            self._check_longshot_bias,
            self._check_kelly_sizing,
            self._check_minimum_edge,
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

    # ══════════════════════════════════════════════════════════════════════
    # GAME THEORY CHECKS (from 72M trade analysis)
    # ══════════════════════════════════════════════════════════════════════

    def _check_longshot_bias(self, d: TradeDecision, s: MarketSnapshot) -> RiskVerdict:
        """
        Reject buying YES on longshots (<10¢).
        
        From empirical data: 1¢ contracts return only 43¢ per dollar.
        Longshots <10¢ are overpriced by 16-57%.
        """
        if d.action.upper() == "HOLD":
            return RiskVerdict(True, "")
        
        # Determine which side we're buying
        is_buying_yes = d.side.upper() == "BUY" and d.token_id == s.token_id_yes
        
        if is_buying_yes and is_longshot_trap(s.outcome_yes_price):
            mispricing = detect_mispricing(s.outcome_yes_price)
            return RiskVerdict(
                False,
                f"LONGSHOT TRAP: Buying YES at {s.outcome_yes_price*100:.0f}¢ is "
                f"historically overpriced by {abs(mispricing.estimated_mispricing_pct):.0f}%. "
                f"Consider BUY NO instead."
            )
        
        return RiskVerdict(True, "")

    def _check_kelly_sizing(self, d: TradeDecision, s: MarketSnapshot) -> RiskVerdict:
        """
        Validate position size against Kelly criterion.
        
        Quarter-Kelly with 5% max per position.
        Reject if order exceeds 2x the Kelly recommendation.
        """
        if d.action.upper() == "HOLD" or d.size_usdc <= 0:
            return RiskVerdict(True, "")
        
        # Use confidence as proxy for edge
        # confidence 0.7 → estimated prob is 70% vs market
        estimated_prob = d.confidence if d.confidence > 0.5 else 0.5
        
        # Check if buying YES or NO
        if d.token_id == s.token_id_yes:
            market_price = s.outcome_yes_price
        else:
            market_price = s.outcome_no_price
            estimated_prob = 1 - estimated_prob  # Flip for NO side
        
        kelly_result = self.kelly.calculate(market_price, estimated_prob)
        
        # Allow up to 2x Kelly (some flexibility)
        max_allowed = kelly_result.bet_amount * 2
        
        if d.size_usdc > max_allowed and max_allowed > 0:
            return RiskVerdict(
                False,
                f"Order ${d.size_usdc:.2f} exceeds 2x Kelly sizing ${max_allowed:.2f}. "
                f"Kelly recommends ${kelly_result.bet_amount:.2f}."
            )
        
        return RiskVerdict(True, "")

    def _check_minimum_edge(self, d: TradeDecision, s: MarketSnapshot) -> RiskVerdict:
        """
        Require minimum 3% edge to trade.
        
        Below 3% edge, transaction costs and variance eat profits.
        """
        if d.action.upper() == "HOLD":
            return RiskVerdict(True, "")
        
        # Use confidence as proxy for true probability
        if d.confidence < 0.53:  # Need at least 53% confidence for 3% edge
            return RiskVerdict(
                False,
                f"Confidence {d.confidence:.0%} implies <3% edge. "
                f"Minimum 53% confidence required for positive expected value."
            )
        
        return RiskVerdict(True, "")
