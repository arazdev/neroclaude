"""Shared type definitions for NEROCLAUDE bot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class MarketSnapshot:
    """Universal market snapshot for any platform."""
    condition_id: str
    question: str
    token_id_yes: str
    token_id_no: str
    outcome_yes_price: float
    outcome_no_price: float
    volume_24h: float
    liquidity: float
    best_bid: float
    best_ask: float
    spread: float
    end_date: str = ""
    platform: str = "unknown"  # "kalshi", "polymarket_us", etc.
