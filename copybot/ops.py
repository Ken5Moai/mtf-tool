"""
Unattended PAPER operation (run by .github/workflows/copybot.yml every few hours).

One run =
  1. load state from DATA dir (bankroll, leader list, full paper log)
  2. score leaders on what copying them really made -> keep / drop / wait
  3. fill free slots with new candidates found on DexScreener trending tokens
  4. paper-follow the chosen wallets for --minutes, then close everything
  5. save state, write REPORT.md, send a one-line ntfy summary

  python -m copybot.ops --data data --minutes 340

No keys, no signing, no real orders: the bankroll is a number in state.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

import requests

from .engine import Config, CopyEngine
from .live import DexPrices, Solana, fee_payer, paper_loop, token_deltas
from .score import score

DEFAULTS = dict(budget=149.0, max_leaders=6, min_trades=20, delay_s=5.0, take_profit_x=10.0,
                size_frac=0.2, stop_loss_x=0.6, max_chase_pct=15.0)

# Program / router accounts that show up as signers or owners but are not traders.
NOT_TRADERS = {"11111111111111111111111111111111", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
               "ComputeBudget111111111111111111111111111111"}


def short(addr: str) -> str:
    return f"{addr[:4]}…{addr[-4:]}"


# ---------------- state ----------------
def load_state(data: Path) -> dict:
    p = data / "state.json"
    st = json.loads(p.read_text()) if p.exists() else {}
    st.setdefault("bankroll", DEFAULTS["budget"])
    st.setdefault("start_bankroll", DEFAULTS["budget"])
    st.setdefault("leaders", {})          # addr -> {status, added, source}
    st.setdefault("runs", [])             # [{start, end, bankroll_before, bankroll_after, ...}]
    return st


def save_state(data: Path, st: dict) -> None:
    (data / "state.json").write_text(json.dumps(st, indent=1))


def load_log(data: Path) -> list[dict]:
    p = data / "paper_log.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


# ---------------- discovery ----------------
def trending_mints(limit: int = 8) -> list[str]:
    out = []
    for url in ("https://api.dexscreener.com/token-boosts/top/v1",
                "https://api.dexscreener.com/token-profiles/latest/v1"):
        try:
            for b in requests.get(url, timeout=15).json():
                if b.get("chainId") == "solana" and b.get("tokenAddress") not in out:
                    out.append(b["tokenAddress"])
        except Exception as e:
            print(f"dexscreener error: {e}", file=sys.stderr)
    return out[:limit]


def discover(sol: Solana, exclude: set, want: int, mints_n: int = 6, per_mint: int = 12,
             budget_s: float = 420) -> dict:
    """Wallets that recently traded trending tokens, minus obvious bots. -> {addr: source_mint}"""
    t_end = time.time() + budget_s
    seen: Counter = Counter()
    source = {}
    for mint in trending_mints(mints_n):
        if time.time() > t_end:
            break
        try:
            sigs = sol.signatures(mint, limit=per_mint)
        except Exception as e:
            print(f"discover sigs error: {e}", file=sys.stderr)
            continue
        for s in sigs:
            if s.get("err") or time.time() > t_end:
                continue
            try:
                tx = sol.tx(s["signature"])
            except Exception as e:
                print(f"discover tx error: {e}", file=sys.stderr)
                continue
            payer = fee_payer(tx)
            if not payer or payer in exclude or payer in NOT_TRADERS:
                continue
            if mint in token_deltas(tx, payer):
                seen[payer] += 1
                source.setdefault(payer, mint)
    picked = {}
    for addr, _ in seen.most_common():
        if len(picked) >= want or time.time() > t_end:
            break
        try:                                   # bot filter: 20 txs inside 10 minutes = too fast for a human
            sigs = sol.signatures(addr, limit=20)
            times = [x.get("blockTime") or 0 for x in sigs]
            if len(times) == 20 and times[0] - times[-1] < 600:
                print(f"skip bot-like {short(addr)}")
                continue
        except Exception:
            continue
        picked[addr] = source[addr]
    return picked


# ---------------- selection ----------------
def hold_times(log: list[dict]) -> dict:
    """leader -> list of seconds between the leader's own buy and sell of a token (from 'watch' rows)."""
    opened, out = {}, {}
    for a in log:
        if a.get("kind") != "watch" or not a.get("leader"):
            continue
        k = (a["leader"], a.get("token"))
        if a.get("side") == "buy":
            opened.setdefault(k, a["t"])
        elif a.get("side") == "sell" and k in opened:
            out.setdefault(a["leader"], []).append(a["t"] - opened.pop(k))
    return out


def update_statuses(st: dict, log: list[dict], min_trades: int, min_hold_s: float = 90) -> list[dict]:
    """Score leaders; also drop scalpers whose median hold is shorter than we can react."""
    for leader, hs in hold_times(log).items():
        info = st["leaders"].get(leader)
        if info and info["status"] == "candidate" and len(hs) >= 5 and sorted(hs)[len(hs) // 2] < min_hold_s:
            info["status"] = "drop"
            info["why"] = f"scalper: median hold {sorted(hs)[len(hs) // 2]:.0f}s"
    sc = score(log, min_trades=min_trades)
    for r in sc:
        info = st["leaders"].get(r["leader"])
        if not info:
            continue
        if r["verdict"] == "KEEP":
            info["status"] = "keep"
        elif r["verdict"] == "DROP":
            info["status"] = "drop"
    return sc


def choose(st: dict, max_leaders: int) -> list[str]:
    L = st["leaders"]
    keep = [a for a, i in L.items() if i["status"] == "keep"]
    cand = sorted([a for a, i in L.items() if i["status"] == "candidate"], key=lambda a: L[a]["added"])
    return (keep + cand)[:max_leaders]


# ---------------- report / notify ----------------
def write_report(data: Path, st: dict, sc: list[dict], log: list[dict]) -> str:
    b0, b = st["start_bankroll"], st["bankroll"]
    lines = ["# Copy Bot paper report", "",
             f"Updated: {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC  ",
             f"**Bankroll (paper): ${b:,.2f}** (start ${b0:,.2f}, {b / b0:.2f}x, {(b / b0 - 1) * 100:+.1f}%)  ",
             f"Runs: {len(st['runs'])}", "",
             "## Leaders (scored on what copying them made)", "",
             "| wallet | status | copies | total $ | median % | win % |", "|---|---|---|---|---|---|"]
    by = {r["leader"]: r for r in sc}
    for addr, info in sorted(st["leaders"].items(), key=lambda kv: -by.get(kv[0], {}).get("total_pnl", 0)):
        r = by.get(addr, {})
        lines.append(f"| `{short(addr)}` | {info['status']} | {r.get('copies', 0)} | {r.get('total_pnl', 0):.2f} | "
                     f"{r.get('median_pct', 0):.1f} | {r.get('win_rate', 0):.1f} |")
    lines += ["", "## Runs", "", "| start (UTC) | min | followed | copies | before | after |", "|---|---|---|---|---|---|"]
    for r in st["runs"][-30:][::-1]:
        lines.append(f"| {r['start']} | {r['minutes']} | {r['followed']} | {r['copies']} | "
                     f"${r['before']:,.2f} | ${r['after']:,.2f} |")
    lags = [a["lag_s"] for a in log if "lag_s" in a]
    if lags:
        lags.sort()
        lines += ["", f"Detection lag (on-chain -> seen): median {lags[len(lags) // 2]:.1f}s, "
                      f"p90 {lags[int(len(lags) * 0.9)]:.1f}s over {len(lags)} trades. "
                      "Actual copy delay = this + configured delay."]
    lines += ["", "Paper trading only. Not financial advice."]
    txt = "\n".join(lines) + "\n"
    (data / "REPORT.md").write_text(txt)
    return txt


def notify(msg: str, title: str = "Copy Bot (paper)") -> None:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC not set - skip notify")
        return
    try:
        requests.post(f"https://ntfy.sh/{topic}", data=msg.encode(), headers={"Title": title, "Tags": "robot"},
                      timeout=20)
    except Exception as e:
        print(f"ntfy error: {e}", file=sys.stderr)


# ---------------- main ----------------
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--minutes", type=float, default=340)
    ap.add_argument("--max-leaders", type=int, default=DEFAULTS["max_leaders"])
    ap.add_argument("--rpc", default=os.environ.get("SOLANA_RPC") or "https://api.mainnet-beta.solana.com")
    ap.add_argument("--no-discover", action="store_true")
    a = ap.parse_args(argv)

    data = Path(a.data); data.mkdir(parents=True, exist_ok=True)
    st, log = load_state(data), load_log(data)
    sol = Solana(a.rpc)
    t_start = time.time()
    t_end = t_start + a.minutes * 60

    sc = update_statuses(st, log, DEFAULTS["min_trades"])
    follow = choose(st, a.max_leaders)
    if len(follow) < a.max_leaders and not a.no_discover:
        known = set(st["leaders"])
        new = discover(sol, known, a.max_leaders - len(follow), budget_s=min(420, a.minutes * 60 * 0.3))
        for addr, mint in new.items():
            st["leaders"][addr] = {"status": "candidate", "added": int(time.time()), "source": mint}
        follow = choose(st, a.max_leaders)
    save_state(data, st)
    if not follow:
        print("no leaders to follow")
        notify("No leaders found this run.")
        return

    before = st["bankroll"]
    cfg = Config(leaders=follow, budget=before, delay_s=DEFAULTS["delay_s"], take_profit_x=DEFAULTS["take_profit_x"],
                 size_frac=DEFAULTS["size_frac"], stop_loss_x=DEFAULTS["stop_loss_x"],
                 max_chase_pct=DEFAULTS["max_chase_pct"])
    eng = CopyEngine(cfg, DexPrices())
    print(f"PAPER run {a.minutes:g} min, bankroll ${before:,.2f}, following {[short(x) for x in follow]}", flush=True)
    with open(data / "paper_log.jsonl", "a") as f:
        try:
            paper_loop(eng, {addr: addr for addr in follow}, sol, until=t_end, log=f)
        finally:
            if not eng.locked:
                n0 = len(eng.activity)
                eng._lock(time.time())        # close everything so every run is fully accounted
                for act in eng.activity[n0:]:
                    f.write(json.dumps(act) + "\n")
    s = eng.stats()
    st["bankroll"] = round(s["end"], 2)
    st["runs"].append({"start": dt.datetime.fromtimestamp(t_start, dt.timezone.utc).strftime("%Y-%m-%d %H:%M"),
                       "minutes": round((time.time() - t_start) / 60), "followed": len(follow),
                       "copies": s["trades"], "before": round(before, 2), "after": st["bankroll"]})
    log = load_log(data)
    sc = update_statuses(st, log, DEFAULTS["min_trades"])
    save_state(data, st)
    write_report(data, st, sc, log)
    keep = sum(1 for i in st["leaders"].values() if i["status"] == "keep")
    msg = (f"${before:,.2f} -> ${st['bankroll']:,.2f} this run ({s['trades']} copies). "
           f"Total {st['bankroll'] / st['start_bankroll']:.2f}x since start. "
           f"Leaders: {keep} keep / {len(follow)} followed.")
    print(msg)
    notify(msg)


if __name__ == "__main__":
    main()
