"""カレンダーページ: 月のマスをクリックして、その日の損益を記録する。"""
from __future__ import annotations

from datetime import date

import streamlit as st

import store
from stats import month_grid, progress, shift_month
from ui import data, persist, trades_frame

WEEK = ["月", "火", "水", "木", "金", "土", "日"]


def amount(x: float) -> str:
    """カレンダーのマスに収まる短い金額表記。"""
    return f"{x:+,.1f}" if abs(x) < 10 else f"{x:+,.0f}"


def cell_label(c: dict, today: str) -> str:
    """日付マスのラベル。損益の符号を色つきの四角で示す。"""
    head = f"**{c['day']}**" if c["date"] == today else str(c["day"])
    if c["pnl"] is None:
        return f"{head}  \n　"
    mark = "🟩" if c["pnl"] > 0 else "🟥" if c["pnl"] < 0 else "⬜"
    return f"{head}  \n{mark} {amount(c['pnl'])}"


def month_nav(y: int, m: int) -> None:
    c1, c2, c3, c4 = st.columns([1, 1, 1, 6])
    if c1.button("◀ 前月", width="stretch"):
        st.session_state.ym = shift_month(y, m, -1)
        st.rerun()
    if c2.button("今月", width="stretch"):
        st.session_state.ym = (date.today().year, date.today().month)
        st.rerun()
    if c3.button("翌月 ▶", width="stretch"):
        st.session_state.ym = shift_month(y, m, 1)
        st.rerun()


def calendar(d: dict, y: int, m: int) -> None:
    """月グリッド。マスをクリックするとその日が入力対象になる。"""
    ym = f"{y:04d}-{m:02d}"
    rows = store.month_of(d["trades"], ym)
    today = date.today().isoformat()

    for col, name in zip(st.columns(7), WEEK):
        color = {"土": "#3b82f6", "日": "#ef4444"}.get(name, "gray")
        col.markdown(f"<div style='text-align:center;color:{color}'>{name}</div>",
                     unsafe_allow_html=True)

    for week in month_grid(y, m, rows):
        for col, c in zip(st.columns(7), week):
            if c is None:
                col.markdown("&nbsp;", unsafe_allow_html=True)
                continue
            selected = c["date"] == st.session_state.sel
            col.button(cell_label(c, today), key=f"d{c['date']}", width="stretch",
                       type="primary" if selected else "secondary",
                       on_click=lambda k=c["date"]: st.session_state.update(sel=k))


def day_form(d: dict, day: str) -> None:
    """選択中の日の入力フォームと、その日の記録一覧。"""
    st.subheader(f"{day} の記録")

    with st.form("entry", clear_on_submit=True):
        c1, c2 = st.columns([1, 3])
        pnl = c1.number_input("損益 ($)", value=0.0, step=0.5, format="%.2f")
        memo = c2.text_input("メモ", placeholder="H1上昇の押し目、M15で反発を確認")

        with st.expander("銘柄・価格も残す（任意）"):
            e1, e2, e3 = st.columns(3)
            symbol = e1.text_input("銘柄", placeholder="BTC/USD")
            side = e2.selectbox("方向", ["", store.BUY, store.SELL])
            fee = e3.number_input("手数料 ($)", value=0.0, step=0.01, format="%.2f")
            p1, p2, p3, p4 = st.columns(4)
            entry = p1.number_input("エントリー", value=0.0, step=0.1, format="%.4f")
            exit_ = p2.number_input("決済", value=0.0, step=0.1, format="%.4f")
            sl = p3.number_input("SL", value=0.0, step=0.1, format="%.4f")
            tags = p4.text_input("タグ（スペース区切り）", placeholder="押し目 順張り")

        if st.form_submit_button("この日に記録する", type="primary", width="stretch"):
            opt = lambda v: None if v == 0 else float(v)  # noqa: E731
            t = store.Trade(
                date=day, pnl=round(pnl, 2), symbol=symbol.strip().upper(), side=side,
                entry=opt(entry), exit=opt(exit_), sl=opt(sl), fee=round(fee, 2),
                memo=memo.strip(), tags=[x for x in tags.split() if x])
            store.add(d, t)
            persist(d)
            st.rerun()

    rows = store.day_of(d["trades"], day)
    if not rows:
        st.caption("この日はまだ記録がありません（ノートレード）。")
        return

    st.dataframe(trades_frame(rows), width="stretch", hide_index=True)
    c1, c2 = st.columns([2, 1])
    target = c1.selectbox("削除する取引", [t.id for t in rows], key="del",
                          index=None, placeholder="ID を選択")
    if c2.button("削除", width="stretch") and target:
        store.remove(d, target)
        persist(d)
        st.rerun()


def main() -> None:
    d = data()

    st.session_state.setdefault("sel", date.today().isoformat())
    st.session_state.setdefault("ym", (date.today().year, date.today().month))
    y, m = st.session_state.ym

    st.title(f"{y}年 {m}月")
    month_nav(y, m)

    rows = store.month_of(d["trades"], f"{y:04d}-{m:02d}")
    total = round(sum(t.net for t in rows), 2)
    p = progress(d)
    # metric の delta は増減を表す矢印が付いてしまうので、補足は caption で出す
    cards = [
        (f"{y}年{m}月の損益", f"{total:+,.2f} $", f"{len(rows)} 件"),
        ("今月の達成率", f"{p['achieved'] * 100:.1f} %", p["status"]),
        ("必要ペース/日", f"{p['need_per_day']:,.2f} $", f"残り {p['days_left']} 日"),
        ("口座", f"{p['equity_usd']:,.2f} $", "種銭維持OK" if p["seed_ok"] else "⚠ 種銭割れ"),
    ]
    for col, (label, value, note) in zip(st.columns(4), cards):
        col.metric(label, value)
        col.caption(note)

    calendar(d, y, m)
    st.divider()
    day_form(d, st.session_state.sel)


main()
