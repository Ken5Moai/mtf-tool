"""Streamlit page: 毎日の利益を記録して達成率とペースを確認する日報ダッシュボード。"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from journal import add_entry, load, parse_entry, report, save, summarize

st.set_page_config(page_title="日報 / Daily journal", page_icon=":memo:", layout="wide")
st.title("日報ダッシュボード")
st.caption("「今日の利益：〇ドル、メモ：〇〇」を入力 → 累計・達成率・必要ペースを自動集計")

data = load()
cfg = data["config"]

with st.sidebar:
    st.header("目標設定")
    cfg["monthly_goal_jpy"] = st.number_input("月間利益目標 (円)", 10_000, 10_000_000, int(cfg["monthly_goal_jpy"]), 10_000)
    cfg["seed_jpy"] = st.number_input("種銭 (円)", 10_000, 10_000_000, int(cfg["seed_jpy"]), 10_000)
    cfg["usdjpy"] = st.number_input("USD/JPY", 80.0, 300.0, float(cfg["usdjpy"]), 0.5)
    cfg["trading_days"] = st.number_input("月の稼働日数", 1, 31, int(cfg["trading_days"]))
    if st.button("設定を保存", use_container_width=True):
        save(data)
        st.success("保存しました")

text = st.text_input("今日の入力", placeholder="今日の利益：15ドル、メモ：BTC 押し目買いで勝ち")
c1, c2 = st.columns([1, 4])
if c1.button("記録する", type="primary", use_container_width=True) and text:
    try:
        profit, memo = parse_entry(text)
    except ValueError as e:
        st.error(str(e))
    else:
        add_entry(data, profit, memo)
        save(data)
        st.success(f"{date.today().isoformat()} に {profit:+.2f} ドルを記録しました")

s = summarize(data)
m1, m2, m3, m4 = st.columns(4)
m1.metric("本日", f"{s['today_profit']:+,.2f} $")
m2.metric("今月累計", f"{s['cum_usd']:,.2f} $", f"想定比 {s['gap_usd']:+,.2f} $")
m3.metric("達成率", f"{s['achieved']*100:.1f} %", s["status"])
m4.metric("必要ペース/日", f"{s['need_per_day']:,.2f} $", f"残り {s['days_left']} 日")

st.progress(min(max(s["achieved"], 0.0), 1.0))
st.code(report(s), language=None)

rows = [e for e in data["entries"] if str(e["date"]).startswith(s["month"])]
if rows:
    df = pd.DataFrame(rows)
    df["累計"] = df["profit_usd"].cumsum()
    st.subheader("今月の推移")
    st.line_chart(df.set_index("date")["累計"])
    st.dataframe(df.rename(columns={"date": "日付", "profit_usd": "利益($)", "memo": "メモ"}),
                 use_container_width=True, hide_index=True)
else:
    st.info("今月の記録はまだありません。上のフォームから最初の1件を入力してください。")
