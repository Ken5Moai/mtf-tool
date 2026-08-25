"""
取引記録ツール（Streamlit）。
1トレードずつ記録し、日報と分析ページで振り返る。

    streamlit run app.py
"""
from __future__ import annotations

from datetime import date

import streamlit as st

import store
from stats import progress
from ui import config_sidebar, data, persist, style, trades_frame

st.set_page_config(page_title="取引記録ツール", page_icon=":memo:", layout="wide")
style()


# -------------------------------------------------------------------- page
def main() -> None:
    d = data()
    config_sidebar(d)

    st.title("取引記録ツール")
    st.caption("1トレードずつ記録 → 日報ページで進捗、分析ページで勝ちパターンを確認")

    st.subheader("取引を記録する")
    tab_quick, tab_detail = st.tabs(["かんたん入力", "詳細入力"])

    with tab_quick:
        st.caption('例: `BTC/USD 買い +15ドル メモ：H1上昇の押し目 タグ：押し目` / `今日の利益：15ドル、メモ：〇〇`')
        text = st.text_input("入力", key="quick", label_visibility="collapsed",
                             placeholder="BTC/USD 買い +15ドル メモ：押し目買いで勝ち")
        c1, c2 = st.columns([1, 3])
        on = c2.date_input("日付", value=date.today(), key="quick_date")
        if c1.button("記録する", type="primary", width="stretch", key="quick_go"):
            if not text.strip():
                st.warning("入力が空です。")
            else:
                try:
                    t = store.parse_line(text, on=on)
                except ValueError as e:
                    st.error(str(e))
                else:
                    store.add(d, t)
                    persist(d)
                    st.success(f"[{t.id}] {t.date} {t.symbol or '-'} {t.net:+,.2f} ドルを記録しました")
                    st.rerun()

    with tab_detail:
        with st.form("detail", clear_on_submit=True):
            c1, c2, c3, c4 = st.columns(4)
            f_date = c1.date_input("日付", value=date.today())
            f_symbol = c2.text_input("銘柄", placeholder="BTC/USD")
            f_side = c3.selectbox("方向", ["", store.BUY, store.SELL])
            f_size = c4.number_input("ロット / 数量", value=0.0, step=0.01, format="%.4f")

            c5, c6, c7, c8 = st.columns(4)
            f_entry = c5.number_input("エントリー", value=0.0, step=0.1, format="%.4f")
            f_exit = c6.number_input("決済", value=0.0, step=0.1, format="%.4f")
            f_sl = c7.number_input("SL", value=0.0, step=0.1, format="%.4f")
            f_tp = c8.number_input("TP", value=0.0, step=0.1, format="%.4f")

            c9, c10 = st.columns(2)
            f_pnl = c9.number_input("損益 ($)", value=0.0, step=0.01, format="%.2f")
            f_fee = c10.number_input("手数料 ($)", value=0.0, step=0.01, format="%.2f")

            f_memo = st.text_input("メモ", placeholder="H1上昇の押し目、M15で反発を確認")
            f_tags = st.text_input("タグ（スペース区切り）", placeholder="押し目 順張り")

            if st.form_submit_button("記録する", type="primary", width="stretch"):
                opt = lambda v: None if v == 0 else float(v)  # noqa: E731
                t = store.Trade(
                    date=f_date.isoformat(), pnl=round(f_pnl, 2), symbol=f_symbol.strip().upper(),
                    side=f_side, size=opt(f_size), entry=opt(f_entry), exit=opt(f_exit),
                    sl=opt(f_sl), tp=opt(f_tp), fee=round(f_fee, 2), memo=f_memo.strip(),
                    tags=[x for x in f_tags.split() if x],
                )
                store.add(d, t)
                persist(d)
                st.success(f"[{t.id}] {t.date} {t.net:+,.2f} ドルを記録しました")
                st.rerun()

    st.divider()
    p = progress(d)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("本日", f"{p['today_pnl']:+,.2f} $", f"{p['today_trades']} 件")
    m2.metric("今月累計", f"{p['cum_usd']:+,.2f} $", f"想定比 {p['gap_usd']:+,.2f} $")
    m3.metric("達成率", f"{p['achieved'] * 100:.1f} %", p["status"])
    m4.metric("必要ペース/日", f"{p['need_per_day']:,.2f} $", f"残り {p['days_left']} 日")

    st.subheader(f"{p['month']} の記録")
    rows = store.month_of(d["trades"], p["month"])
    if not rows:
        st.info("まだ記録がありません。上のフォームから最初の1件を入力してください。")
        return

    st.dataframe(trades_frame(rows), width="stretch", hide_index=True)

    c1, c2 = st.columns([2, 1])
    target = c1.selectbox("削除する取引", [""] + [t.id for t in sorted(rows, key=lambda t: t.id, reverse=True)])
    if c2.button("削除", width="stretch") and target:
        store.remove(d, target)
        persist(d)
        st.rerun()


main()
