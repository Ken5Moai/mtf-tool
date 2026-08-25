"""store.py: 入力パース・派生値・永続化のテスト。"""
from datetime import date

import pytest

import store
from store import BUY, SELL, Trade, parse_line

D = date(2026, 8, 25)


# ------------------------------------------------------------------ parse
@pytest.mark.parametrize("text,expected", [
    ("今日の利益：15ドル、メモ：押し目買い", {"pnl": 15.0, "memo": "押し目買い"}),
    ("今日の利益:-8ドル", {"pnl": -8.0}),
    ("今日の利益：マイナス12.5ドル", {"pnl": -12.5}),
    ("利益：1,200ドル", {"pnl": 1200.0}),
    ("BTC/USD 買い +15ドル", {"pnl": 15.0, "symbol": "BTC/USD", "side": BUY}),
    ("usd/jpy ショート -8ドル", {"pnl": -8.0, "symbol": "USD/JPY", "side": SELL}),
    ("今日の利益：10ドル 手数料0.4ドル", {"pnl": 10.0, "fee": 0.4}),
    ("BTC/USD 買い ロット0.05 利益：3ドル", {"pnl": 3.0, "size": 0.05}),
])
def test_parse_fields(text, expected):
    t = parse_line(text, on=D)
    for k, v in expected.items():
        assert getattr(t, k) == v, k
    assert t.date == "2026-08-25"


def test_parse_levels_and_bare_amount_after_labels():
    t = parse_line("BTC/USD 買い エントリー64000 決済64500 SL63800 TP64600 +25ドル", on=D)
    assert (t.entry, t.exit, t.sl, t.tp) == (64000.0, 64500.0, 63800.0, 64600.0)
    assert t.pnl == 25.0            # ラベルに消費されず残った「+25ドル」を拾う
    assert t.side == BUY


def test_parse_memo_keeps_label_like_words():
    """メモ本文の「利確」「決済」をラベルと誤認しない（直後が数字でないため）。"""
    t = parse_line("今日の利益：-12.5ドル、メモ：利確が早すぎた、次は決済を伸ばす", on=D)
    assert t.pnl == -12.5
    assert t.memo == "利確が早すぎた、次は決済を伸ばす"


def test_parse_side_not_taken_from_memo():
    """「押し目買い」という語だけで BUY と判定しない。"""
    t = parse_line("今日の利益：15ドル、メモ：押し目買い", on=D)
    assert t.side == ""


def test_parse_tags():
    t = parse_line("BTC/USD 売り -6ドル タグ：逆張り 反省", on=D)
    assert t.tags == ["逆張り", "反省"]
    assert t.side == SELL


@pytest.mark.parametrize("text", ["", "今日はノートレード", "   "])
def test_parse_rejects_missing_amount(text):
    with pytest.raises(ValueError):
        parse_line(text)


# ---------------------------------------------------------------- derived
def test_net_and_result():
    assert Trade("2026-08-25", 10.0, fee=0.4).net == 9.6
    assert Trade("2026-08-25", 10.0).result == "win"
    assert Trade("2026-08-25", -1.0).result == "loss"
    assert Trade("2026-08-25", 0.5, fee=0.5).result == "be"


def test_r_multiple_long_and_short():
    long = Trade("2026-08-25", 25.0, side=BUY, entry=64000, exit=64500, sl=63800, tp=64600)
    assert long.risk_per_unit == 200.0
    assert long.r_multiple == 2.5
    assert long.planned_rr == 3.0

    short = Trade("2026-08-25", -10.0, side=SELL, entry=150.0, exit=150.5, sl=150.5)
    assert short.r_multiple == -1.0


def test_r_multiple_needs_full_levels():
    assert Trade("2026-08-25", 5.0, side=BUY, entry=100).r_multiple is None
    assert Trade("2026-08-25", 5.0, side=BUY, entry=100, sl=100, exit=105).r_multiple is None  # リスク0


# ------------------------------------------------------------------ store
def test_add_assigns_sequential_ids_and_roundtrips(tmp_path):
    path = tmp_path / "trades.json"
    d = store.load(path)
    assert d["config"]["usdjpy"] == store.DEFAULT_CONFIG["usdjpy"]

    a = store.add(d, parse_line("BTC/USD 買い +15ドル", on=D))
    b = store.add(d, parse_line("今日の利益：-5ドル", on=D))
    assert (a.id, b.id) == ("t00001", "t00002")

    d["config"]["usdjpy"] = 150.0
    store.save(d, path)

    back = store.load(path)
    assert [t.id for t in back["trades"]] == ["t00001", "t00002"]
    assert back["config"]["usdjpy"] == 150.0
    assert back["trades"][0].symbol == "BTC/USD"

    # 読み込み後も ID が衝突しない
    assert store.add(back, parse_line("利益：1ドル", on=D)).id == "t00003"


def test_remove_and_filters(tmp_path):
    d = store.load(tmp_path / "x.json")
    store.add(d, parse_line("利益：1ドル", on=date(2026, 7, 31)))
    store.add(d, parse_line("利益：2ドル", on=date(2026, 8, 1)))
    store.add(d, parse_line("利益：3ドル", on=date(2026, 8, 1)))

    assert len(store.month_of(d["trades"], "2026-08")) == 2
    assert len(store.day_of(d["trades"], "2026-08-01")) == 2

    assert store.remove(d, "t00002").pnl == 2.0
    assert store.remove(d, "nope") is None
    assert len(d["trades"]) == 2


def test_load_ignores_unknown_fields(tmp_path):
    path = tmp_path / "x.json"
    path.write_text('{"config": {}, "trades": [{"date": "2026-08-01", "pnl": 5, "legacy": 1}]}',
                    encoding="utf-8")
    d = store.load(path)
    assert d["trades"][0].pnl == 5
