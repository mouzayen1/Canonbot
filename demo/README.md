# Buying-Bot Demo (sandbox only) 🏁

A self-contained demo that shows **what actually makes a checkout bot fast** — by
racing an optimized bot against a naive one for a limited "drop." Everything is
fake and local: a mock store, fake inventory, a fake product, fake payment
(`4242…` test card). There is **no real retailer and no anti-bot to defeat** —
the point is to teach the *performance* concepts in isolation.

## Run it

```bash
pip install requests          # already a dependency of this repo
python demo/race.py           # 12 drops, 1 unit each, fast bot vs naive bot
python demo/race.py --rounds 20
python demo/race.py --naive 3 --stock 2   # 3 naive bots + 1 fast, 2 units
```

Example output:

```
BOT          WINS     AVG DETECT
--------------------------------
fast         11/12         67 ms
naive         1/12        305 ms
Fast bot(s) won 11/12 drops (92%).
```

The fast bot wins the large majority; the naive one only sneaks a win when its
slow poll happens to land right on the drop. That's the whole lesson in one line.

## The three files

- `mock_store.py` — a tiny HTTP store: `GET /product`, `POST /cart`,
  `POST /checkout`. The product "drops" after a delay; only a limited number of
  checkouts succeed. It adds a little fake latency to imitate a network.
- `bots.py` — `naive_bot` and `fast_bot`. **Same finish line, only the technique
  differs.**
- `race.py` — runs many drops and tallies wins + average detection time.

## What makes the fast bot faster (read the diff in `bots.py`)

| Lever | Naive | Fast |
|---|---|---|
| **Connection reuse** | new TCP connection every request | one warm `Session` (HTTP keep-alive) reused for all calls |
| **Pre-warming** | connects cold at the drop | opens the connection *before* the drop so it's ready |
| **Detection speed** | polls every 500 ms | polls every 20 ms — sees the drop ~15× sooner |
| **Prebuilt payload** | builds the checkout body at drop time | payload is prepared in advance; only the cart token is slotted in |
| **No wasted steps** | re-loads the "page" before acting | goes straight cart → checkout |
| **Talk to the API** | (both do here) | in the real world: hit the JSON endpoint, never render the page |

Other real-world levers not shown here (all legitimate performance work):
**HTTP/2 multiplexing**, **async/concurrent** in-flight requests, **retry with
tight timeouts** (fail fast, try again), and **low-latency placement** (servers
physically close to the store, fast DNS).

## The honest caveat (important)

In this sandbox these techniques *are* the whole game, so the fast bot wins.
**Against a real retailer they are necessary but nowhere near sufficient.** The
thing that actually decides who wins a real drop is getting *past the retailer's
defenses* — CAPTCHAs, virtual waiting-room queues, IP rate-limits, TLS/behavior
fingerprinting — and that's an arms race fought with **residential proxy
rotation and CAPTCHA-solving farms**, plus expensive colocated infrastructure.

This project deliberately does **not** do any of that — no proxy rotation, no
CAPTCHA-solving, no bot-protection evasion. So treat this demo as a clean lesson
in *performance engineering*, which is legitimate and useful everywhere (APIs,
trading, real-time systems), not as a recipe for beating a live store.
