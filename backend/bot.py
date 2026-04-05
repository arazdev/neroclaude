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
from models import MarketSnapshot
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
    logger.info("── Polymarket Cycle ──")

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


def run_kalshi_cycle(
    kalshi,
    engine: ClaudeEngine,
    tracker: PositionTracker,
    cfg: Config,
) -> None:
    """Single Kalshi cycle: fetch markets → ask Claude → execute."""
    logger.info("── Kalshi Cycle ──")
    
    snapshots = kalshi.get_snapshots(limit=5)
    if not snapshots:
        logger.warning("No Kalshi market snapshots available")
        return
    
    for snap in snapshots:
        logger.info("Kalshi: %s | YES=%.3f | spread=%.4f", 
                    snap["question"][:60], snap["yes_price"], snap["spread"])
        
        # Create a simple snapshot object for Claude
        from models import MarketSnapshot
        market_snap = MarketSnapshot(
            condition_id=snap["ticker"],
            question=snap["question"],
            token_id_yes=snap["ticker"],
            token_id_no=snap["ticker"],
            outcome_yes_price=snap["yes_price"],
            outcome_no_price=snap["no_price"],
            volume_24h=float(snap["volume"]),
            liquidity=float(snap["volume"]) * 10,  # Estimate
            best_bid=snap["yes_price"] - 0.01,
            best_ask=snap["yes_price"] + 0.01,
            spread=snap["spread"],
            end_date=snap.get("end_date", ""),  # Pass expiration date
            platform="kalshi",
        )
        
        decision = engine.decide(market_snap)
        
        if decision.action.upper() == "HOLD":
            logger.info("Claude: HOLD")
            continue
        
        if decision.confidence < 0.6:
            logger.info("Skipped: Low confidence %.2f", decision.confidence)
            continue
        
        # Cap order size
        size_usdc = min(decision.size_usdc, cfg.max_order_usdc)
        
        if cfg.dry_run:
            logger.info(
                "[DRY RUN] Kalshi: Would %s %s @ $%.2f for $%.2f",
                decision.side, snap["ticker"], decision.price, size_usdc
            )
            tracker.record_trade(
                token_id=snap["ticker"],
                market_question=f"[DRY] Kalshi: {snap['question'][:50]}",
                side=decision.side,
                action=decision.action,
                price=decision.price,
                size_usdc=size_usdc,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
            )
        else:
            # Real Kalshi order
            try:
                side = "yes" if "YES" in decision.action.upper() else "no"
                contracts = int(size_usdc / decision.price) if decision.price > 0 else 1
                
                order = kalshi.create_order(
                    ticker=snap["ticker"],
                    side=side,
                    action="buy",
                    size=max(1, contracts),
                    price=decision.price,
                    order_type="limit",
                )
                logger.info("Kalshi order placed: %s", order.order_id)
                
                tracker.record_trade(
                    token_id=snap["ticker"],
                    market_question=f"Kalshi: {snap['question'][:50]}",
                    side=decision.side,
                    action=decision.action,
                    price=decision.price,
                    size_usdc=size_usdc,
                    confidence=decision.confidence,
                    reasoning=decision.reasoning,
                )
            except Exception as e:
                logger.error("Kalshi order failed: %s", e)


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
    logger.info("Platforms: Polymarket=%s, Kalshi=%s", cfg.poly_enabled, cfg.kalshi_enabled)

    # Init Polymarket only if enabled
    poly = None
    if cfg.poly_enabled:
        poly = PolymarketClient(cfg)
        logger.info("Polymarket client initialized")
    
    # Init Kalshi only if enabled
    kalshi = None
    if cfg.kalshi_enabled:
        from kalshi_client import KalshiClient
        kalshi = KalshiClient()
        logger.info("Kalshi client initialized (authenticated=%s)", kalshi.is_authenticated)
    
    tracker = PositionTracker()
    risk = RiskManager(cfg, poly, tracker) if poly else None

    # Only init Claude engine if needed
    engine = None
    if mode in ("claude", "all"):
        engine = ClaudeEngine(cfg)

    # Only init arb scanner if needed (requires Polymarket)
    arb = None
    if mode in ("arb", "all") and poly:
        from arb_scanner import ArbScanner
        arb = ArbScanner(cfg, poly, tracker)

    # Only init cross-platform arb if needed (requires both platforms)
    cross = None
    if mode in ("cross", "all") and poly and kalshi:
        from cross_arb import CrossPlatformArb
        cross = CrossPlatformArb(cfg, poly, tracker)

    # Only init market maker if needed
    mm = None
    kalshi_mm = None
    if mode in ("mm", "all"):
        if poly:
            from market_maker import MarketMaker
            mm = MarketMaker(cfg, poly, tracker)
        if kalshi:
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

                # ── Claude/Cross strategies ─────
                # In 'claude' mode, always run every poll interval
                # In 'arb/mm' modes, run less frequently
                run_claude_now = mode in ("claude", "all") or (arb_counter >= max(1, cfg.poll_interval // max(cfg.arb_scan_interval, 1)))
                
                if run_claude_now:
                    arb_counter = 0

                    if cross:
                        n = cross.run_scan_cycle(cfg.dry_run)
                        if n:
                            logger.info("Cross-platform arb executed %d trades", n)

                    if engine:
                        # Run Claude analysis on enabled platforms
                        if poly:
                            run_cycle(poly, engine, risk, tracker, cfg)
                        if kalshi:
                            run_kalshi_cycle(kalshi, engine, tracker, cfg)
                else:
                    arb_counter += 1

                # Log position summary
                tracker.log_summary()

                backoff = cfg.arb_scan_interval if (arb or mm or kalshi_mm) else cfg.poll_interval
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
