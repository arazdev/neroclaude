"""
Game Theory Strategy Module — Based on 72M trade analysis.

Implements 5 key formulas:
1. Expected Value (EV) calculator
2. Longshot bias detection (mispricing)
3. Kelly Criterion position sizing
4. Bayesian probability updater
5. Maker vs Taker strategy

TRADING STYLE: Short-term trades only
- No multi-year positions
- Quick entry/exit
- Set MAX_DAYS_TO_EXPIRATION in .env (1=same-day, 5=weekly, 30=monthly)

Source: @0xMovez analysis of 72.1M trades on Kalshi/Polymarket
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List

logger = logging.getLogger(__name__)

# Maximum days until expiration - configurable via .env
# Set MAX_DAYS_TO_EXPIRATION=1 for same-day, 5 for weekly, 30 for monthly
MAX_DAYS_TO_EXPIRATION = int(os.getenv("MAX_DAYS_TO_EXPIRATION", "30"))


# ══════════════════════════════════════════════════════════════════════════════
# 1. EXPECTED VALUE CALCULATOR
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EVResult:
    """Expected value calculation result."""
    ev_per_contract: float
    roi_percent: float
    edge: float  # your_prob - market_prob
    verdict: str  # "BUY YES ✅", "BUY NO ✅", "SKIP ❌"
    side: str  # "YES", "NO", or "NONE"


def calculate_ev(market_price: float, your_probability: float) -> EVResult:
    """
    Calculate expected value for a YES contract.
    
    Args:
        market_price: current YES price (0.01 to 0.99)
        your_probability: your estimated true probability (0.0 to 1.0)
    
    Returns:
        EVResult with EV, ROI, and trade recommendation
    """
    # YES side calculation
    cost_yes = market_price
    payout_yes = 1.0 - market_price  # profit if YES wins
    
    ev_yes = (your_probability * payout_yes) - ((1 - your_probability) * cost_yes)
    roi_yes = (ev_yes / cost_yes * 100) if cost_yes > 0 else 0
    
    # NO side calculation
    no_price = 1.0 - market_price
    cost_no = no_price
    payout_no = market_price  # profit if NO wins (YES loses)
    no_prob = 1 - your_probability
    
    ev_no = (no_prob * payout_no) - ((1 - no_prob) * cost_no)
    roi_no = (ev_no / cost_no * 100) if cost_no > 0 else 0
    
    edge = your_probability - market_price
    
    # Determine best action
    if ev_yes > 0 and ev_yes >= ev_no:
        return EVResult(
            ev_per_contract=round(ev_yes, 4),
            roi_percent=round(roi_yes, 2),
            edge=round(edge, 4),
            verdict="BUY YES ✅",
            side="YES"
        )
    elif ev_no > 0:
        return EVResult(
            ev_per_contract=round(ev_no, 4),
            roi_percent=round(roi_no, 2),
            edge=round(-edge, 4),  # edge for NO side
            verdict="BUY NO ✅",
            side="NO"
        )
    else:
        return EVResult(
            ev_per_contract=round(max(ev_yes, ev_no), 4),
            roi_percent=round(max(roi_yes, roi_no), 2),
            edge=round(edge, 4),
            verdict="SKIP ❌",
            side="NONE"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 2. LONGSHOT BIAS / MISPRICING DETECTION
# ══════════════════════════════════════════════════════════════════════════════

@dataclass 
class MispricingResult:
    """Mispricing analysis result."""
    price: float
    category: str  # "LONGSHOT", "NEAR_CERTAINTY", "FAIR"
    estimated_mispricing_pct: float
    recommended_action: str
    historical_return_per_dollar: float


# Empirical data from 72M trades (Becker research)
MISPRICING_TABLE = {
    # price_range: (actual_win_rate, return_per_dollar_taker)
    0.01: (0.0043, 0.43),   # 1¢ contracts win 0.43% vs implied 1%
    0.05: (0.0418, 0.84),   # 5¢ contracts win 4.18% vs implied 5%
    0.10: (0.085, 0.85),    # 10¢ contracts
    0.20: (0.185, 0.93),    # 20¢ - getting closer to fair
    0.50: (0.50, 1.00),     # 50¢ - perfectly calibrated
    0.80: (0.82, 1.02),     # 80¢ - slight edge for YES
    0.90: (0.92, 1.02),     # 90¢ - underpriced
    0.95: (0.97, 1.02),     # 95¢ - underpriced near-certainties
}


def detect_mispricing(yes_price: float) -> MispricingResult:
    """
    Detect longshot bias and mispricing based on historical data.
    
    Key insight: Contracts <10¢ are systematically OVERPRICED (longshot bias)
                 Contracts >90¢ are systematically UNDERPRICED
    
    Args:
        yes_price: current YES price (0.01 to 0.99)
    
    Returns:
        MispricingResult with category and recommended action
    """
    if yes_price < 0.10:
        # LONGSHOT - historically overpriced by 16-57%
        mispricing = -16 * (0.10 - yes_price) / 0.10  # scales from -16% at 10¢ to worse
        if yes_price < 0.05:
            mispricing = -30 - (0.05 - yes_price) * 540  # gets much worse below 5¢
        
        return MispricingResult(
            price=yes_price,
            category="LONGSHOT",
            estimated_mispricing_pct=round(mispricing, 1),
            recommended_action="SELL YES / BUY NO (longshot bias)",
            historical_return_per_dollar=0.43 + (yes_price - 0.01) * 4.5
        )
    
    elif yes_price > 0.90:
        # NEAR CERTAINTY - historically underpriced
        mispricing = 2.0 + (yes_price - 0.90) * 5  # ~2-5% underpriced
        return MispricingResult(
            price=yes_price,
            category="NEAR_CERTAINTY",
            estimated_mispricing_pct=round(mispricing, 1),
            recommended_action="BUY YES (near-certainty edge)",
            historical_return_per_dollar=1.02
        )
    
    else:
        # FAIR RANGE - market is well-calibrated between 10-90¢
        return MispricingResult(
            price=yes_price,
            category="FAIR",
            estimated_mispricing_pct=0.0,
            recommended_action="Trade on edge only (EV-based)",
            historical_return_per_dollar=0.95 + yes_price * 0.1
        )


# ══════════════════════════════════════════════════════════════════════════════
# 3. KELLY CRITERION POSITION SIZING
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class KellyResult:
    """Kelly criterion calculation result."""
    side: str  # "YES", "NO", or "NO_BET"
    full_kelly_pct: float
    adjusted_kelly_pct: float  # after fraction applied
    bet_amount: float
    contracts: int
    max_profit: float
    max_loss: float
    risk_reward_ratio: float
    reason: str


class KellyCalculator:
    """
    Kelly Criterion position sizer for prediction markets.
    
    CRITICAL: Never use full Kelly! Use quarter-Kelly (25%) for:
    - Manageable drawdowns
    - Steady growth
    - Psychological sustainability
    """
    
    def __init__(
        self,
        bankroll: float,
        kelly_fraction: float = 0.25,  # Quarter Kelly default
        max_bet_pct: float = 0.05,     # Hard cap 5% per position
        min_edge_required: float = 0.03  # 3% minimum edge to bet
    ):
        self.bankroll = bankroll
        self.fraction = kelly_fraction
        self.max_bet_pct = max_bet_pct
        self.min_edge = min_edge_required
    
    def calculate(
        self,
        market_price: float,
        your_probability: float,
        is_correlated: bool = False
    ) -> KellyResult:
        """
        Calculate optimal bet size using Kelly Criterion.
        
        Args:
            market_price: YES contract price (0.01-0.99)
            your_probability: your estimated probability
            is_correlated: if True, halves position (correlated bets)
        
        Returns:
            KellyResult with optimal position size
        """
        # Calculate edge
        edge = your_probability - market_price
        
        # Check YES side
        b_yes = (1 - market_price) / market_price  # net odds for YES
        q_yes = 1 - your_probability
        
        full_kelly_yes = (your_probability * b_yes - q_yes) / b_yes
        
        # Check NO side
        no_price = 1 - market_price
        no_prob = 1 - your_probability
        b_no = market_price / no_price  # net odds for NO
        
        full_kelly_no = (no_prob * b_no - your_probability) / b_no
        
        # Determine best side
        if full_kelly_yes > 0 and full_kelly_yes >= full_kelly_no:
            return self._build_result(
                full_kelly_yes, market_price, "YES", 
                your_probability, is_correlated
            )
        elif full_kelly_no > 0:
            return self._build_result(
                full_kelly_no, no_price, "NO",
                no_prob, is_correlated
            )
        else:
            return KellyResult(
                side="NO_BET",
                full_kelly_pct=0,
                adjusted_kelly_pct=0,
                bet_amount=0,
                contracts=0,
                max_profit=0,
                max_loss=0,
                risk_reward_ratio=0,
                reason=f"No edge: your {your_probability*100:.0f}% vs market {market_price*100:.0f}%"
            )
    
    def _build_result(
        self,
        full_kelly: float,
        price: float,
        side: str,
        win_prob: float,
        is_correlated: bool
    ) -> KellyResult:
        """Build Kelly result with all adjustments applied."""
        # Apply Kelly fraction
        adjusted = full_kelly * self.fraction
        
        # Halve for correlated positions
        if is_correlated:
            adjusted *= 0.5
        
        # Apply hard cap
        adjusted = min(adjusted, self.max_bet_pct)
        
        # Calculate bet details
        bet_amount = round(self.bankroll * adjusted, 2)
        contracts = int(bet_amount / price) if price > 0 else 0
        max_profit = round(contracts * (1 - price), 2)
        max_loss = round(contracts * price, 2)
        risk_reward = round(max_profit / max_loss, 2) if max_loss > 0 else 0
        
        return KellyResult(
            side=side,
            full_kelly_pct=round(full_kelly * 100, 1),
            adjusted_kelly_pct=round(adjusted * 100, 2),
            bet_amount=bet_amount,
            contracts=contracts,
            max_profit=max_profit,
            max_loss=max_loss,
            risk_reward_ratio=risk_reward,
            reason=f"Edge detected on {side}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 4. BAYESIAN PROBABILITY UPDATER
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class BayesianUpdate:
    """Single Bayesian update record."""
    event: str
    prior: float
    posterior: float
    shift: float  # in percentage points
    likelihood_ratio: float


class BayesianTracker:
    """
    Track probability updates as new evidence arrives.
    
    Use likelihood ratios for quick updates:
    - LR > 1: evidence supports YES
    - LR < 1: evidence supports NO
    - LR = 1: neutral evidence
    """
    
    def __init__(self, prior: float, market_name: str = "Unnamed"):
        self.prior = prior
        self.current = prior
        self.market = market_name
        self.history: List[BayesianUpdate] = [
            BayesianUpdate(
                event="Initial prior",
                prior=prior,
                posterior=prior,
                shift=0,
                likelihood_ratio=1.0
            )
        ]
    
    def update(
        self,
        p_evidence_if_true: float,
        p_evidence_if_false: float,
        evidence_name: str = ""
    ) -> "BayesianTracker":
        """
        Apply Bayesian update given new evidence.
        
        Args:
            p_evidence_if_true: P(evidence | YES wins)
            p_evidence_if_false: P(evidence | NO wins)
            evidence_name: description of the evidence
        
        Returns:
            self (for chaining)
        """
        # Bayes' theorem
        numerator = p_evidence_if_true * self.current
        denominator = numerator + (p_evidence_if_false * (1 - self.current))
        
        posterior = numerator / denominator if denominator > 0 else self.current
        shift = posterior - self.current
        
        lr = p_evidence_if_true / p_evidence_if_false if p_evidence_if_false > 0 else float('inf')
        
        self.history.append(BayesianUpdate(
            event=evidence_name,
            prior=round(self.current, 3),
            posterior=round(posterior, 3),
            shift=round(shift, 3),
            likelihood_ratio=round(lr, 2)
        ))
        
        self.current = posterior
        return self
    
    def update_with_lr(self, likelihood_ratio: float, evidence_name: str = "") -> "BayesianTracker":
        """
        Quick update using just likelihood ratio.
        
        Common LRs:
        - Strong evidence FOR: LR = 3-10
        - Moderate evidence FOR: LR = 2-3
        - Weak evidence: LR = 1-2
        - Against: LR < 1
        """
        # Convert LR to P(E|H) and P(E|~H) assuming P(E|~H) = 0.5
        p_if_false = 0.5
        p_if_true = likelihood_ratio * p_if_false
        
        # Clamp to valid probability
        p_if_true = min(p_if_true, 0.99)
        
        return self.update(p_if_true, p_if_false, evidence_name)
    
    def edge_vs_market(self, market_price: float) -> dict:
        """Compare your posterior to current market price."""
        diff = self.current - market_price
        
        if abs(diff) < 0.03:
            return {
                "has_edge": False,
                "side": "NONE",
                "edge": 0,
                "message": "No edge (within 3pp of market)"
            }
        
        side = "YES" if diff > 0 else "NO"
        return {
            "has_edge": True,
            "side": side,
            "edge": abs(diff),
            "message": f"Edge on {side}: your {self.current*100:.0f}% vs market {market_price*100:.0f}%"
        }
    
    def summary(self) -> str:
        """Get formatted summary of all updates."""
        lines = [f"\n=== {self.market} ==="]
        for h in self.history:
            if h.event == "Initial prior":
                lines.append(f"  Initial: {h.posterior*100:.0f}%")
            else:
                direction = "+" if h.shift > 0 else ""
                lines.append(
                    f"  {h.event}: {h.prior*100:.0f}% → {h.posterior*100:.0f}% "
                    f"({direction}{h.shift*100:.1f}pp, LR={h.likelihood_ratio})"
                )
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# 5. MAKER VS TAKER STRATEGY
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class OrderStrategy:
    """Recommended order strategy."""
    order_type: str  # "LIMIT" or "MARKET"
    reason: str
    maker_edge_pct: float  # historical maker advantage
    recommended_price: Optional[float]  # for limit orders
    urgency: str  # "LOW", "MEDIUM", "HIGH"


# Empirical data: Makers gain +1.12%, Takers lose -1.12% per trade
MAKER_EDGE = 0.0112  # 1.12%
TAKER_LOSS = -0.0112

# Category-specific maker ratios (from Nash equilibrium analysis)
OPTIMAL_MAKER_RATIO = {
    "politics": 0.70,      # 70% maker
    "finance": 0.75,       # 75% maker (most efficient)
    "crypto": 0.65,        # 65% maker
    "sports": 0.60,        # 60% maker (more emotional)
    "entertainment": 0.55, # 55% maker (most emotional)
    "default": 0.65
}


def recommend_order_strategy(
    market_category: str = "default",
    spread: float = 0.02,
    your_edge: float = 0.0,
    time_sensitive: bool = False
) -> OrderStrategy:
    """
    Recommend LIMIT vs MARKET order based on game theory.
    
    Key insight: Post-2024, optimal strategy is 65-70% maker.
    Use limit orders unless urgency is high.
    
    Args:
        market_category: type of market (politics, crypto, etc.)
        spread: current bid-ask spread
        your_edge: your estimated edge (positive = have edge)
        time_sensitive: True if news is breaking
    
    Returns:
        OrderStrategy with recommendation
    """
    maker_ratio = OPTIMAL_MAKER_RATIO.get(market_category.lower(), 0.65)
    
    # Always use LIMIT unless compelling reason not to
    if time_sensitive and your_edge > 0.05:
        return OrderStrategy(
            order_type="MARKET",
            reason="Time-sensitive with strong edge (>5%)",
            maker_edge_pct=TAKER_LOSS * 100,
            recommended_price=None,
            urgency="HIGH"
        )
    
    if spread < 0.01:
        # Very tight spread - market likely efficient
        return OrderStrategy(
            order_type="LIMIT",
            reason="Tight spread - use limit to capture maker edge",
            maker_edge_pct=MAKER_EDGE * 100,
            recommended_price=None,  # will be set by caller
            urgency="LOW"
        )
    
    if spread > 0.05:
        # Wide spread - definitely use limit
        return OrderStrategy(
            order_type="LIMIT",
            reason=f"Wide spread ({spread*100:.1f}%) - capture maker edge",
            maker_edge_pct=MAKER_EDGE * 100,
            recommended_price=None,
            urgency="LOW"
        )
    
    # Default: use limit orders (maker strategy)
    return OrderStrategy(
        order_type="LIMIT",
        reason=f"Maker strategy optimal ({maker_ratio*100:.0f}% of trades)",
        maker_edge_pct=MAKER_EDGE * 100,
        recommended_price=None,
        urgency="MEDIUM"
    )


# ══════════════════════════════════════════════════════════════════════════════
# UNIFIED STRATEGY ANALYZER
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class StrategyRecommendation:
    """Complete strategy recommendation combining all 5 formulas."""
    # From EV calculation
    ev_result: EVResult
    
    # From mispricing detection
    mispricing: MispricingResult
    
    # From Kelly sizing
    kelly_result: KellyResult
    
    # From order strategy
    order_strategy: OrderStrategy
    
    # Final recommendation
    should_trade: bool
    action: str  # "BUY YES", "BUY NO", "HOLD"
    confidence: float  # 0-1
    reasoning: str


class StrategyAnalyzer:
    """
    Unified strategy analyzer combining all 5 game theory formulas.
    
    Usage:
        analyzer = StrategyAnalyzer(bankroll=1000)
        rec = analyzer.analyze(
            market_price=0.30,
            your_probability=0.45,
            spread=0.02
        )
    """
    
    def __init__(
        self,
        bankroll: float,
        kelly_fraction: float = 0.25,
        max_position_pct: float = 0.05,
        min_edge_required: float = 0.03
    ):
        self.bankroll = bankroll
        self.kelly = KellyCalculator(
            bankroll=bankroll,
            kelly_fraction=kelly_fraction,
            max_bet_pct=max_position_pct,
            min_edge_required=min_edge_required
        )
        self.min_edge = min_edge_required
    
    def analyze(
        self,
        market_price: float,
        your_probability: float,
        spread: float = 0.02,
        market_category: str = "default",
        is_correlated: bool = False,
        time_sensitive: bool = False,
        end_date: Optional[str] = None
    ) -> StrategyRecommendation:
        """
        Run full strategy analysis using all 5 formulas.
        
        Args:
            market_price: YES contract price (0.01-0.99)
            your_probability: your estimated probability
            spread: current bid-ask spread
            market_category: type of market for maker ratio
            is_correlated: True if correlated with existing positions
            time_sensitive: True if breaking news
            end_date: market expiration date (REQUIRED for short-term filtering)
        
        Returns:
            StrategyRecommendation with complete analysis
        """
        # 0. Check expiration - REJECT long-term markets FIRST
        if end_date:
            is_rejected, reason = is_too_long_term(end_date)
            if is_rejected:
                # Create rejection recommendation with correct field names
                return StrategyRecommendation(
                    ev_result=EVResult(
                        ev_per_contract=0.0,
                        roi_percent=0.0,
                        edge=0.0,
                        side="NONE",
                        verdict="REJECT - TOO LONG TERM"
                    ),
                    mispricing=MispricingResult(
                        price=market_price,
                        category="REJECTED",
                        estimated_mispricing_pct=0.0,
                        recommended_action="REJECT",
                        historical_return_per_dollar=0.0
                    ),
                    kelly_result=KellyResult(
                        side="NO_BET",
                        full_kelly_pct=0.0,
                        adjusted_kelly_pct=0.0,
                        bet_amount=0.0,
                        contracts=0,
                        max_profit=0.0,
                        max_loss=0.0,
                        risk_reward_ratio=0.0,
                        reason=reason
                    ),
                    order_strategy=OrderStrategy(
                        order_type="REJECT",
                        reason=reason,
                        maker_edge_pct=0.0,
                        recommended_price=None,
                        urgency="NONE"
                    ),
                    should_trade=False,
                    action="REJECT",
                    confidence=0.0,
                    reasoning=f"❌ {reason}"
                )
        
        # 1. Expected Value
        ev_result = calculate_ev(market_price, your_probability)
        
        # 2. Mispricing Detection
        mispricing = detect_mispricing(market_price)
        
        # 3. Kelly Sizing
        kelly_result = self.kelly.calculate(
            market_price, your_probability, is_correlated
        )
        
        # 4. Order Strategy
        order_strategy = recommend_order_strategy(
            market_category=market_category,
            spread=spread,
            your_edge=abs(your_probability - market_price),
            time_sensitive=time_sensitive
        )
        
        # 5. Synthesize recommendation
        edge = abs(your_probability - market_price)
        
        # Decision logic
        should_trade = False
        action = "HOLD"
        confidence = 0.0
        reasons = []
        
        # Check EV
        if ev_result.side != "NONE" and ev_result.ev_per_contract > 0:
            reasons.append(f"+EV: ${ev_result.ev_per_contract}/contract ({ev_result.roi_percent}% ROI)")
            confidence += 0.3
        else:
            reasons.append("Negative EV")
        
        # Check edge threshold
        if edge >= self.min_edge:
            reasons.append(f"Edge: {edge*100:.1f}% above threshold")
            confidence += 0.3
        else:
            reasons.append(f"Edge {edge*100:.1f}% below {self.min_edge*100}% threshold")
        
        # Check mispricing
        if mispricing.category == "LONGSHOT":
            # If we're buying NO on a longshot, that's good
            if ev_result.side == "NO":
                reasons.append("Exploiting longshot bias (selling YES)")
                confidence += 0.2
            else:
                reasons.append("⚠️ Buying overpriced longshot")
                confidence -= 0.2
        elif mispricing.category == "NEAR_CERTAINTY":
            if ev_result.side == "YES":
                reasons.append("Buying underpriced near-certainty")
                confidence += 0.2
        
        # Check Kelly
        if kelly_result.side != "NO_BET" and kelly_result.bet_amount > 0:
            reasons.append(f"Kelly: ${kelly_result.bet_amount} ({kelly_result.adjusted_kelly_pct}%)")
            confidence += 0.2
        
        # Final decision
        confidence = max(0, min(1, confidence))
        
        if confidence >= 0.5 and ev_result.side != "NONE" and kelly_result.side != "NO_BET":
            should_trade = True
            action = f"BUY {ev_result.side}"
        
        return StrategyRecommendation(
            ev_result=ev_result,
            mispricing=mispricing,
            kelly_result=kelly_result,
            order_strategy=order_strategy,
            should_trade=should_trade,
            action=action,
            confidence=confidence,
            reasoning=" | ".join(reasons)
        )


# ══════════════════════════════════════════════════════════════════════════════
# QUICK HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def quick_ev_check(market_price: float, your_prob: float) -> str:
    """One-liner EV check for quick decisions."""
    ev = calculate_ev(market_price, your_prob)
    return f"{ev.verdict} | EV: ${ev.ev_per_contract} | ROI: {ev.roi_percent}%"


def is_longshot_trap(price: float) -> bool:
    """Quick check if price is in the longshot trap zone."""
    return price < 0.10


def optimal_position_size(bankroll: float, price: float, your_prob: float) -> float:
    """Quick quarter-Kelly position size."""
    kelly = KellyCalculator(bankroll)
    result = kelly.calculate(price, your_prob)
    return result.bet_amount


# ══════════════════════════════════════════════════════════════════════════════
# EXPIRATION / SHORT-TERM TRADING FILTERS
# ══════════════════════════════════════════════════════════════════════════════

def parse_expiration_date(date_str: str) -> Optional[datetime]:
    """Parse various date formats from market data."""
    if not date_str:
        return None
    
    # Try ISO format first (most common: "2026-04-08T23:59:59Z")
    try:
        clean = date_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        # Return naive datetime for comparison with utcnow()
        return dt.replace(tzinfo=None)
    except (ValueError, AttributeError):
        pass
    
    # Try common formats
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str[:len(date_str.split("+")[0].split("Z")[0])], fmt)
        except (ValueError, IndexError):
            continue
    
    # Try extracting year (for far-future markets like "2050")
    try:
        import re
        year_match = re.search(r'20\d{2}', date_str)
        if year_match:
            year = int(year_match.group())
            # If just a year, assume end of year
            return datetime(year, 12, 31)
    except:
        pass
    
    return None


def days_until_expiration(date_str: str) -> Optional[int]:
    """Calculate days until market expires."""
    exp_date = parse_expiration_date(date_str)
    if not exp_date:
        return None
    
    now = datetime.utcnow()
    delta = exp_date - now
    return delta.days


def is_short_term_market(end_date: str, max_days: int = MAX_DAYS_TO_EXPIRATION) -> bool:
    """
    Check if market expires within the allowed timeframe.
    
    Args:
        end_date: expiration date string
        max_days: maximum days until expiration (default 30)
    
    Returns:
        True if market expires within max_days, False otherwise
    """
    days = days_until_expiration(end_date)
    
    if days is None:
        # Can't parse date - be conservative, reject
        logger.warning(f"Could not parse expiration date: {end_date}")
        return False
    
    if days < 0:
        # Already expired
        return False
    
    return days <= max_days


def is_too_long_term(end_date: str, max_days: int = MAX_DAYS_TO_EXPIRATION) -> tuple[bool, str]:
    """
    Check if market is too long-term for quick trading.
    
    Returns:
        (is_rejected, reason)
    """
    days = days_until_expiration(end_date)
    
    if days is None:
        return True, "Could not parse expiration date - rejecting for safety"
    
    if days < 0:
        return True, "Market already expired"
    
    if days > max_days:
        if days > 365:
            years = days // 365
            return True, f"Market expires in {years}+ years - too long term (max {max_days} days)"
        elif days > 30:
            months = days // 30
            return True, f"Market expires in {months}+ months ({days} days) - too long term (max {max_days} days)"
        else:
            return True, f"Market expires in {days} days - exceeds max {max_days} days"
    
    return False, f"OK - expires in {days} days"


def filter_short_term_markets(markets: List[dict], max_days: int = MAX_DAYS_TO_EXPIRATION) -> List[dict]:
    """
    Filter a list of markets to only include short-term ones.
    
    Args:
        markets: list of market dicts with 'end_date' or 'close_time' field
        max_days: maximum days until expiration
    
    Returns:
        Filtered list of short-term markets
    """
    result = []
    for m in markets:
        end_date = m.get("end_date") or m.get("close_time") or m.get("expiration_time", "")
        if is_short_term_market(str(end_date), max_days):
            result.append(m)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE USAGE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Example: BTC $150K contract at 12¢, you think 20%
    print("=== EV Calculation ===")
    ev = calculate_ev(0.12, 0.20)
    print(f"EV: ${ev.ev_per_contract} per contract")
    print(f"ROI: {ev.roi_percent}%")
    print(f"Verdict: {ev.verdict}")
    
    print("\n=== Mispricing Detection ===")
    mp = detect_mispricing(0.05)
    print(f"Category: {mp.category}")
    print(f"Mispricing: {mp.estimated_mispricing_pct}%")
    print(f"Action: {mp.recommended_action}")
    
    print("\n=== Kelly Sizing ===")
    kelly = KellyCalculator(bankroll=1000)
    k = kelly.calculate(0.30, 0.45)
    print(f"Side: {k.side}")
    print(f"Bet: ${k.bet_amount} ({k.adjusted_kelly_pct}% of bankroll)")
    print(f"Risk/Reward: {k.risk_reward_ratio}x")
    
    print("\n=== Bayesian Update ===")
    tracker = BayesianTracker(0.35, "Fed Rate Cut")
    tracker.update(0.70, 0.25, "Weak jobs report")
    tracker.update(0.60, 0.30, "Dovish Fed speech")
    tracker.update(0.20, 0.50, "Hot CPI print")
    print(tracker.summary())
    print(tracker.edge_vs_market(0.45))
    
    print("\n=== Full Strategy Analysis ===")
    analyzer = StrategyAnalyzer(bankroll=1000)
    rec = analyzer.analyze(
        market_price=0.30,
        your_probability=0.45,
        spread=0.02
    )
    print(f"Should Trade: {rec.should_trade}")
    print(f"Action: {rec.action}")
    print(f"Confidence: {rec.confidence}")
    print(f"Reasoning: {rec.reasoning}")
    print(f"Kelly Bet: ${rec.kelly_result.bet_amount}")
    print(f"Order Type: {rec.order_strategy.order_type}")
