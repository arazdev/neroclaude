"""Claude-powered trading decision engine using structured outputs."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict

import anthropic
from pydantic import BaseModel, Field

from config import Config
from polymarket_client import MarketSnapshot

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
    """Asks Claude for a structured BUY / SELL / HOLD decision."""

    MAX_TOKENS = 1024

    SYSTEM_PROMPT = """\
You are a quantitative prediction-market analyst. You receive snapshots of \
Polymarket binary-outcome markets and decide whether to BUY YES, BUY NO, \
SELL YES, SELL NO, or HOLD for each market.

Rules:
- Only recommend a trade when the market price materially diverges from your \
  estimated probability.
- Prefer limit orders at the best bid/ask when spreads are wide.
- If the spread is < 0.02 or liquidity is very low, prefer HOLD.
- Never exceed the position budget provided.
- Return EXACTLY ONE JSON object matching the schema — no extra text."""

    def __init__(self, cfg: Config) -> None:
        self.client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        self.cfg = cfg
        self.max_order_usdc = cfg.max_order_usdc
        self.model = cfg.claude_model or "claude-sonnet-4-20250514"

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
        return (
            "Analyze this Polymarket market and submit your trading decision.\n"
            f"Your maximum order size is ${self.max_order_usdc:.0f} USDC.\n\n"
            f"```json\n{json.dumps(data, indent=2)}\n```"
        )
