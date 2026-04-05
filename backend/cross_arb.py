"""Cross-platform arbitrage: Polymarket vs Kalshi.

Finds matching markets across platforms and exploits price differences.
If Polymarket YES is cheaper than Kalshi YES, buy on Polymarket.
If Polymarket NO is cheaper than (1 - Kalshi YES), buy on Polymarket.

We only execute trades on Polymarket — Kalshi prices are used as signals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from config import Config
from kalshi_client import KalshiClient, KalshiMarket
from models import MarketSnapshot
from position_tracker import PositionTracker

logger = logging.getLogger("cross_arb")


@dataclass
class CrossPlatformOpportunity:
    """A detected cross-platform price discrepancy."""
    poly_question: str
    kalshi_title: str
    match_score: float  # 0-1 text similarity

    poly_yes_price: float
    poly_no_price: float
    kalshi_yes_price: float
    kalshi_no_price: float

    edge: float  # price difference favoring Polymarket
    side: str  # BUY YES or BUY NO on Polymarket
    target_token_id: str
    target_price: float
    suggested_size_usdc: float
    reasoning: str


class CrossPlatformArb:
    """Compares Polymarket and Kalshi prices to find edges."""

    # Minimum text similarity to consider two markets as "same event"
    MIN_MATCH_SCORE = 0.55
    # Minimum price edge to act on (after estimated fees on both platforms)
    MIN_EDGE = 0.03  # 3 cents

    def __init__(
        self,
        cfg: Config,
        poly: PolymarketClient,
        tracker: PositionTracker,
    ) -> None:
        self.cfg = cfg
        self.poly = poly
        self.kalshi = KalshiClient()
        self.tracker = tracker
        self.max_order = cfg.max_order_usdc

    def find_opportunities(self, poly_limit: int = 20, kalshi_limit: int = 100) -> list[CrossPlatformOpportunity]:
        """Scan both platforms and find matching markets with price edges."""
        logger.info("Scanning for cross-platform arbitrage...")

        # Fetch markets from both platforms
        try:
            poly_snaps = self.poly.get_snapshots(limit=poly_limit)
        except Exception as exc:
            logger.error("Failed to fetch Polymarket data: %s", exc)
            return []

        try:
            kalshi_markets = self.kalshi.get_active_markets(limit=kalshi_limit)
        except Exception as exc:
            logger.error("Failed to fetch Kalshi data: %s", exc)
            return []

        if not poly_snaps or not kalshi_markets:
            logger.info("Not enough data (poly=%d, kalshi=%d)", len(poly_snaps), len(kalshi_markets))
            return []

        logger.info("Comparing %d Polymarket × %d Kalshi markets", len(poly_snaps), len(kalshi_markets))

        opportunities: list[CrossPlatformOpportunity] = []

        for snap in poly_snaps:
            best_match = self._find_best_match(snap, kalshi_markets)
            if not best_match:
                continue

            kalshi_mkt, score = best_match
            opp = self._evaluate_edge(snap, kalshi_mkt, score)
            if opp:
                opportunities.append(opp)

        opportunities.sort(key=lambda o: o.edge, reverse=True)
        return opportunities

    def _find_best_match(
        self,
        snap: MarketSnapshot,
        kalshi_markets: list[KalshiMarket],
    ) -> tuple[KalshiMarket, float] | None:
        """Find the Kalshi market most similar to a Polymarket question."""
        poly_text = snap.question.lower().strip()
        best: tuple[KalshiMarket, float] | None = None

        for km in kalshi_markets:
            kalshi_text = f"{km.title} {km.subtitle}".lower().strip()
            score = SequenceMatcher(None, poly_text, kalshi_text).ratio()
            if score >= self.MIN_MATCH_SCORE:
                if best is None or score > best[1]:
                    best = (km, score)

        return best

    def _evaluate_edge(
        self,
        snap: MarketSnapshot,
        km: KalshiMarket,
        match_score: float,
    ) -> CrossPlatformOpportunity | None:
        """Check if there's a tradeable edge between platforms."""
        # Compare YES prices
        # If Polymarket YES ask < Kalshi YES → buy YES on Polymarket
        yes_edge = km.yes_price - snap.best_ask  # positive = Poly is cheaper
        # If Polymarket NO ask < Kalshi NO → buy NO on Polymarket
        no_edge = km.no_price - snap.outcome_no_price

        if yes_edge >= self.MIN_EDGE and yes_edge >= no_edge:
            return CrossPlatformOpportunity(
                poly_question=snap.question,
                kalshi_title=f"{km.title} {km.subtitle}",
                match_score=match_score,
                poly_yes_price=snap.best_ask,
                poly_no_price=snap.outcome_no_price,
                kalshi_yes_price=km.yes_price,
                kalshi_no_price=km.no_price,
                edge=yes_edge,
                side="BUY",
                target_token_id=snap.token_id_yes,
                target_price=snap.best_ask,
                suggested_size_usdc=min(self.max_order, self.cfg.max_position_usdc - self.tracker.total_exposure()),
                reasoning=f"Poly YES={snap.best_ask:.4f} vs Kalshi YES={km.yes_price:.4f}, edge={yes_edge:.4f} ({match_score:.0%} match)",
            )

        if no_edge >= self.MIN_EDGE:
            return CrossPlatformOpportunity(
                poly_question=snap.question,
                kalshi_title=f"{km.title} {km.subtitle}",
                match_score=match_score,
                poly_yes_price=snap.best_ask,
                poly_no_price=snap.outcome_no_price,
                kalshi_yes_price=km.yes_price,
                kalshi_no_price=km.no_price,
                edge=no_edge,
                side="BUY",
                target_token_id=snap.token_id_no,
                target_price=snap.outcome_no_price,
                suggested_size_usdc=min(self.max_order, self.cfg.max_position_usdc - self.tracker.total_exposure()),
                reasoning=f"Poly NO={snap.outcome_no_price:.4f} vs Kalshi NO={km.no_price:.4f}, edge={no_edge:.4f} ({match_score:.0%} match)",
            )

        return None

    def execute_opportunity(self, opp: CrossPlatformOpportunity, dry_run: bool) -> dict[str, Any]:
        """Execute a cross-platform arb trade on Polymarket."""
        if opp.suggested_size_usdc <= 0:
            logger.info("Skipping — no budget remaining")
            return {"skipped": True, "reason": "no budget"}

        shares = opp.suggested_size_usdc / opp.target_price if opp.target_price > 0 else 0

        logger.info(
            "CROSS-ARB: %s | edge=%.4f | %s $%.2f @ %.4f",
            opp.poly_question[:50], opp.edge, opp.side,
            opp.suggested_size_usdc, opp.target_price,
        )

        if dry_run:
            logger.info("[DRY RUN] Would %s token=%s @ %.4f", opp.side, opp.target_token_id[:16], opp.target_price)
            self.tracker.record_trade(
                token_id=opp.target_token_id,
                market_question=f"CROSS-ARB: {opp.poly_question}",
                side=opp.side,
                action="CROSS_ARB",
                price=opp.target_price,
                size_usdc=opp.suggested_size_usdc,
                confidence=min(opp.match_score, 0.99),
                reasoning=opp.reasoning,
            )
            return {"dry_run": True, "opportunity": opp.__dict__}

        try:
            result = self.poly.place_limit_order(
                token_id=opp.target_token_id,
                side=opp.side,
                price=opp.target_price,
                size=shares,
            )
            self.tracker.record_trade(
                token_id=opp.target_token_id,
                market_question=f"CROSS-ARB: {opp.poly_question}",
                side=opp.side,
                action="CROSS_ARB",
                price=opp.target_price,
                size_usdc=opp.suggested_size_usdc,
                confidence=min(opp.match_score, 0.99),
                reasoning=opp.reasoning,
            )
            return {"order": result}
        except Exception as exc:
            logger.error("Cross-arb execution failed: %s", exc)
            return {"error": str(exc)}

    def run_scan_cycle(self, dry_run: bool) -> int:
        """Run one cross-platform scan cycle. Returns trades executed."""
        opps = self.find_opportunities()
        executed = 0

        if not opps:
            logger.debug("No cross-platform opportunities found")
            return 0

        for opp in opps[:3]:  # Cap at 3 per cycle to manage risk
            result = self.execute_opportunity(opp, dry_run)
            if "error" not in result and not result.get("skipped"):
                executed += 1

        return executed
