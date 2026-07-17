#!/usr/bin/env python3
"""Canonbot entry point.

Usage:
  python run.py                 # start monitoring (uses config.yaml + .env)
  python run.py --config x.yaml # use a different config file
  python run.py --once          # run a single sweep and exit (good for testing)
  python run.py --minutes 50    # scan continuously for 50 min then exit (CI burst)
  python run.py --no-startup    # skip the Discord "ACTIVE" message
  python run.py --test-webhook  # send a test message to Discord and exit
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading

from dotenv import load_dotenv

from canonbot.config import load_config
from canonbot.monitor import Monitor, install_signal_handlers
from canonbot.notifier import DiscordNotifier


def main() -> int:
    parser = argparse.ArgumentParser(description="Canonbot restock monitor")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML")
    parser.add_argument("--once", action="store_true", help="Run one sweep and exit")
    parser.add_argument(
        "--minutes", type=float, default=0.0,
        help="Scan continuously for N minutes then exit (0 = run forever)",
    )
    parser.add_argument(
        "--no-startup", action="store_true",
        help="Skip the Discord 'ACTIVE' startup message (use for scheduled bursts)",
    )
    parser.add_argument(
        "--test-webhook", action="store_true", help="Send a Discord test message and exit"
    )
    parser.add_argument(
        "--heartbeat", action="store_true",
        help="Send one 'still watching' Discord summary and exit (for a daily cron)",
    )
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    load_dotenv()

    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(
            f"Config file not found: {args.config}\n"
            "  Locally:  cp config.example.yaml config.yaml  (then edit it)\n"
            "  In CI:    config.yaml must be committed to the repo.",
            file=sys.stderr,
        )
        return 1
    except ValueError as exc:
        msg = str(exc)
        print(f"Configuration error: {msg}", file=sys.stderr)
        if "DISCORD_WEBHOOK_URL" in msg:
            print(
                "\nSet your Discord webhook:\n"
                "  Locally:  put DISCORD_WEBHOOK_URL in a .env file\n"
                "  In CI:    add it under Repo Settings -> Secrets and variables"
                " -> Actions -> New repository secret.",
                file=sys.stderr,
            )
        return 1

    if args.test_webhook:
        DiscordNotifier(config.webhook_url, config.mention).send_plain(
            "✅ Canonbot test message — your webhook works. Watching "
            f"{len(config.targets)} listing(s)."
        )
        print("Test message sent to Discord.")
        return 0

    monitor = Monitor(config)
    install_signal_handlers(monitor)

    if args.heartbeat:
        lines = []
        for t in config.targets:
            status = monitor.last_status.get(t.key, "unknown")
            flag = "🚨" if status == "in_stock" else "•"
            lines.append(f"{flag} {t.retailer}: {t.product_name[:32]} — {status}")
        monitor.notifier.send_heartbeat(len(config.targets), lines)
        print("Heartbeat sent.")
        return 0

    if args.once:
        monitor._sweep()  # noqa: SLF001 - intentional single sweep for testing
        return 0

    # Bounded run (CI burst): stop cleanly after N minutes.
    if args.minutes and args.minutes > 0:
        timer = threading.Timer(args.minutes * 60.0, monitor.stop)
        timer.daemon = True
        timer.start()
        logging.getLogger("canonbot").info(
            "Burst mode: scanning for ~%.0f minutes then exiting.", args.minutes
        )

    monitor.run(announce=not args.no_startup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
