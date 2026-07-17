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

import json
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


def check_product(session: requests.Session, url: str, timeout: int) -> StockResult:
    """Fetch a product page and determine stock + price."""
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

    soup = BeautifulSoup(resp.text, "html.parser")

    result = _from_json_ld(soup)
    if result is not None:
        if result.price is None:
            result.price = _from_meta(soup)
        result.http_status = 200
        return result

    result = _from_text(soup)
    result.http_status = 200
    return result
