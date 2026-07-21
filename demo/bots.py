"""Two bots that race for the sandbox drop: a naive one and an optimized one.

The only differences between them are the six performance levers from the
README — no tricks, no evasion. Same store, same finish line.
"""

from __future__ import annotations

import time

import requests

# A fake, pre-built checkout payload (test card number, like Stripe's 4242...).
PAYMENT = {"card": "4242424242424242", "exp": "12/30", "cvc": "123", "zip": "00000"}


def _now() -> float:
    return time.monotonic()


def naive_bot(base_url: str, results: dict, name: str = "naive") -> None:
    """Everything the slow way: new connection per call, lazy polling, extra
    page loads, nothing prepared in advance."""
    r = {"name": name, "got": False, "detect": None, "bought": None, "reason": ""}
    results[name] = r
    deadline = _now() + 10
    # Poll slowly, opening a brand-new connection every time (module-level
    # requests.* makes a fresh Session per call = new TCP connection each time).
    while True:
        try:
            if requests.get(f"{base_url}/product", timeout=5).json()["available"]:
                r["detect"] = _now()
                break
        except requests.RequestException:
            pass
        if _now() > deadline:
            r["reason"] = "missed the drop"
            return
        time.sleep(0.5)  # slow poll: up to half a second late to notice the drop
    # Re-fetch the "page" again before acting (wasted round trip), then go step
    # by step with fresh connections.
    requests.get(f"{base_url}/product", timeout=5)
    cart = requests.post(f"{base_url}/cart", json={"sku": "cam"}, timeout=5)
    if cart.status_code != 200:
        r["reason"] = "cart_failed"
        return
    token = cart.json()["cart_token"]
    co = requests.post(f"{base_url}/checkout", json={"cart_token": token, **PAYMENT}, timeout=5)
    r["bought"] = _now()
    if co.status_code == 200 and co.json().get("status") == "confirmed":
        r["got"] = True
        r["order_id"] = co.json()["order_id"]
    else:
        r["reason"] = co.json().get("status", "failed")


def fast_bot(base_url: str, results: dict, name: str = "fast") -> None:
    """Optimized: one warm keep-alive Session, tight polling, payload prebuilt,
    connection pre-warmed, no wasted steps."""
    r = {"name": name, "got": False, "detect": None, "bought": None, "reason": ""}
    results[name] = r
    session = requests.Session()  # reuses ONE connection for every request
    checkout_body = {"cart_token": None, **PAYMENT}  # payload prebuilt; only the token is missing

    # Pre-warm: open the keep-alive connection before the drop so the socket +
    # any handshake cost is already paid when it counts.
    session.get(f"{base_url}/product", timeout=5)

    deadline = _now() + 10
    while True:
        try:
            if session.get(f"{base_url}/product", timeout=5).json()["available"]:
                r["detect"] = _now()
                break
        except requests.RequestException:
            pass
        if _now() > deadline:
            r["reason"] = "missed the drop"
            return
        time.sleep(0.02)  # tight poll: notices the drop within ~20 ms
    # Straight to cart -> checkout on the warm connection, no re-fetch.
    cart = session.post(f"{base_url}/cart", json={"sku": "cam"}, timeout=5)
    if cart.status_code != 200:
        r["reason"] = "cart_failed"
        return
    checkout_body["cart_token"] = cart.json()["cart_token"]
    co = session.post(f"{base_url}/checkout", json=checkout_body, timeout=5)
    r["bought"] = _now()
    if co.status_code == 200 and co.json().get("status") == "confirmed":
        r["got"] = True
        r["order_id"] = co.json()["order_id"]
    else:
        r["reason"] = co.json().get("status", "failed")
