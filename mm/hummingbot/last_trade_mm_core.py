"""直近約定価格ベースのマーケットメイク — 戦略の本体。

ここには外部依存を一切入れない。理由は2つ。

  1. Hummingbot の script にそのままコピーして使うため。
  2. この環境で pytest から直接叩いて検算するため。

戦略の骨子（依頼どおり）:

    適正価格 = 直近の約定価格
    その少し下に買い指値、少し上に売り指値
    新しい約定が出たら、古い注文を消して出し直す

これに、実運用で必要になる3つを足してある。

  - 手数料フロア : 往復手数料を割るスプレッドでは絶対に出さない
  - 在庫上限     : 片側に溜まったらその側の発注を止める
  - ボラ連動     : 荒れたらスプレッドを広げる
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field, replace

BPS = 1e-4


# ---------------------------------------------------------------- 手数料
@dataclass(frozen=True)
class Fees:
    """片道の手数料（bps）。板に置いた側が maker、取りに行った側が taker。"""

    maker_bps: float = 10.0
    taker_bps: float = 10.0

    @property
    def round_trip_maker_bps(self) -> float:
        """買いも売りも指値で約定した場合の往復コスト。"""
        return self.maker_bps * 2.0

    def breakeven_full_spread_bps(self, buffer_bps: float = 0.0) -> float:
        """損益がゼロになる『買値から売値までの幅』。これ以下は構造的に負け。"""
        return self.round_trip_maker_bps + buffer_bps

    def breakeven_half_spread_bps(self, buffer_bps: float = 0.0) -> float:
        """適正価格から片側までの距離の下限。"""
        return self.breakeven_full_spread_bps(buffer_bps) / 2.0


# ---------------------------------------------------------------- 設定
@dataclass(frozen=True)
class Params:
    order_amount: float = 0.001            # 1回の発注量（建玉通貨）

    base_half_spread_bps: float = 10.0     # 平常時の片側スプレッド
    max_half_spread_bps: float = 100.0     # 広げすぎの上限

    vol_window: int = 50                   # ボラ推定に使う約定本数
    vol_widen_k: float = 1.0               # 片側 += k × ボラ(bps)

    inventory_cap_base: float = 0.010      # これ以上持ったら買いを止める
    inventory_floor_base: float = -0.010   # これ以下になったら売りを止める
    inventory_target_base: float = 0.0     # 戻したい在庫
    inventory_skew_bps_at_cap: float = 10.0  # 上限時に価格をずらす量

    # これだけ動いたら出し直す。片側スプレッドと同じかそれ以上にすること。
    # 小さくすると board を触る回数が増えるだけで、手数料で負ける（README 参照）。
    requote_threshold_bps: float = 8.0

    # 直近約定価格は「中値 ± 板の片側幅」なので、そのままだと必ず偏る。
    # 直近の買い約定と売り約定の中点を取って、その偏りを外す。
    debias_last_trade: bool = True

    fees: Fees = field(default_factory=Fees)
    fee_buffer_bps: float = 1.0            # 手数料フロアに足す余裕
    enforce_fee_floor: bool = True         # False にすると画一的に base を使う

    def with_(self, **kw) -> "Params":
        return replace(self, **kw)


@dataclass(frozen=True)
class Quote:
    """1回ぶんの気配。size が 0 のときは、その側を出さないという意味。"""

    fair: float
    bid: float | None
    ask: float | None
    bid_size: float
    ask_size: float
    half_spread_bps: float
    vol_bps: float
    skew_bps: float
    notes: tuple[str, ...] = ()

    @property
    def full_spread_bps(self) -> float:
        if self.bid is None or self.ask is None or self.fair <= 0:
            return 0.0
        return (self.ask - self.bid) / self.fair / BPS


# ---------------------------------------------------------------- ボラ
class VolEstimator:
    """直近 window 本の対数リターンの標準偏差を bps で返す。

    約定ごとに1本。時間ではなく約定本数で測るので、動きが激しい時間帯ほど
    自動的に窓が短くなる（＝反応が速くなる）。
    """

    def __init__(self, window: int = 50) -> None:
        if window < 2:
            raise ValueError("vol_window は 2 以上にしてください")
        self.window = window
        self._prices: deque[float] = deque(maxlen=window + 1)
        self._value_bps = 0.0

    def update(self, price: float) -> float:
        if price > 0:
            self._prices.append(price)
        self._value_bps = self._compute()
        return self._value_bps

    def _compute(self) -> float:
        n = len(self._prices)
        if n < 3:
            return 0.0
        rets = []
        it = iter(self._prices)
        prev = next(it)
        for p in it:
            if prev > 0 and p > 0:
                rets.append(math.log(p / prev))
            prev = p
        if len(rets) < 2:
            return 0.0
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        return math.sqrt(var) / BPS

    @property
    def value_bps(self) -> float:
        return self._value_bps

    @property
    def ready(self) -> bool:
        return len(self._prices) >= self.window


# ---------------------------------------------------------------- 気配づくり
def effective_half_spread_bps(p: Params, vol_bps: float) -> float:
    """平常スプレッドにボラを足し、手数料フロアと上限で挟む。"""
    spread = p.base_half_spread_bps + p.vol_widen_k * max(0.0, vol_bps)
    if p.enforce_fee_floor:
        spread = max(spread, p.fees.breakeven_half_spread_bps(p.fee_buffer_bps))
    return min(spread, p.max_half_spread_bps)


def inventory_skew_bps(p: Params, inventory: float) -> float:
    """在庫が目標から離れるほど、気配全体をその逆方向へずらす。

    買いが溜まっている（inventory > target）なら価格を下げる。
    売りが先に当たりやすく、買いは当たりにくくなる。
    """
    span = p.inventory_cap_base - p.inventory_target_base
    if span <= 0:
        return 0.0
    z = (inventory - p.inventory_target_base) / span
    z = max(-1.0, min(1.0, z))
    return -z * p.inventory_skew_bps_at_cap


def make_quote(fair: float, inventory: float, vol_bps: float, p: Params) -> Quote:
    """適正価格・在庫・ボラから、出すべき気配を決める。"""
    notes: list[str] = []

    if not (fair > 0) or not math.isfinite(fair):
        return Quote(fair=fair, bid=None, ask=None, bid_size=0.0, ask_size=0.0,
                     half_spread_bps=0.0, vol_bps=vol_bps, skew_bps=0.0,
                     notes=("適正価格が不正",))

    half = effective_half_spread_bps(p, vol_bps)
    skew = inventory_skew_bps(p, inventory)

    floor = p.fees.breakeven_half_spread_bps(p.fee_buffer_bps)
    if p.enforce_fee_floor and p.base_half_spread_bps + p.vol_widen_k * vol_bps < floor:
        notes.append(f"手数料フロアでスプレッドを {floor:.2f}bps に引き上げ")

    center = fair * (1.0 + skew * BPS)
    bid = center * (1.0 - half * BPS)
    ask = center * (1.0 + half * BPS)

    bid_size = p.order_amount
    ask_size = p.order_amount

    if inventory >= p.inventory_cap_base:
        bid_size = 0.0
        notes.append(f"在庫上限 {p.inventory_cap_base:g} に到達：買いを停止")
    if inventory <= p.inventory_floor_base:
        ask_size = 0.0
        notes.append(f"在庫下限 {p.inventory_floor_base:g} に到達：売りを停止")

    return Quote(
        fair=fair,
        bid=bid if bid_size > 0 else None,
        ask=ask if ask_size > 0 else None,
        bid_size=bid_size,
        ask_size=ask_size,
        half_spread_bps=half,
        vol_bps=vol_bps,
        skew_bps=skew,
        notes=tuple(notes),
    )


def should_requote(prev_fair: float | None, new_fair: float, p: Params) -> bool:
    """適正価格がしきい値ぶん動いたか。動いていなければ board を触らない。

    約定のたびに無条件でキャンセル→再発注すると、列の後ろに並び直すだけで
    かえって約定しなくなる。しきい値はそのための歯止め。
    """
    if prev_fair is None:
        return True
    if not (prev_fair > 0) or not (new_fair > 0):
        return True
    move_bps = abs(new_fair - prev_fair) / prev_fair / BPS
    return move_bps >= p.requote_threshold_bps


# ---------------------------------------------------------------- 状態機械
class LastTradeMarketMaker:
    """約定テープを食べて、出すべき気配を返すだけの小さな状態機械。

    Hummingbot 側もシミュレータ側も、どちらもこのクラスを使う。
    発注・キャンセルの実行は呼び出し側の仕事。

    適正価格の取り方に注意。直近約定価格をそのまま使うと、

        直近約定価格 = 中値 ± 板の片側幅（最後に取りに来た側の符号）

    なので、**常に「直前に動いた側」へ寄った値**になる。その値を中心に
    気配を置くと、買いが入った直後は自分も高く買いに行き、売りが入った
    直後は安く売りに行く。これは追いかけているだけで、鞘にならない。

    そこで既定では、直近の買い約定と売り約定の中点を適正価格とする。
    これで板の片側幅ぶんの偏りが外れる（実測は README 参照）。
    """

    def __init__(self, params: Params) -> None:
        self.p = params
        self.vol = VolEstimator(params.vol_window)
        self.fair: float | None = None
        self.quoted_fair: float | None = None
        self.inventory = 0.0
        self.quote: Quote | None = None
        self.last_buy_price: float | None = None    # 攻め手が買い＝アスク側
        self.last_sell_price: float | None = None   # 攻め手が売り＝ビッド側

    def on_trade(self, price: float, side: str = "buy") -> bool:
        """新しい約定を1本取り込む。出し直すべきなら True。"""
        if not (price > 0):
            return False
        if side == "buy":
            self.last_buy_price = price
        else:
            self.last_sell_price = price

        self.fair = self._fair_from(price)
        self.vol.update(self.fair)
        return should_requote(self.quoted_fair, self.fair, self.p)

    def _fair_from(self, price: float) -> float:
        if not self.p.debias_last_trade:
            return price
        if self.last_buy_price is None or self.last_sell_price is None:
            return price
        mid = (self.last_buy_price + self.last_sell_price) / 2.0
        return mid if mid > 0 else price

    @property
    def market_half_spread_bps(self) -> float:
        """テープから読み取れる板の片側幅。スプレッドの下限の目安になる。"""
        if self.last_buy_price is None or self.last_sell_price is None:
            return 0.0
        mid = (self.last_buy_price + self.last_sell_price) / 2.0
        if mid <= 0:
            return 0.0
        return max(0.0, (self.last_buy_price - self.last_sell_price) / 2.0 / mid / BPS)

    def on_fill(self, side: str, amount: float) -> None:
        """自分の注文が約定したときに在庫を動かす。"""
        if side == "buy":
            self.inventory += amount
        elif side == "sell":
            self.inventory -= amount
        else:
            raise ValueError(f"side は buy/sell: {side!r}")

    def build_quote(self) -> Quote | None:
        """いまの適正価格で気配を作り、『出した』状態として覚える。"""
        if self.fair is None:
            return None
        q = make_quote(self.fair, self.inventory, self.vol.value_bps, self.p)
        self.quote = q
        self.quoted_fair = self.fair
        return q
