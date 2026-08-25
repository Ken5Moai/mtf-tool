"""journal.py の集計ロジックのテスト。"""
from datetime import date

import pytest

from journal import DEFAULT_CONFIG, add_entry, parse_entry, report, summarize


def blank():
    return {"config": dict(DEFAULT_CONFIG), "entries": []}


@pytest.mark.parametrize("text,profit,memo", [
    ("今日の利益：15ドル、メモ：BTC押し目買いで勝ち", 15.0, "BTC押し目買いで勝ち"),
    ("今日の利益:-8ドル, メモ: 損切り", -8.0, "損切り"),
    ("今日の利益：マイナス12.5ドル", -12.5, ""),
    ("今日の利益：1,200ドル、メモ：大勝ち。", 1200.0, "大勝ち"),
    ("今日は 20 ドル 取れた", 20.0, ""),
])
def test_parse_entry(text, profit, memo):
    assert parse_entry(text) == (profit, memo)


def test_parse_entry_rejects_garbage():
    with pytest.raises(ValueError):
        parse_entry("今日はノートレード")


def test_summarize_pace_and_goal():
    d = blank()
    d["config"]["usdjpy"] = 100.0  # 目標10万円 -> 1000ドル / 30日 -> 33.33ドル/日
    for i in range(1, 11):
        add_entry(d, 50.0, "", date(2026, 8, i))
    s = summarize(d, date(2026, 8, 10))

    assert s["cum_usd"] == 500.0
    assert s["goal_usd"] == 1000.0
    assert s["achieved"] == pytest.approx(0.5)
    assert s["days_done"] == 10 and s["days_left"] == 20
    assert s["on_track_usd"] == pytest.approx(333.33, abs=0.01)
    assert s["gap_usd"] > 0 and s["status"] == "順調"
    assert s["need_per_day"] == pytest.approx(25.0)


def test_summarize_behind_and_seed_break():
    d = blank()
    d["config"]["usdjpy"] = 100.0  # 種銭5万円 -> 500ドル
    for i in range(1, 11):
        add_entry(d, -60.0, "負け", date(2026, 8, i))
    s = summarize(d, date(2026, 8, 10))

    assert s["status"] == "要巻き返し"
    assert s["equity_usd"] == -100.0
    assert s["seed_ok"] is False
    assert s["win_rate"] == 0.0
    assert "種銭割れ" in report(s)


def test_summarize_ignores_other_months():
    d = blank()
    add_entry(d, 999.0, "先月", date(2026, 7, 31))
    add_entry(d, 10.0, "今月", date(2026, 8, 1))
    s = summarize(d, date(2026, 8, 1))
    assert s["cum_usd"] == 10.0 and s["trades"] == 1


def test_report_sections_present():
    d = blank()
    add_entry(d, 15.0, "BTC押し目買い", date(2026, 8, 25))
    text = report(summarize(d, date(2026, 8, 25)), "BTC押し目買い")
    assert "【結論】" in text and "【根拠】" in text and "【次のアクション】" in text
    assert text.count("①") == 1 and text.count("②") == 1 and text.count("③") == 1
