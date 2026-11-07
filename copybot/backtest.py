"""
Backtest copy trading on the leaders' REAL past trades (no waiting for live weeks).

  python -m copybot.backtest --data data --wallets 12 --tx 150

1. candidates = wallets in state.json + fresh discovery (early buyers of runners)
2. read each wallet's last --tx transactions from chain -> its buys/sells with fill prices
3. drop bots / scalpers (median hold < 90s) and the tokens used for discovery (look-ahead)
4. walk-forward: pick leaders on the OLDER half of history, test them on the NEWER half
5. the extra price we pay for copying late is unknown from history, so every result is
   shown for several "copy cost per side" scenarios (1/3/5/10 %)

Price model: a token's price at time t = the last fill price any leader got on it at or
before t. Our buy = leader buy price x (1 + cost), our sell = leader sell price x (1 - cost).
Stops / take-profit between trades cannot be seen with this model, so only mirrored exits
count. It is a rough but honest estimate; the live paper run keeps measuring the real cost.
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

from .engine import Config, LeaderTrade, run
from .live import DexPrices, Solana, trades_from_tx
from .ops import discover, load_state, short

COSTS = (1.0, 3.0, 5.0, 10.0)


# ---------------- data ----------------
def history(sol: Solana, wallet: str, max_tx: int, sol_usd: float, cache: Path) -> list[dict]:
    f = cache / f"{wallet}.json"
    if f.exists():
        return json.loads(f.read_text())
    out, before = [], None
    sigs = []
    while len(sigs) < max_tx:
        page = sol.signatures(wallet, limit=min(1000, max_tx - len(sigs)), before=before)
        if not page:
            break
        sigs += [s for s in page]
        before = page[-1]["signature"]
        if len(page) < 1000:
            break
    for s in reversed(sigs):
        if s.get("err"):
            continue
        try:
            tx = sol.tx(s["signature"])
        except Exception as e:
            print(f"  tx error {s['signature'][:8]}: {e}", file=sys.stderr)
            continue
        if not tx:
            continue
        for tr in trades_from_tx(tx, wallet, wallet, sol_usd=sol_usd):
            if tr.px:
                out.append({"t": tr.t, "side": tr.side, "token": tr.token, "px": tr.px, "frac": tr.frac})
    f.write_text(json.dumps(out))
    return out


def median_hold(trades: list[dict]) -> float | None:
    opened, holds = {}, []
    for x in trades:
        if x["side"] == "buy":
            opened.setdefault(x["token"], x["t"])
        elif x["token"] in opened:
            holds.append(x["t"] - opened.pop(x["token"]))
    return statistics.median(holds) if len(holds) >= 3 else None


# ---------------- replay ----------------
def price_book(trades_by_leader: dict) -> callable:
    series = defaultdict(list)
    for w, ts in trades_by_leader.items():
        for x in ts:
            series[x["token"]].append((x["t"], x["px"]))
    for k in series:
        series[k].sort()
    times = {k: [t for t, _ in v] for k, v in series.items()}

    def price(token, t):
        v = series.get(token)
        if not v:
            return None
        i = bisect.bisect_right(times[token], t) - 1
        return v[max(i, 0)][1]
    return price


def replay(trades_by_leader: dict, cost_pct: float, t0: float, t1: float, budget: float = 149.0,
           size: float = 0.2, delay: float = 5.0) -> dict:
    leaders = [w for w, ts in trades_by_leader.items() if ts]
    if not leaders:
        return {"end": budget, "multiple": 1.0, "trades": 0, "win_rate": 0.0}
    evs = [LeaderTrade(x["t"], w, x["side"], x["token"], frac=x["frac"], px=x["px"])
           for w, ts in trades_by_leader.items() for x in ts if t0 <= x["t"] <= t1]
    cfg = Config(leaders=leaders, budget=budget, delay_s=delay, take_profit_x=None, size_frac=size,
                 fee_bps=100, slippage_bps=cost_pct * 100, stop_loss_x=None, max_chase_pct=None)
    eng = run(cfg, price_book(trades_by_leader), evs, t0, t1 + 600, step_s=30)
    eng._lock(t1 + 600)
    return eng.stats()


def per_leader(trades: list[dict], w: str, cost: float, t0: float, t1: float) -> dict:
    return replay({w: trades}, cost, t0, t1)


# ---------------- main ----------------
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--wallets", type=int, default=12, help="how many candidate wallets to test")
    ap.add_argument("--tx", type=int, default=150, help="past transactions per wallet")
    ap.add_argument("--min-trades", type=int, default=6, help="min copied exits in the selection half")
    ap.add_argument("--rpc", default="https://api.mainnet-beta.solana.com")
    a = ap.parse_args(argv)

    data = Path(a.data); data.mkdir(parents=True, exist_ok=True)
    cache = data / "bt_cache"; cache.mkdir(exist_ok=True)
    st = load_state(data)
    sol = Solana(a.rpc)
    sol_usd = DexPrices().sol_usd() or 150.0

    wallets = dict.fromkeys(st["leaders"])
    exclude_tokens = {(i.get("source") or "").split(":")[-1] for i in st["leaders"].values()}
    if len(wallets) < a.wallets:
        print(f"discovering {a.wallets - len(wallets)} more candidates...", flush=True)
        new = discover(sol, set(wallets), a.wallets - len(wallets), budget_s=420)
        for w, src in new.items():
            wallets[w] = None
            exclude_tokens.add(src.split(":")[-1])
            st["leaders"].setdefault(w, {"status": "candidate", "added": int(time.time()), "source": src})
    wallets = list(wallets)[: max(a.wallets, len(st["leaders"]))]

    hist = {}
    for i, w in enumerate(wallets, 1):
        t = time.time()
        h = [x for x in history(sol, w, a.tx, sol_usd, cache) if x["token"] not in exclude_tokens]
        mh = median_hold(h)
        tag = ""
        if mh is not None and mh < 90:
            tag = f"  -> scalper (median hold {mh:.0f}s), excluded"
        elif len([x for x in h if x["side"] == "sell"]) < 2:
            tag = "  -> too few trades, excluded"
        else:
            hist[w] = h
        print(f"[{i}/{len(wallets)}] {short(w)}: {len(h)} trades{tag} ({time.time() - t:.0f}s)", flush=True)

    if not hist:
        print("no usable wallets"); return
    all_t = sorted(x["t"] for h in hist.values() for x in h)
    t0, t1 = all_t[0], all_t[-1]
    mid = t0 + (t1 - t0) / 2
    days = (t1 - t0) / 86400

    res = {"period_days": round(days, 1), "wallets": len(hist), "costs": {}, "leaders": {}}
    for c in COSTS:
        sel = {w: per_leader(h, w, c, t0, mid) for w, h in hist.items()}
        picked = [w for w, r in sel.items() if r["exits"] >= a.min_trades and r["pnl"] > 0]
        test_sel = replay({w: hist[w] for w in picked}, c, mid, t1) if picked else None
        test_all = replay(hist, c, mid, t1)
        full_all = replay(hist, c, t0, t1)
        res["costs"][c] = {"picked": picked,
                           "test_picked": test_sel and round(test_sel["multiple"], 3),
                           "test_picked_trades": test_sel and test_sel["trades"],
                           "test_all": round(test_all["multiple"], 3),
                           "full_all": round(full_all["multiple"], 3)}
        for w, r in sel.items():
            full = per_leader(hist[w], w, c, t0, t1)
            res["leaders"].setdefault(w, {})[c] = {"first_half": round(r["multiple"], 3), "first_exits": r["exits"],
                                                   "full": round(full["multiple"], 3), "exits": full["exits"]}
    (data / "backtest.json").write_text(json.dumps(res, indent=1))
    write_report(data, res)
    print((data / "BACKTEST.md").read_text())


def write_report(data: Path, res: dict) -> None:
    L = ["# 過去データ検証（バックテスト）", "",
         f"対象: {res['wallets']}人の実際の売買、期間 約{res['period_days']}日。元手 $149、1回20%、5秒遅れ、手数料1%。", "",
         "**やり方**: 期間の前半で「コピーしたら儲かった相手」を選び、**後半（選ぶときに見ていない期間）**でその相手だけを"
         "コピーした場合を再現。遅れて買うことによる追加コストは過去データから正確にわからないため、片道1〜10%で幅を見ています。", "",
         "| 遅れのコスト(片道) | 前半で選ばれた人数 | 後半: 選んだ人だけコピー | 後半: 全員コピー | 全期間: 全員コピー |",
         "|---|---|---|---|---|"]
    for c, r in res["costs"].items():
        tp = f"{r['test_picked']:.2f}倍（{r['test_picked_trades']}回）" if r["test_picked"] else "該当者なし"
        L.append(f"| {float(c):g}% | {len(r['picked'])}人 | {tp} | {r['test_all']:.2f}倍 | {r['full_all']:.2f}倍 |")
    L += ["", "## 相手ごと（遅れのコスト3%の場合）", "", "| ウォレット | 前半 | 全期間 | 決済回数 |", "|---|---|---|---|"]
    rows = sorted(res["leaders"].items(), key=lambda kv: -kv[1].get(3.0, kv[1].get("3.0", {})).get("full", 0))
    for w, by in rows:
        r = by.get(3.0) or by.get("3.0")
        L.append(f"| `{short(w)}` | {r['first_half']:.2f}倍 | {r['full']:.2f}倍 | {r['exits']} |")
    L += ["", "※1倍より上なら増えた、下なら減った。途中の損切り・利確は再現できないため、相手が売った時に売る前提です。",
          "※仮想の計算です。実際のお金は動いていません。"]
    (data / "BACKTEST.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
