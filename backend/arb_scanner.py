"""Same-market arbitrage scanner — buys YES + NO when total cost < $1.

No Claude needed. Pure math, fast loop.
If best_ask(YES) + best_ask(NO) < 1.0 - fee_threshold, buy both
to lock in a guaranteed profit since one must resolve to $1.

Polymarket fee: 2% on winnings (not on cost), so effective threshold
is ~$0.98 for break-even. We target < $0.97 for safe profit.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from config import Config
from polymarket_client import MarketSnapshot, PolymarketClient
from position_tracker import PositionTracker

logger = logging.getLogger("arb_scanner")

# Polymarket charges ~2% on profits. To guarantee profit after fees:
# cost(YES) + cost(NO) < 1.0 - fee_buffer
DEFAULT_FEE_BUFFER = 0.02  # 2 cents minimum profit per share


@dataclass
class ArbOpportunity:
    """Detected arbitrage opportunity."""
    question: str
    condition_id: str
    token_id_yes: str
    token_id_no: str
    ask_yes: float  # cost to buy YES
    ask_no: float   # cost to buy NO
    total_cost: float  # ask_yes + ask_no
    profit_per_share: float  # 1.0 - total_cost
    profit_pct: float  # profit_per_share / total_cost * 100
    max_shares: float  # limited by order book depth
    estimated_profit_usdc: float


class ArbScanner:
    """Scans Polymarket for same-market YES+NO mispricing."""

    def __init__(
        self,
        cfg: Config,
        poly: PolymarketClient,
        tracker: PositionTracker,
    ) -> None:
        self.cfg = cfg
        self.poly = poly
        self.tracker = tracker
        self.fee_buffer = float(
            getattr(cfg, "arb_fee_buffer", DEFAULT_FEE_BUFFER)
        )
        self.min_profit_pct = float(
            getattr(cfg, "arb_min_profit_pct", 0.5)
        )
        self.arb_max_usdc = float(
            getattr(cfg, "arb_max_usdc", cfg.max_order_usdc)
        )

    def scan(self, limit: int = 30) -> list[ArbOpportunity]:
        """Scan active markets for YES+NO arbitrage.

        Returns opportunities sorted by profit % descending.
        """
        opportunities: list[ArbOpportunity] = []

        raw_markets = self.poly.fetch_active_markets(limit=limit)

        for market in raw_markets:
            opp = self._check_market(market)
            if opp:
                opportunities.append(opp)

        opportunities.sort(key=lambda o: o.profit_pct, reverse=True)
        return opportunities

    def _check_market(self, market: dict[str, Any]) -> ArbOpportunity | None:
        """Check a single market for YES+NO arbitrage."""
        raw_tokens = market.get("clobTokenIds")
        if not raw_tokens:
            return None

        tokens = json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
        if not tokens or len(tokens) < 2:
            return None

        token_yes, token_no = tokens[0], tokens[1]

        try:
            book_yes = self.poly._reader.get_order_book(token_yes)
            book_no = self.poly._reader.get_order_book(token_no)
        except Exception:
            return None

        asks_yes = book_yes.asks or []
        asks_no = book_no.asks or []

        if not asks_yes or not asks_no:
            return None

        # Best ask = lowest price someone is willing to sell at
        best_ask_yes = min(float(a.price) for a in asks_yes)
        best_ask_no = min(float(a.price) for a in asks_no)

        total_cost = best_ask_yes + best_ask_no

        # Is there profit after fees?
        threshold = 1.0 - self.fee_buffer
        if total_cost >= threshold:
            return None

        profit_per_share = 1.0 - total_cost
        profit_pct = (profit_per_share / total_cost) * 100

        if profit_pct < self.min_profit_pct:
            return None

        # Calculate max shares based on order book depth
        yes_depth = sum(float(a.size) for a in asks_yes if float(a.price) <= best_ask_yes + 0.01)
        no_depth = sum(float(a.size) for a in asks_no if float(a.price) <= best_ask_no + 0.01)
        max_shares_by_book = min(yes_depth, no_depth)

        # Cap by our budget
        max_shares_by_budget = self.arb_max_usdc / total_cost
        max_shares = min(max_shares_by_book, max_shares_by_budget)

        if max_shares < 1.0:
            return None

        return ArbOpportunity(
            question=market.get("question", "?"),
            condition_id=market.get("conditionId", ""),
            token_id_yes=token_yes,
            token_id_no=token_no,
            ask_yes=best_ask_yes,
            ask_no=best_ask_no,
            total_cost=total_cost,
            profit_per_share=profit_per_share,
            profit_pct=profit_pct,
            max_shares=max_shares,
            estimated_profit_usdc=profit_per_share * max_shares,
        )

    def execute_arb(self, opp: ArbOpportunity, dry_run: bool) -> dict[str, Any]:
        """Execute an arbitrage by buying both YES and NO."""
        shares = opp.max_shares
        cost_yes = opp.ask_yes * shares
        cost_no = opp.ask_no * shares
        total_cost = cost_yes + cost_no

        logger.info(
            "ARB FOUND: %s | YES=%.4f + NO=%.4f = %.4f | profit=%.2f%% | ~$%.2f",
            opp.question[:50], opp.ask_yes, opp.ask_no,
            opp.total_cost, opp.profit_pct, opp.estimated_profit_usdc,
        )

        if dry_run:
            logger.info("[DRY RUN] Would buy %.1f shares YES @ %.4f + NO @ %.4f", shares, opp.ask_yes, opp.ask_no)
            self._record_arb(opp, shares, dry_run=True)
            return {"dry_run": True, "opportunity": opp.__dict__}

        # Execute both legs
        try:
            result_yes = self.poly.place_limit_order(
                token_id=opp.token_id_yes,
                side="BUY",
                price=opp.ask_yes,
                size=shares,
            )
            result_no = self.poly.place_limit_order(
                token_id=opp.token_id_no,
                side="BUY",
                price=opp.ask_no,
                size=shares,
            )
            self._record_arb(opp, shares, dry_run=False)
            return {"yes_order": result_yes, "no_order": result_no, "shares": shares}
        except Exception as exc:
            logger.error("Arb execution failed: %s", exc)
            # Try to cancel any partial fills
            try:
                self.poly.cancel_all()
            except Exception:
                pass
            return {"error": str(exc)}

    def _record_arb(self, opp: ArbOpportunity, shares: float, dry_run: bool) -> None:
        """Record both legs in the position tracker."""
        prefix = "[DRY] " if dry_run else ""
        self.tracker.record_trade(
            token_id=opp.token_id_yes,
            market_question=f"{prefix}ARB YES: {opp.question}",
            side="BUY",
            action="ARB_YES",
            price=opp.ask_yes,
            size_usdc=opp.ask_yes * shares,
            confidence=1.0,
            reasoning=f"Arbitrage: YES({opp.ask_yes:.4f})+NO({opp.ask_no:.4f})={opp.total_cost:.4f} < 1.0, profit={opp.profit_pct:.2f}%",
        )
        self.tracker.record_trade(
            token_id=opp.token_id_no,
            market_question=f"{prefix}ARB NO: {opp.question}",
            side="BUY",
            action="ARB_NO",
            price=opp.ask_no,
            size_usdc=opp.ask_no * shares,
            confidence=1.0,
            reasoning=f"Arbitrage: YES({opp.ask_yes:.4f})+NO({opp.ask_no:.4f})={opp.total_cost:.4f} < 1.0, profit={opp.profit_pct:.2f}%",
        )

    def run_scan_cycle(self, dry_run: bool) -> int:
        """Run one scan cycle. Returns number of arbs executed."""
        opps = self.scan()
        executed = 0

        if not opps:
            logger.debug("No arbitrage opportunities found")
            return 0

        for opp in opps:
            # Check exposure limit
            current_exposure = self.tracker.total_exposure()
            if current_exposure + (opp.total_cost * opp.max_shares) > self.cfg.max_position_usdc:
                logger.info("Skipping arb — would exceed exposure limit")
                continue

            result = self.execute_arb(opp, dry_run)
            if "error" not in result:
                executed += 1

        return executed
