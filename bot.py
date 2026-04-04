#!/usr/bin/env python3
"""
BOTCLAUDE — Polymarket trading bot powered by Claude.

Flow per cycle:
  1. Fetch top active market snapshots from Polymarket.
  2. For each snapshot, ask Claude for a structured BUY/SELL/HOLD decision.
  3. Validate the decision through risk checks.
  4. If approved (and not dry-run), sign and post the order.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

from claude_engine import ClaudeEngine, TradeDecision
from config import Config
from polymarket_client import MarketSnapshot, PolymarketClient
from position_tracker import PositionTracker
from risk_manager import RiskManager

logger = logging.getLogger("bot")


def execute_decision(
    poly: PolymarketClient,
    decision: TradeDecision,
    snapshot: MarketSnapshot,
    tracker: PositionTracker,
    dry_run: bool,
) -> dict[str, Any] | None:
    """Post the order to Polymarket (or log it in dry-run mode)."""
    if decision.action.upper() == "HOLD":
        return None

    if dry_run:
        logger.info(
            "[DRY RUN] Would %s token=%s price=%.4f size=$%.2f",
            decision.side,
            decision.token_id[:16],
            decision.price,
            decision.size_usdc,
        )
        tracker.record_trade(
            token_id=decision.token_id,
            market_question=snapshot.question,
            side=decision.side,
            action=decision.action,
            price=decision.price,
            size_usdc=decision.size_usdc,
            confidence=decision.confidence,
            reasoning=decision.reasoning,
        )
        return {"dry_run": True, "decision": decision.model_dump()}

    # Use limit order when a price is specified, market order otherwise.
    if decision.price > 0:
        result = poly.place_limit_order(
            token_id=decision.token_id,
            side=decision.side,
            price=decision.price,
            size=decision.size_usdc / decision.price,  # convert $ to shares
        )
    else:
        result = poly.place_market_order(
            token_id=decision.token_id,
            side=decision.side,
            amount_usdc=decision.size_usdc,
        )

    tracker.record_trade(
        token_id=decision.token_id,
        market_question=snapshot.question,
        side=decision.side,
        action=decision.action,
        price=decision.price,
        size_usdc=decision.size_usdc,
        confidence=decision.confidence,
        reasoning=decision.reasoning,
    )
    return result


def run_cycle(
    poly: PolymarketClient,
    engine: ClaudeEngine,
    risk: RiskManager,
    tracker: PositionTracker,
    cfg: Config,
) -> None:
    """Single bot cycle: fetch → decide → validate → execute."""
    logger.info("── Cycle start ──")

    snapshots = poly.get_snapshots(limit=5)
    if not snapshots:
        logger.warning("No market snapshots available")
        return

    for snap in snapshots:
        logger.info("Market: %s | YES=%.3f | spread=%.4f", snap.question[:60], snap.outcome_yes_price, snap.spread)

        decision = engine.decide(snap)
        verdict = risk.check(decision, snap)

        if not verdict.approved:
            logger.info("Skipped: %s", verdict.reason)
            continue

        result = execute_decision(poly, decision, snap, tracker, cfg.dry_run)
        if result:
            logger.info("Order result: %s", result)

    # Log position summary at end of each cycle
    tracker.log_summary()


def main() -> None:
    cfg = Config()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)-12s %(levelname)-8s %(message)s",
    )

    if cfg.dry_run:
        logger.info("*** DRY RUN MODE — no real orders will be placed ***")

    poly = PolymarketClient(cfg)
    engine = ClaudeEngine(cfg)
    tracker = PositionTracker()
    risk = RiskManager(cfg, poly, tracker)

    logger.info("Bot started. Poll interval: %ds", cfg.poll_interval)

    backoff = cfg.poll_interval
    try:
        while True:
            try:
                run_cycle(poly, engine, risk, tracker, cfg)
                backoff = cfg.poll_interval  # reset on success
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                logger.exception("Cycle error")
                # If Claude credits exhausted, back off aggressively
                if "credit balance is too low" in str(exc):
                    backoff = min(backoff * 4, 3600)
                    logger.warning(
                        "Claude credits depleted — sleeping %ds before retry", backoff
                    )
            time.sleep(backoff)
    except KeyboardInterrupt:
        logger.info("Shutting down.")
        sys.exit(0)


if __name__ == "__main__":
    main()
