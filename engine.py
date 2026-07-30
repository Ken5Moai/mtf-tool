"""
MTF trade decision engine (pure logic, UI-independent).
From H1/M15/M5 bars -> BUY / SELL / NO-TRADE + entry/SL/TP/reason.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

EMA_F, EMA_S = 20, 50
SWING, ZMULT, PROX = 2, 0.5, 1.0
ATRP, SLM, MIN_RR = 14, 1.2, 1.5


def ema(s, p): return s.ewm(span=p, adjust=False).mean()

def atr(b, p=ATRP):
    if len(b) < p + 1: return 0.0
    h, l, c = b["high"], b["low"], b["close"]; pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(p).mean().iloc[-1])

def trend(b):
    if len(b) < EMA_S + 2: return 0
    c = b["close"]; ef, es = ema(c, EMA_F), ema(c, EMA_S)
    fn, fp, sn = float(ef.iloc[-1]), float(ef.iloc[-3]), float(es.iloc[-1])
    if fn > sn and fn - fp > 0: return 1
    if fn < sn and fn - fp < 0: return -1
    return 0

def bias(h1, m15, m5):
    ht, mt, lt = trend(h1), trend(m15), trend(m5)
    if ht == 0: return 0, "H1 range", "H1 is ranging (no trend)"
    if mt != 0 and mt != ht: return 0, "H1/M15 mismatch", "H1 and M15 disagree"
    if lt != 0 and lt != ht: return 0, "M5 counter", "M5 counter-trend"
    if ht == 1: return ht, "uptrend pullback", "uptrend pullback"
    return ht, "downtrend rally", "downtrend rally"

def zones(b, a):
    if len(b) < SWING * 2 + 1 or a <= 0: return []
    hs, ls = b["high"].to_numpy(), b["low"].to_numpy(); n = len(b); L = SWING
    sh, sl = [], []
    for i in range(L, n - L):
        wh, wl = hs[i - L:i + L + 1], ls[i - L:i + L + 1]
        if hs[i] == wh.max() and wh.argmax() == L: sh.append(float(hs[i]))
        if ls[i] == wl.min() and wl.argmin() == L: sl.append(float(ls[i]))
    tol = a * ZMULT; half = tol * 0.5
    def cl(lv, k):
        if not lv: return []
        lv = sorted(lv); out = []; bk = [lv[0]]
        for x in lv[1:]:
            if x - bk[-1] <= tol: bk.append(x)
            else: out.append(bk); bk = [x]
        out.append(bk)
        return [{"price": sum(g) / len(g), "kind": k, "low": min(g) - half, "high": max(g) + half} for g in out]
    z = cl(sl, "support") + cl(sh, "resistance"); z.sort(key=lambda d: d["price"]); return z

def nsup(z, p):
    c = [d for d in z if d["price"] <= p]; return max(c, key=lambda d: d["price"]) if c else None
def nres(z, p):
    c = [d for d in z if d["price"] >= p]; return min(c, key=lambda d: d["price"]) if c else None


def evaluate(bars: dict) -> dict:
    h1, m15, m5 = bars.get("H1"), bars.get("M15"), bars.get("M5")
    out = {"verdict": "NO-TRADE", "entry": None, "sl": None, "tp": None,
           "rr": None, "reason": "", "reason_en": "", "trends": {}}

    def stop(ja, en):
        out["reason"], out["reason_en"] = ja, en; return out

    if any(x is None or len(x) < 55 for x in (h1, m15, m5)):
        return stop("insufficient data", "insufficient data")

    out["trends"] = {"H1": trend(h1), "M15": trend(m15), "M5": trend(m5)}
    d, why, why_en = bias(h1, m15, m5)
    if d == 0:
        return stop("no-trade: " + why, "no-trade: " + why_en)

    a = atr(m15)
    z = zones(m15, a)
    if a <= 0 or not z:
        return stop("no-trade: no S/R", "no-trade: no valid S/R")

    price = float(m5["close"].iloc[-1]); prox = a * PROX
    if d == 1:
        zn = nsup(z, price)
        if zn is None or price - zn["high"] > prox:
            return stop("no-trade: far from support", "no-trade: price far from support")
        sl = zn["low"] - a * SLM; opp = nres(z, price)
        if sl >= price: return stop("no-trade: SL error", "no-trade: SL calc error")
        tp = opp["low"] if opp else price + (price - sl) * MIN_RR
    else:
        zn = nres(z, price)
        if zn is None or zn["low"] - price > prox:
            return stop("no-trade: far from resistance", "no-trade: price far from resistance")
        sl = zn["high"] + a * SLM; opp = nsup(z, price)
        if sl <= price: return stop("no-trade: SL error", "no-trade: SL calc error")
        tp = opp["high"] if opp else price - (sl - price) * MIN_RR

    rec = m5.iloc[-3:]; last = m5.iloc[-1]; body = last["close"] - last["open"]
    if d == 1:
        ok = (rec["low"] <= zn["high"]).any() and last["close"] >= zn["price"] and body >= -1e-9
    else:
        ok = (rec["high"] >= zn["low"]).any() and last["close"] <= zn["price"] and body <= 1e-9
    if not ok:
        return stop("no-trade: no bounce yet", "no-trade: no bounce confirmation yet")

    risk = abs(price - sl); reward = abs(tp - price)
    rr = reward / risk if risk > 0 else 0
    if rr < MIN_RR - 1e-9:
        return stop(f"no-trade: RR {rr:.2f} < 1.5", f"no-trade: RR {rr:.2f} < 1.5 (wall too close)")

    out.update({
        "verdict": "BUY" if d == 1 else "SELL",
        "entry": round(price, 4), "sl": round(sl, 4), "tp": round(tp, 4),
        "rr": round(rr, 2),
        "reason": f"MTF aligned + S/R bounce, RR {rr:.2f}:1",
        "reason_en": f"MTF aligned + S/R bounce, RR {rr:.2f}:1",
        "zone": zn,
    })
    return out
