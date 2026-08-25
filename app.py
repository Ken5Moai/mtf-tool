"""
取引記録ツール — カレンダーで日々の損益を記録し、日報と分析で振り返る。

    streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

from ui import config_sidebar, data, style

st.set_page_config(page_title="取引記録", page_icon="📅", layout="wide")
style()

# ここはページ切り替えのたびに実行されるので、目標設定サイドバーは全ページ共通になる
config_sidebar(data())

st.navigation([
    st.Page("views/calendar_view.py", title="カレンダー", icon=":material/calendar_month:", default=True),
    st.Page("views/journal_view.py", title="日報", icon=":material/description:"),
    st.Page("views/analysis_view.py", title="分析", icon=":material/insights:"),
]).run()
