"""Claude-powered trading decision engine using structured outputs + game theory."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict

import anthropic
from pydantic import BaseModel, Field

from config import Config
from models import MarketSnapshot
from strategy import (
    StrategyAnalyzer,
    calculate_ev,
    detect_mispricing,
    recommend_order_strategy,
    is_longshot_trap,
)

logger = logging.getLogger(__name__)


# ── Structured output schema ────────────────────────────────────────────────


class TradeDecision(BaseModel):
    action: str = Field(description="BUY, SELL, or HOLD")
    token_id: str = Field(description="Token ID to trade (empty if HOLD)")
    side: str = Field(description="BUY or SELL (empty if HOLD)")
    price: float = Field(description="Limit price 0.01–0.99, or 0.0 for market order / HOLD")
    size_usdc: float = Field(description="Dollar amount to risk, 0.0 if HOLD")
    confidence: float = Field(description="0.0–1.0 confidence in this decision")
    reasoning: str = Field(description="Brief explanation of the decision")


class ClaudeEngine:
    """Asks Claude for a structured BUY / SELL / HOLD decision with game theory."""

    MAX_TOKENS = 1024

    SYSTEM_PROMPT = """\
You are a quantitative prediction-market analyst using GAME THEORY formulas \
tested on 72 million trades. You receive market snapshots WITH pre-calculated \
strategy analysis and decide whether to BUY YES, BUY NO, or HOLD.

CRITICAL RULES (from empirical data):

1. EXPECTED VALUE: Only trade when EV > 0. The strategy analysis shows EV.

2. LONGSHOT BIAS: Contracts <10¢ are OVERPRICED by 16-57% historically.
   - NEVER buy YES on longshots (<10¢) unless you have exceptional edge
   - Prefer BUY NO (selling longshots) on cheap contracts
   - Contracts at 1¢ return only 43¢ per dollar historically

3. NEAR-CERTAINTIES: Contracts >90¢ are UNDERPRICED historically.
   - BUY YES on near-certainties has +2-5% edge

4. KELLY CRITERION: Use the Kelly bet size provided. NEVER exceed it.
   - The analysis uses quarter-Kelly (conservative)
   - Max 5% of bankroll per position

5. MAKER STRATEGY: Prefer LIMIT orders over MARKET orders.
   - Makers gain +1.12% per trade on average
   - Takers lose -1.12% per trade on average
   - Only use MARKET orders if time-sensitive with >5% edge

6. MINIMUM EDGE: Only trade when your edge > 3% (3 percentage points).

Return EXACTLY ONE JSON decision. Trust the pre-calculated strategy analysis."""

    def __init__(self, cfg: Config) -> None:
        self.client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        self.cfg = cfg
        self.max_order_usdc = cfg.max_order_usdc
        self.model = cfg.claude_model or "claude-sonnet-4-20250514"
        
        # Initialize strategy analyzer with bankroll
        self.strategy = StrategyAnalyzer(
            bankroll=cfg.max_order_usdc * 20,  # Estimate bankroll as 20x max order
            kelly_fraction=0.25,
            max_position_pct=0.05,
            min_edge_required=0.03
        )

    def decide(self, snapshot: MarketSnapshot) -> TradeDecision:
        user_msg = self._build_prompt(snapshot)

        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.MAX_TOKENS,
            system=self.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            tools=[
                {
                    "name": "submit_decision",
                    "description": "Submit a trading decision for this market.",
                    "input_schema": TradeDecision.model_json_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": "submit_decision"},
        )

        # Extract tool-use block
        for block in resp.content:
            if block.type == "tool_use" and block.name == "submit_decision":
                decision = TradeDecision.model_validate(block.input)
                logger.info(
                    "Claude decision: %s %s confidence=%.2f | %s",
                    decision.action,
                    snapshot.question[:60],
                    decision.confidence,
                    decision.reasoning[:120],
                )
                return decision

        # Fallback: HOLD
        logger.warning("No tool_use block in Claude response — defaulting to HOLD")
        return TradeDecision(
            action="HOLD",
            token_id="",
            side="",
            price=0.0,
            size_usdc=0.0,
            confidence=0.0,
            reasoning="Could not parse Claude response.",
        )

    # ── Prompt builder ───────────────────────────────────────────────────

    def _build_prompt(self, snap: MarketSnapshot) -> str:
        # Run strategy analysis
        # Claude will estimate probability - we use market price as baseline
        # and let Claude adjust based on its analysis
        
        # Detect mispricing category
        mispricing = detect_mispricing(snap.outcome_yes_price)
        
        # Get order strategy recommendation
        order_strat = recommend_order_strategy(
            spread=snap.spread,
            your_edge=0.0  # Claude will determine edge
        )
        
        # Check if longshot trap
        longshot_warning = ""
        if is_longshot_trap(snap.outcome_yes_price):
            longshot_warning = (
                "\n⚠️ LONGSHOT WARNING: This contract is <10¢. "
                "Historical data shows 16-57% overpricing. "
                "Prefer BUY NO (sell the longshot) unless you have exceptional edge."
            )
        
        # Build market data
        data = {
            "question": snap.question,
            "yes_price": snap.outcome_yes_price,
            "no_price": snap.outcome_no_price,
            "best_bid": snap.best_bid,
            "best_ask": snap.best_ask,
            "spread": round(snap.spread, 4),
            "volume_24h": snap.volume_24h,
            "liquidity": snap.liquidity,
            "token_id_yes": snap.token_id_yes,
            "token_id_no": snap.token_id_no,
            "end_date": snap.end_date,
            "max_order_usdc": self.max_order_usdc,
        }
        
        # Strategy analysis section
        strategy_analysis = f"""
STRATEGY ANALYSIS (pre-calculated):
- Price Category: {mispricing.category}
- Historical Mispricing: {mispricing.estimated_mispricing_pct}%
- Expected Return per $1: ${mispricing.historical_return_per_dollar:.2f}
- Recommended Order Type: {order_strat.order_type}
- Maker Edge: +{order_strat.maker_edge_pct:.2f}% per trade
{longshot_warning}

KELLY SIZING (quarter-Kelly, max 5%):
- Your max bet: ${self.max_order_usdc:.0f}
- Use limit orders at best_bid (for YES) or 1-best_ask (for NO)

YOUR TASK:
1. Estimate the TRUE probability of this event
2. Compare to market price to find edge
3. If edge > 3%, recommend BUY YES or BUY NO
4. If no edge or edge < 3%, recommend HOLD
5. Use LIMIT orders (maker strategy)
"""
        
        return (
            "Analyze this prediction market and submit your trading decision.\n\n"
            f"```json\n{json.dumps(data, indent=2)}\n```\n"
            f"{strategy_analysis}"
        )
