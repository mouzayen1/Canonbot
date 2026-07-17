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


@dataclass
class Settings:
    poll_interval_seconds: int = 90
    request_timeout_seconds: int = 20
    alert_above_max_price: bool = False
    treat_unknown_as_out_of_stock: bool = True


@dataclass
class ProductTarget:
    """A single (product, retailer-url) pair to monitor."""

    product_name: str
    url: str
    max_price: float
    retailer: str  # human-readable, resolved from the URL host

    @property
    def key(self) -> str:
        return f"{self.product_name}|{self.url}"


@dataclass
class Config:
    settings: Settings
    targets: list[ProductTarget] = field(default_factory=list)
    webhook_url: str = ""
    mention: str = ""


def _retailer_for_url(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    for domain, label in ALLOWED_RETAILERS.items():
        if host == domain or host.endswith("." + domain):
            return label
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
        request_timeout_seconds=int(settings_raw.get("request_timeout_seconds", 20)),
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
        for url in product.get("urls", []) or []:
            retailer = _retailer_for_url(url)
            if retailer is None:
                errors.append(
                    f"Refusing to monitor non-authorized retailer URL "
                    f"(not in the allowed list): {url}"
                )
                continue
            targets.append(
                ProductTarget(
                    product_name=name,
                    url=url,
                    max_price=float(max_price),
                    retailer=retailer,
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
    )
