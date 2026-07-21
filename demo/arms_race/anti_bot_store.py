"""A mock store with a toy 'anti-bot gate' — for understanding the arms race.

This is a SIMULATION with toy parts. Nothing here talks to a network, uses real
proxies, or solves a real CAPTCHA. The identities are fake labels, the CAPTCHA is
grade-school arithmetic. The goal is to show *how the pieces fit together* and,
especially, *how a defender detects bots* — not to provide a working bypass.

The gate models three real defenses, in toy form:
  1. Per-identity rate limiting  (too many requests from one 'IP' -> blocked)
  2. A CAPTCHA challenge on checkout (must answer to proceed)
  3. Behavioral fingerprinting     (a signature that DOESN'T change when you
                                    rotate IPs — the thing that beats rotation)
"""

from __future__ import annotations

from collections import defaultdict


class Challenge:
    """A toy CAPTCHA: solve simple arithmetic. Real CAPTCHAs are nothing like
    this — that's the point; we're modeling the *flow*, not the puzzle."""

    _seq = 0

    def __init__(self, a: int, b: int):
        Challenge._seq += 1
        self.token = f"cap-{Challenge._seq}"
        self.question = f"{a} + {b}"
        self._answer = a + b

    def check(self, answer) -> bool:
        try:
            return int(answer) == self._answer
        except (TypeError, ValueError):
            return False


class AntiBotStore:
    def __init__(self, per_key_budget: int = 6, fingerprint_mode: bool = False):
        # How many requests one "key" may make before it's blocked. The key is
        # the source IP normally, or the behavioral fingerprint when the
        # defender turns fingerprinting on.
        self.per_key_budget = per_key_budget
        self.fingerprint_mode = fingerprint_mode
        # Product becomes buyable only after this many total requests, so a bot
        # has to keep polling (and burn its rate-limit budget) waiting for it.
        self.drop_after = 0
        self.counts: dict[str, int] = defaultdict(int)
        self.blocked: set[str] = set()
        self.open_challenges: dict[str, Challenge] = {}
        self.stock = 1
        self.orders: list[str] = []
        # bookkeeping so the demo can report what happened
        self.stats = {"requests": 0, "blocks": 0, "captchas_issued": 0}

    def _key(self, ip: str, fingerprint: str) -> str:
        return fingerprint if self.fingerprint_mode else ip

    def _gate(self, ip: str, fingerprint: str) -> str | None:
        """Return a block reason, or None if the request may proceed."""
        self.stats["requests"] += 1
        key = self._key(ip, fingerprint)
        if key in self.blocked:
            return "blocked"
        self.counts[key] += 1
        if self.counts[key] > self.per_key_budget:
            self.blocked.add(key)
            self.stats["blocks"] += 1
            return "blocked"
        return None

    def get_product(self, ip: str, fingerprint: str) -> dict:
        block = self._gate(ip, fingerprint)
        if block:
            return {"status": "blocked"}
        available = self.stats["requests"] >= self.drop_after and self.stock > 0
        return {"status": "ok", "available": available, "stock": self.stock}

    def checkout(self, ip: str, fingerprint: str,
                 captcha_token: str | None = None, captcha_answer=None) -> dict:
        block = self._gate(ip, fingerprint)
        if block:
            return {"status": "blocked"}

        # CAPTCHA wall: first checkout attempt gets a challenge; the caller must
        # come back with a correct answer for the issued token.
        if captcha_token is None:
            ch = Challenge(3 + len(self.orders), 4)
            self.open_challenges[ch.token] = ch
            self.stats["captchas_issued"] += 1
            return {"status": "captcha_required", "token": ch.token, "question": ch.question}

        ch = self.open_challenges.get(captcha_token)
        if ch is None or not ch.check(captcha_answer):
            return {"status": "captcha_failed"}
        del self.open_challenges[captcha_token]

        if self.stock <= 0:
            return {"status": "sold_out"}
        self.stock -= 1
        order_id = f"order-{len(self.orders) + 1}"
        self.orders.append(order_id)
        return {"status": "purchased", "order_id": order_id}
