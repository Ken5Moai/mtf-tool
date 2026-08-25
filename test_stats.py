"""stats.py / report.py: 集計と日報生成のテスト。"""
from datetime import date

import pytest

import report
import store
from stats import (by_symbol, by_tag, by_weekday, daily_pnl, equity_curve,
                   max_drawdown, performance, progress)
from store import BUY, SELL, Trade


def T(day, pnl, **kw):
    return Trade(date=f"2026-08-{day:02d}", pnl=pnl, **kw)


def data(trades, **cfg):
    d = {"config": dict(store.DEFAULT_CONFIG), "trades": []}
    d["config"]["usdjpy"] = 100.0     # 目標10万円 -> 1,000ドル / 30日 -> 33.33ドル/日
    d["config"].update(cfg)
    for t in trades:
        store.add(d, t)
    return d


# ------------------------------------------------------------ performance
def test_performance_core_metrics():
    p = performance([T(1, 30), T(2, -10), T(3, 20, fee=2), T(4, -10), T(5, 0)])
    assert p["trades"] == 5
    assert (p["wins"], p["losses"], p["breakeven"]) == (2, 2, 1)
    assert p["win_rate"] == 0.5          # 引き分けは母数から除く
    assert p["net"] == 28.0
    assert p["gross_profit"] == 48.0 and p["gross_loss"] == 20.0
    assert p["profit_factor"] == 2.4
    assert p["avg_win"] == 24.0 and p["avg_loss"] == 10.0
    assert p["payoff"] == 2.4
    assert p["expectancy"] == 5.6
    assert (p["best"], p["worst"]) == (30.0, -10.0)
    assert p["fees"] == 2.0


def test_performance_empty_is_safe():
    p = performance([])
    assert p["trades"] == 0 and p["net"] == 0.0
    assert p["win_rate"] == 0.0 and p["profit_factor"] == 0.0
    assert p["max_drawdown"] == 0.0 and p["avg_r"] is None


def test_profit_factor_infinite_without_losses():
    assert performance([T(1, 10), T(2, 5)])["profit_factor"] == float("inf")


def test_streaks_and_drawdown():
    trades = [T(1, 10), T(2, -5), T(3, -5), T(4, -5), T(5, 20), T(6, 20)]
    p = performance(trades)
    assert p["max_loss_streak"] == 3
    assert p["max_win_streak"] == 2
    assert equity_curve(trades) == [10.0, 5.0, 0.0, -5.0, 15.0, 35.0]
    assert max_drawdown(trades) == 15.0      # ピーク10 -> ボトム -5


def test_avg_r_uses_only_trades_with_levels():
    trades = [T(1, 25, side=BUY, entry=64000, exit=64500, sl=63800),   # +2.5R
              T(2, -10, side=BUY, entry=100, exit=99, sl=99),          # -1.0R
              T(3, 5)]                                                 # R 計算不可
    assert performance(trades)["avg_r"] == 0.75


# -------------------------------------------------------------- breakdown
def test_breakdowns():
    trades = [T(20, 25, symbol="BTC/USD", tags=["押し目"]),
              T(21, -12, symbol="USD/JPY", tags=["逆張り"]),
              T(22, -6, symbol="BTC/USD", tags=["逆張り"]),
              T(24, 8)]
    sym = by_symbol(trades)
    assert sym["BTC/USD"]["net"] == 19.0 and sym["BTC/USD"]["trades"] == 2
    assert sym["(未指定)"]["net"] == 8.0

    tag = by_tag(trades)
    assert tag["逆張り"]["net"] == -18.0 and tag["逆張り"]["trades"] == 2
    assert tag["(タグなし)"]["trades"] == 1

    assert by_weekday(trades)["木"]["trades"] == 1     # 2026-08-20 は木曜
    assert daily_pnl(trades) == {"2026-08-20": 25.0, "2026-08-21": -12.0,
                                 "2026-08-22": -6.0, "2026-08-24": 8.0}


def test_daily_pnl_sums_multiple_trades_per_day():
    assert daily_pnl([T(1, 10), T(1, -3), T(2, 5)]) == {"2026-08-01": 7.0, "2026-08-02": 5.0}


# ------------------------------------------------------------------ goal
def test_progress_on_track():
    d = data([T(i, 50.0) for i in range(1, 11)])
    p = progress(d, date(2026, 8, 10))

    assert p["cum_usd"] == 500.0 and p["goal_usd"] == 1000.0
    assert p["achieved"] == pytest.approx(0.5)
    assert (p["days_done"], p["days_left"]) == (10, 20)
    assert p["on_track_usd"] == pytest.approx(333.33, abs=0.01)
    assert p["gap_usd"] > 0 and p["status"] == "順調"
    assert p["need_per_day"] == pytest.approx(25.0)
    assert p["par_per_day"] == pytest.approx(33.33, abs=0.01)
    assert p["today_pnl"] == 50.0 and p["today_trades"] == 1


def test_progress_counts_days_not_trades():
    d = data([T(1, 10), T(1, 10), T(1, 10)])
    p = progress(d, date(2026, 8, 1))
    assert p["days_done"] == 1 and p["perf"]["trades"] == 3
    assert p["today_pnl"] == 30.0 and p["today_trades"] == 3


def test_progress_seed_break_and_risk():
    d = data([T(i, -60.0) for i in range(1, 11)])       # 種銭5万円 = 500ドル
    p = progress(d, date(2026, 8, 10))
    assert p["status"] == "要巻き返し"
    assert p["equity_usd"] == -100.0
    assert p["seed_ok"] is False and p["seed_alert"] is True
    assert "種銭割れ" in report.daily(p, d["trades"])


def test_seed_alert_only_past_max_drawdown():
    """わずかなマイナスでは警告を出さず、許容ドローダウン超過で初めて立てる。"""
    d = data([T(1, -50.0)], max_drawdown_pct=20.0)      # 種銭500ドル -> 下限400ドル
    p = progress(d, date(2026, 8, 1))
    assert p["equity_usd"] == 450.0 and p["seed_floor"] == 400.0
    assert p["seed_ok"] is False and p["seed_alert"] is False

    d2 = data([T(1, -150.0)], max_drawdown_pct=20.0)
    assert progress(d2, date(2026, 8, 1))["seed_alert"] is True


def test_progress_risk_follows_equity():
    d = data([T(1, 100.0)], risk_per_trade_pct=2.0)
    p = progress(d, date(2026, 8, 1))
    assert p["equity_usd"] == 600.0 and p["risk_usd"] == 12.0


def test_progress_ignores_other_months():
    d = data([Trade("2026-07-31", 999.0), Trade("2026-08-01", 10.0)])
    p = progress(d, date(2026, 8, 1))
    assert p["cum_usd"] == 10.0 and p["perf"]["trades"] == 1


def test_progress_last_day_has_no_required_pace():
    d = data([T(i, 1.0) for i in range(1, 31)])
    p = progress(d, date(2026, 8, 30))
    assert p["days_left"] == 0 and p["need_per_day"] == 0.0
    assert "稼働日は消化済み" in report.daily(p, d["trades"])


# ---------------------------------------------------------------- report
def test_daily_report_structure():
    d = data([T(25, 15.0, symbol="BTC/USD", side=BUY)])
    text = report.daily(progress(d, date(2026, 8, 25)), d["trades"], "押し目買い")
    assert "【結論】" in text and "【根拠】" in text and "【次のアクション】" in text
    for mark in ("①", "②", "③"):
        assert text.count(mark) == 1
    assert "押し目買い" in text
    assert text.count("\n  ・") == 2          # 次のアクションは2行まで


def test_daily_report_flags_losing_streak():
    d = data([T(i, -10.0) for i in range(20, 26)])
    text = report.daily(progress(d, date(2026, 8, 25)), d["trades"])
    assert "ロット" in text


def test_daily_report_names_worst_bucket_when_pf_below_one():
    trades = [T(20, -20, symbol="USD/JPY"), T(21, -20, symbol="USD/JPY"),
              T(22, 5, symbol="BTC/USD"), T(23, 5, symbol="BTC/USD"), T(24, 5, symbol="BTC/USD")]
    d = data(trades)
    text = report.daily(progress(d, date(2026, 8, 24)), d["trades"])
    assert "USD/JPY" in text


def test_monthly_report():
    d = data([T(20, 25, symbol="BTC/USD", side=BUY, entry=64000, exit=64500, sl=63800),
              T(21, -12, symbol="USD/JPY", side=SELL, fee=0.3)])
    text = report.monthly(progress(d, date(2026, 8, 21)), d["trades"])
    assert "月次サマリー 2026-08" in text
    assert "BTC/USD" in text and "USD/JPY" in text
    assert "平均R" in text


def test_bar_is_clamped():
    assert report.bar(0.0) == "░" * 20
    assert report.bar(1.0) == "█" * 20
    assert report.bar(3.0) == "█" * 20
    assert report.bar(-1.0) == "░" * 20
