"""
Render the decision as one PNG (for saving / sharing). Streamlit-independent.
"""
from __future__ import annotations
import io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyBboxPatch

from engine import atr, zones

_COLOR = {"BUY": "#2f9e44", "SELL": "#e03131", "NO-TRADE": "#868e96"}
_LABEL = {"BUY": "BUY", "SELL": "SELL", "NO-TRADE": "NO-TRADE (wait)"}
_ARROW = {1: "UP", -1: "DOWN", 0: "range"}


def summary_png(symbol: str, res: dict, m5) -> bytes:
    v = res["verdict"]; col = _COLOR[v]
    fig = plt.figure(figsize=(10, 6.4), facecolor="#0e1117")
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    bg = fig.add_axes([0, 0, 1, 1]); bg.axis("off")

    bg.add_patch(FancyBboxPatch((0.04, 0.83), 0.92, 0.12, boxstyle="round,pad=0.01",
                                mutation_aspect=0.3, fc=col, ec="none", transform=bg.transAxes))
    bg.text(0.07, 0.89, _LABEL[v], color="#fff", fontsize=26, fontweight="bold",
            va="center", transform=bg.transAxes)
    bg.text(0.93, 0.89, symbol, color="#fff", fontsize=18, ha="right", va="center",
            alpha=0.9, transform=bg.transAxes)

    tr = res.get("trends", {})
    mtf = "H1 %s   M15 %s   M5 %s" % (_ARROW.get(tr.get("H1", 0)),
                                      _ARROW.get(tr.get("M15", 0)), _ARROW.get(tr.get("M5", 0)))
    bg.text(0.07, 0.76, mtf, color="#adb5bd", fontsize=13, transform=bg.transAxes)
    if v != "NO-TRADE":
        cells = [("ENTRY", res["entry"]), ("SL", res["sl"]), ("TP", res["tp"]), ("R:R", f"{res['rr']} : 1")]
        for i, (k, val) in enumerate(cells):
            x = 0.07 + i * 0.22
            bg.text(x, 0.70, k, color="#868e96", fontsize=11, transform=bg.transAxes)
            bg.text(x, 0.655, str(val), color="#f1f3f5", fontsize=16, fontweight="bold", transform=bg.transAxes)
    bg.text(0.07, 0.60, res.get("reason_en", "")[:80], color="#adb5bd", fontsize=10, transform=bg.transAxes)

    ax = fig.add_axes([0.06, 0.08, 0.88, 0.46]); ax.set_facecolor("#161b22")
    d = m5.tail(80).reset_index(drop=True)
    a = atr(m5)
    for z in zones(m5, a):
        c = "#2f9e44" if z["kind"] == "support" else "#e03131"
        ax.axhspan(z["low"], z["high"], color=c, alpha=0.08)
    for i, row in d.iterrows():
        up = row["close"] >= row["open"]; c = "#26a65b" if up else "#e03131"
        ax.plot([i, i], [row["low"], row["high"]], color=c, lw=0.7)
        h = abs(row["close"] - row["open"]) or (a * 0.02)
        ax.add_patch(Rectangle((i - 0.3, min(row["open"], row["close"])), 0.6, h, color=c))
    for key, c, ls in (("entry", "#4dabf7", "-"), ("sl", "#ff6b6b", "--"), ("tp", "#69db7c", "--")):
        if res.get(key) is not None:
            ax.axhline(res[key], color=c, ls=ls, lw=1.2)
            ax.text(len(d) + 0.5, res[key], key.upper(), color=c, fontsize=9, va="center")
    ax.set_xlim(-1, len(d) + 6); ax.margins(y=0.05)
    ax.set_title(f"{symbol}  M5", color="#adb5bd", loc="left", fontsize=11)
    ax.tick_params(colors="#495057", labelsize=7)
    for s in ax.spines.values(): s.set_color("#30363d")
    bg.text(0.5, 0.03, "MTF analysis tool - not financial advice", color="#495057",
            fontsize=8, ha="center", transform=bg.transAxes)

    buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=130, facecolor="#0e1117")
    plt.close(fig); buf.seek(0); return buf.getvalue()
