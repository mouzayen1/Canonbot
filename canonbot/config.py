"""Configuration loading and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

import yaml

# Retailers we consider first-party / authorized for these Canon cameras.
# The monitor refuses to watch anything outside this list so you never get
# steered toward a marketplace reseller charging over MSRP.
ALLOWED_RETAILERS = {
    "usa.canon.com": "Canon USA",
    "canon.com": "Canon USA",
    "bestbuy.com": "Best Buy",
    "target.com": "Target",
    "bhphotovideo.com": "B&H Photo",
    "adorama.com": "Adorama",
    "walmart.com": "Walmart",
    "bedfords.com": "Bedfords",
    "camerawholesalers.com": "Camera Wholesalers",
    "camcor.com": "Camcor",
}

MIN_POLL_INTERVAL_SECONDS = 30

# Valid detection modes per listing.
#   auto    - pick the best available: Best Buy API if key+sku, else browser for
#             JS-heavy sites (Target/Best Buy), else plain HTTP.
#   http    - fetch the page and read JSON-LD/meta/text (fast, no browser).
#   api     - Best Buy Developer API (needs BESTBUY_API_KEY + a sku).
#   browser - render the page in headless Chromium, then read it (for JS sites).
VALID_MODES = {"auto", "http", "api", "browser"}

# Retailer hosts we know render stock client-side, so plain HTTP often can't
# read them. In `auto` mode these prefer the browser checker.
JS_HEAVY_HOSTS = {"target.com", "bestbuy.com"}


@dataclass
class Settings:
    poll_interval_seconds: int = 90
    priority_poll_interval_seconds: int = 45
    request_timeout_seconds: int = 20
    max_backoff_seconds: int = 900
    heartbeat_hours: float = 12.0
    degraded_alert_after_minutes: int = 60
    # False-positive guard: a single "in stock" read can be backend noise (some
    # retailer servers briefly report IN_STOCK for an item that isn't buyable).
    # Require this many extra confirming reads, all in stock, before alerting.
    confirm_reads: int = 3
    confirm_delay_seconds: float = 1.5
    # Don't alert the same listing more than once within this window.
    alert_cooldown_minutes: int = 20
    alert_above_max_price: bool = False
    treat_unknown_as_out_of_stock: bool = True


@dataclass
class ProductTarget:
    """A single (product, retailer-url) pair to monitor."""

    product_name: str
    url: str
    max_price: float
    retailer: str  # human-readable, resolved from the URL host
    host: str  # bare host, e.g. "bestbuy.com"
    sku: str | None = None  # Best Buy numeric SKU, enables the API checker
    mode: str = "auto"
    priority: bool = False  # poll at the faster priority interval
    interval_seconds: float | None = None  # explicit per-listing poll interval
    best_effort: bool = False  # gentle: no degraded alerts when it can't be read

    @property
    def key(self) -> str:
        return f"{self.product_name}|{self.url}"


@dataclass
class Config:
    settings: Settings
    targets: list[ProductTarget] = field(default_factory=list)
    webhook_url: str = ""
    mention: str = ""
    bestbuy_api_key: str = ""
    target_api_key: str = ""
    discord_username: str = "Canonbot 📷"
    discord_avatar_url: str = ""


def _retailer_for_url(url: str) -> tuple[str, str] | None:
    """Return (retailer_label, bare_domain) if the URL is an authorized host."""
    host = (urlparse(url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    for domain, label in ALLOWED_RETAILERS.items():
        if host == domain or host.endswith("." + domain):
            return label, domain
    return None


def load_config(path: str) -> Config:
    """Load and validate config.yaml + environment secrets."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    settings_raw = raw.get("settings", {}) or {}
    settings = Settings(
        poll_interval_seconds=max(
            MIN_POLL_INTERVAL_SECONDS,
            int(settings_raw.get("poll_interval_seconds", 90)),
        ),
        priority_poll_interval_seconds=max(
            MIN_POLL_INTERVAL_SECONDS,
            int(settings_raw.get("priority_poll_interval_seconds", 45)),
        ),
        request_timeout_seconds=int(settings_raw.get("request_timeout_seconds", 20)),
        max_backoff_seconds=int(settings_raw.get("max_backoff_seconds", 900)),
        heartbeat_hours=float(settings_raw.get("heartbeat_hours", 12)),
        degraded_alert_after_minutes=int(
            settings_raw.get("degraded_alert_after_minutes", 60)
        ),
        confirm_reads=max(0, int(settings_raw.get("confirm_reads", 3))),
        confirm_delay_seconds=float(settings_raw.get("confirm_delay_seconds", 1.5)),
        alert_cooldown_minutes=int(settings_raw.get("alert_cooldown_minutes", 20)),
        alert_above_max_price=bool(settings_raw.get("alert_above_max_price", False)),
        treat_unknown_as_out_of_stock=bool(
            settings_raw.get("treat_unknown_as_out_of_stock", True)
        ),
    )

    targets: list[ProductTarget] = []
    errors: list[str] = []
    for product in raw.get("products", []) or []:
        name = product.get("name")
        max_price = product.get("max_price")
        if not name or max_price is None:
            errors.append(f"Product entry missing 'name' or 'max_price': {product!r}")
            continue
        for entry in product.get("urls", []) or []:
            # An entry can be a plain URL string, or a dict with url/sku/mode/priority.
            if isinstance(entry, str):
                url, sku, mode, priority = entry, None, "auto", False
                interval_seconds, best_effort = None, False
            elif isinstance(entry, dict):
                url = entry.get("url", "")
                sku = entry.get("sku")
                sku = str(sku) if sku is not None else None
                mode = str(entry.get("mode", "auto")).lower()
                priority = bool(entry.get("priority", False))
                interval_seconds = entry.get("interval_seconds")
                if interval_seconds is not None:
                    interval_seconds = max(
                        MIN_POLL_INTERVAL_SECONDS, float(interval_seconds)
                    )
                best_effort = bool(entry.get("best_effort", False))
            else:
                errors.append(f"Unrecognized listing entry: {entry!r}")
                continue

            if mode not in VALID_MODES:
                errors.append(f"Invalid mode '{mode}' for {url} (use {VALID_MODES})")
                continue

            resolved = _retailer_for_url(url)
            if resolved is None:
                errors.append(
                    f"Refusing to monitor non-authorized retailer URL "
                    f"(not in the allowed list): {url}"
                )
                continue
            retailer, host = resolved

            if mode == "api" and host != "bestbuy.com":
                errors.append(f"mode 'api' is Best Buy-only, but {url} is {retailer}")
                continue

            targets.append(
                ProductTarget(
                    product_name=name,
                    url=url,
                    max_price=float(max_price),
                    retailer=retailer,
                    host=host,
                    sku=sku,
                    mode=mode,
                    priority=priority,
                    interval_seconds=interval_seconds,
                    best_effort=best_effort,
                )
            )

    if errors:
        raise ValueError(
            "Config problems found:\n  - " + "\n  - ".join(errors)
        )
    if not targets:
        raise ValueError("No valid product URLs to monitor. Check config.yaml.")

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise ValueError(
            "DISCORD_WEBHOOK_URL is not set. Copy .env.example to .env and fill it in."
        )

    return Config(
        settings=settings,
        targets=targets,
        webhook_url=webhook_url,
        mention=os.environ.get("DISCORD_MENTION", "").strip(),
        bestbuy_api_key=os.environ.get("BESTBUY_API_KEY", "").strip(),
        target_api_key=os.environ.get("TARGET_API_KEY", "").strip(),
        discord_username=os.environ.get("DISCORD_USERNAME", "Canonbot 📷").strip()
        or "Canonbot 📷",
        discord_avatar_url=os.environ.get("DISCORD_AVATAR_URL", "").strip(),
    )
