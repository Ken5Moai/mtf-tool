"""
取引記録の集計。勝率・プロフィットファクター・期待値・ドローダウン、
および月間目標に対する進捗を計算する。
"""
from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any, Callable

from store import Trade, month_of

WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]


# ------------------------------------------------------------- performance
def performance(trades: list[Trade]) -> dict[str, Any]:
    """トレード群のパフォーマンス指標。空リストでも安全に 0 を返す。"""
    nets = [t.net for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x < 0]
    gross_profit = round(sum(wins), 2)
    gross_loss = round(-sum(losses), 2)
    n = len(trades)
    decided = len(wins) + len(losses)

    avg_win = round(gross_profit / len(wins), 2) if wins else 0.0
    avg_loss = round(gross_loss / len(losses), 2) if losses else 0.0
    win_rate = len(wins) / decided if decided else 0.0

    if gross_loss > 0:
        profit_factor = round(gross_profit / gross_loss, 2)
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    rs = [t.r_multiple for t in trades if t.r_multiple is not None]

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": n - decided,
        "win_rate": win_rate,
        "net": round(sum(nets), 2),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff": round(avg_win / avg_loss, 2) if avg_loss else 0.0,
        "expectancy": round(sum(nets) / n, 2) if n else 0.0,
        "best": round(max(nets), 2) if nets else 0.0,
        "worst": round(min(nets), 2) if nets else 0.0,
        "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
        "max_win_streak": _streak(nets, lambda x: x > 0),
        "max_loss_streak": _streak(nets, lambda x: x < 0),
        "max_drawdown": max_drawdown(trades),
        "fees": round(sum(t.fee for t in trades), 2),
    }


def _streak(nets: list[float], pred: Callable[[float], bool]) -> int:
    best = cur = 0
    for x in nets:
        cur = cur + 1 if pred(x) else 0
        best = max(best, cur)
    return best


def equity_curve(trades: list[Trade], start: float = 0.0) -> list[float]:
    """取引順の累積損益（start を起点とする）。"""
    out, acc = [], start
    for t in sorted(trades, key=lambda t: (t.date, t.id)):
        acc = round(acc + t.net, 2)
        out.append(acc)
    return out


def max_drawdown(trades: list[Trade]) -> float:
    """累積損益カーブの最大下落幅（正の値。無ければ 0）。"""
    peak = 0.0
    dd = 0.0
    for eq in equity_curve(trades):
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return round(dd, 2)


# ------------------------------------------------------------------ breakdown
def by(trades: list[Trade], key: Callable[[Trade], Any]) -> dict[Any, dict[str, Any]]:
    """任意のキーでグループ化してパフォーマンスを出す。"""
    groups: dict[Any, list[Trade]] = defaultdict(list)
    for t in trades:
        k = key(t)
        for kk in (k if isinstance(k, list) else [k]):
            groups[kk].append(t)
    return {k: performance(v) for k, v in groups.items()}


def by_symbol(trades: list[Trade]) -> dict[str, dict]:
    return by(trades, lambda t: t.symbol or "(未指定)")


def by_tag(trades: list[Trade]) -> dict[str, dict]:
    return by(trades, lambda t: t.tags or ["(タグなし)"])


def by_weekday(trades: list[Trade]) -> dict[str, dict]:
    return by(trades, lambda t: WEEKDAYS[datetime.fromisoformat(t.date).weekday()])


def daily_pnl(trades: list[Trade]) -> dict[str, float]:
    """日付 -> その日の純損益。"""
    out: dict[str, float] = defaultdict(float)
    for t in trades:
        out[t.date] = round(out[t.date] + t.net, 2)
    return dict(sorted(out.items()))


def month_grid(year: int, month: int, trades: list[Trade]) -> list[list[dict | None]]:
    """
    カレンダー表示用に、月曜始まりの週ごとの日セルを返す。
    前後月にはみ出すマスは None。各セルは {day, date, pnl, trades}。
    """
    daily = daily_pnl(trades)
    counts = Counter(t.date for t in trades)
    grid: list[list[dict | None]] = []
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, month):
        row: list[dict | None] = []
        for d in week:
            if d == 0:
                row.append(None)
                continue
            key = date(year, month, d).isoformat()
            row.append({"day": d, "date": key,
                        "pnl": daily.get(key), "trades": counts.get(key, 0)})
        grid.append(row)
    return grid


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """年月を delta か月ずらす。"""
    i = year * 12 + (month - 1) + delta
    return i // 12, i % 12 + 1


# ------------------------------------------------------------------- goal
def progress(data: dict, today: date | None = None) -> dict[str, Any]:
    """今月の目標に対する進捗。日報の材料になる値をすべて返す。"""
    cfg = data["config"]
    today = today or date.today()
    ym = today.strftime("%Y-%m")
    rate = float(cfg["usdjpy"])
    days_total = int(cfg["trading_days"])

    month = month_of(data["trades"], ym)
    perf = performance(month)
    cum = perf["net"]

    daily = daily_pnl(month)
    days_done = len(daily)
    days_left = max(days_total - days_done, 0)

    goal_usd = cfg["monthly_goal_jpy"] / rate
    seed_usd = cfg["seed_jpy"] / rate
    seed_floor = seed_usd * (1 - float(cfg["max_drawdown_pct"]) / 100)
    on_track = goal_usd * days_done / days_total if days_total else 0.0
    pace = cum / on_track if on_track > 0 else (1.0 if cum >= 0 else 0.0)

    today_key = today.isoformat()
    today_trades = [t for t in month if t.date == today_key]

    status = "順調" if pace >= 1.0 else "ややビハインド" if pace >= 0.8 else "要巻き返し"

    return {
        "month": ym,
        "date": today_key,
        "today_pnl": round(sum(t.net for t in today_trades), 2),
        "today_trades": len(today_trades),
        "cum_usd": cum,
        "cum_jpy": round(cum * rate),
        "goal_usd": round(goal_usd, 2),
        "goal_jpy": int(cfg["monthly_goal_jpy"]),
        "achieved": cum / goal_usd if goal_usd else 0.0,
        "days_done": days_done,
        "days_total": days_total,
        "days_left": days_left,
        "on_track_usd": round(on_track, 2),
        "gap_usd": round(cum - on_track, 2),
        "pace": pace,
        "need_per_day": round((goal_usd - cum) / days_left, 2) if days_left else 0.0,
        "par_per_day": round(goal_usd / days_total, 2) if days_total else 0.0,
        "seed_usd": round(seed_usd, 2),
        "equity_usd": round(seed_usd + cum, 2),
        # 種銭を1ドルでも下回れば seed_ok は False。許容ドローダウンを超えて
        # 初めて seed_alert（ロットを落とす判断）が立つ。
        "seed_ok": (seed_usd + cum) >= seed_usd,
        "seed_floor": round(seed_floor, 2),
        "seed_alert": (seed_usd + cum) < seed_floor,
        "risk_usd": round((seed_usd + cum) * cfg["risk_per_trade_pct"] / 100, 2),
        "status": status,
        "rate": rate,
        "perf": perf,
    }
