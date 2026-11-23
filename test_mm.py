"""マーケットメイク戦略の検算。

Hummingbot はこの環境で動かせないので、戦略の本体（mm/core.py）と
シミュレータのロジックだけを直接叩いて確かめる。
"""

import csv
import math
from pathlib import Path

import pytest

from mm.core import (
    BPS, Fees, LastTradeMarketMaker, Params, VolEstimator,
    effective_half_spread_bps, inventory_skew_bps, make_quote, should_requote,
)
from mm.sim import run_last_trade_mm, run_simple_pmm
from mm.tape import BUY, SELL, Trade, ranging_tape, synthetic_tape, trending_tape


# ---------------------------------------------------------------- 手数料
def test_breakeven_is_two_times_maker_fee():
    f = Fees(maker_bps=10.0)
    assert f.round_trip_maker_bps == 20.0
    assert f.breakeven_full_spread_bps() == 20.0
    assert f.breakeven_half_spread_bps() == 10.0


def test_breakeven_includes_buffer():
    f = Fees(maker_bps=2.0)
    assert f.breakeven_full_spread_bps(buffer_bps=1.0) == 5.0
    assert f.breakeven_half_spread_bps(buffer_bps=1.0) == 2.5


# ---------------------------------------------------------------- スプレッド
def test_fee_floor_lifts_a_too_tight_spread():
    p = Params(fees=Fees(maker_bps=10.0), base_half_spread_bps=1.0,
               vol_widen_k=0.0, fee_buffer_bps=1.0)
    assert effective_half_spread_bps(p, vol_bps=0.0) == pytest.approx(10.5)


def test_fee_floor_can_be_disabled():
    p = Params(fees=Fees(maker_bps=10.0), base_half_spread_bps=1.0,
               vol_widen_k=0.0, enforce_fee_floor=False)
    assert effective_half_spread_bps(p, vol_bps=0.0) == pytest.approx(1.0)


def test_volatility_widens_the_spread():
    p = Params(fees=Fees(maker_bps=0.0), base_half_spread_bps=5.0,
               vol_widen_k=2.0, fee_buffer_bps=0.0)
    assert effective_half_spread_bps(p, vol_bps=0.0) == pytest.approx(5.0)
    assert effective_half_spread_bps(p, vol_bps=3.0) == pytest.approx(11.0)


def test_spread_is_capped():
    p = Params(fees=Fees(maker_bps=0.0), base_half_spread_bps=5.0,
               vol_widen_k=10.0, max_half_spread_bps=20.0, fee_buffer_bps=0.0)
    assert effective_half_spread_bps(p, vol_bps=100.0) == pytest.approx(20.0)


# ---------------------------------------------------------------- 在庫スキュー
def test_long_inventory_pushes_quotes_down():
    p = Params(inventory_cap_base=0.01, inventory_skew_bps_at_cap=10.0)
    assert inventory_skew_bps(p, 0.0) == pytest.approx(0.0)
    assert inventory_skew_bps(p, 0.01) == pytest.approx(-10.0)
    assert inventory_skew_bps(p, -0.01) == pytest.approx(10.0)


def test_skew_is_clamped_beyond_the_cap():
    p = Params(inventory_cap_base=0.01, inventory_skew_bps_at_cap=10.0)
    assert inventory_skew_bps(p, 5.0) == pytest.approx(-10.0)


# ---------------------------------------------------------------- 気配
def test_quote_straddles_the_fair_price():
    p = Params(fees=Fees(maker_bps=0.0), base_half_spread_bps=10.0,
               vol_widen_k=0.0, fee_buffer_bps=0.0)
    q = make_quote(100.0, inventory=0.0, vol_bps=0.0, p=p)
    assert q.bid == pytest.approx(100.0 * (1 - 10 * BPS))
    assert q.ask == pytest.approx(100.0 * (1 + 10 * BPS))
    assert q.full_spread_bps == pytest.approx(20.0)


def test_inventory_cap_stops_buying():
    p = Params(inventory_cap_base=0.01, inventory_floor_base=-0.01)
    q = make_quote(100.0, inventory=0.01, vol_bps=0.0, p=p)
    assert q.bid_size == 0.0 and q.bid is None
    assert q.ask_size > 0
    assert any("買いを停止" in n for n in q.notes)


def test_inventory_floor_stops_selling():
    p = Params(inventory_cap_base=0.01, inventory_floor_base=-0.01)
    q = make_quote(100.0, inventory=-0.01, vol_bps=0.0, p=p)
    assert q.ask_size == 0.0 and q.ask is None
    assert q.bid_size > 0


def test_bad_fair_price_quotes_nothing():
    q = make_quote(0.0, 0.0, 0.0, Params())
    assert q.bid is None and q.ask is None


# ---------------------------------------------------------------- 出し直し
@pytest.mark.parametrize("prev,new,threshold,expected", [
    (None, 100.0, 1.0, True),
    (100.0, 100.0, 1.0, False),
    (100.0, 100.005, 1.0, False),   # 0.5bps の動きでは出し直さない
    (100.0, 100.02, 1.0, True),     # 2bps なら出し直す
    (100.0, 99.98, 1.0, True),
])
def test_requote_threshold(prev, new, threshold, expected):
    p = Params(requote_threshold_bps=threshold)
    assert should_requote(prev, new, p) is expected


# ---------------------------------------------------------------- ボラ
def test_vol_is_zero_for_a_flat_tape():
    v = VolEstimator(10)
    for _ in range(20):
        v.update(100.0)
    assert v.value_bps == pytest.approx(0.0)


def test_vol_rises_with_movement():
    calm, wild = VolEstimator(20), VolEstimator(20)
    for i in range(40):
        calm.update(100.0 + (i % 2) * 0.001)
        wild.update(100.0 + (i % 2) * 1.0)
    assert wild.value_bps > calm.value_bps > 0


def test_vol_window_must_be_sane():
    with pytest.raises(ValueError):
        VolEstimator(1)


# ------------------------------------------------- 適正価格の偏り除去
def test_debias_returns_the_midpoint_of_both_sides():
    """直近約定は「中値 ± 板の片側幅」。買い約定と売り約定の中点で偏りが外れる。"""
    p = Params(debias_last_trade=True)
    mm = LastTradeMarketMaker(p)
    mm.on_trade(100.10, BUY)     # アスクが取られた
    mm.on_trade(99.90, SELL)     # ビッドが叩かれた
    assert mm.fair == pytest.approx(100.0)
    assert mm.market_half_spread_bps == pytest.approx(10.0, rel=1e-3)


def test_without_debias_the_fair_price_chases_the_last_trade():
    p = Params(debias_last_trade=False)
    mm = LastTradeMarketMaker(p)
    mm.on_trade(100.10, BUY)
    assert mm.fair == pytest.approx(100.10)
    mm.on_trade(99.90, SELL)
    assert mm.fair == pytest.approx(99.90)


def test_debias_falls_back_before_both_sides_are_seen():
    mm = LastTradeMarketMaker(Params(debias_last_trade=True))
    mm.on_trade(100.10, BUY)
    assert mm.fair == pytest.approx(100.10)


def test_fills_move_inventory():
    mm = LastTradeMarketMaker(Params())
    mm.on_fill("buy", 0.003)
    mm.on_fill("sell", 0.001)
    assert mm.inventory == pytest.approx(0.002)
    with pytest.raises(ValueError):
        mm.on_fill("hold", 1.0)


# ---------------------------------------------------------------- 約定モデル
def test_only_an_aggressive_sell_can_hit_our_bid():
    """攻め手が買い（アスクを取った）なら、同じ価格でも自分のビッドは当たらない。"""
    p = Params(fees=Fees(maker_bps=0.0), base_half_spread_bps=10.0, order_amount=1.0,
               vol_widen_k=0.0, enforce_fee_floor=False, debias_last_trade=False,
               requote_threshold_bps=1e9)   # 一度出したら動かさない
    tape = [Trade(0.0, 100.0, 1.0, BUY), Trade(1.0, 99.0, 10.0, BUY)]
    assert run_last_trade_mm(tape, p).n_buys == 0

    tape = [Trade(0.0, 100.0, 1.0, BUY), Trade(1.0, 99.0, 10.0, SELL)]
    assert run_last_trade_mm(tape, p).n_buys == 1


# ---------------------------------------------------------------- 全体
def _cap_only_params(cap=0.005):
    """在庫スキューを切って、上限そのものの効きだけを見るための設定。"""
    return Params(fees=Fees(maker_bps=0.0), base_half_spread_bps=2.0, order_amount=0.001,
                  inventory_cap_base=cap, inventory_floor_base=-cap,
                  inventory_skew_bps_at_cap=0.0,
                  vol_widen_k=0.0, enforce_fee_floor=False, requote_threshold_bps=0.5)


def test_falling_market_fills_our_bids_and_the_cap_stops_buying():
    """下げ相場では売り手がビッドを叩いてくるので、買いばかり約定して在庫が増える。

    依頼にあった「買いばかり約定してポジションが片側に偏る」のはこちら。
    """
    p = _cap_only_params()
    falling = synthetic_tape(8000, vol_bps=1.0, drift_bps=-1.0, buy_ratio=0.35, seed=3)
    r = run_last_trade_mm(falling, p)
    assert r.fills, "下げ相場のテープで一度も約定しないのはおかしい"
    assert r.n_buys > r.n_sells, "下げ相場なら買い約定が多くなるはず"
    assert r.max_abs_inventory <= p.inventory_cap_base + p.order_amount + 1e-9
    assert r.blocked_buy_ticks > 0, "上限に当たって買いが止まった形跡が無い"


def test_rising_market_lifts_our_asks_and_the_floor_stops_selling():
    """上げ相場は逆。買い手にアスクを取られ続けて、在庫はマイナス側に偏る。"""
    p = _cap_only_params()
    rising = synthetic_tape(8000, vol_bps=1.0, drift_bps=1.0, buy_ratio=0.65, seed=4)
    r = run_last_trade_mm(rising, p)
    assert r.fills
    assert r.n_sells > r.n_buys, "上げ相場なら売り約定が多くなるはず"
    assert r.max_abs_inventory <= p.inventory_cap_base + p.order_amount + 1e-9
    assert r.blocked_sell_ticks > 0, "下限に当たって売りが止まった形跡が無い"


def test_skew_keeps_inventory_closer_to_target():
    """上限に触る前に、スキューが在庫を戻しておいてくれるか。"""
    tape = synthetic_tape(8000, vol_bps=1.0, drift_bps=-0.6, buy_ratio=0.40, seed=5)
    base = dict(fees=Fees(maker_bps=0.0), base_half_spread_bps=2.0, order_amount=0.001,
                inventory_cap_base=1.0, inventory_floor_base=-1.0,   # 上限で隠れないように
                vol_widen_k=0.0, enforce_fee_floor=False, requote_threshold_bps=0.5)
    flat = run_last_trade_mm(tape, Params(inventory_skew_bps_at_cap=0.0, **base))
    skewed = run_last_trade_mm(tape, Params(inventory_skew_bps_at_cap=10.0, **base))
    assert skewed.mean_abs_inventory < flat.mean_abs_inventory


def test_no_cap_lets_inventory_run_further():
    tape = trending_tape(8000)
    base = dict(fees=Fees(maker_bps=0.0), base_half_spread_bps=2.0, order_amount=0.001,
                vol_widen_k=0.0, enforce_fee_floor=False, requote_threshold_bps=0.5)
    capped = run_last_trade_mm(tape, Params(inventory_cap_base=0.005,
                                            inventory_floor_base=-0.005, **base))
    loose = run_last_trade_mm(tape, Params(inventory_cap_base=1.0,
                                           inventory_floor_base=-1.0, **base))
    assert loose.max_abs_inventory > capped.max_abs_inventory


def test_wider_spread_means_fewer_fills():
    tape = ranging_tape(8000)
    base = dict(fees=Fees(maker_bps=0.0), order_amount=0.001, vol_widen_k=0.0,
                enforce_fee_floor=False, requote_threshold_bps=0.5,
                inventory_cap_base=1.0, inventory_floor_base=-1.0)
    tight = run_last_trade_mm(tape, Params(base_half_spread_bps=1.0, **base))
    wide = run_last_trade_mm(tape, Params(base_half_spread_bps=6.0, **base))
    assert len(tight.fills) > len(wide.fills)


def test_debias_improves_gross_edge_on_a_bouncing_tape():
    """中点を使うと、板の片側幅ぶんの偏りが消えて粗利が改善する。"""
    tape = synthetic_tape(15000, vol_bps=0.5, market_half_spread_bps=2.0, seed=11)
    base = dict(fees=Fees(maker_bps=0.0), base_half_spread_bps=2.0, order_amount=0.001,
                vol_widen_k=0.0, enforce_fee_floor=False, requote_threshold_bps=0.5,
                inventory_cap_base=1.0, inventory_floor_base=-1.0)
    raw = run_last_trade_mm(tape, Params(debias_last_trade=False, **base))
    fixed = run_last_trade_mm(tape, Params(debias_last_trade=True, **base))
    assert fixed.pnl_before_fees > raw.pnl_before_fees


def test_fees_are_the_difference_between_gross_and_net():
    tape = ranging_tape(5000)
    p = Params(fees=Fees(maker_bps=5.0), base_half_spread_bps=10.0, order_amount=0.001,
               vol_widen_k=0.0, requote_threshold_bps=1.0)
    r = run_last_trade_mm(tape, p)
    assert r.pnl_before_fees == pytest.approx(r.total_pnl + r.fees_paid)
    assert r.fees_paid >= 0.0


def test_simple_pmm_baseline_runs_and_requotes_on_a_timer():
    tape = ranging_tape(4000)
    p = Params(fees=Fees(maker_bps=0.0), base_half_spread_bps=3.0, order_amount=0.001)
    r = run_simple_pmm(tape, p, refresh_every=50)
    assert r.requotes == pytest.approx(len(tape) / 50, rel=0.2)


# ---------------------------------------------------------------- CSV
def test_fill_csv_has_the_requested_columns(tmp_path):
    tape = ranging_tape(4000)
    p = Params(fees=Fees(maker_bps=1.0), base_half_spread_bps=3.0, order_amount=0.001,
               vol_widen_k=0.0, enforce_fee_floor=False, requote_threshold_bps=0.5)
    r = run_last_trade_mm(tape, p)
    assert r.fills
    path = r.write_csv(tmp_path / "fills.csv")
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(r.fills)
    for col in ("timestamp", "side", "price", "amount", "fee",
                "half_spread_bps", "inventory_after", "equity_after"):
        assert col in rows[0], f"{col} 列が無い"
    assert rows[0]["side"] in ("buy", "sell")


def test_metrics_are_self_consistent():
    tape = ranging_tape(6000)
    p = Params(fees=Fees(maker_bps=1.0), base_half_spread_bps=4.0, order_amount=0.001,
               vol_widen_k=0.0, enforce_fee_floor=False, requote_threshold_bps=1.0)
    r = run_last_trade_mm(tape, p)
    assert len(r.fills) == r.n_buys + r.n_sells
    assert r.round_trips == min(r.n_buys, r.n_sells)
    assert r.max_drawdown >= 0.0
    assert r.equity == pytest.approx(r.cash + r.inventory * r.final_price)


# ---------------------------------------------------------------- 同期
def test_hummingbot_copy_of_core_is_identical():
    """戦略の本体を2箇所で持たないための検査。ずれたら sync を走らせる。"""
    root = Path(__file__).resolve().parent
    src = (root / "mm" / "core.py").read_text(encoding="utf-8")
    dst = (root / "mm" / "hummingbot" / "last_trade_mm_core.py").read_text(encoding="utf-8")
    assert src == dst, "python scripts/sync_hummingbot.py を実行してください"
