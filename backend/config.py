from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str | None = None, required: bool = True) -> str:
    val = os.getenv(key, default)
    if required and not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val  # type: ignore[return-value]


@dataclass(frozen=True)
class Config:
    # Anthropic
    anthropic_api_key: str = _env("ANTHROPIC_API_KEY")

    # Polymarket
    poly_host: str = _env("POLYMARKET_HOST", "https://clob.polymarket.com")
    poly_chain_id: int = int(_env("POLYMARKET_CHAIN_ID", "137"))
    poly_private_key: str = _env("POLYMARKET_PRIVATE_KEY")
    poly_funder: str = _env("POLYMARKET_FUNDER", "", required=False)
    poly_signature_type: int = int(_env("POLYMARKET_SIGNATURE_TYPE", "0"))
    poly_api_key: str = _env("POLYMARKET_API_KEY", "", required=False)
    poly_api_secret: str = _env("POLYMARKET_API_SECRET", "", required=False)
    poly_passphrase: str = _env("POLYMARKET_PASSPHRASE", "", required=False)

    # Risk
    max_position_usdc: float = float(_env("MAX_POSITION_USDC", "100.0"))
    max_order_usdc: float = float(_env("MAX_ORDER_USDC", "25.0"))
    max_open_orders: int = int(_env("MAX_OPEN_ORDERS", "5"))
    min_liquidity_usdc: float = float(_env("MIN_LIQUIDITY_USDC", "500.0"))

    # Bot
    poll_interval: int = int(_env("POLL_INTERVAL_SECONDS", "60"))
    dry_run: bool = _env("DRY_RUN", "true").lower() == "true"
    log_level: str = _env("LOG_LEVEL", "INFO")
    # Modes: "claude" (AI directional), "arb" (YES+NO mispricing),
    #        "cross" (Kalshi cross-platform), "mm" (market making), "all"
    bot_mode: str = _env("BOT_MODE", "all")
    # Claude AI model (claude-3-haiku-20240307 is cheapest, claude-sonnet-4-20250514 is best)
    claude_model: str = _env("CLAUDE_MODEL", "claude-sonnet-4-20250514", required=False)
    # Platform enables
    poly_enabled: bool = _env("POLY_ENABLED", "true", required=False).lower() == "true"
    kalshi_enabled: bool = _env("KALSHI_ENABLED", "true", required=False).lower() == "true"

    # Arbitrage scanner
    arb_fee_buffer: float = float(_env("ARB_FEE_BUFFER", "0.02"))
    arb_min_profit_pct: float = float(_env("ARB_MIN_PROFIT_PCT", "0.5"))
    arb_max_usdc: float = float(_env("ARB_MAX_USDC", "25.0"))
    arb_scan_interval: int = int(_env("ARB_SCAN_INTERVAL", "5"))

    # Market making
    mm_max_usdc: float = float(_env("MM_MAX_USDC", "25.0"))
    mm_max_markets: int = int(_env("MM_MAX_MARKETS", "3"))

    # API
    api_port: int = int(_env("API_PORT", "8080"))
    api_secret: str = _env("API_SECRET", "", required=False)
    cors_origins: str = _env("CORS_ORIGINS", "*", required=False)
