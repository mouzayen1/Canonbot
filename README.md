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

# 2. config.yaml already ships with the two cameras — edit it to taste.
#    Create your secrets file:
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

### Detection modes (per listing)

Set `mode` on any listing in `config.yaml`:

| Mode | What it does | Best for |
|---|---|---|
| `auto` *(default)* | Best Buy API if you gave a key+sku, else headless browser for Target/Best Buy, else plain HTTP | just leave it on auto |
| `http` | Fast page fetch, reads JSON-LD/meta/text | Canon USA, B&H, Walmart |
| `api` | Official **Best Buy Developer API** (needs key + sku) | Best Buy — most reliable |
| `browser` | Renders the page in headless Chromium first | Target and other JS apps |

**Best Buy API (recommended for Best Buy):** grab a free key at
<https://developer.bestbuy.com/>, put it in `.env` as `BESTBUY_API_KEY`, and add
the numeric `sku` to each Best Buy listing (shown on the product page as
"SKU: 6377340"). This reads real-time price + `orderable` status straight from
Best Buy — no scraping, no bot-protection issues.

**Browser mode (for Target):** install it once with
`pip install playwright && playwright install chromium`. Then set `mode: browser`
on JS-rendered listings. It respects your `HTTPS_PROXY` and auto-detects Chromium;
override with `CANONBOT_CHROMIUM` if needed.

### Why some fetches say `unknown`

Because the bot refuses to evade bot-protection, a site can still refuse a
request (e.g. **Canon USA** sometimes returns HTTP 403). When that happens you'll
see `unknown` in the logs and it's treated as out of stock — that's the bot
failing safe, never a false "in stock!" ping.

## Discord alert style

Alerts are formatted to match the **Trend Radar 2.0** style: webhook posts with a
custom `username`/`avatar`, rich embeds with an emoji title, bold section headers,
bullet points, a divider, footer, and timestamp, color-coded green (at/below your
price) or orange (over your cap). It also handles Discord's 429 rate limits with
retry, sends an "ACTIVE" startup message when the worker boots, and paces
back-to-back alerts — same behavior as Trend Radar. Set `DISCORD_USERNAME` /
`DISCORD_AVATAR_URL` in `.env` to customize the look.

## Running 24/7

Three options, same deployment style as Trend Radar:

### 1. Render worker (recommended — continuous, fastest reaction)

`render.yaml` defines a Docker `worker` service. `config.yaml` is already in the
repo, so just deploy:

```bash
# In Render: New -> Blueprint -> pick this repo.
# Set DISCORD_WEBHOOK_URL (and BESTBUY_API_KEY etc.) in the dashboard.
```

State (`state.json`) persists on the worker disk, so a restart won't re-spam you.
The Docker image includes Chromium, so browser mode (Target) works here — this is
the most reliable option for all retailers.

### 2. GitHub Actions cron (free, zero-maintenance)

`.github/workflows/monitor.yml` runs `python run.py --once` every 5 minutes.
Add your secrets under **Repo Settings → Secrets and variables → Actions**
(`DISCORD_WEBHOOK_URL` is **required**, plus optionally `BESTBUY_API_KEY`,
`DISCORD_MENTION`). State is carried between runs with `actions/cache`.

**Reliability note:** the free Actions runner has no browser and no Best Buy key
by default, so Target (needs a browser) and Best Buy (blocks plain requests) will
report `unknown` and won't alert. To make this path genuinely useful, **add a
`BESTBUY_API_KEY` secret** and give your Best Buy listings a `sku` + `mode: api`
in `config.yaml` — that checks Best Buy reliably with no browser. For full
coverage of every retailer (incl. Target), use the **Render worker** above.
Scheduled runs can also be delayed under load, so this reacts a bit slower.

### 3. Any always-on box (VPS, Raspberry Pi)

```bash
# example systemd unit: /etc/systemd/system/canonbot.service
[Service]
WorkingDirectory=/opt/Canonbot
ExecStart=/opt/Canonbot/.venv/bin/python run.py
Restart=always
```

Or run the Docker image directly:

```bash
docker build -t canonbot . && docker run -d --env-file .env canonbot
```

## Testing

```bash
python tests/test_checkers.py
```

## A note on responsible use

This tool exists to get **you** a fair shot at buying **one** of these cameras at
retail price. Please keep it that way: reasonable poll rates, authorized
retailers, and a normal manual checkout. That's what keeps you inside every
retailer's terms and keeps stock reachable for the next person too.
