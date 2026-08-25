"""
日報テキストの生成。結論 -> 根拠 -> 次のアクション の3部構成、要点は各3つまで。
"""
from __future__ import annotations

from typing import Any

from stats import by_symbol, by_tag


def bar(ratio: float, width: int = 20) -> str:
    n = max(0, min(width, round(ratio * width)))
    return "█" * n + "░" * (width - n)


def _pf(v: float) -> str:
    return "∞" if v == float("inf") else f"{v:.2f}"


def _cheer(p: dict[str, Any]) -> str:
    tp, perf = p["today_pnl"], p["perf"]
    if not p["today_trades"]:
        return "ノートレードも立派な判断。条件が来ない日に耐えられる人だけが月末に残る。"
    if tp > 0 and p["pace"] >= 1.0:
        return "ナイストレード。ペースに貯金あり、この型を明日もそのまま再現しよう。"
    if tp > 0:
        return "小さくてもプラス。積み上げは裏切らないので、同じ手順を明日も淡々と。"
    if tp == 0:
        return "資金を減らさずに1日を終えられた。守れた日は次の勝負に繋がる。"
    if perf["max_loss_streak"] >= 3:
        return "連敗中は判断力が落ちる。ロットを落として、明日は1トレードだけに絞ろう。"
    return "損切りできた時点で及第点。1日の負けは30日の中の1マス、明日リセット。"


def _next_action(p: dict[str, Any], trades: list) -> str:
    perf = p["perf"]
    if p["seed_alert"]:
        return (f"種銭割れ（口座 {p['equity_usd']:,.2f} ドル < 下限 {p['seed_floor']:,.2f} ドル）。"
                f"ロットを半分にして、明日は最も自信のある1トレードだけ")
    if perf["max_loss_streak"] >= 3 and p["today_pnl"] < 0:
        return "3連敗以上。明日はロット半分・1日1トレード上限で、まず流れを止める"
    if p["days_left"] and p["need_per_day"] > p["par_per_day"] * 1.5:
        return (f"必要ペースが平常の1.5倍（{p['need_per_day']:,.2f} ドル/日）。"
                f"狙う回数は増やさず、RR 2.0 以上の場面だけに絞る")
    if perf["trades"] >= 5 and perf["profit_factor"] < 1.0:
        worst = _worst_bucket(trades)
        return f"今月の PF が {_pf(perf['profit_factor'])}。{worst}を止めるだけで収支が変わる"
    if p["days_left"] == 0:
        return "稼働日を消化。今月の数字を確定し、勝ちパターンを来月のルールに書き写す"
    return (f"明日も {p['par_per_day']:,.2f} ドル（約 {round(p['par_per_day'] * p['rate']):,} 円）を淡々と。"
            f"1トレードの許容損失は {p['risk_usd']:,.2f} ドルまで")


def _worst_bucket(trades: list) -> str:
    """最も負けている銘柄／タグを指摘する。"""
    cands: list[tuple[float, str]] = []
    for label, table in (("", by_symbol(trades)), ("タグ ", by_tag(trades))):
        for k, v in table.items():
            if v["trades"] >= 2 and v["net"] < 0:
                cands.append((v["net"], f"{label}{k}（{v['net']:+,.2f} ドル / {v['trades']}件）"))
    if not cands:
        return "負けトレード"
    return min(cands)[1]


def daily(p: dict[str, Any], trades: list, memo: str = "") -> str:
    """日報テキストを組み立てる。p は stats.progress()、trades は今月分。"""
    perf = p["perf"]
    tp = p["today_pnl"]
    sign = "+" if tp >= 0 else ""
    L: list[str] = []

    L.append(f"■ 日報 {p['date']}")
    L.append("")
    L.append("【結論】")
    L.append(f"  本日 {sign}{tp:,.2f} ドル（{p['today_trades']}件） / 今月累計 {p['cum_usd']:+,.2f} ドル"
             f"（約 {p['cum_jpy']:+,} 円）")
    L.append(f"  達成率 {p['achieved'] * 100:5.1f}%  {bar(p['achieved'])}  "
             f"目標 {p['goal_usd']:,.2f} ドル / {p['goal_jpy']:,} 円")
    if memo:
        L.append(f"  メモ: {memo}")

    gap, gs = p["gap_usd"], ("貯金" if p["gap_usd"] >= 0 else "ビハインド")
    L.append("")
    L.append("【根拠】")
    L.append(f"  ① {p['days_done']}/{p['days_total']} 日目・想定ライン {p['on_track_usd']:,.2f} ドルに対し "
             f"{abs(gap):,.2f} ドルの{gs} → {p['status']}")
    if p["days_left"]:
        L.append(f"  ② 残り {p['days_left']} 日、必要ペース {p['need_per_day']:,.2f} ドル/日"
                 f"（平常ペース {p['par_per_day']:,.2f} ドル/日）")
    else:
        L.append("  ② 稼働日は消化済み。今月の結果を確定して来月の設定に反映しよう")
    seed_note = ("（種銭維持OK）" if p["seed_ok"]
                 else f"**（種銭割れ・下限 {p['seed_floor']:,.2f} ドル）**")
    L.append(f"  ③ 勝率 {perf['win_rate'] * 100:.0f}%（{perf['wins']}勝{perf['losses']}敗） / "
             f"PF {_pf(perf['profit_factor'])} / 期待値 {perf['expectancy']:+,.2f} ドル / "
             f"口座 {p['equity_usd']:,.2f} ドル {seed_note}")

    L.append("")
    L.append("【次のアクション】")
    L.append(f"  ・{_next_action(p, trades)}")
    L.append(f"  ・{_cheer(p)}")
    return "\n".join(L)


def monthly(p: dict[str, Any], trades: list) -> str:
    """月次サマリー。"""
    perf = p["perf"]
    L = [f"■ 月次サマリー {p['month']}", ""]
    L.append(f"  純損益        {perf['net']:+,.2f} ドル（約 {p['cum_jpy']:+,} 円） / 目標比 {p['achieved'] * 100:.1f}%")
    L.append(f"  トレード数    {perf['trades']}件（{perf['wins']}勝 {perf['losses']}敗 {perf['breakeven']}分） 勝率 {perf['win_rate'] * 100:.0f}%")
    L.append(f"  PF / 期待値   {_pf(perf['profit_factor'])} / {perf['expectancy']:+,.2f} ドル")
    L.append(f"  平均利益/損失 {perf['avg_win']:,.2f} / {perf['avg_loss']:,.2f} ドル（ペイオフ {perf['payoff']:.2f}）")
    L.append(f"  最大DD        {perf['max_drawdown']:,.2f} ドル / 最大連敗 {perf['max_loss_streak']}")
    if perf["avg_r"] is not None:
        L.append(f"  平均R         {perf['avg_r']:+.2f} R")
    if perf["fees"]:
        L.append(f"  手数料合計    {perf['fees']:,.2f} ドル")

    tbl = {k: v for k, v in by_symbol(trades).items() if v["trades"]}
    if len(tbl) > 1:
        L.append("")
        L.append("  銘柄別:")
        width = max(len(k) for k in tbl)
        for k, v in sorted(tbl.items(), key=lambda kv: -kv[1]["net"]):
            L.append(f"    {k:<{width}}  {v['net']:+9,.2f} ドル  {v['trades']:>3}件  勝率 {v['win_rate'] * 100:3.0f}%")
    return "\n".join(L)
