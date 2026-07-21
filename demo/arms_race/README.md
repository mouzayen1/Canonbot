# Bot vs. Anti-Bot: a simulated arms race 🛡️⚔️

A self-contained **simulation** of how automated bots try to beat a store's
anti-bot defenses — and how defenders beat them back. It exists to *explain the
dynamics*, especially from the **defender's** side, which is the genuinely useful
knowledge here.

Run it:

```bash
python demo/arms_race/simulate.py
```

```
ROUND                                       OUTCOME                    REQ PROXY SOLVE
1. Naive bot (one IP, no solver)            ❌ BLOCKED (rate-limited)    7     1     0
2. + proxy rotation (no solver)             🧱 STUCK at CAPTCHA         11    11     0
3. + CAPTCHA solver (full evasion stack)    ✅ BOUGHT IT               12    12     1
4. defender enables FINGERPRINTING          ❌ BLOCKED (rate-limited)    7     7     0
```

## Everything here is a toy — on purpose

This is **not** a working evasion tool and cannot be turned into one by editing a
config:

- **`ProxyPool`** hands out fake `10.0.0.x` labels. It does not route traffic
  through anything. Real proxy farms rotate real (often residential) IPs and cost
  real money — this is a Python list.
- **The CAPTCHA is grade-school arithmetic** (`3 + 4`), and `solve_toy_captcha`
  only answers *that*. It cannot touch a real reCAPTCHA/hCaptcha — those are
  defeated by human-solver farms or ML models, which this project does **not**
  build or integrate.
- Nothing here makes a real network request to any real site.

I deliberately won't wire in real proxy providers or real CAPTCHA-solving APIs
(2captcha, CapMonster, etc.), even "just for the sandbox" — that integration code
*is* the actual weapon, and it's reusable against real stores. The toy version
teaches the architecture without handing over a capability.

## What the four rounds teach

1. **Rate limiting** stops naive bots. One IP hammering a drop trips a simple
   per-IP request limit. Most casual scripts die here.
2. **Proxy rotation** beats per-IP limits by spreading requests across many
   identities — but it does nothing about the **CAPTCHA** at checkout.
3. **CAPTCHA-solving** is the other half of the stack. With both, the bot gets
   through. This is what a real botting operation is actually paying for: proxies
   *plus* solves.
4. **Behavioral fingerprinting** is the defender's answer. Rotating your IP does
   **not** change your TLS handshake (JA3/JA4), header order, or timing
   signature. Rate-limit on *that* and rotation stops helping — the same bot is
   caught again.

## The real lessons (the point of all this)

- **Defense in depth wins.** No single control stops bots; layered controls
  (rate limit + CAPTCHA + fingerprint + behavior + queue) each raise the cost.
- **IP rotation ≠ invisibility.** Fingerprinting is why "just use proxies"
  doesn't actually work against a serious defender.
- **Cost asymmetry is everything.** Look at the `PROXY`/`SOLVE` columns: every
  single drop costs the attacker proxies and paid solves, while the defender
  flips one setting. Defenders win by making bots *uneconomical*, not impossible.
- **This is why we don't build it.** The reason these cameras are hard to get is
  that beating real defenses means running this cost-heavy, ToS-violating,
  legally-gray machinery — an arms race, not a clever script. Understanding it is
  useful (for building defenses, for CTFs, for knowing what you're up against);
  operating it against a real retailer is the line.

If you're on the **defender** side and want to go further, good next steps are
reading about JA3/JA4 TLS fingerprinting, proof-of-work challenges, and
behavioral scoring — I'm happy to build simulations of any of those.
