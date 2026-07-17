# Canonbot 📷

A polite **restock monitor** for hard-to-find Canon cameras. It watches
first-party / authorized retailers and sends you a **Discord alert the moment an
item comes back in stock at or below the price you set** — so you can complete a
normal checkout yourself, fast.

It is deliberately **not** an auto-checkout or scalping bot. It doesn't bypass
CAPTCHAs or bot protection, doesn't hammer checkout endpoints, and won't touch
marketplace resellers. It reads the stock data retailers already publish, at a
respectful polling rate, and tells you when to go buy.

---

## What it watches (out of the box)

| Camera | MSRP (your default max) | Authorized retailers at MSRP |
|---|---|---|
| **PowerShot G7 X Mark III** | **$879.99** | Canon USA, Best Buy, B&H, Target |
| **PowerShot SX740 HS** | **$399.99** (2018 launch) | Best Buy, B&H, Walmart, Bedfords, Camera Wholesalers (Canon = refurb only) |

Notes:
- The **G7 X Mark III** is the tough one — it sells out within hours of a restock.
- The **SX740 HS** is largely discontinued; at true MSRP you're mostly watching
  authorized dealers. Ignore the ~$745 Amazon "bundle" listings — that's not MSRP.
- Only retailers on the authorized allow-list in `canonbot/config.py` can be
  monitored. Paste an eBay or Amazon-marketplace URL and the bot refuses it.

---

## Setup

Requires Python 3.10+.

```bash
# 1. Install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Create your config from the examples
cp config.example.yaml config.yaml
cp .env.example .env

# 3. Get a Discord webhook URL:
#    Server Settings -> Integrations -> Webhooks -> New Webhook -> Copy URL
#    Paste it into .env as DISCORD_WEBHOOK_URL

# 4. Test that Discord works
python run.py --test-webhook

# 5. Do a single dry-run sweep to see current status
python run.py --once

# 6. Run it for real (24/7)
python run.py
```

## Configuration

Everything lives in `config.yaml`:

- **`products[].max_price`** — your ceiling for that item. Alerts fire only when
  the detected price is at or below this (that's the "never over MSRP" rule).
- **`products[].urls`** — the exact product pages to watch. Add your B&H links etc.
- **`settings.poll_interval_seconds`** — seconds between sweeps (min 30, default
  90). Each request gets random jitter so the cadence isn't robotic. **Please
  don't lower this a lot** — aggressive polling gets your IP rate-limited and is
  rude to the retailer. 60–120s is plenty for a restock you'll act on manually.
- **`settings.alert_above_max_price`** — set `true` if you also want a
  (clearly-flagged) heads-up when an item is in stock but over your cap.

Secrets go in `.env` (never committed):
- **`DISCORD_WEBHOOK_URL`** — required.
- **`DISCORD_MENTION`** — optional, e.g. `@everyone` or `<@&ROLE_ID>` to get pinged.

## How stock detection works

For each product URL, in order of reliability:
1. **schema.org JSON-LD** — structured `Product`/`Offer` data the site publishes
   for search engines. Most reliable, gives both availability and price.
2. **Open Graph / meta price tags** — for the price when JSON-LD lacks it.
3. **Conservative text heuristics** ("Add to Cart" vs "Sold Out") — last resort.

If a page won't load or is ambiguous, it's reported as **unknown** and treated as
out of stock, so you never get a false "in stock!" ping.

### Retailer reliability (be aware)

Because the bot refuses to evade bot-protection, results vary by site:

- ✅ **Reliable**: retailers that render product/price server-side with JSON-LD.
- ⚠️ **Flaky**: **Canon USA** often returns HTTP 403 to simple requests, and
  **Best Buy** / **Target** are JavaScript apps that may not expose stock to a
  plain fetch. For these you'll see `unknown`/`HTTP 403` in the logs — that's the
  bot backing off, not a bug.

If you want rock-solid coverage on those specific sites, the clean (non-evasive)
upgrades are: use **Best Buy's official Developer API** (free key) for Best Buy,
and add an optional headless-browser checker for JS-rendered pages. Both are
easy to bolt on to `canonbot/checkers.py` — ask and I'll add them.

## Running 24/7

Simplest option is a small always-on box (a $5 VPS, a Raspberry Pi, etc.) with a
`systemd` service or a `tmux`/`screen` session:

```bash
# example systemd unit: /etc/systemd/system/canonbot.service
[Service]
WorkingDirectory=/opt/Canonbot
ExecStart=/opt/Canonbot/.venv/bin/python run.py
Restart=always
```

State is saved to `state.json`, so a restart won't re-spam you for items that
were already in stock.

## Testing

```bash
python tests/test_checkers.py
```

## A note on responsible use

This tool exists to get **you** a fair shot at buying **one** of these cameras at
retail price. Please keep it that way: reasonable poll rates, authorized
retailers, and a normal manual checkout. That's what keeps you inside every
retailer's terms and keeps stock reachable for the next person too.
