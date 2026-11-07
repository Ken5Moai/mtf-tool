"""
Backtest the existing MTF engine (engine.evaluate) on real 5-minute history.

  python research/backtest_mtf.py --out research_out      # needs open internet (run in GitHub Actions)

For every closed M5 bar: build H1/M15/M5 from 5m data up to that bar, ask the engine.
On BUY/SELL (and no open trade) enter at the NEXT bar's open with the engine's SL/TP.
Exit when a later bar touches SL or TP (both in one bar -> assume SL: conservative).
Costs: --cost-pct per side (fee + spread). Sizing: risk 1% of equity per trade.
Writes trades CSV + RESULT.md (Japanese) per symbol and overall.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import evaluate  # noqa: E402

CRYPTO = {"BTC/USD": "BTC-USD", "ETH/USD": "ETH-USD", "SOL/USD": "SOL-USD", "XRP/USD": "XRP-USD"}
FX = {"USD/JPY": "USDJPY=X", "EUR/USD": "EURUSD=X", "GBP/JPY": "GBPJPY=X", "GOLD": "GC=F"}


# ---------------- data ----------------
def coinbase_5m(product: str, days: int) -> pd.DataFrame:
    import requests
    end = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    start = end - dt.timedelta(days=days)
    rows, cur = [], start
    while cur < end:
        nxt = min(cur + dt.timedelta(minutes=5 * 300), end)
        for i in range(5):
            r = requests.get(f"https://api.exchange.coinbase.com/products/{product}/candles",
                             params={"granularity": 300, "start": cur.isoformat(), "end": nxt.isoformat()}, timeout=30)
            if r.status_code == 200:
                rows += r.json()
                break
            time.sleep(2 ** i)
        cur = nxt
        time.sleep(0.35)
    df = pd.DataFrame(rows, columns=["ts", "low", "high", "open", "close", "volume"]).drop_duplicates("ts")
    df.index = pd.to_datetime(df["ts"], unit="s", utc=True)
    return df.sort_index()[["open", "high", "low", "close", "volume"]].astype(float)


def yahoo_5m(sym: str) -> pd.DataFrame:
    import yfinance as yf
    d = yf.Ticker(sym).history(period="60d", interval="5m")
    d.columns = [c.lower() for c in d.columns]
    d.index = d.index.tz_convert("UTC")
    return d[["open", "high", "low", "close", "volume"]].astype(float).dropna()


def resample(m5: pd.DataFrame, rule: str) -> pd.DataFrame:
    return m5.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


# ---------------- backtest ----------------
def backtest(m5: pd.DataFrame, cost_pct: float = 0.1, step: int = 1, warmup: int = 260 * 12) -> list[dict]:
    m15_all, h1_all = resample(m5, "15min"), resample(m5, "1h")
    trades, pos = [], None
    idx = m5.index
    o, h, l = m5["open"].to_numpy(), m5["high"].to_numpy(), m5["low"].to_numpy()
    for i in range(warmup, len(m5) - 1):
        if pos is not None:                                   # manage open trade on bar i
            hit_sl = l[i] <= pos["sl"] if pos["side"] == 1 else h[i] >= pos["sl"]
            hit_tp = h[i] >= pos["tp"] if pos["side"] == 1 else l[i] <= pos["tp"]
            if hit_sl or hit_tp:
                exit_px = pos["sl"] if hit_sl else pos["tp"]
                gross = (exit_px - pos["entry"]) * pos["side"]
                cost = (pos["entry"] + exit_px) * cost_pct / 100
                risk = abs(pos["entry"] - pos["sl"])
                trades.append({**pos, "exit_time": str(idx[i]), "exit": exit_px,
                               "r": (gross - cost) / risk if risk else 0.0, "result": "SL" if hit_sl else "TP"})
                pos = None
            continue
        if (i - warmup) % step:
            continue
        t = idx[i] + pd.Timedelta(minutes=5)                  # bar i closed
        bars = {"M5": m5.iloc[max(0, i - 249):i + 1],
                "M15": m15_all[m15_all.index + pd.Timedelta(minutes=15) <= t].tail(250),
                "H1": h1_all[h1_all.index + pd.Timedelta(hours=1) <= t].tail(250)}
        try:
            res = evaluate(bars)
        except Exception:
            continue
        if res["verdict"] in ("BUY", "SELL"):
            side = 1 if res["verdict"] == "BUY" else -1
            entry = o[i + 1]
            sl, tp = res["sl"], res["tp"]
            if (side == 1 and not (sl < entry < tp)) or (side == -1 and not (tp < entry < sl)):
                continue                                      # gap through levels: skip
            pos = {"time": str(idx[i + 1]), "side": side, "entry": entry, "sl": sl, "tp": tp, "rr_plan": res["rr"]}
    return trades


def summarize(trades: list[dict], risk_pct: float = 1.0) -> dict:
    if not trades:
        return {"trades": 0}
    r = np.array([x["r"] for x in trades])
    eq = np.cumprod(1 + r * risk_pct / 100)
    peak = np.maximum.accumulate(eq)
    return {"trades": len(r), "win_rate": round(float((r > 0).mean() * 100), 1),
            "avg_r": round(float(r.mean()), 3), "total_r": round(float(r.sum()), 2),
            "equity_x": round(float(eq[-1]), 4), "max_dd_pct": round(float(((peak - eq) / peak).max() * 100), 1)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="research_out")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--step", type=int, default=1, help="evaluate every N bars")
    ap.add_argument("--only", default="", help="comma list of symbols")
    a = ap.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    jobs = [(k, "cb", v) for k, v in CRYPTO.items()] + [(k, "yf", v) for k, v in FX.items()]
    if a.only:
        jobs = [j for j in jobs if j[0] in a.only.split(",")]
    results = {}
    for name, src, sym in jobs:
        t = time.time()
        try:
            m5 = coinbase_5m(sym, a.days) if src == "cb" else yahoo_5m(sym)
        except Exception as e:
            print(f"{name}: data error {e}", flush=True)
            continue
        cost = 0.1 if src == "cb" else 0.01
        tr = backtest(m5, cost_pct=cost, step=a.step)
        pd.DataFrame(tr).to_csv(out / f"trades_{name.replace('/', '')}.csv", index=False)
        results[name] = {**summarize(tr), "bars": len(m5), "from": str(m5.index[0])[:10], "to": str(m5.index[-1])[:10],
                         "cost_pct_side": cost}
        print(name, results[name], f"{time.time() - t:.0f}s", flush=True)
    allr = [x for n in results for x in pd.read_csv(out / f"trades_{n.replace('/', '')}.csv").to_dict("records")] \
        if results else []
    results["ALL"] = summarize(sorted(allr, key=lambda x: x["time"])) if allr else {"trades": 0}
    (out / "result.json").write_text(json.dumps(results, indent=1))
    L = ["# チャート判断ツール（MTF）の過去検証", "",
         "5分足の過去データで、ツールの「買い/売り」判断どおりに売買した場合を再現。1回の損失を資金の1%に固定。"
         "損切りと利確が同じ足で両方届いたら損切り扱い（厳しめ）。", "",
         "| 銘柄 | 期間 | 取引回数 | 勝率 | 1回平均（R） | 資金の推移 | 最大の落ち込み |", "|---|---|---|---|---|---|---|"]
    for n, r in results.items():
        if not r.get("trades"):
            L.append(f"| {n} | {r.get('from', '')}〜{r.get('to', '')} | 0 | - | - | - | - |")
            continue
        L.append(f"| {n} | {r.get('from', '')}〜{r.get('to', '')} | {r['trades']} | {r['win_rate']}% | {r['avg_r']:+.3f} | "
                 f"{r['equity_x']:.3f}倍 | {r['max_dd_pct']}% |")
    L += ["", "R＝1回の想定損失を1とした損益。平均が0より大きければ長く続けるほど増える見込み、0以下なら減る見込み。",
          "※過去の成績は将来を保証しません。"]
    (out / "RESULT.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
