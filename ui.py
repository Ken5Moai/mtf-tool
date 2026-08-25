"""Streamlit ページ共通のヘルパー（データ共有・サイドバー・表組み）。"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import store

CSS = """
<style>
.block-container {padding-top: 2.2rem; max-width: 1200px;}
[data-testid="stMetric"] {background:#161b22; border:1px solid #2b3138;
    padding:12px 16px; border-radius:10px;}
[data-testid="stMetricLabel"] {color:#8b949e;}
</style>
"""


@st.cache_resource
def _handle() -> dict:
    """プロセス内で 1 つの取引データを共有する。"""
    return store.load()


def data() -> dict:
    return _handle()


def persist(d: dict) -> None:
    store.save(d)
    _handle.clear()


def style() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def config_sidebar(d: dict) -> None:
    """目標設定サイドバー。全ページで共通。"""
    cfg = d["config"]
    with st.sidebar:
        st.header("目標設定")
        cfg["monthly_goal_jpy"] = st.number_input(
            "月間利益目標 (円)", 10_000, 10_000_000, int(cfg["monthly_goal_jpy"]), 10_000)
        cfg["seed_jpy"] = st.number_input(
            "種銭 (円)", 10_000, 10_000_000, int(cfg["seed_jpy"]), 10_000)
        cfg["usdjpy"] = st.number_input("USD/JPY", 80.0, 300.0, float(cfg["usdjpy"]), 0.5)
        cfg["trading_days"] = st.number_input("月の稼働日数", 1, 31, int(cfg["trading_days"]))
        cfg["risk_per_trade_pct"] = st.number_input(
            "1トレードの許容リスク (%)", 0.1, 20.0, float(cfg["risk_per_trade_pct"]), 0.1)
        cfg["max_drawdown_pct"] = st.number_input(
            "許容ドローダウン (%)", 1.0, 90.0, float(cfg["max_drawdown_pct"]), 1.0)

        goal_usd = cfg["monthly_goal_jpy"] / cfg["usdjpy"]
        st.caption(f"月間目標 ≒ {goal_usd:,.2f} ドル / 1日あたり {goal_usd / cfg['trading_days']:,.2f} ドル")
        if st.button("設定を保存", width="stretch"):
            persist(d)
            st.success("保存しました")


def trades_frame(trades: list[store.Trade]) -> pd.DataFrame:
    """取引一覧を表示用の DataFrame にする（新しい順）。"""
    return pd.DataFrame([{
        "ID": t.id, "日付": t.date, "銘柄": t.symbol or "-", "方向": t.side or "-",
        "損益($)": t.net, "R": t.r_multiple, "手数料($)": t.fee,
        "メモ": t.memo, "タグ": " ".join(t.tags),
    } for t in sorted(trades, key=lambda t: (t.date, t.id), reverse=True)])
