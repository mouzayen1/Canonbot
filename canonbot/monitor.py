"""The monitor loop: poll targets politely, alert on restock transitions.

Uses an independent per-listing scheduler so priority listings (e.g. Canon and
Target) can be checked more often than the rest. When a retailer blocks or times
out, that one listing backs off exponentially (up to a cap) instead of hammering
the site, while every other listing keeps scanning normally. A periodic
heartbeat and a degraded-monitoring warning keep you sure it's alive 24/7.
"""

from __future__ import annotations

import json
import logging
import os
import random
import signal
import time
from dataclasses import dataclass, field

from . import checkers
from .checkers import StockResult
from .config import Config, ProductTarget
from .notifier import DiscordNotifier

log = logging.getLogger("canonbot")

STATE_PATH = os.environ.get("CANONBOT_STATE", "state.json")

# HTTP statuses (or a network error -> None) that mean "the site pushed back";
# these trigger per-listing backoff rather than a normal re-check interval.
_BLOCK_STATUSES = {403, 429, 500, 502, 503, 504}


@dataclass
class _Listing:
    """Per-listing runtime scheduling state (not persisted)."""

    target: ProductTarget
    base_interval: float
    next_due: float = 0.0        # time.monotonic() deadline
    failure_streak: int = 0
    last_success_at: float = 0.0  # last definitive in/out read
    degraded_notified: bool = False


class Monitor:
    def __init__(self, config: Config):
        self.config = config
        self.session = checkers.new_session()
        self.notifier = DiscordNotifier(
            config.webhook_url,
            config.mention,
            username=config.discord_username,
            avatar_url=config.discord_avatar_url,
        )
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
            result = checkers.check_target(
                self.session,
                target,
                settings.request_timeout_seconds,
                self.config.bestbuy_api_key,
                self.config.target_api_key,
            )
            self._handle_result(target, result)
            # Small jittered gap between individual requests within a sweep so we
            # don't fire them all at once.
            time.sleep(random.uniform(1.0, 3.0))
        self._save_state()

    # --- per-listing check + scheduling -----------------------------------
    def _check_listing(self, ls: _Listing) -> StockResult:
        """Run one check for a listing and route it through the alert logic."""
        result = checkers.check_target(
            self.session,
            ls.target,
            self.config.settings.request_timeout_seconds,
            self.config.bestbuy_api_key,
            self.config.target_api_key,
        )
        self._handle_result(ls.target, result)
        return result

    def _reschedule(self, ls: _Listing, result: StockResult) -> None:
        """Update a listing's next check time, backing off if the site blocked us."""
        settings = self.config.settings
        now = time.monotonic()
        blocked = result.http_status in _BLOCK_STATUSES or result.http_status is None

        if blocked:
            ls.failure_streak += 1
            interval = min(
                ls.base_interval * (2 ** ls.failure_streak),
                float(settings.max_backoff_seconds),
            )
            log.warning(
                "%s @ %s unreadable (%s) — backing off to ~%ds (streak %d)",
                ls.target.product_name[:30], ls.target.retailer,
                result.detail, int(interval), ls.failure_streak,
            )
        else:
            ls.failure_streak = 0
            interval = ls.base_interval

        if result.status in (checkers.IN_STOCK, checkers.OUT_OF_STOCK):
            ls.last_success_at = now
            if ls.degraded_notified:
                ls.degraded_notified = False
                self._safe_notify(
                    lambda: self.notifier.send_recovered(
                        ls.target.retailer, ls.target.product_name
                    ),
                    "recovered",
                )

        self._maybe_degraded(ls, now, result)
        ls.next_due = now + interval * random.uniform(1.0, 1.3)

    def _maybe_degraded(self, ls: _Listing, now: float, result: StockResult) -> None:
        """Warn once (per outage) if a listing has been unreadable too long."""
        # Best-effort listings (e.g. Target, which CAPTCHA-blocks polling) are
        # expected to fail sometimes — stay quiet instead of nagging.
        if ls.target.best_effort:
            return
        threshold = self.config.settings.degraded_alert_after_minutes * 60
        stale_for = now - ls.last_success_at
        if stale_for > threshold and not ls.degraded_notified:
            ls.degraded_notified = True
            self._safe_notify(
                lambda: self.notifier.send_degraded(
                    ls.target.retailer, ls.target.product_name, result.detail
                ),
                "degraded",
            )
            log.error(
                "%s @ %s degraded: no clean read for %.0f min",
                ls.target.product_name[:30], ls.target.retailer, stale_for / 60,
            )

    def _safe_notify(self, fn, label: str) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to send %s notice: %s", label, exc)

    def run(self, announce: bool = True) -> None:
        settings = self.config.settings
        listings = [
            _Listing(
                target=t,
                base_interval=(
                    t.interval_seconds
                    if t.interval_seconds is not None
                    else settings.priority_poll_interval_seconds
                    if t.priority
                    else settings.poll_interval_seconds
                ),
                last_success_at=time.monotonic(),
            )
            for t in self.config.targets
        ]
        # Stagger the first checks so we don't fire every request at once.
        start = time.monotonic()
        for i, ls in enumerate(listings):
            ls.next_due = start + i * 2.0

        n_priority = sum(1 for ls in listings if ls.target.priority)
        log.info(
            "Canonbot watching %d listing(s) (%d priority @ ~%ds, rest @ ~%ds). Ctrl-C to stop.",
            len(listings), n_priority,
            settings.priority_poll_interval_seconds, settings.poll_interval_seconds,
        )
        if announce:
            self._safe_notify(
                lambda: self.notifier.send_startup(
                    len(listings), settings.priority_poll_interval_seconds
                ),
                "startup",
            )

        last_heartbeat = time.monotonic()
        while self._running:
            now = time.monotonic()
            for ls in listings:
                if not self._running:
                    break
                if now >= ls.next_due:
                    result = self._check_listing(ls)
                    self._reschedule(ls, result)
                    # Small global pacing gap between real requests.
                    self._interruptible_sleep(random.uniform(0.5, 1.5))
                    now = time.monotonic()

            self._save_state()
            last_heartbeat = self._maybe_heartbeat(listings, last_heartbeat)

            # Sleep until the soonest listing is due (bounded), interruptibly.
            if not self._running:
                break
            soonest = min(ls.next_due for ls in listings) - time.monotonic()
            self._interruptible_sleep(max(1.0, min(soonest, 15.0)))

    def _maybe_heartbeat(self, listings: list[_Listing], last_heartbeat: float) -> float:
        settings = self.config.settings
        if settings.heartbeat_hours <= 0:
            return last_heartbeat
        now = time.monotonic()
        if now - last_heartbeat < settings.heartbeat_hours * 3600:
            return last_heartbeat
        lines = []
        for ls in listings:
            status = self.last_status.get(ls.target.key, "unknown")
            flag = "🚨" if status == checkers.IN_STOCK else "•"
            lines.append(f"{flag} {ls.target.retailer}: {ls.target.product_name[:32]} — {status}")
        self._safe_notify(
            lambda: self.notifier.send_heartbeat(len(listings), lines), "heartbeat"
        )
        log.info("Heartbeat sent.")
        return now

    def _interruptible_sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))


def install_signal_handlers(monitor: Monitor) -> None:
    signal.signal(signal.SIGINT, monitor.stop)
    signal.signal(signal.SIGTERM, monitor.stop)
