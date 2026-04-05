#!/usr/bin/env python3
"""
NEROCLAUDE — Polymarket trading bot powered by Claude.

Modes:
  claude  — AI directional trading (ask Claude for BUY/SELL/HOLD)
  arb     — Same-market YES+NO mispricing scanner (fast, no AI)
  cross   — Cross-platform Polymarket vs Kalshi arbitrage
  mm      — Automated market making (earn the spread)
  all     — Run all modes each cycle
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

    mode = cfg.bot_mode.lower()
    valid_modes = {"claude", "arb", "cross", "mm", "all"}
    if mode not in valid_modes:
        logger.error("Invalid BOT_MODE=%s. Choose from: %s", mode, valid_modes)
        sys.exit(1)

    if cfg.dry_run:
        logger.info("*** DRY RUN MODE — no real orders will be placed ***")

    logger.info("Bot mode: %s", mode.upper())

    poly = PolymarketClient(cfg)
    tracker = PositionTracker()
    risk = RiskManager(cfg, poly, tracker)

    # Only init Claude engine if needed
    engine = None
    if mode in ("claude", "all"):
        engine = ClaudeEngine(cfg)

    # Only init arb scanner if needed
    arb = None
    if mode in ("arb", "all"):
        from arb_scanner import ArbScanner
        arb = ArbScanner(cfg, poly, tracker)

    # Only init cross-platform arb if needed
    cross = None
    if mode in ("cross", "all"):
        from cross_arb import CrossPlatformArb
        cross = CrossPlatformArb(cfg, poly, tracker)

    # Only init market maker if needed
    mm = None
    kalshi_mm = None
    if mode in ("mm", "all"):
        from market_maker import MarketMaker
        mm = MarketMaker(cfg, poly, tracker)
        # Also init Kalshi MM
        from kalshi_mm import KalshiMarketMaker
        kalshi_mm = KalshiMarketMaker(cfg, tracker)

    logger.info("Bot started. Poll interval: %ds", cfg.poll_interval)

    backoff = cfg.poll_interval
    arb_counter = 0  # arb runs more frequently

    try:
        while True:
            try:
                # ── Fast strategies (run every cycle) ────────────
                if arb:
                    n = arb.run_scan_cycle(cfg.dry_run)
                    if n:
                        logger.info("Arb scanner executed %d trades", n)

                if mm:
                    n = mm.run_cycle(cfg.dry_run)
                    if n:
                        logger.info("Market maker posted %d quotes", n)
                
                if kalshi_mm:
                    n = kalshi_mm.run_cycle(cfg.dry_run)
                    if n:
                        logger.info("Kalshi market maker posted %d quotes", n)

                # ── Slower strategies (run at poll_interval) ─────
                arb_counter += 1
                full_cycle = arb_counter >= max(1, cfg.poll_interval // max(cfg.arb_scan_interval, 1))

                if full_cycle:
                    arb_counter = 0

                    if cross:
                        n = cross.run_scan_cycle(cfg.dry_run)
                        if n:
                            logger.info("Cross-platform arb executed %d trades", n)

                    if engine:
                        run_cycle(poly, engine, risk, tracker, cfg)

                # Log position summary
                tracker.log_summary()

                backoff = cfg.arb_scan_interval if (arb or mm) else cfg.poll_interval
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                logger.exception("Cycle error")
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
