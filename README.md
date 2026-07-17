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
- **`settings.poll_interval_seconds`** — seconds between checks for a normal
  listing (min 30, default 90). Jitter is added so the cadence isn't robotic.
- **`settings.priority_poll_interval_seconds`** — faster cadence (default 45s)
  for listings marked `priority: true` — e.g. Canon and Target.
- **`settings.max_backoff_seconds`**, **`heartbeat_hours`**,
  **`degraded_alert_after_minutes`** — see "24/7 reliability" below.
- **`settings.alert_above_max_price`** — set `true` if you also want a
  (clearly-flagged) heads-up when an item is in stock but over your cap.

Secrets go in `.env` (never committed):
- **`DISCORD_WEBHOOK_URL`** — required.
- **`DISCORD_MENTION`** — optional, e.g. `@everyone` or `<@&ROLE_ID>` to get pinged.
- **`BESTBUY_API_KEY`** — optional, only for Best Buy listings.
- **`TARGET_API_KEY`** — optional; a stable default is built in.

## How stock detection works

Each retailer is read the most reliable, non-evasive way — the same public data
its own product page uses. In `auto` mode the right method is picked per site:

| Retailer | How it's read | Browser needed? |
|---|---|---|
| **Target** | Public **RedSky** fulfillment JSON API (the API target.com's own pages call) | No |
| **Canon USA** | Canon's **Magento GraphQL** backend (`cusa-prod.usa.canon.com/graphql`) | No |
| **Best Buy** | Official **Best Buy Developer API** if you set a key + `sku`, else headless browser | Only as fallback |
| **B&H / Walmart / other** | schema.org JSON-LD → meta tags → text heuristics | No |

Both of the priority sites — Canon and Target — are read via plain JSON API
calls, so they're fast, reliable, and work everywhere (including free CI runners,
no browser required).

**Why not just scrape the pages?** Target renders stock client-side, and Canon's
storefront sits behind Akamai + a Queue-It waiting room that 403s plain requests
(and can even trap a headless browser). Their *backends* — the same ones the site
itself reads — return clean JSON to a normal request. That's what Canonbot uses.

If a read is ever ambiguous or refused, it's reported as **unknown** and treated
as out of stock, so you never get a false "in stock!" ping.

### Detection modes (per listing)

Leave `mode: auto` (recommended) and the table above applies. You can override:
`http` (plain page fetch / API), `api` (Best Buy Developer API, needs key + sku),
or `browser` (force headless Chromium — needs `pip install playwright &&
playwright install chromium`).

## 24/7 reliability

Built to run unattended and stay honest about its own health:

- **Independent per-listing scheduling** — each listing has its own timer, so a
  slow or blocked retailer never holds up the others, and `priority: true`
  listings (Canon, Target) are checked on the faster interval.
- **Exponential backoff** — if a retailer starts blocking or timing out, *that*
  listing backs off (up to `max_backoff_seconds`) instead of hammering it; the
  rest keep scanning. It recovers automatically.
- **Heartbeat** — every `heartbeat_hours` (default 12) it posts a Discord "still
  watching" summary so you know it's alive. Set to 0 to disable.
- **Degraded alert** — if a listing can't get a clean read for
  `degraded_alert_after_minutes` (e.g. Target's key rotated), it warns you once
  on Discord, then posts a recovery notice when it's readable again.
- **State persistence** — `state.json` remembers what was in stock, so a restart
  never re-spams you.

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
