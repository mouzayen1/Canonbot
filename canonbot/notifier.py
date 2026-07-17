"""Discord webhook notifications.

Styled to match the Trend Radar 2.0 alert format: webhook posts with a
username + avatar, rich sectioned embeds (emoji title, bold section headers,
bullet points, a divider, footer, timestamp), per-type colors, and 429
rate-limit retry handling.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

_DIVIDER = "━━━━━━━━━━━━━━━━━━━━━━"
_RATE_LIMIT_DELAY = 3.0  # seconds between messages, mirrors Trend Radar

# Per-alert colors (same palette family as Trend Radar).
COLORS = {
    "in_stock": 0x2ECC71,     # Green  — in stock, at/below your price
    "over_price": 0xE67E22,   # Orange — in stock, over your price cap
    "startup": 0x2ECC71,      # Green  — active/startup
    "info": 0x3498DB,         # Blue   — info
}


class DiscordNotifier:
    def __init__(
        self,
        webhook_url: str,
        mention: str = "",
        username: str = "Canonbot 📷",
        avatar_url: str = "",
    ):
        self.webhook_url = webhook_url
        self.mention = mention
        self.username = username
        self.avatar_url = avatar_url
        self.session = requests.Session()

    # --- low-level send, with Discord 429 retry (matches Trend Radar) ------
    def send_discord(self, embed: dict, content: str = "", retries: int = 3) -> bool:
        payload: dict = {
            "embeds": [embed],
            "username": self.username,
        }
        if self.avatar_url:
            payload["avatar_url"] = self.avatar_url
        if content:
            payload["content"] = content

        for attempt in range(retries):
            try:
                resp = self.session.post(self.webhook_url, json=payload, timeout=10)
                if resp.status_code in (200, 204):
                    return True
                if resp.status_code == 429:
                    try:
                        retry_after = resp.json().get("retry_after", 2)
                    except ValueError:
                        retry_after = 2
                    time.sleep(float(retry_after) + 0.5)
                    continue
                # Other error — don't spin, just report.
                return False
            except requests.RequestException:
                if attempt < retries - 1:
                    time.sleep(1)
                    continue
                return False
        return False

    # --- embed builders ----------------------------------------------------
    def _restock_embed(
        self,
        *,
        product_name: str,
        retailer: str,
        url: str,
        price: float | None,
        max_price: float,
        currency: str | None,
        within_budget: bool,
    ) -> dict:
        cur = currency or "USD"
        if price is not None:
            price_str = f"{cur} ${price:,.2f}"
            budget_tag = (
                f" (at/below your ${max_price:,.2f} cap ✅)"
                if within_budget
                else f" (OVER your ${max_price:,.2f} cap ⚠️)"
            )
        else:
            price_str = "price unconfirmed"
            budget_tag = " (verify on the page)"

        if within_budget:
            title = f"🟢 IN STOCK: {product_name}"
            color = COLORS["in_stock"]
        else:
            title = f"🟠 IN STOCK (OVER MSRP): {product_name}"
            color = COLORS["over_price"]

        parts = [
            "**📷 What is it:**",
            product_name,
            "",
            "**🛒 Where & price:**",
            f"• Retailer: **{retailer}**",
            f"• Price: **{price_str}**{budget_tag}",
            f"• Your max: ${max_price:,.2f}",
            "",
            "**🔗 Buy it now:**",
            f"• {url}",
            "",
            _DIVIDER,
            "_Canonbot | Authorized-retailer restock_",
        ]

        return {
            "title": title,
            "url": url,
            "description": "\n".join(parts),
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Canonbot | Authorized retailers only • verify price before paying"},
        }

    # --- public API --------------------------------------------------------
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
    ) -> bool:
        embed = self._restock_embed(
            product_name=product_name,
            retailer=retailer,
            url=url,
            price=price,
            max_price=max_price,
            currency=currency,
            within_budget=within_budget,
        )
        ok = self.send_discord(embed, content=self.mention)
        time.sleep(_RATE_LIMIT_DELAY)  # pace back-to-back alerts, like Trend Radar
        return ok

    def send_startup(self, num_listings: int, poll_interval: int) -> bool:
        embed = {
            "title": "📷 Canonbot — ACTIVE",
            "description": (
                "Restock monitor is now watching authorized retailers.\n\n"
                f"**Watching:** {num_listings} listing(s)\n"
                f"**Check cadence:** every ~{poll_interval}s (jittered)\n\n"
                "**You'll be alerted when:**\n"
                "• An item is IN STOCK, and\n"
                "• The price is at or below your MSRP cap\n\n"
                "_Authorized/first-party retailers only. No marketplace resellers._"
            ),
            "color": COLORS["startup"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Canonbot | Cross-retailer restock monitor"},
        }
        return self.send_discord(embed)

    def send_plain(self, message: str) -> bool:
        return self.send_discord(
            {"description": message, "color": COLORS["info"]},
        )
