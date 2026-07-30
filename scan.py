"""
Scan all symbols with the MTF engine and push an ntfy notification on BUY/SELL.
Run by GitHub Actions on a schedule (see .github/workflows/scan.yml).
Set repo secret NTFY_TOPIC to your ntfy topic name.

Manual run (Actions -> Run workflow):
  - always sends a small test ping (confirms delivery).
  - if the 'demo' checkbox is on, also sends a SAMPLE BUY alert so you can
    preview what a real signal looks like.
"""
import os
import sys
import datetime
import pandas as pd
import requests
from engine import evaluate

CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD"]
FX = {"USD/JPY": "USDJPY=X", "EUR/USD": "EURUSD=X", "GBP/JPY": "GBPJPY=X", "GOLD (XAU)": "GC=F"}
FX_TF = {"H1": ("1h", "1mo"), "M15": ("15m", "1mo"), "M5": ("5m", "7d")}
CRYPTO_TF = {"H1": "1h", "M15": "15m", "M5": "5m"}
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
EVENT = os.environ.get("GITHUB_EVENT_NAME", "")
DEMO = os.environ.get("DEMO", "").lower() == "true"


def fetch_crypto(symbol, tf, limit=250):
    import ccxt
    ex = ccxt.kraken({"enableRateLimit": True})
    o = ex.fetch_ohlcv(symbol, tf, limit=limit)
    df = pd.DataFrame(o, columns=["ts", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def fetch_fx(yf_symbol, tf):
    import yfinance as yf
    interval, period = FX_TF[tf]
    d = yf.Ticker(yf_symbol).history(period=period, interval=interval).reset_index()
    d.columns = [str(c).lower() for c in d.columns]
    tc = "datetime" if "datetime" in d.columns else "date"
    df = pd.DataFrame({"open": d["open"], "high": d["high"], "low": d["low"],
                       "close": d["close"], "volume": d.get("volume", 0)}).astype(float)
    df.index = pd.to_datetime(d[tc], utc=True)
    return df.dropna().sort_index()


def notify(title, body, tags="chart_with_upwards_trend", priority="default"):
    if not NTFY_TOPIC:
        print("NTFY_TOPIC not set - skipping notification", file=sys.stderr)
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": tags},
            timeout=20,
        )
        print("notification sent")
    except Exception as e:
        print(f"ntfy send failed: {e}", file=sys.stderr)


def check(name, get_bars):
    try:
        bars = {tf: get_bars(tf) for tf in ("H1", "M15", "M5")}
        r = evaluate(bars)
        if r["verdict"] in ("BUY", "SELL"):
            return (f"{name}  {r['verdict']}\n"
                    f"entry {r['entry']} / SL {r['sl']} / TP {r['tp']}  (RR {r['rr']}:1)")
    except Exception as e:
        print(f"{name} error: {e}", file=sys.stderr)
    return None


def main():
    if DEMO:
        sample = ("BTC/USD  BUY\n"
                  "entry 61240.0 / SL 61160.66 / TP 61373.29  (RR 1.68:1)\n\n"
                  "** SAMPLE ** this is an example of a real alert, not a live signal.")
        notify("MTF signal (SAMPLE)", sample, priority="high")
        print("demo sample sent")
        return

    hits = []
    for s in CRYPTO:
        m = check(s, lambda tf, s=s: fetch_crypto(s, CRYPTO_TF[tf]))
        if m:
            hits.append(m)
    for name, yfs in FX.items():
        m = check(name, lambda tf, yfs=yfs: fetch_fx(yfs, tf))
        if m:
            hits.append(m)

    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print(f"[{stamp}] signals: {len(hits)}")
    for h in hits:
        print("  " + h.replace("\n", " | "))

    if hits:
        notify("MTF signal", "\n\n".join(hits), priority="high")
    elif EVENT == "workflow_dispatch":
        notify("MTF scan test",
               f"Test run OK. No signals right now ({stamp}).",
               tags="white_check_mark")


if __name__ == "__main__":
    main()
