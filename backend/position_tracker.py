"""Track active positions, executed trades, and total exposure."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

POSITIONS_FILE = Path(__file__).parent / "positions.json"


class Position(BaseModel):
    id: str = Field(description="Unique position ID (token_id + timestamp)")
    token_id: str
    market_question: str
    side: str  # BUY or SELL
    action: str  # Original action (BUY YES, SELL NO, etc.)
    entry_price: float
    size_usdc: float
    shares: float
    confidence: float
    reasoning: str
    opened_at: str
    status: str = "OPEN"  # OPEN or CLOSED
    closed_at: str | None = None
    exit_price: float | None = None
    pnl: float | None = None


class PositionTracker:
    """Persists positions to a JSON file and provides summaries."""

    def __init__(self, path: Path = POSITIONS_FILE) -> None:
        self._path = path
        self._positions: list[Position] = self._load()

    # ── Persistence ──────────────────────────────────────────────────────

    def _load(self) -> list[Position]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text())
            return [Position.model_validate(p) for p in data]
        except Exception as exc:
            logger.error("Failed to load positions file: %s", exc)
            return []

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps([p.model_dump() for p in self._positions], indent=2))
        tmp.replace(self._path)

    # ── Record trades ────────────────────────────────────────────────────

    def record_trade(
        self,
        token_id: str,
        market_question: str,
        side: str,
        action: str,
        price: float,
        size_usdc: float,
        confidence: float,
        reasoning: str,
    ) -> Position:
        now = datetime.now(timezone.utc).isoformat()
        shares = size_usdc / price if price > 0 else 0.0
        pos = Position(
            id=f"{token_id[:12]}_{int(datetime.now(timezone.utc).timestamp())}",
            token_id=token_id,
            market_question=market_question,
            side=side,
            action=action,
            entry_price=price,
            size_usdc=size_usdc,
            shares=shares,
            confidence=confidence,
            reasoning=reasoning,
            opened_at=now,
        )
        self._positions.append(pos)
        self._save()
        logger.info(
            "Position opened: %s %s $%.2f @ %.4f (%s)",
            side, token_id[:12], size_usdc, price, market_question[:50],
        )
        return pos

    def close_position(self, position_id: str, exit_price: float) -> Position | None:
        for pos in self._positions:
            if pos.id == position_id and pos.status == "OPEN":
                pos.status = "CLOSED"
                pos.closed_at = datetime.now(timezone.utc).isoformat()
                pos.exit_price = exit_price
                if pos.side == "BUY":
                    pos.pnl = (exit_price - pos.entry_price) * pos.shares
                else:
                    pos.pnl = (pos.entry_price - exit_price) * pos.shares
                self._save()
                logger.info("Position closed: %s P&L=$%.2f", position_id, pos.pnl)
                return pos
        return None

    # ── Queries ──────────────────────────────────────────────────────────

    @property
    def open_positions(self) -> list[Position]:
        return [p for p in self._positions if p.status == "OPEN"]

    @property
    def closed_positions(self) -> list[Position]:
        return [p for p in self._positions if p.status == "CLOSED"]

    @property
    def all_positions(self) -> list[Position]:
        return list(self._positions)

    def total_exposure(self) -> float:
        """Sum of size_usdc for all OPEN positions."""
        return sum(p.size_usdc for p in self.open_positions)

    def exposure_for_token(self, token_id: str) -> float:
        """Exposure on a specific token."""
        return sum(
            p.size_usdc for p in self.open_positions if p.token_id == token_id
        )

    def total_realized_pnl(self) -> float:
        return sum(p.pnl for p in self.closed_positions if p.pnl is not None)

    # ── Display ──────────────────────────────────────────────────────────

    def summary(self) -> str:
        """Human-readable summary of all positions."""
        open_pos = self.open_positions
        closed_pos = self.closed_positions

        lines = [
            "═" * 60,
            "  POSITION TRACKER",
            "═" * 60,
            f"  Open positions:  {len(open_pos)}",
            f"  Total exposure:  ${self.total_exposure():.2f}",
            f"  Closed trades:   {len(closed_pos)}",
            f"  Realized P&L:    ${self.total_realized_pnl():.2f}",
            "─" * 60,
        ]

        if open_pos:
            lines.append("  OPEN POSITIONS:")
            for i, p in enumerate(open_pos, 1):
                lines.append(
                    f"  {i}. {p.side} ${p.size_usdc:.2f} @ {p.entry_price:.4f}"
                    f"  conf={p.confidence:.0%}"
                )
                lines.append(f"     {p.market_question[:55]}")
                lines.append(f"     opened {p.opened_at[:19]}Z  token={p.token_id[:16]}…")
                lines.append("")
        else:
            lines.append("  No open positions.")

        if closed_pos:
            lines.append("  RECENT CLOSED:")
            for p in closed_pos[-5:]:
                pnl_str = f"${p.pnl:+.2f}" if p.pnl is not None else "n/a"
                lines.append(
                    f"  • {p.side} ${p.size_usdc:.2f} → {pnl_str}  {p.market_question[:40]}"
                )

        lines.append("═" * 60)
        return "\n".join(lines)

    def log_summary(self) -> None:
        """Log the position summary."""
        for line in self.summary().split("\n"):
            logger.info(line)
