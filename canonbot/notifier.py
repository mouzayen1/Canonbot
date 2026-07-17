"""Discord webhook notifications."""

from __future__ import annotations

import requests

_GREEN = 0x2ECC71  # in stock, at/below your price
_ORANGE = 0xE67E22  # in stock, but over your price cap
_TIMEOUT = 15


class DiscordNotifier:
    def __init__(self, webhook_url: str, mention: str = ""):
        self.webhook_url = webhook_url
        self.mention = mention

    def _post(self, payload: dict) -> None:
        resp = requests.post(self.webhook_url, json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()

    def send_restock(
        self,
        *,
        product_name: str,
        retailer: str,
        url: str,
        price: float | None,
        max_price: float,
        currency: str | None,
        within_budget: bool,
    ) -> None:
        cur = currency or "USD"
        price_str = f"{cur} ${price:,.2f}" if price is not None else "price unconfirmed"
        if within_budget:
            color = _GREEN
            headline = "🟢 IN STOCK — at or below your price"
        else:
            color = _ORANGE
            headline = "🟠 IN STOCK — but OVER your max price"

        embed = {
            "title": product_name,
            "url": url,
            "description": f"**{headline}**",
            "color": color,
            "fields": [
                {"name": "Retailer", "value": retailer, "inline": True},
                {"name": "Price", "value": price_str, "inline": True},
                {"name": "Your max", "value": f"${max_price:,.2f}", "inline": True},
                {"name": "Buy it", "value": f"[Open product page]({url})", "inline": False},
            ],
            "footer": {"text": "Canonbot • verify the price on the page before you pay"},
        }
        payload: dict = {"embeds": [embed]}
        if self.mention:
            payload["content"] = self.mention
        self._post(payload)

    def send_plain(self, message: str) -> None:
        self._post({"content": message})
