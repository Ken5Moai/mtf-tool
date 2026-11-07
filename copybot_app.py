"""
Copy Bot (Streamlit) - reproduction of the "Grok Bot copies traders overnight" flow.

  streamlit run copybot_app.py

Flow (same as the video):
  1. deposit (paper) budget, e.g. $149
  2. tell the bot in plain words who to follow and the rules
  3. watch the night replay: Watcher / Copier / Executor / Risk agents, activity, balance
  4. "Reality check": the same rules over hundreds of nights
Everything is simulated. For real wallets in paper mode use `python -m copybot.live`.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from copybot.command import parse_task
from copybot.sim import monte_carlo, simulate

st.set_page_config(page_title="Copy Bot", page_icon=":robot_face:", layout="centered")
st.markdown("""
<style>
.block-container {max-width: 760px; padding-top: 1.5rem;}
.bal {font-size: 56px !important; font-weight: 800; line-height: 1.05; margin: 0;}
.sub {color:#2f9e44; font-weight:600;}
.sub.neg {color:#e03131;}
.badge {background:#2f9e44; color:#fff; border-radius:6px; padding:1px 7px; margin-left:6px; font-size:14px;}
.badge.neg {background:#e03131;}
.card {background:#161b22; border:1px solid #2b3138; border-radius:12px; padding:10px 12px; min-height:118px;}
.card h4 {margin:0 0 2px; font-size:15px;}
.card .k {color:#8b949e; font-size:12px;}
.row {display:flex; justify-content:space-between; border-bottom:1px solid #21262d; padding:7px 0;}
.row .l {font-weight:600;} .row .n {color:#8b949e; font-size:12px;}
.pos {color:#2f9e44;} .neg {color:#e03131;}
.bubble {background:#1c7ed6; color:#fff; border-radius:14px; padding:10px 14px; margin:6px 0 6px 18%;}
.bot {background:#21262d; color:#e6edf3; border-radius:14px; padding:10px 14px; margin:6px 18% 6px 0;}
</style>
""", unsafe_allow_html=True)

DEFAULT_TASK = ("follow surgz and CXCinvest on fomo. copy every buy 30s after them, sell when they sell. "
                "take profit at 10x, lock the rest by 6:30am")
START = dt.datetime(2026, 11, 1, 19, 55)

with st.sidebar:
    st.header("Wallet (paper)")
    budget = st.number_input("Deposit $", 10.0, 100000.0, 149.0, step=10.0)
    st.divider()
    st.header("Simulation")
    edge = st.slider("Leader skill (share of picks that run)", 0.05, 0.8, 0.30, 0.05,
                     help="0.30 = 3 of 10 tokens the traders buy actually pump.")
    seed = st.number_input("Night #", 0, 100000, 7, step=1)
    fee = st.slider("Fee + slippage per side (%)", 0.0, 6.0, 2.5, 0.5)
    st.divider()
    st.header("Risk controls")
    safe = st.toggle("Recommended preset", value=False,
                     help="20% per trade, stop -40%, skip if already +15% since the leader bought.")
    size_pct = st.slider("Size per copy (% of equity)", 5, 100, 20 if safe else 50, 5)
    sl = st.slider("Stop loss (%)", 0, 90, 40 if safe else 0, 5, help="0 = off")
    chase = st.slider("Skip if price already ran (%)", 0, 100, 15 if safe else 0, 5, help="0 = off")
    st.caption("Simulated market. No real money, no real orders.")

st.markdown("### 🤖 Copy Bot")
task = st.text_area("New task", DEFAULT_TASK, height=90)
pt = parse_task(task, budget=budget, now=START)
cfg = pt.config
cfg.fee_bps = fee * 100 * 0.4
cfg.slippage_bps = fee * 100 * 0.6
cfg.size_frac = size_pct / 100
cfg.stop_loss_x = 1 - sl / 100 if sl else None
cfg.max_chase_pct = chase or None

st.markdown(f"<div class='bubble'>{task}</div>", unsafe_allow_html=True)
st.markdown(f"<div class='bot'>{pt.reply}</div>", unsafe_allow_html=True)
for w in pt.warnings:
    st.warning(w)
if not cfg.leaders:
    st.stop()

tab_live, tab_real = st.tabs(["Tonight", "Reality check"])

with tab_live:
    eng, night = simulate(cfg, int(seed), edge=edge, start=START)
    s = eng.stats()
    neg = s["pnl"] < 0
    cls = " neg" if neg else ""
    st.markdown(f"<div class='k'>copying {' · '.join(cfg.leaders)} · {s['open']} open</div>"
                f"<div class='bal'>${s['end']:,.2f}</div>"
                f"<span class='sub{cls}'>{'▼' if neg else '▲'} ${abs(s['pnl']):,.2f} ({s['pct']:+.2f}%)</span>"
                f"<span style='background:{'#e03131' if neg else '#2f9e44'};color:#fff;border-radius:6px;"
                f"padding:1px 7px;margin-left:8px;font-size:14px;font-weight:700'>{s['multiple']:.2f}x</span>", unsafe_allow_html=True)

    eq = pd.DataFrame(eng.equity, columns=["t", "v"])
    eq["time"] = pd.to_datetime(eq["t"], unit="s")
    fig = go.Figure(go.Scatter(x=eq["time"], y=eq["v"], mode="lines",
                               line=dict(color="#e03131" if neg else "#2f9e44", width=2.5),
                               fill="tozeroy", fillcolor="rgba(47,158,68,0.08)" if not neg else "rgba(224,49,49,0.08)"))
    for a in eng.activity:
        if a["kind"] in ("buy", "sell", "tp", "stop"):
            v = np.interp(a["t"], eq["t"], eq["v"])
            txt = f"Bought {a['token']}" if a["kind"] == "buy" else f"{a['pnl']:+,.0f}"
            fig.add_annotation(x=pd.to_datetime(a["t"], unit="s"), y=v, text=txt, showarrow=False,
                               yshift=14, font=dict(size=9, color="#fff"),
                               bgcolor="#1c7ed6" if a["kind"] == "buy" else ("#2f9e44" if a["pnl"] > 0 else "#e03131"))
    fig.update_layout(template="plotly_dark", height=280, margin=dict(l=0, r=0, t=10, b=0),
                      showlegend=False, xaxis_title=None, yaxis_title=None)
    st.plotly_chart(fig, use_container_width=True)

    # ---- agents ----
    st.markdown("#### Agents <span class='k'>4 running</span>", unsafe_allow_html=True)
    acts = eng.activity
    watch = [a for a in acts if a["kind"] == "watch"]
    buys = [a for a in acts if a["kind"] == "buy"]
    exits = [a for a in acts if a["kind"] in ("sell", "tp", "stop", "lock") and "pnl" in a]
    tps = [a for a in exits if a["kind"] == "tp"]
    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(f"<div class='card'><h4>Watcher</h4><div class='k'>{len(cfg.leaders)} traders</div>"
                f"<br>{len(watch)} moves seen</div>", unsafe_allow_html=True)
    c2.markdown(f"<div class='card'><h4>Copier</h4><div class='k'>{cfg.delay_s:g}s behind</div>"
                f"<br>{len(buys)} copied</div>", unsafe_allow_html=True)
    c3.markdown(f"<div class='card'><h4>Executor</h4><div class='k'>{len(buys) + len(exits)} trades</div>"
                f"<br>win {s['win_rate']:.0f}%</div>", unsafe_allow_html=True)
    c4.markdown(f"<div class='card'><h4>Risk</h4><div class='k'>"
                f"{'same exits' if cfg.mirror_exits else 'TP/lock only'}</div>"
                f"<br>TP {cfg.take_profit_x or '-'}x · {len(tps)} hit</div>", unsafe_allow_html=True)

    # ---- activity ----
    st.markdown("#### Activity")
    rows = []
    for a in reversed(acts):
        if a["kind"] == "watch":
            continue
        t = dt.datetime.fromtimestamp(a["t"]).strftime("%-I:%M %p")
        if a["kind"] == "buy":
            right, sub, c = f"${-a['usd']:,.2f}", "on fomo", ""
        elif "pnl" in a:
            c = "pos" if a["pnl"] >= 0 else "neg"
            right, sub = f"{a['pnl']:+,.2f}", f"{a['pct']:+.1f}%"
        else:
            right, sub, c = f"${a.get('usd', 0):,.2f}", "", ""
        rows.append(f"<div class='row'><div><div class='l'>{a['text']}</div><div class='n'>{a.get('note') or ''} · {t}</div></div>"
                    f"<div style='text-align:right'><div class='l {c}'>{right}</div><div class='n'>{sub}</div></div></div>")
    st.markdown("".join(rows[:40]), unsafe_allow_html=True)

with tab_real:
    st.write("The video shows **one** night. Here are the same rules over many nights, "
             "and the same nights with **zero** copy delay for comparison.")
    runs = st.select_slider("Nights", [100, 200, 300, 500], 200)
    if st.button("Run", type="primary"):
        with st.spinner("Simulating..."):
            r = monte_carlo(cfg, runs=runs, edge=edge)
            c0 = type(cfg)(**{**cfg.__dict__, "delay_s": 0})
            r0 = monte_carlo(c0, runs=runs, edge=edge)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Median result", f"{r['median']:.2f}x",
                  help=f"With 0s delay the median would be {r0['median']:.2f}x.")
        m2.metric("Nights losing money", f"{r['p_loss']:.0f}%")
        m3.metric("Nights ≥ 13x (video)", f"{r['p_13x']:.1f}%")
        m4.metric("Best night", f"{r['best_multiple']:.1f}x", f"night #{r['best_seed']}", delta_color="off")
        h = go.Figure()
        h.add_histogram(x=np.log10(np.clip(r["multiples"], 1e-3, None)), name=f"{cfg.delay_s:g}s delay",
                        marker_color="#e03131", opacity=0.75, nbinsx=40)
        h.add_histogram(x=np.log10(np.clip(r0["multiples"], 1e-3, None)), name="0s delay",
                        marker_color="#4dabf7", opacity=0.5, nbinsx=40)
        h.add_vline(x=0, line_dash="dash", line_color="#adb5bd", annotation_text="break-even")
        h.add_vline(x=np.log10(13.07), line_dash="dot", line_color="#2f9e44", annotation_text="video 13x")
        h.update_layout(barmode="overlay", template="plotly_dark", height=320,
                        xaxis=dict(title="final balance (multiple, log scale)",
                                   tickvals=[-3, -2, -1, 0, 1, 2], ticktext=["0.001x", "0.01x", "0.1x", "1x", "10x", "100x"]),
                        margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(h, use_container_width=True)
        st.caption(f"Set 'Night #' to {r['best_seed']} in the sidebar to see the best night - "
                   "that is the kind of night that gets posted.")
