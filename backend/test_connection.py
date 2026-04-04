#!/usr/bin/env python3
"""Quick connectivity test — no orders placed."""

import sys
from dotenv import load_dotenv

load_dotenv()

print("=" * 60)
print("BOTCLAUDE — Connection Test")
print("=" * 60)

# 1. Test config loading
print("\n[1/4] Loading config...")
try:
    from config import Config
    cfg = Config()
    print(f"  ✓ Config loaded (dry_run={cfg.dry_run})")
except Exception as e:
    print(f"  ✗ Config failed: {e}")
    sys.exit(1)

# 2. Test Polymarket read-only (no auth needed)
print("\n[2/4] Fetching active markets from Gamma API...")
events: list = []
try:
    import httpx
    resp = httpx.get(
        "https://gamma-api.polymarket.com/events",
        params={"active": "true", "closed": "false", "limit": "3"},
        timeout=15,
    )
    resp.raise_for_status()
    events = resp.json()
    print(f"  ✓ Got {len(events)} events")
    for ev in events[:3]:
        title = ev.get("title", "?")[:60]
        markets = ev.get("markets", [])
        print(f"    • {title} ({len(markets)} markets)")
except Exception as e:
    print(f"  ✗ Gamma API failed: {e}")

# 3. Test CLOB read-only (order book)
print("\n[3/4] Fetching order book from CLOB...")
from py_clob_client.client import ClobClient
try:
    reader = ClobClient(cfg.poly_host)
    ok = reader.get_ok()
    print(f"  ✓ CLOB health: {ok}")

    # Grab a token_id from the first market to test the order book
    book_found = False
    if events:  # noqa: F821
        for ev in events:
            for mkt in ev.get("markets", []):
                tokens = mkt.get("clobTokenIds", [])
                if tokens:
                    token_id = tokens[0]
                    try:
                        book = reader.get_order_book(token_id)
                    except Exception:
                        continue
                    bids = book.bids or []
                    asks = book.asks or []
                    q = mkt.get("question", "?")[:50]
                    print(f"  ✓ Order book for '{q}'")
                    print(f"    Bids: {len(bids)}  Asks: {len(asks)}")
                    if bids:
                        print(f"    Best bid: {bids[0].price}")
                    if asks:
                        print(f"    Best ask: {asks[0].price}")
                    book_found = True
                    break
            if book_found:
                break
    if not book_found:
        print("  ⚠ No order book found (all test markets inactive)")
except Exception as e:
    print(f"  ✗ CLOB read failed: {e}")

# 4. Test authenticated client (derive creds, no trading)
print("\n[4/4] Testing authenticated CLOB client...")
try:
    trader = ClobClient(
        cfg.poly_host,
        key=cfg.poly_private_key,
        chain_id=cfg.poly_chain_id,
        signature_type=cfg.poly_signature_type,
        funder=cfg.poly_funder,
    )
    # Always derive creds from the private key (most reliable)
    creds = trader.create_or_derive_api_creds()
    trader.set_api_creds(creds)
    print(f"  ✓ Derived API creds (key={creds.api_key[:16]}...)")

    # Try fetching open orders (proves auth works)
    from py_clob_client.clob_types import OpenOrderParams
    orders = trader.get_orders(OpenOrderParams())
    print(f"  ✓ Auth working — {len(orders)} open orders")
except Exception as e:
    print(f"  ✗ Auth failed: {e}")

print("\n" + "=" * 60)
print("Test complete. If all 4 checks passed, run:")
print("  python bot.py")
print("(DRY_RUN=true by default — no real orders)")
print("=" * 60)
