"""日報ページ: 結論 -> 根拠 -> 次のアクション の3部構成でその日を締める。"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

import report
import store
from stats import daily_pnl, progress
from ui import config_sidebar, data, style, trades_frame

st.set_page_config(page_title="日報", page_icon=":memo:", layout="wide")
style()

d = data()
config_sidebar(d)

st.title("日報")
on = st.date_input("対象日", value=date.today())
p = progress(d, on)
rows = store.month_of(d["trades"], p["month"])

st.progress(min(max(p["achieved"], 0.0), 1.0))
st.code(report.daily(p, rows), language=None)

if rows:
    daily = daily_pnl(rows)
    df = pd.DataFrame({"日付": list(daily), "損益($)": list(daily.values())})
    df["累計($)"] = df["損益($)"].cumsum()
    df["想定ライン($)"] = [p["par_per_day"] * (i + 1) for i in range(len(df))]

    st.subheader("今月の推移")
    st.line_chart(df.set_index("日付")[["累計($)", "想定ライン($)"]])
    st.bar_chart(df.set_index("日付")["損益($)"])

    with st.expander("今日の取引"):
        today = [t for t in rows if t.date == on.isoformat()]
        if today:
            st.dataframe(trades_frame(today), width="stretch", hide_index=True)
        else:
            st.write("この日の記録はありません（ノートレード）。")
else:
    st.info("今月の記録はまだありません。")
