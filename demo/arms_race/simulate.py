"""Simulate the bot-vs-anti-bot arms race, one escalation at a time.

Run: python demo/arms_race/simulate.py

It walks through four rounds against a mock store whose product 'drops' only
after enough polling (so rate limits actually bite):

  1. Naive bot            -> rate-limited and BLOCKED before the drop
  2. + proxy rotation     -> beats the rate limit, but STUCK at the CAPTCHA
  3. + CAPTCHA solver     -> gets all the way through and BUYS it
  4. defender turns on behavioral FINGERPRINTING -> the same full bot is
                             BLOCKED again, because rotating IPs doesn't change
                             your fingerprint. The arms race resets.
"""

from __future__ import annotations

from anti_bot_store import AntiBotStore
from evasion import ProxyPool, solve_toy_captcha

DROP_AFTER = 10   # product becomes buyable only after this many total requests
BUDGET = 6        # requests allowed per key (IP, or fingerprint) before a block


def run_bot(use_proxy: bool, use_solver: bool, fingerprint_mode: bool,
            fingerprint: str = "ja3:771,4865-4866~bot") -> dict:
    store = AntiBotStore(per_key_budget=BUDGET, fingerprint_mode=fingerprint_mode)
    store.drop_after = DROP_AFTER
    pool = ProxyPool(40) if use_proxy else None
    fixed_ip = "203.0.113.7"
    solver_calls = 0

    def ip() -> str:
        return pool.next_ip() if pool else fixed_ip

    def result(outcome: str) -> dict:
        return {
            "outcome": outcome,
            "requests": store.stats["requests"],
            "proxies": len(pool.used) if pool else 1,
            "solver_calls": solver_calls,
        }

    # Poll for the drop.
    available = False
    for _ in range(60):
        r = store.get_product(ip(), fingerprint)
        if r["status"] == "blocked":
            return result("❌ BLOCKED (rate-limited)")
        if r.get("available"):
            available = True
            break
    if not available:
        return result("❌ gave up (never reached the drop)")

    # Try to check out (past the CAPTCHA wall).
    r = store.checkout(ip(), fingerprint)
    if r["status"] == "blocked":
        return result("❌ BLOCKED (rate-limited)")
    if r["status"] == "captcha_required":
        if not use_solver:
            return result("🧱 STUCK at CAPTCHA")
        solver_calls += 1
        answer = solve_toy_captcha(r["question"])
        r = store.checkout(ip(), fingerprint, r["token"], answer)
        if r["status"] == "blocked":
            return result("❌ BLOCKED (rate-limited)")

    if r["status"] == "purchased":
        return result(f"✅ BOUGHT IT ({r['order_id']})")
    return result(f"❌ {r['status']}")


def main() -> None:
    rounds = [
        ("1. Naive bot (one IP, no solver)",           dict(use_proxy=False, use_solver=False, fingerprint_mode=False)),
        ("2. + proxy rotation (no solver)",            dict(use_proxy=True,  use_solver=False, fingerprint_mode=False)),
        ("3. + CAPTCHA solver (full evasion stack)",   dict(use_proxy=True,  use_solver=True,  fingerprint_mode=False)),
        ("4. defender enables FINGERPRINTING",         dict(use_proxy=True,  use_solver=True,  fingerprint_mode=True)),
    ]

    print("\nBot vs anti-bot — a simulated arms race (all toy components)\n")
    print(f"{'ROUND':<44}{'OUTCOME':<28}{'REQ':>4}{'PROXY':>7}{'SOLVE':>7}")
    print("-" * 90)
    for label, cfg in rounds:
        r = run_bot(**cfg)
        print(f"{label:<44}{r['outcome']:<28}{r['requests']:>4}{r['proxies']:>7}{r['solver_calls']:>7}")

    print("\nWhat each round teaches:")
    print("  1  One IP polling a drop trips the rate limit — the simplest defense stops naive bots.")
    print("  2  Rotating 'IPs' spreads the load under the per-IP limit... but the CAPTCHA still blocks checkout.")
    print("  3  Add CAPTCHA-solving and the full stack gets through. This is what a real botting setup buys.")
    print("  4  The defender rate-limits by BEHAVIORAL FINGERPRINT instead of IP. Rotation changes your IP,")
    print("     not your TLS/behavior signature — so the same bot is caught again. The race never really ends.")
    print("\nNote the attacker's cost columns (PROXY, SOLVE): evasion isn't free — every drop burns proxies and")
    print("paid solves. The defender flips one setting. That cost asymmetry is the real story.\n")


if __name__ == "__main__":
    main()
