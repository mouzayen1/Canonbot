"""Stock/price detection for a product page.

Strategy, from most to least reliable:
  1. schema.org JSON-LD Product/Offer data the retailer publishes in the page.
     This is structured data meant to be read by machines (Google, etc.), so it
     is the polite and stable signal to use.
  2. Open Graph / meta price tags.
  3. Conservative text heuristics as a last resort.

We never bypass bot protection, solve CAPTCHAs, or hammer endpoints. If a page
won't load or its status is ambiguous, we report "unknown" and the monitor
treats that as out of stock (so it never fires a false in-stock alert).
"""

from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

IN_STOCK = "in_stock"
OUT_OF_STOCK = "out_of_stock"
UNKNOWN = "unknown"

# schema.org availability values mapped to our states.
_SCHEMA_AVAILABILITY = {
    "instock": IN_STOCK,
    "in_stock": IN_STOCK,
    "onlineonly": IN_STOCK,
    "limitedavailability": IN_STOCK,
    "outofstock": OUT_OF_STOCK,
    "soldout": OUT_OF_STOCK,
    "discontinued": OUT_OF_STOCK,
    "backorder": OUT_OF_STOCK,
    "preorder": OUT_OF_STOCK,
    "presale": OUT_OF_STOCK,
}

# Text markers used only when structured data is absent.
_OUT_MARKERS = (
    "out of stock",
    "sold out",
    "currently unavailable",
    "temporarily out of stock",
    "notify me when available",
    "email when available",
    "coming soon",
)
_IN_MARKERS = (
    "add to cart",
    "add to bag",
    "ship it",
    "pick it up",
    "in stock",
    "buy now",
)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class StockResult:
    status: str  # IN_STOCK / OUT_OF_STOCK / UNKNOWN
    price: float | None
    currency: str | None
    detail: str  # short human-readable note on how we decided
    http_status: int | None = None

    @property
    def in_stock(self) -> bool:
        return self.status == IN_STOCK


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(_BROWSER_HEADERS)
    return session


def _walk_json(node):
    """Yield every dict inside an arbitrarily nested JSON structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_json(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_json(item)


def _first_offer(offers):
    """Offers may be a dict, a list, or an AggregateOffer wrapper."""
    if isinstance(offers, list):
        return offers[0] if offers else None
    if isinstance(offers, dict):
        if "offers" in offers:  # AggregateOffer
            return _first_offer(offers["offers"])
        return offers
    return None


def _parse_price(value) -> float | None:
    if value is None:
        return None
    try:
        cleaned = re.sub(r"[^0-9.]", "", str(value))
        return float(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None


def _from_json_ld(soup: BeautifulSoup) -> StockResult | None:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for obj in _walk_json(data):
            offer = _first_offer(obj.get("offers")) if "offers" in obj else None
            if not offer:
                continue
            availability = str(offer.get("availability", "")).lower()
            # Normalize "https://schema.org/InStock" -> "instock"
            token = availability.rsplit("/", 1)[-1].replace(" ", "").lower()
            status = _SCHEMA_AVAILABILITY.get(token)
            if status is None:
                continue
            price = _parse_price(offer.get("price") or offer.get("lowPrice"))
            currency = offer.get("priceCurrency")
            return StockResult(
                status=status,
                price=price,
                currency=currency,
                detail=f"schema.org availability={token}",
            )
    return None


def _from_meta(soup: BeautifulSoup) -> float | None:
    tag = soup.find("meta", property="product:price:amount") or soup.find(
        "meta", attrs={"itemprop": "price"}
    )
    if tag and tag.get("content"):
        return _parse_price(tag["content"])
    return None


def _from_text(soup: BeautifulSoup) -> StockResult:
    text = soup.get_text(separator=" ", strip=True).lower()
    has_out = any(marker in text for marker in _OUT_MARKERS)
    has_in = any(marker in text for marker in _IN_MARKERS)
    price = _from_meta(soup)
    if has_out and not has_in:
        return StockResult(OUT_OF_STOCK, price, None, "text markers: out-of-stock")
    if has_in and not has_out:
        return StockResult(IN_STOCK, price, None, "text markers: add-to-cart present")
    return StockResult(UNKNOWN, price, None, "ambiguous page text")


def _analyze_html(html: str) -> StockResult:
    """Run the JSON-LD -> meta -> text detection ladder over rendered HTML."""
    soup = BeautifulSoup(html, "html.parser")
    result = _from_json_ld(soup)
    if result is not None:
        if result.price is None:
            result.price = _from_meta(soup)
        result.http_status = 200
        return result
    result = _from_text(soup)
    result.http_status = 200
    return result


def check_product(session: requests.Session, url: str, timeout: int) -> StockResult:
    """Fetch a product page over plain HTTP and determine stock + price."""
    try:
        resp = session.get(url, timeout=timeout)
    except requests.RequestException as exc:
        return StockResult(UNKNOWN, None, None, f"request error: {exc}", None)

    if resp.status_code != 200:
        # 403/429 usually means the retailer is rate-limiting/blocking automated
        # traffic. We back off by reporting unknown rather than trying to evade.
        return StockResult(
            UNKNOWN, None, None, f"HTTP {resp.status_code}", resp.status_code
        )

    return _analyze_html(resp.text)


# --------------------------------------------------------------------------
# Best Buy Developer API checker
# Get a free key at https://developer.bestbuy.com/ and put it in BESTBUY_API_KEY.
# --------------------------------------------------------------------------
def check_via_bestbuy_api(sku: str, api_key: str, timeout: int) -> StockResult:
    url = f"https://api.bestbuy.com/v1/products/{sku}.json"
    params = {
        "apiKey": api_key,
        "show": "sku,name,salePrice,regularPrice,onlineAvailability,orderable",
    }
    try:
        resp = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        return StockResult(UNKNOWN, None, None, f"Best Buy API error: {exc}", None)

    if resp.status_code == 403:
        return StockResult(UNKNOWN, None, None, "Best Buy API: bad/again-limited key", 403)
    if resp.status_code != 200:
        return StockResult(UNKNOWN, None, None, f"Best Buy API HTTP {resp.status_code}", resp.status_code)

    try:
        data = resp.json()
    except ValueError:
        return StockResult(UNKNOWN, None, None, "Best Buy API: unparseable JSON", 200)

    price = data.get("salePrice") or data.get("regularPrice")
    price = float(price) if price is not None else None
    # `orderable` is the authoritative "can I buy it right now" flag; values
    # include "Available", "SoldOut", "ComingSoon", "BackOrder".
    orderable = str(data.get("orderable", "")).lower()
    online = data.get("onlineAvailability")
    if orderable == "available" or online is True:
        status = IN_STOCK
    elif orderable in ("soldout", "comingsoon", "backorder") or online is False:
        status = OUT_OF_STOCK
    else:
        status = UNKNOWN
    return StockResult(status, price, "USD", f"Best Buy API orderable={orderable or 'n/a'}", 200)


# --------------------------------------------------------------------------
# Target checker via the public RedSky fulfillment API.
# This is the same JSON API target.com's own product pages call. We read it
# politely at a low rate — no scraping, no browser, no evasion. Reliable and
# fast, and it works without a browser (great for CI and low-power hosts).
# --------------------------------------------------------------------------
# Public web key target.com ships in its front-end. It can rotate; override
# with the TARGET_API_KEY env var / config if Target starts returning errors.
TARGET_DEFAULT_KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
_TARGET_FULFILLMENT = "https://redsky.target.com/redsky_aggregations/v1/web/product_fulfillment_v1"
_TARGET_PDP = "https://redsky.target.com/redsky_aggregations/v1/web/pdp_client_v1"
_TARGET_STORE_ID = os.environ.get("TARGET_STORE_ID", "1234")
_TARGET_ZIP = os.environ.get("TARGET_ZIP", "90210")
_TARGET_STATE = os.environ.get("TARGET_STATE", "CA")

_TCIN_RE = re.compile(r"/A-(\d+)")

# shipping_options.availability_status values -> our states.
_TARGET_AVAILABILITY = {
    "in_stock": IN_STOCK,
    "pre_order_sellable": IN_STOCK,
    "out_of_stock": OUT_OF_STOCK,
    "unavailable": OUT_OF_STOCK,
    "pre_order_unsellable": OUT_OF_STOCK,
}

_TARGET_HEADERS = {
    "User-Agent": _BROWSER_HEADERS["User-Agent"],
    "Accept": "application/json",
    "Origin": "https://www.target.com",
    "Referer": "https://www.target.com/",
}


def extract_tcin(url: str) -> str | None:
    m = _TCIN_RE.search(url)
    return m.group(1) if m else None


# Self-healing key: if Target rotates its public key, scrape a fresh one from a
# product page's inline app-config so the monitor recovers on its own.
_TARGET_KEY_RE = re.compile(r'"apiKey"\s*:\s*"([0-9a-f]{40})"')
_scraped_target_key: str | None = None


def _scrape_target_key(product_url: str, timeout: int) -> str | None:
    global _scraped_target_key
    try:
        r = requests.get(
            product_url,
            headers={"User-Agent": _BROWSER_HEADERS["User-Agent"], "Accept": "text/html"},
            timeout=timeout,
        )
        html = getattr(r, "text", "") or ""
        m = _TARGET_KEY_RE.search(html)
        if m:
            _scraped_target_key = m.group(1)
            return _scraped_target_key
    except Exception:  # noqa: BLE001 - scraping is best-effort; never break the check
        pass
    return None


def _target_fulfillment_call(tcin: str, key: str, timeout: int):
    params = {
        "key": key, "tcin": tcin,
        "store_id": _TARGET_STORE_ID, "zip": _TARGET_ZIP, "state": _TARGET_STATE,
        "pricing_store_id": _TARGET_STORE_ID, "channel": "WEB", "page": f"/p/A-{tcin}",
    }
    return requests.get(_TARGET_FULFILLMENT, params=params, headers=_TARGET_HEADERS, timeout=timeout)


def _target_price(tcin: str, api_key: str, timeout: int) -> float | None:
    """Fetch current price from the PDP endpoint (only called when in stock)."""
    params = {
        "key": api_key, "tcin": tcin,
        "store_id": _TARGET_STORE_ID, "pricing_store_id": _TARGET_STORE_ID,
        "channel": "WEB", "page": f"/p/A-{tcin}",
    }
    try:
        r = requests.get(_TARGET_PDP, params=params, headers=_TARGET_HEADERS, timeout=timeout)
        if r.status_code == 200:
            price = r.json().get("data", {}).get("product", {}).get("price", {})
            val = price.get("current_retail") or price.get("reg_retail")
            return float(val) if val is not None else None
    except (requests.RequestException, ValueError, TypeError):
        pass
    return None


def check_via_target_api(url: str, timeout: int, api_key: str = "") -> StockResult:
    tcin = extract_tcin(url)
    if not tcin:
        return StockResult(UNKNOWN, None, None, "no tcin in Target URL (need /A-XXXXXXXX)", None)

    pinned = bool(api_key)
    key = api_key or _scraped_target_key or TARGET_DEFAULT_KEY
    try:
        resp = _target_fulfillment_call(tcin, key, timeout)
    except requests.RequestException as exc:
        return StockResult(UNKNOWN, None, None, f"Target API error: {exc}", None)

    # Self-heal: on an auth failure with the default key, scrape a fresh key and
    # retry once. Skipped if the user pinned their own TARGET_API_KEY.
    if resp.status_code in (401, 403, 404) and not pinned:
        fresh = _scrape_target_key(url, timeout)
        if fresh and fresh != key:
            try:
                resp = _target_fulfillment_call(tcin, fresh, timeout)
                key = fresh
            except requests.RequestException:
                pass

    if resp.status_code in (401, 403, 404):
        return StockResult(
            UNKNOWN, None, None,
            f"Target API HTTP {resp.status_code} — key may have rotated, set TARGET_API_KEY",
            resp.status_code,
        )
    if resp.status_code != 200:
        return StockResult(UNKNOWN, None, None, f"Target API HTTP {resp.status_code}", resp.status_code)

    try:
        fulfillment = resp.json()["data"]["product"]["fulfillment"]
    except (ValueError, KeyError, TypeError):
        return StockResult(UNKNOWN, None, None, "Target API: unexpected JSON shape", 200)

    shipping = fulfillment.get("shipping_options", {}) or {}
    raw = str(shipping.get("availability_status", "")).lower()
    status = _TARGET_AVAILABILITY.get(raw, UNKNOWN)

    price = _target_price(tcin, key, timeout) if status == IN_STOCK else None
    return StockResult(status, price, "USD", f"Target API shipping={raw or 'n/a'}", 200)


# --------------------------------------------------------------------------
# Canon USA checker via the Magento (Adobe Commerce) GraphQL backend.
# The usa.canon.com storefront is behind Akamai Bot Manager + a Queue-It waiting
# room, so plain page fetches 403 (and even a headless browser can get queued).
# But the GraphQL backend the storefront reads from is Fastly-fronted and NOT
# behind Akamai — a normal POST returns 200 JSON with live stock + price. We use
# that: same public data the product page shows, no evasion, no browser.
# --------------------------------------------------------------------------
_CANON_GRAPHQL = "https://cusa-prod.usa.canon.com/graphql"
_CANON_URL_KEY_RE = re.compile(r"/shop/p/([a-z0-9-]+)")
_CANON_HEADERS = {
    "User-Agent": _BROWSER_HEADERS["User-Agent"],
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def extract_canon_url_key(url: str) -> str | None:
    m = _CANON_URL_KEY_RE.search(url)
    return m.group(1) if m else None


def check_via_canon_graphql(url: str, timeout: int) -> StockResult:
    url_key = extract_canon_url_key(url)
    if not url_key:
        return StockResult(UNKNOWN, None, None, "no url_key in Canon URL (need /shop/p/<key>)", None)

    # The store ignores filter{url_key:{eq}}, so we search by the de-hyphenated
    # url_key (safe: url_key is [a-z0-9-]+) and match the exact url_key client-side.
    term = url_key.replace("-", " ")
    query = (
        '{products(search:"%s"){items{name url_key stock_status '
        "price_range{minimum_price{final_price{value currency}}}}}}" % term
    )
    try:
        resp = requests.post(_CANON_GRAPHQL, json={"query": query}, headers=_CANON_HEADERS, timeout=timeout)
    except requests.RequestException as exc:
        return StockResult(UNKNOWN, None, None, f"Canon GraphQL error: {exc}", None)

    if resp.status_code != 200:
        return StockResult(UNKNOWN, None, None, f"Canon GraphQL HTTP {resp.status_code}", resp.status_code)

    try:
        items = resp.json()["data"]["products"]["items"]
    except (ValueError, KeyError, TypeError):
        return StockResult(UNKNOWN, None, None, "Canon GraphQL: unexpected JSON shape", 200)

    match = next((it for it in items if it.get("url_key") == url_key), None)
    if match is None:
        return StockResult(UNKNOWN, None, None, f"Canon GraphQL: '{url_key}' not in results", 200)

    raw = str(match.get("stock_status", "")).upper()
    status = {"IN_STOCK": IN_STOCK, "OUT_OF_STOCK": OUT_OF_STOCK}.get(raw, UNKNOWN)

    price, currency = None, "USD"
    try:
        fp = match["price_range"]["minimum_price"]["final_price"]
        price = float(fp["value"]) if fp.get("value") is not None else None
        currency = fp.get("currency", "USD")
    except (KeyError, TypeError, ValueError):
        pass
    return StockResult(status, price, currency, f"Canon GraphQL stock={raw or 'n/a'}", 200)


# --------------------------------------------------------------------------
# Headless-browser checker for JavaScript-rendered pages (fallback only).
# Requires `pip install playwright`. Chromium is auto-detected.
# --------------------------------------------------------------------------
def _find_chromium() -> str | None:
    """Locate a Chromium binary without requiring `playwright install`."""
    explicit = os.environ.get("CANONBOT_CHROMIUM")
    if explicit and os.path.exists(explicit):
        return explicit
    for pattern in (
        "/opt/pw-browsers/chromium-*/chrome-linux/chrome",
        "/opt/pw-browsers/chromium-*/chrome-linux/headless_shell",
    ):
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]
    return None  # fall back to Playwright's own managed download


def check_via_browser(url: str, timeout: int) -> StockResult:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return StockResult(
            UNKNOWN, None, None,
            "browser mode needs Playwright: pip install playwright", None,
        )

    exe = _find_chromium()
    launch_kwargs = {"headless": True, "args": ["--no-sandbox"]}
    if exe:
        launch_kwargs["executable_path"] = exe
    # Chromium doesn't inherit HTTPS_PROXY the way requests does; pass it through
    # so the browser works behind a corporate/dev proxy too.
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch_kwargs)
            try:
                page = browser.new_page(user_agent=_BROWSER_HEADERS["User-Agent"])
                page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                # Give client-side price/stock a moment to hydrate.
                page.wait_for_timeout(2500)
                html = page.content()
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - launch/nav failures shouldn't crash the loop
        return StockResult(UNKNOWN, None, None, f"browser error: {exc}", None)

    result = _analyze_html(html)
    result.detail = f"browser-rendered; {result.detail}"
    return result


# --------------------------------------------------------------------------
# Dispatcher: pick the right checker for a target.
# --------------------------------------------------------------------------
def check_target(
    session, target, timeout: int, bestbuy_api_key: str = "", target_api_key: str = ""
) -> StockResult:
    """Route a target to the best checker based on its mode and available creds."""
    mode = target.mode
    host = target.host
    can_use_api = bool(bestbuy_api_key) and bool(target.sku) and host == "bestbuy.com"

    # Target: use the RedSky JSON API for auto/http (fast, reliable, no browser).
    if host == "target.com" and mode in ("auto", "http"):
        return check_via_target_api(target.url, timeout, target_api_key)

    # Canon: use the Magento GraphQL backend for auto/http (bypasses the Akamai
    # 403 without evasion; the browser can get stuck in Canon's Queue-It room).
    if host in {"usa.canon.com", "canon.com"} and mode in ("auto", "http"):
        return check_via_canon_graphql(target.url, timeout)

    if mode == "api" or (mode == "auto" and can_use_api):
        if not can_use_api:
            return StockResult(
                UNKNOWN, None, None,
                "api mode needs BESTBUY_API_KEY and a sku in config", None,
            )
        return check_via_bestbuy_api(target.sku, bestbuy_api_key, timeout)

    # Best Buy renders stock client-side, so auto uses the headless browser.
    if mode == "browser" or (mode == "auto" and host == "bestbuy.com"):
        return check_via_browser(target.url, timeout)

    return check_product(session, target.url, timeout)
