"""分析ページ: 銘柄・タグ・曜日ごとの成績から勝ちパターンを洗い出す。"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import report
import store
from stats import (by_symbol, by_tag, by_weekday, equity_curve, performance,
                   progress)
from ui import data

d = data()

st.title("分析")

months = sorted({t.date[:7] for t in d["trades"]}, reverse=True) or [date.today().strftime("%Y-%m")]
c1, c2 = st.columns([1, 3])
ym = c1.selectbox("対象月", months + ["全期間"])
rows = d["trades"] if ym == "全期間" else store.month_of(d["trades"], ym)

if not rows:
    st.info("記録がありません。")
    st.stop()

perf = performance(rows)
m = st.columns(5)
m[0].metric("純損益", f"{perf['net']:+,.2f} $")
m[1].metric("勝率", f"{perf['win_rate'] * 100:.0f} %", f"{perf['wins']}勝 {perf['losses']}敗")
m[2].metric("PF", "∞" if perf["profit_factor"] == float("inf") else f"{perf['profit_factor']:.2f}")
m[3].metric("期待値/回", f"{perf['expectancy']:+,.2f} $")
m[4].metric("最大DD", f"{perf['max_drawdown']:,.2f} $", f"最大連敗 {perf['max_loss_streak']}")

st.subheader("累積損益")
st.line_chart(pd.DataFrame({"累計($)": equity_curve(rows)}))

st.subheader("切り口別の成績")


def table(d_: dict) -> pd.DataFrame:
    return pd.DataFrame([{
        "区分": k, "件数": v["trades"], "純損益($)": v["net"],
        "勝率(%)": round(v["win_rate"] * 100),
        "PF": "∞" if v["profit_factor"] == float("inf") else f"{v['profit_factor']:.2f}",
        "期待値($)": v["expectancy"],
    } for k, v in d_.items()]).sort_values("純損益($)", ascending=False)


t1, t2, t3 = st.tabs(["銘柄別", "タグ別", "曜日別"])
t1.dataframe(table(by_symbol(rows)), width="stretch", hide_index=True)
t2.dataframe(table(by_tag(rows)), width="stretch", hide_index=True)
t3.dataframe(table(by_weekday(rows)), width="stretch", hide_index=True)

st.subheader("月次サマリー")
st.code(report.monthly(progress(d), rows), language=None)
