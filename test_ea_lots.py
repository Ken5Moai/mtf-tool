"""ea/Step100Man/LotTable.mqh が画像の表と一致しているかを検算する。

MQL5 はこの環境でコンパイルできないので、せめて「数字が合っているか」は
出荷ファイルを直接読んで確かめる。
"""

import pytest

from ea_lots import LotTable, apply_risk_cap, loss_at_stop, risk_percent

# 画像「資金ごとのロット目安」をそのまま書き写したもの（検算の正解）
IMAGE_ROWS = [
    (    5000, 0.02,   1000,   1500),
    (   10000, 0.03,   1500,   2000),
    (   20000, 0.06,   3000,   4000),
    (   30000, 0.09,   4500,   6000),
    (   50000, 0.15,   7500,  10000),
    (   70000, 0.20,  10000,  15000),
    (  100000, 0.30,  15000,  20000),
    (  150000, 0.45,  22500,  30000),
    (  200000, 0.60,  30000,  40000),
    (  300000, 0.90,  45000,  60000),
    (  500000, 1.50,  75000, 100000),
    (  600000, 1.80,  90000, 120000),
    (  750000, 2.10, 105000, 150000),
    (  900000, 2.70, 135000, 200000),
    ( 1000000, 3.00, 150000, 300000),
]


@pytest.fixture(scope="module")
def table():
    return LotTable.load()


def test_table_matches_image(table):
    assert len(table) == len(IMAGE_ROWS)
    for (jpy, lot, _, _), gj, gl in zip(IMAGE_ROWS, table.jpy, table.lot):
        assert gj == pytest.approx(jpy), f"{jpy}円 の段"
        assert gl == pytest.approx(lot), f"{jpy}円 のロット"


def test_tiers_are_ascending(table):
    assert list(table.jpy) == sorted(table.jpy)
    assert list(table.lot) == sorted(table.lot)


@pytest.mark.parametrize(
    "balance,expected",
    [
        (     0, 0.02),   # 5千円未満も最小段で扱う
        (  4999, 0.02),
        (  5000, 0.02),   # 段ちょうど
        (  9999, 0.02),   # 次の段に届くまでは据え置き
        ( 10000, 0.03),
        ( 99999, 0.20),
        (100000, 0.30),
        (749999, 1.80),
        (750000, 2.10),
        (9999999, 3.00),  # 100万円超は既定では打ち止め
    ],
)
def test_lot_for_balance(table, balance, expected):
    assert table.lot_for(balance) == pytest.approx(expected)


def test_extrapolation_above_one_million(table):
    assert table.lot_for(2_000_000, extrapolate=True) == pytest.approx(6.00)
    assert table.lot_for(1_000_000, extrapolate=True) == pytest.approx(3.00)
    assert table.lot_for(2_000_000, extrapolate=False) == pytest.approx(3.00)


def test_tier_index(table):
    assert table.tier_index(4999) == -1
    assert table.tier_index(10000) == 1
    assert table.tier_index(1_000_000) == len(table) - 1


# --- 画像の「1回の利益目安」から、前提にしているレートを割り出す -------------
#
# 低い方の列（40pips）はすべて ロット × 50,000円 ちょうどで一致する。
# つまり画像は 1ロット・1pip ≒ 1,250円（＝$10/pip を 125円/ドルで換算）を
# 前提にしている。いまのドル円では実際の金額はこれより大きくなる。
IMPLIED_PIP_VALUE_PER_LOT = 1250.0


def test_image_profit_column_implies_1250_yen_per_pip():
    off = []
    for jpy, lot, low, _high in IMAGE_ROWS:
        calc = loss_at_stop(lot, 40, IMPLIED_PIP_VALUE_PER_LOT)
        if calc != pytest.approx(low, rel=0.02):
            off.append((jpy, low, calc))
    # 15段すべてが一致する。画像の表はこの前提で作られている。
    assert [row[0] for row in off] == [], f"想定と違う段: {off}"


@pytest.mark.parametrize("sl_pips", [40, 50])
def test_each_tier_risks_a_big_slice_of_the_account(table, sl_pips):
    """画像どおりだと 1回の損切りが資金の 15〜20% になることを明示しておく。

    損切りも 40-50pips なので、『1回の利益目安』はそのまま『1回の損失目安』。
    """
    for jpy, lot in zip(table.jpy, table.lot):
        pct = risk_percent(lot, sl_pips, IMPLIED_PIP_VALUE_PER_LOT, jpy)
        assert 9.0 <= pct <= 26.0, f"{jpy}円 の段で {pct:.1f}%"


def test_one_man_yen_tier_is_15_to_20_percent(table):
    lot = table.lot_for(10_000)
    assert risk_percent(lot, 40, IMPLIED_PIP_VALUE_PER_LOT, 10_000) == pytest.approx(15.0)
    assert risk_percent(lot, 50, IMPLIED_PIP_VALUE_PER_LOT, 10_000) == pytest.approx(18.75)


# --- 安全装置 ---------------------------------------------------------------
def test_risk_cap_shrinks_the_lot():
    # 1万円・0.03lot・45pips。2% に抑えるとロットはぐっと下がる。
    capped = apply_risk_cap(0.03, 45, 2.0, 10_000, IMPLIED_PIP_VALUE_PER_LOT)
    assert capped < 0.03
    assert risk_percent(capped, 45, IMPLIED_PIP_VALUE_PER_LOT, 10_000) == pytest.approx(2.0)


def test_risk_cap_never_raises_the_lot():
    # 上限が緩ければ表のロットのまま
    assert apply_risk_cap(0.03, 45, 90.0, 10_000, IMPLIED_PIP_VALUE_PER_LOT) == pytest.approx(0.03)


def test_risk_cap_disabled_passes_through():
    assert apply_risk_cap(3.00, 45, 0.0, 10_000, IMPLIED_PIP_VALUE_PER_LOT) == pytest.approx(3.00)
