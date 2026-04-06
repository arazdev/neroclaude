"""Kalshi market maker — posts limit bids on both YES and NO to earn the spread.

Same strategy as Polymarket MM:
  - Find markets with wide spreads
  - Buy YES at (best_bid + offset)
  - Buy NO at (best_no_bid + offset)
  - Profit = $1 - (cost of YES + cost of NO)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from config import Config
from kalshi_client import KalshiClient, KalshiMarket
from position_tracker import PositionTracker

logger = logging.getLogger("kalshi_mm")


@dataclass
class KalshiMMQuote:
    """A two-sided quote for market making on Kalshi."""
    ticker: str
    title: str
    
    bid_yes_price: float  # our buy price for YES (0-1)
    bid_no_price: float   # our buy price for NO (0-1)
    total_cost: float     # bid_yes + bid_no
    spread_profit: float  # 1.0 - total_cost (per contract)
    
    contracts: int
    estimated_profit_usdc: float
    
    market_yes_price: float
    market_no_price: float


class KalshiMarketMaker:
    """Posts two-sided quotes on Kalshi to earn the bid-ask spread."""

    # Minimum spread to participate
    MIN_SPREAD = 0.04  # 4 cents (Kalshi has higher fees)
    # How much below market to place our bids
    BID_OFFSET = 0.02  # 2 cents below market
    # Minimum profit per contract after fees (~7% Kalshi fee)
    MIN_PROFIT_PER_CONTRACT = 0.02  # 2 cents

    def __init__(
        self,
        cfg: Config,
        tracker: PositionTracker,
    ) -> None:
        self.cfg = cfg
        self.kalshi = KalshiClient()
        self.tracker = tracker
        self.max_order_usdc = float(cfg.max_order_usdc)
        self.max_markets = 3

    def find_opportunities(self) -> list[KalshiMMQuote]:
        """Find Kalshi markets suitable for market making."""
        if not self.kalshi.is_authenticated:
            logger.warning("Kalshi not authenticated - skipping MM")
            return []
        
        markets = self.kalshi.get_active_markets(limit=50)
        logger.info("Kalshi MM: Fetched %d markets to scan", len(markets))
        
        # Log first 8 market tickers as sample
        sample_tickers = [m.ticker[:20] for m in markets[:8]]
        logger.info("Kalshi MM: Markets → %s%s", ", ".join(sample_tickers), "..." if len(markets) > 8 else "")
        
        quotes: list[KalshiMMQuote] = []
        skipped_tight = 0
        skipped_profit = 0
        
        for market in markets:
            yes_price = market.yes_price
            no_price = market.no_price
            spread = 1.0 - yes_price - no_price if (yes_price > 0 and no_price > 0) else 0
            if spread < 0:
                spread = abs(spread)
            
            # Log each market scan
            logger.debug(
                "  → %s | YES=%.2f NO=%.2f | spread=%.1f%%",
                market.ticker[:25], yes_price, no_price, spread * 100
            )
            
            quote = self._build_quote(market)
            if quote:
                quotes.append(quote)
                logger.info(
                    "Kalshi MM: ✓ %s spread=%.1f%% (potential profit)",
                    market.ticker[:30], quote.spread_profit * 100
                )
            elif spread < self.MIN_SPREAD:
                skipped_tight += 1
            else:
                skipped_profit += 1
        
        if skipped_tight > 0 or skipped_profit > 0:
            logger.info(
                "Kalshi MM: Skipped %d (tight spread <%.0f%%) + %d (low profit)",
                skipped_tight, self.MIN_SPREAD * 100, skipped_profit
            )
        
        # Sort by profit potential
        quotes.sort(key=lambda q: q.spread_profit, reverse=True)
        return quotes[:self.max_markets]

    def _build_quote(self, market: KalshiMarket) -> KalshiMMQuote | None:
        """Build a two-sided quote for a Kalshi market."""
        yes_price = market.yes_price
        no_price = market.no_price
        
        # Kalshi prices are already 0-1 dollar scale
        if yes_price <= 0 or no_price <= 0:
            return None
        
        # Check spread (1 - yes - no = spread)
        spread = 1.0 - yes_price - no_price
        if spread < 0:
            spread = abs(spread)  # markets can be slightly above 1.0
        
        # Skip tight spreads
        if spread < self.MIN_SPREAD:
            return None
        
        # Our bid prices (below market to increase fill probability)
        our_yes_bid = max(0.01, yes_price - self.BID_OFFSET)
        our_no_bid = max(0.01, no_price - self.BID_OFFSET)
        
        # Total cost and profit calculation
        total_cost = our_yes_bid + our_no_bid
        profit_per_contract = 1.0 - total_cost
        
        # Skip if profit too low (after ~7% Kalshi fees)
        if profit_per_contract < self.MIN_PROFIT_PER_CONTRACT:
            return None
        
        # Skip if total cost > $1 (would lose money)
        if total_cost >= 1.0:
            return None
        
        # Calculate contract size based on order limit
        # Each contract costs $total_cost, so contracts = budget / cost
        contracts = int(self.max_order_usdc / total_cost)
        contracts = max(1, min(contracts, 50))  # Kalshi limit
        
        estimated_profit = contracts * profit_per_contract
        
        return KalshiMMQuote(
            ticker=market.ticker,
            title=market.title[:60],
            bid_yes_price=our_yes_bid,
            bid_no_price=our_no_bid,
            total_cost=total_cost,
            spread_profit=profit_per_contract,
            contracts=contracts,
            estimated_profit_usdc=estimated_profit,
            market_yes_price=yes_price,
            market_no_price=no_price,
        )

    def run_cycle(self, dry_run: bool = True) -> int:
        """Execute one MM cycle on Kalshi. Returns number of quotes placed."""
        quotes = self.find_opportunities()
        
        if not quotes:
            logger.info("Kalshi MM: Scanned markets - no spread opportunities found")
            return 0
        
        placed = 0
        for quote in quotes:
            logger.info(
                "Kalshi MM: %s | YES@%.2f + NO@%.2f = $%.2f | profit=%.1f%% ($%.2f)",
                quote.ticker,
                quote.bid_yes_price,
                quote.bid_no_price,
                quote.total_cost,
                quote.spread_profit * 100,
                quote.estimated_profit_usdc,
            )
            
            if dry_run:
                # Record simulated trades
                self.tracker.record_trade(
                    token_id=f"kalshi_{quote.ticker}_yes",
                    market_question=f"[DRY] Kalshi MM YES: {quote.title}",
                    side="BUY",
                    action="MM_BID_YES",
                    price=quote.bid_yes_price,
                    size_usdc=quote.contracts * quote.bid_yes_price,
                    confidence=0.8,
                    reasoning=f"Kalshi MM: spread={quote.spread_profit:.2%}, profit=${quote.estimated_profit_usdc:.2f}",
                )
                self.tracker.record_trade(
                    token_id=f"kalshi_{quote.ticker}_no",
                    market_question=f"[DRY] Kalshi MM NO: {quote.title}",
                    side="BUY",
                    action="MM_BID_NO",
                    price=quote.bid_no_price,
                    size_usdc=quote.contracts * quote.bid_no_price,
                    confidence=0.8,
                    reasoning=f"Kalshi MM: spread={quote.spread_profit:.2%}, profit=${quote.estimated_profit_usdc:.2f}",
                )
                placed += 2
            else:
                # Place real orders
                try:
                    self.kalshi.create_order(
                        ticker=quote.ticker,
                        side="yes",
                        action="buy",
                        size=quote.contracts,
                        price=quote.bid_yes_price,
                        order_type="limit",
                    )
                    self.tracker.record_trade(
                        token_id=f"kalshi_{quote.ticker}_yes",
                        market_question=f"Kalshi MM YES: {quote.title}",
                        side="BUY",
                        action="MM_BID_YES",
                        price=quote.bid_yes_price,
                        size_usdc=quote.contracts * quote.bid_yes_price,
                        confidence=0.8,
                        reasoning=f"Kalshi MM: spread={quote.spread_profit:.2%}",
                    )
                    placed += 1
                except Exception as e:
                    logger.error("Failed to place Kalshi YES order: %s", e)
                
                try:
                    self.kalshi.create_order(
                        ticker=quote.ticker,
                        side="no",
                        action="buy",
                        size=quote.contracts,
                        price=quote.bid_no_price,
                        order_type="limit",
                    )
                    self.tracker.record_trade(
                        token_id=f"kalshi_{quote.ticker}_no",
                        market_question=f"Kalshi MM NO: {quote.title}",
                        side="BUY",
                        action="MM_BID_NO",
                        price=quote.bid_no_price,
                        size_usdc=quote.contracts * quote.bid_no_price,
                        confidence=0.8,
                        reasoning=f"Kalshi MM: spread={quote.spread_profit:.2%}",
                    )
                    placed += 1
                except Exception as e:
                    logger.error("Failed to place Kalshi NO order: %s", e)
        
        return placed
