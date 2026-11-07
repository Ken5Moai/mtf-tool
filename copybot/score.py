"""
Rank leaders by what COPYING them actually made (after your delay, fees and
slippage) - not by their own PnL. Input: the JSONL log written by copybot.live.

  python -m copybot.score copybot_paper_log.jsonl

Rule of thumb: keep a leader only after >= 20 closed copies with a positive
total and a positive median. Drop anyone who fails, then re-run.
"""
from __future__ import annotations

import json
import statistics
import sys


def score(rows: list[dict], min_trades: int = 20) -> list[dict]:
    by = {}
    for a in rows:
        if a.get("kind") in ("sell", "tp", "stop", "lock") and "pnl" in a and a.get("leader"):
            by.setdefault(a["leader"], []).append(a)
    out = []
    for leader, xs in by.items():
        pnls = [x["pnl"] for x in xs]
        pcts = [x.get("pct", 0.0) for x in xs]
        total, med = sum(pnls), statistics.median(pcts)
        n = len(xs)
        if n < min_trades:
            verdict = f"WAIT ({n}/{min_trades})"
        elif total > 0 and med > 0:
            verdict = "KEEP"
        else:
            verdict = "DROP"
        out.append({"leader": leader, "copies": n, "total_pnl": round(total, 2),
                    "median_pct": round(med, 1), "win_rate": round(sum(p > 0 for p in pnls) / n * 100, 1),
                    "verdict": verdict})
    return sorted(out, key=lambda r: r["total_pnl"], reverse=True)


def main(argv=None):
    argv = argv or sys.argv[1:]
    path = argv[0] if argv else "copybot_paper_log.jsonl"
    with open(path) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    res = score(rows)
    if not res:
        print("No closed copies yet.")
        return
    print(f"{'leader':20} {'copies':>6} {'total $':>10} {'median %':>9} {'win %':>6}  verdict")
    for r in res:
        print(f"{r['leader']:20} {r['copies']:>6} {r['total_pnl']:>10.2f} {r['median_pct']:>9.1f} {r['win_rate']:>6.1f}  {r['verdict']}")


if __name__ == "__main__":
    main()
