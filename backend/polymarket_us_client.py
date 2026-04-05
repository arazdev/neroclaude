"""
Polymarket US Client - for US residents
Uses the polymarket-us SDK instead of py-clob-client
"""
from __future__ import annotations
import logging
import requests
from typing import Optional, List
from dataclasses import dataclass

from polymarket_us import PolymarketUS, AuthenticationError, BadRequestError

logger = logging.getLogger(__name__)

POLY_US_API_BASE = "https://api.polymarket.us"


@dataclass
class PolyUSMarket:
    """Simplified market data structure."""
    slug: str
    title: str
    question: str
    yes_price: float
    no_price: float
    volume: float
    liquidity: float
    expires_at: Optional[str] = None


@dataclass
class PaymentMethod:
    """Bank/card payment method."""
    id: str
    type: str  # "ach" or "card"
    name: str
    last4: str
    deposit_limit: float
    withdrawal_limit: float
    

class PolymarketUSClient:
    """Client for Polymarket US (api.polymarket.us)."""
    
    def __init__(self, key_id: str, secret_key: str):
        """Initialize with API credentials from polymarket.us/developer."""
        self._key_id = key_id
        self._secret_key = secret_key
        self._client: Optional[PolymarketUS] = None
        self._public_client = PolymarketUS()  # No auth for public data
        self._is_authenticated = False
        
        # Try to authenticate
        try:
            self._client = PolymarketUS(key_id=key_id, secret_key=secret_key)
            # Test auth by getting balances
            self._client.account.balances()
            self._is_authenticated = True
            logger.info("Polymarket US: Authenticated successfully")
        except AuthenticationError as e:
            logger.warning(f"Polymarket US auth failed: {e}")
            self._client = None
        except Exception as e:
            logger.warning(f"Polymarket US init error: {e}")
            self._client = None
    
    @property
    def is_authenticated(self) -> bool:
        return self._is_authenticated and self._client is not None
    
    def get_balance(self) -> dict:
        """Get account balances."""
        if not self._client:
            return {"usd": 0.0, "available": 0.0}
        try:
            balances = self._client.account.balances()
            return {
                "usd": float(getattr(balances, "usd", 0) or 0),
                "available": float(getattr(balances, "buying_power", 0) or 0),
            }
        except Exception as e:
            logger.error(f"Balance fetch error: {e}")
            return {"usd": 0.0, "available": 0.0}
    
    def get_positions(self) -> List[dict]:
        """Get current positions."""
        if not self._client:
            return []
        try:
            positions = self._client.portfolio.positions()
            result = []
            for p in getattr(positions, "items", []):
                result.append({
                    "market_slug": getattr(p, "market_slug", ""),
                    "side": getattr(p, "side", ""),
                    "quantity": float(getattr(p, "quantity", 0) or 0),
                    "avg_price": float(getattr(p, "average_price", 0) or 0),
                    "current_value": float(getattr(p, "current_value", 0) or 0),
                })
            return result
        except Exception as e:
            logger.error(f"Positions fetch error: {e}")
            return []
    
    def get_markets(self, limit: int = 50) -> List[PolyUSMarket]:
        """Get available markets."""
        try:
            events = self._public_client.events.list()
            markets = []
            
            count = 0
            for event in getattr(events, "items", []):
                if count >= limit:
                    break
                    
                for market in getattr(event, "markets", []):
                    if count >= limit:
                        break
                    
                    slug = getattr(market, "slug", "")
                    try:
                        book = self._public_client.markets.book(slug)
                        yes_price = float(getattr(book, "best_bid", 0.5) or 0.5)
                        no_price = 1.0 - yes_price
                    except:
                        yes_price = 0.5
                        no_price = 0.5
                    
                    markets.append(PolyUSMarket(
                        slug=slug,
                        title=getattr(event, "title", ""),
                        question=getattr(market, "question", ""),
                        yes_price=yes_price,
                        no_price=no_price,
                        volume=float(getattr(market, "volume", 0) or 0),
                        liquidity=float(getattr(market, "liquidity", 0) or 0),
                    ))
                    count += 1
            
            return markets
        except Exception as e:
            logger.error(f"Markets fetch error: {e}")
            return []
    
    def place_order(
        self,
        market_slug: str,
        side: str,  # "yes" or "no"
        price: float,
        quantity: int,
        order_type: str = "limit",
    ) -> Optional[dict]:
        """Place an order on a market."""
        if not self._client:
            logger.error("Cannot place order: not authenticated")
            return None
        
        try:
            intent = "ORDER_INTENT_BUY_LONG" if side.lower() == "yes" else "ORDER_INTENT_BUY_SHORT"
            order_type_api = "ORDER_TYPE_LIMIT" if order_type == "limit" else "ORDER_TYPE_MARKET"
            
            order = self._client.orders.create(
                market_slug=market_slug,
                intent=intent,
                type=order_type_api,
                price={"value": str(price), "currency": "USD"},
                quantity=quantity,
                tif="TIME_IN_FORCE_GOOD_TILL_CANCEL",
            )
            
            return {
                "order_id": getattr(order, "id", ""),
                "status": getattr(order, "status", ""),
                "filled": getattr(order, "filled_quantity", 0),
            }
        except BadRequestError as e:
            logger.error(f"Order rejected: {e}")
            return None
        except Exception as e:
            logger.error(f"Order error: {e}")
            return None
    
    def get_orders(self) -> List[dict]:
        """Get orders."""
        if not self._client:
            return []
        try:
            orders = self._client.orders.list()
            return [
                {
                    "id": getattr(o, "id", ""),
                    "market_slug": getattr(o, "market_slug", ""),
                    "side": getattr(o, "intent", ""),
                    "price": float(getattr(o, "price", 0) or 0),
                    "quantity": int(getattr(o, "quantity", 0) or 0),
                    "status": getattr(o, "status", ""),
                }
                for o in getattr(orders, "items", [])
            ]
        except Exception as e:
            logger.error(f"Orders fetch error: {e}")
            return []

    # ==================== PAYMENT / DEPOSIT METHODS ====================
    
    def _api_request(self, method: str, endpoint: str, data: dict = None) -> dict:
        """Make authenticated API request."""
        if not self._client:
            raise AuthenticationError("Not authenticated")
        
        url = f"{POLY_US_API_BASE}{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "X-API-Key-Id": self._key_id,
            "X-API-Secret": self._secret_key,
        }
        
        try:
            if method.upper() == "GET":
                resp = requests.get(url, headers=headers, timeout=30)
            else:
                resp = requests.post(url, headers=headers, json=data or {}, timeout=30)
            
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"API error {endpoint}: {e.response.text if e.response else e}")
            raise
    
    def get_payment_methods(self) -> List[PaymentMethod]:
        """Get linked bank accounts and cards."""
        if not self._client:
            return []
        try:
            data = self._api_request("GET", "/v1/aeropay/methods")
            methods = []
            for m in data.get("methods", []):
                methods.append(PaymentMethod(
                    id=m.get("id", ""),
                    type=m.get("type", "ach"),
                    name=m.get("name", "Bank"),
                    last4=m.get("last4", "****"),
                    deposit_limit=float(m.get("deposit_limit", 0) or 0),
                    withdrawal_limit=float(m.get("withdrawal_limit", 0) or 0),
                ))
            return methods
        except Exception as e:
            logger.error(f"Payment methods fetch error: {e}")
            return []
    
    def initialize_bank_link(self) -> dict:
        """Start bank linking process via Aeropay."""
        if not self._client:
            return {"error": "Not authenticated"}
        try:
            data = self._api_request("POST", "/v1/aeropay/initialize")
            return {
                "success": True,
                "requires_mfa": data.get("requires_mfa", False),
                "session_id": data.get("session_id", ""),
                "link_url": data.get("link_url", ""),
            }
        except Exception as e:
            logger.error(f"Bank link init error: {e}")
            return {"error": str(e)}
    
    def validate_mfa(self, session_id: str, code: str) -> dict:
        """Submit MFA code for bank linking."""
        if not self._client:
            return {"error": "Not authenticated"}
        try:
            data = self._api_request("POST", "/v1/aeropay/validate-mfa", {
                "session_id": session_id,
                "code": code,
            })
            return {"success": True, "data": data}
        except Exception as e:
            logger.error(f"MFA validation error: {e}")
            return {"error": str(e)}
    
    def deposit_ach(self, payment_method_id: str, amount: float) -> dict:
        """Create ACH deposit from linked bank."""
        if not self._client:
            return {"error": "Not authenticated"}
        try:
            data = self._api_request("POST", "/v1/aeropay/deposits", {
                "payment_method_id": payment_method_id,
                "amount": str(amount),
            })
            return {
                "success": True,
                "deposit_id": data.get("id", ""),
                "status": data.get("status", "pending"),
                "amount": float(data.get("amount", amount)),
            }
        except Exception as e:
            logger.error(f"ACH deposit error: {e}")
            return {"error": str(e)}
    
    def withdraw_ach(self, payment_method_id: str, amount: float) -> dict:
        """Create ACH withdrawal to linked bank."""
        if not self._client:
            return {"error": "Not authenticated"}
        try:
            data = self._api_request("POST", "/v1/aeropay/withdrawals", {
                "payment_method_id": payment_method_id,
                "amount": str(amount),
            })
            return {
                "success": True,
                "withdrawal_id": data.get("id", ""),
                "status": data.get("status", "pending"),
                "amount": float(data.get("amount", amount)),
            }
        except Exception as e:
            logger.error(f"ACH withdrawal error: {e}")
            return {"error": str(e)}
    
    def create_card_session(self) -> dict:
        """Create payment session for card tokenization."""
        if not self._client:
            return {"error": "Not authenticated"}
        try:
            data = self._api_request("POST", "/v1/checkout/payment-sessions")
            return {
                "success": True,
                "session_id": data.get("id", ""),
                "client_secret": data.get("client_secret", ""),
            }
        except Exception as e:
            logger.error(f"Card session error: {e}")
            return {"error": str(e)}
    
    def deposit_card(self, instrument_id: str, amount: float) -> dict:
        """Process card deposit."""
        if not self._client:
            return {"error": "Not authenticated"}
        try:
            data = self._api_request("POST", "/v1/checkout/deposits", {
                "instrument_id": instrument_id,
                "amount": str(amount),
            })
            return {
                "success": True,
                "deposit_id": data.get("id", ""),
                "status": data.get("status", ""),
                "amount": float(data.get("amount", amount)),
            }
        except Exception as e:
            logger.error(f"Card deposit error: {e}")
            return {"error": str(e)}


def get_polymarket_us_client(cfg) -> Optional[PolymarketUSClient]:
    """Factory function to create Polymarket US client from config."""
    key_id = getattr(cfg, "poly_us_key_id", "") or getattr(cfg, "poly_api_key", "")
    secret = getattr(cfg, "poly_us_secret", "") or getattr(cfg, "poly_api_secret", "")
    
    if not key_id or not secret:
        logger.warning("Polymarket US credentials not configured")
        return None
    
    return PolymarketUSClient(key_id=key_id, secret_key=secret)
