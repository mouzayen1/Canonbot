"""The monitor loop: poll targets politely, alert on restock transitions."""

from __future__ import annotations

import json
import logging
import os
import random
import signal
import time

from . import checkers
from .checkers import StockResult
from .config import Config, ProductTarget
from .notifier import DiscordNotifier

log = logging.getLogger("canonbot")

STATE_PATH = os.environ.get("CANONBOT_STATE", "state.json")


class Monitor:
    def __init__(self, config: Config):
        self.config = config
        self.session = checkers.new_session()
        self.notifier = DiscordNotifier(config.webhook_url, config.mention)
        # Maps target.key -> last known status string ("in_stock"/"out_of_stock").
        self.last_status: dict[str, str] = self._load_state()
        self._running = True

    # --- state persistence so a restart doesn't re-spam you ---------------
    def _load_state(self) -> dict[str, str]:
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_state(self) -> None:
        try:
            with open(STATE_PATH, "w", encoding="utf-8") as fh:
                json.dump(self.last_status, fh, indent=2)
        except OSError as exc:
            log.warning("Could not persist state: %s", exc)

    # --- shutdown handling -------------------------------------------------
    def stop(self, *_args) -> None:
        log.info("Shutdown requested — finishing current sweep.")
        self._running = False

    # --- core logic --------------------------------------------------------
    def _handle_result(self, target: ProductTarget, result: StockResult) -> None:
        settings = self.config.settings

        status = result.status
        if status == checkers.UNKNOWN:
            status = (
                checkers.OUT_OF_STOCK
                if settings.treat_unknown_as_out_of_stock
                else checkers.IN_STOCK
            )

        previous = self.last_status.get(target.key)
        self.last_status[target.key] = status

        price_note = f"${result.price:,.2f}" if result.price is not None else "n/a"
        log.info(
            "%-40s %-16s %-13s price=%-10s (%s)",
            target.product_name[:40],
            target.retailer,
            status.upper(),
            price_note,
            result.detail,
        )

        # Only act on a transition INTO stock (out/unknown -> in).
        if status != checkers.IN_STOCK or previous == checkers.IN_STOCK:
            return

        within_budget = result.price is None or result.price <= target.max_price
        if not within_budget and not settings.alert_above_max_price:
            log.info(
                "In stock but $%.2f is over your $%.2f cap — not alerting (per config).",
                result.price,
                target.max_price,
            )
            return

        try:
            self.notifier.send_restock(
                product_name=target.product_name,
                retailer=target.retailer,
                url=target.url,
                price=result.price,
                max_price=target.max_price,
                currency=result.currency,
                within_budget=within_budget,
            )
            log.info("Discord alert sent for %s @ %s", target.product_name, target.retailer)
        except Exception as exc:  # noqa: BLE001 - never let a webhook error kill the loop
            log.error("Failed to send Discord alert: %s", exc)

    def _sweep(self) -> None:
        settings = self.config.settings
        for target in self.config.targets:
            if not self._running:
                break
            result = checkers.check_product(
                self.session, target.url, settings.request_timeout_seconds
            )
            self._handle_result(target, result)
            # Small jittered gap between individual requests within a sweep so we
            # don't fire them all at once.
            time.sleep(random.uniform(1.0, 3.0))
        self._save_state()

    def run(self) -> None:
        settings = self.config.settings
        log.info(
            "Canonbot watching %d listing(s), every ~%ds. Ctrl-C to stop.",
            len(self.config.targets),
            settings.poll_interval_seconds,
        )
        while self._running:
            self._sweep()
            if not self._running:
                break
            # Base interval + up to 50% jitter, so the cadence isn't robotic.
            delay = settings.poll_interval_seconds * random.uniform(1.0, 1.5)
            self._sleep_interruptibly(delay)

    def _sleep_interruptibly(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))


def install_signal_handlers(monitor: Monitor) -> None:
    signal.signal(signal.SIGINT, monitor.stop)
    signal.signal(signal.SIGTERM, monitor.stop)
