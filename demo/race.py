"""Race the two bots over many sandbox drops and report the aggregate.

A single drop is luck (a slow poller can happen to land right on it). Run many
drops with jittered timing and the real advantage shows: the optimized bot wins
the large majority and sees the drop ~20x sooner on average.

Usage:
  python demo/race.py                 # 12 rounds, 1 unit each
  python demo/race.py --rounds 20
  python demo/race.py --stock 2 --naive 3 --rounds 1   # single custom scenario
"""

from __future__ import annotations

import argparse
import random
import statistics
import threading

from bots import fast_bot, naive_bot
from mock_store import StoreState, start_store


def run_round(stock: int, naive_n: int, fast_n: int, latency: float) -> dict:
    drop_delay = random.uniform(0.6, 1.4)  # jitter so no poll cadence lines up systematically
    state = StoreState(stock=stock, drop_delay=drop_delay, latency=latency)
    httpd = start_store(state)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    drop_at = state.drop_at

    results: dict = {}
    threads = []
    for i in range(fast_n):
        threads.append(threading.Thread(
            target=fast_bot, args=(base, results, "fast" if fast_n == 1 else f"fast-{i+1}")))
    for i in range(naive_n):
        threads.append(threading.Thread(
            target=naive_bot, args=(base, results, "naive" if naive_n == 1 else f"naive-{i+1}")))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    httpd.shutdown()
    return {name: {**r, "detect_ms": (r["detect"] - drop_at) * 1000 if r["detect"] else None}
            for name, r in results.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--stock", type=int, default=1)
    ap.add_argument("--naive", type=int, default=1)
    ap.add_argument("--fast", type=int, default=1)
    ap.add_argument("--latency", type=float, default=0.005)
    args = ap.parse_args()

    print(f"\nRacing {args.fast} fast vs {args.naive} naive over {args.rounds} drops "
          f"({args.stock} unit(s) each)...\n")

    wins: dict = {}
    detect_samples: dict = {}
    print(f"{'round':>5}  winner")
    for rnd in range(1, args.rounds + 1):
        res = run_round(args.stock, args.naive, args.fast, args.latency)
        round_winners = [n for n, r in res.items() if r["got"]]
        for n in round_winners:
            wins[n] = wins.get(n, 0) + 1
        for n, r in res.items():
            if r["detect_ms"] is not None:
                detect_samples.setdefault(n, []).append(r["detect_ms"])
        print(f"{rnd:>5}  {', '.join(round_winners) if round_winners else 'nobody'}")

    print("\n" + "=" * 46)
    print("RESULTS")
    print("=" * 46)
    all_names = sorted(set(list(wins) + list(detect_samples)))
    print(f"{'BOT':<10} {'WINS':>6} {'AVG DETECT':>14}")
    print("-" * 32)
    for n in sorted(all_names, key=lambda x: -wins.get(x, 0)):
        avg = statistics.mean(detect_samples[n]) if detect_samples.get(n) else float("nan")
        print(f"{n:<10} {wins.get(n, 0):>4}/{args.rounds:<2} {avg:>10.0f} ms")

    fast_names = [n for n in all_names if n.startswith("fast")]
    fast_wins = sum(wins.get(n, 0) for n in fast_names)
    print(f"\nFast bot(s) won {fast_wins}/{args.rounds} drops "
          f"({100*fast_wins/args.rounds:.0f}%).\n")


if __name__ == "__main__":
    main()
