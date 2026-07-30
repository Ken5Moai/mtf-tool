"""
MTF trade signal tool (Streamlit UI).
Select a symbol -> auto-fetch H1/M15/M5 -> show BUY/SELL/NO-TRADE + entry/SL/TP.
Supports crypto (Binance) and FX/gold (Yahoo). Export result as an image.
"""
from __future__ import annotations
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

from engine import evaluate, atr, zones
from render import summary_png

st.set_page_config(page_title="MTF signal tool", page_icon=":chart_with_upwards_trend:", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 2rem; max-width: 1200px;}
[data-testid="stMetric"] {background:#161b22; border:1px solid #2b3138;
    padding:12px 16px; border-radius:10px;}
[data-testid="stMetricLabel"] {color:#8b949e;}
h1 {letter-spacing:.5px;}
.verdict {padding:18px 24px; border-radius:12px; font-size:28px; font-weight:800;
    text-align:center; color:#fff; margin:6px 0 4px;}
</style>
""", unsafe_allow_html=True)

CRYPTO = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
FX = {"USD/JPY": "USDJPY=X", "EUR/USD": "EURUSD=X", "GBP/JPY": "GBPJPY=X", "GOLD (XAU)": "GC=F"}
FX_TF = {"H1": ("1h", "1mo"), "M15": ("15m", "1mo"), "M5": ("5m", "7d")}
SRC_CRYPTO = "Crypto (Binance)"
SRC_FX = "FX / Gold (Yahoo)"


@st.cache_data(ttl=60, show_spinner=False)
def fetch_crypto(symbol, tf, limit=250):
    import ccxt
    ex = ccxt.binance({"enableRateLimit": True})
    o = ex.fetch_ohlcv(symbol, tf, limit=limit)
    df = pd.DataFrame(o, columns=["ts", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


@st.cache_data(ttl=60, show_spinner=False)
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


def load_all(source, symbol):
    tfs = (("H1", "1h"), ("M15", "15m"), ("M5", "5m"))
    if source == SRC_CRYPTO:
        return {tf: fetch_crypto(symbol, low) for tf, low in tfs}
    yfs = FX[symbol]
    return {tf: fetch_fx(yfs, tf) for tf, _ in tfs}


def candle(df, title, levels=None, show_zones=False):
    d = df.tail(120)
    fig = go.Figure(go.Candlestick(x=d.index, open=d["open"], high=d["high"],
                                   low=d["low"], close=d["close"]))
    if show_zones:
        for z in zones(df, atr(df)):
            c = "rgba(38,166,91,0.13)" if z["kind"] == "support" else "rgba(224,49,49,0.13)"
            fig.add_hrect(y0=z["low"], y1=z["high"], line_width=0, fillcolor=c)
    if levels:
        for k, col, dash in (("entry", "#4dabf7", "solid"), ("sl", "#ff6b6b", "dash"), ("tp", "#69db7c", "dash")):
            if levels.get(k) is not None:
                fig.add_hline(y=levels[k], line_color=col, line_dash=dash,
                              annotation_text=k.upper(), annotation_position="right")
    fig.update_layout(title=title, height=330, template="plotly_dark",
                      margin=dict(l=10, r=10, t=34, b=10),
                      xaxis_rangeslider_visible=False, showlegend=False)
    return fig


st.title("MTF Trade Signal Tool")
st.caption("Multi-timeframe (H1/M15/M5) analysis -> BUY / SELL / NO-TRADE. "
           "Analysis aid only; final decision is yours.")

with st.sidebar:
    st.header("Settings")
    source = st.radio("Market", [SRC_CRYPTO, SRC_FX])
    symbols = CRYPTO if source == SRC_CRYPTO else list(FX)
    symbol = st.selectbox("Symbol", symbols)
    run = st.button("Analyze", use_container_width=True, type="primary")
    st.divider()
    st.caption("Data: Binance / Yahoo Finance (free, auto-fetch)")

if not run:
    st.info("Pick a market and symbol on the left, then press Analyze.")
    st.stop()

try:
    with st.spinner("Fetching H1 / M15 / M5 and analyzing..."):
        bars = load_all(source, symbol)
        res = evaluate(bars)
except Exception as e:
    st.error(f"Data fetch failed: {e}")
    st.stop()

v = res["verdict"]
color = {"BUY": "#2f9e44", "SELL": "#e03131", "NO-TRADE": "#868e96"}[v]
label = {"BUY": "BUY", "SELL": "SELL", "NO-TRADE": "NO-TRADE (wait)"}[v]
st.markdown(f"<div class='verdict' style='background:{color}'>{label}</div>", unsafe_allow_html=True)

tr = res["trends"]; arrow = {1: "UP", -1: "DOWN", 0: "range"}
c1, c2, c3 = st.columns(3)
c1.metric("H1", arrow.get(tr.get("H1", 0)))
c2.metric("M15", arrow.get(tr.get("M15", 0)))
c3.metric("M5", arrow.get(tr.get("M5", 0)))

if v != "NO-TRADE":
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Entry", res["entry"])
    m2.metric("Stop (SL)", res["sl"])
    m3.metric("Target (TP)", res["tp"])
    m4.metric("R:R", f"{res['rr']} : 1")

st.info(res["reason"])

png = summary_png(symbol, res, bars["M5"])
st.download_button("Save result as image", data=png,
                   file_name=f"signal_{symbol.replace('/', '').replace(' ', '')}.png",
                   mime="image/png")

st.subheader("Multi-timeframe charts")
lv = {"entry": res["entry"], "sl": res["sl"], "tp": res["tp"]}
st.plotly_chart(candle(bars["H1"], f"{symbol} H1"), use_container_width=True)
cc1, cc2 = st.columns(2)
cc1.plotly_chart(candle(bars["M15"], f"{symbol} M15", show_zones=True), use_container_width=True)
cc2.plotly_chart(candle(bars["M5"], f"{symbol} M5", levels=lv, show_zones=True), use_container_width=True)
