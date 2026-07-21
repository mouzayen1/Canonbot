"""Toy 'evasion' pieces — deliberately fake, for the simulation only.

These exist so the demo can show the *shape* of how bots try to get past a gate.
They are intentionally powerless in the real world:

  - ProxyPool just hands out fake IP-shaped labels. It does NOT route traffic
    through any real proxy. Real proxy farms cost money and rotate real
    residential IPs; this is a Python list.

  - solve_toy_captcha only answers the arithmetic puzzle from anti_bot_store.
    It cannot touch a real reCAPTCHA/hCaptcha — those need human farms or ML
    models, which this project does not build or integrate.
"""

from __future__ import annotations

import re


class ProxyPool:
    """A rotating set of FAKE source-identity labels."""

    def __init__(self, size: int = 20):
        self.ips = [f"10.0.0.{i}" for i in range(1, size + 1)]
        self._i = 0
        self.used: set[str] = set()

    def next_ip(self) -> str:
        ip = self.ips[self._i % len(self.ips)]
        self._i += 1
        self.used.add(ip)
        return ip


def solve_toy_captcha(question: str) -> int | None:
    """Solve only the toy 'a + b' challenge. Models the outsourced-solver flow
    (question in -> answer out, with a cost), NOT real CAPTCHA defeat."""
    m = re.match(r"\s*(\d+)\s*\+\s*(\d+)\s*$", question)
    if not m:
        return None  # anything that isn't our toy puzzle is unsolvable here
    return int(m.group(1)) + int(m.group(2))
