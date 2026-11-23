"""last_trade_mm.py — Hummingbot script strategy

直近約定価格ベースのマーケットメイク。

    適正価格 ← 直近約定（既定では直近の買い約定と売り約定の中点）
    その少し下に買い指値、少し上に売り指値
    新しい約定が出たら、しきい値を超えて動いていれば出し直す
    在庫が上限に達した側は止める
    ボラが上がったらスプレッドを広げる
    約定はすべて CSV に残す

■ 置き場所

    hummingbot/scripts/last_trade_mm.py
    hummingbot/scripts/last_trade_mm_core.py   ← 同じフォルダに必ず一緒に置く

    last_trade_mm_core.py はリポジトリの mm/core.py と同一ファイル。
    scripts/sync_hummingbot.py で同期し、test_mm.py が一致を検査している。

■ 起動

    まずペーパートレードで。
        config paper_trade_account_balance
        start --script last_trade_mm.py
"""

import os
import sys
from decimal import Decimal
from typing import Dict, List, Optional

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PriceType, TradeType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from last_trade_mm_core import (  # noqa: E402
    Fees,
    LastTradeMarketMaker,
    Params,
)

# --- 約定イベントの購読。Hummingbot のバージョン差で import 名が動くので、
#     取れなければ on_tick のポーリングに落とす（動作自体は変わらない）。
_TRADE_EVENTS_OK = True
try:
    from hummingbot.core.event.event_forwarder import SourceInfoEventForwarder
    from hummingbot.core.data_type.order_book import OrderBookEvent
except Exception:          # pragma: no cover - 環境依存
    _TRADE_EVENTS_OK = False


class LastTradeMM(ScriptStrategyBase):
    # ------------------------------------------------------------ 設定
    exchange: str = "binance_paper_trade"
    trading_pair: str = "BTC-USDT"

    order_amount = Decimal("0.001")
    base_half_spread_bps = 8.0
    max_half_spread_bps = 120.0

    vol_window = 50
    vol_widen_k = 1.0

    inventory_cap_base = 0.01
    inventory_floor_base = -0.01
    inventory_target_base = 0.0
    inventory_skew_bps_at_cap = 10.0

    requote_threshold_bps = 8.0   # 片側スプレッド以上に。下げると手数料負けする

    # 自分の口座の実際の手数料を入れること。ここが戦略の損益分岐を決める。
    maker_fee_bps = 10.0        # spot VIP0 の例。perp なら 2.0 前後
    taker_fee_bps = 10.0
    fee_buffer_bps = 1.0
    enforce_fee_floor = True    # 往復手数料を割るスプレッドでは出さない

    debias_last_trade = True    # 直近約定の偏りを中点で外す

    csv_path = "data/last_trade_mm_fills.csv"
    status_every_ticks = 20

    markets = {exchange: {trading_pair}}

    # ------------------------------------------------------------ 初期化
    def __init__(self, connectors: Dict[str, ConnectorBase]):
        super().__init__(connectors)

        self.params = Params(
            order_amount=float(self.order_amount),
            base_half_spread_bps=self.base_half_spread_bps,
            max_half_spread_bps=self.max_half_spread_bps,
            vol_window=self.vol_window,
            vol_widen_k=self.vol_widen_k,
            inventory_cap_base=self.inventory_cap_base,
            inventory_floor_base=self.inventory_floor_base,
            inventory_target_base=self.inventory_target_base,
            inventory_skew_bps_at_cap=self.inventory_skew_bps_at_cap,
            requote_threshold_bps=self.requote_threshold_bps,
            fees=Fees(maker_bps=self.maker_fee_bps, taker_bps=self.taker_fee_bps),
            fee_buffer_bps=self.fee_buffer_bps,
            enforce_fee_floor=self.enforce_fee_floor,
            debias_last_trade=self.debias_last_trade,
        )
        self.mm = LastTradeMarketMaker(self.params)

        self._ticks = 0
        self._n_buys = 0
        self._n_sells = 0
        self._fees_paid = 0.0
        self._last_polled_price: Optional[float] = None
        self._forwarder = None
        self._subscribed = False

        self._init_csv()
        self.logger().info(
            "LastTradeMM 起動: %s %s / 片側 %.1fbps / 手数料フロア %s",
            self.exchange, self.trading_pair, self.base_half_spread_bps,
            "ON" if self.enforce_fee_floor else "OFF",
        )
        floor = self.params.fees.breakeven_half_spread_bps(self.fee_buffer_bps)
        self.logger().info("往復手数料から決まる片側の下限: %.2f bps", floor)

    # ------------------------------------------------------- 約定イベント
    def _subscribe_trades(self):
        """公開約定を購読する。失敗しても致命傷にしない（ポーリングが残る）。"""
        if self._subscribed or not _TRADE_EVENTS_OK:
            return
        try:
            connector = self.connectors[self.exchange]
            book = connector.get_order_book(self.trading_pair)
            self._forwarder = SourceInfoEventForwarder(self._on_public_trade)
            book.add_listener(OrderBookEvent.TradeEvent, self._forwarder)
            self._subscribed = True
            self.logger().info("公開約定イベントを購読しました")
        except Exception as e:                       # pragma: no cover
            self.logger().warning("約定イベントを購読できないのでポーリングします: %s", e)
            self._subscribed = False

    def _on_public_trade(self, event_tag, order_book, event):
        """新しい約定が出るたびに呼ばれる。ここが戦略の心臓。"""
        try:
            price = float(event.price)
            side = "buy" if int(event.type) == int(TradeType.BUY.value) else "sell"
        except Exception:                            # pragma: no cover
            return
        if self.mm.on_trade(price, side):
            self._requote()

    # ------------------------------------------------------------ tick
    def on_tick(self):
        self._ticks += 1
        self._subscribe_trades()

        # イベントが来ない環境でも動くように、最後の約定値を見に行く
        if not self._subscribed:
            price = self._poll_last_trade_price()
            if price is not None and price != self._last_polled_price:
                self._last_polled_price = price
                side = self._infer_side(price)
                if self.mm.on_trade(price, side):
                    self._requote()

        if self._ticks % self.status_every_ticks == 0:
            q = self.mm.quote
            if q is not None:
                self.logger().info(
                    "適正 %.2f / 片側 %.2fbps (ボラ %.2fbps, スキュー %+.2fbps) / 在庫 %.6f",
                    q.fair, q.half_spread_bps, q.vol_bps, q.skew_bps, self.mm.inventory,
                )

    def _poll_last_trade_price(self) -> Optional[float]:
        try:
            p = self.connectors[self.exchange].get_price_by_type(
                self.trading_pair, PriceType.LastTrade)
            return float(p) if p and float(p) > 0 else None
        except Exception:                            # pragma: no cover
            return None

    def _infer_side(self, price: float) -> str:
        """向きが取れないときはティックルールで推定する。"""
        prev = self.mm.last_buy_price or self.mm.last_sell_price
        if prev is None:
            return "buy"
        return "buy" if price >= prev else "sell"

    # ------------------------------------------------------------ 発注
    def _requote(self):
        quote = self.mm.build_quote()
        if quote is None:
            return
        self.cancel_all_orders()

        proposal: List[OrderCandidate] = []
        if quote.bid_size > 0 and quote.bid:
            proposal.append(OrderCandidate(
                trading_pair=self.trading_pair, is_maker=True,
                order_type=OrderType.LIMIT, order_side=TradeType.BUY,
                amount=Decimal(str(quote.bid_size)), price=Decimal(str(quote.bid))))
        if quote.ask_size > 0 and quote.ask:
            proposal.append(OrderCandidate(
                trading_pair=self.trading_pair, is_maker=True,
                order_type=OrderType.LIMIT, order_side=TradeType.SELL,
                amount=Decimal(str(quote.ask_size)), price=Decimal(str(quote.ask))))
        if not proposal:
            if quote.notes:
                self.logger().info("発注なし: %s", " / ".join(quote.notes))
            return

        adjusted = self.connectors[self.exchange].budget_checker.adjust_candidates(
            proposal, all_or_none=False)
        for order in adjusted:
            if order.amount <= 0:
                continue
            if order.order_side == TradeType.BUY:
                self.buy(self.exchange, self.trading_pair, order.amount,
                         order.order_type, order.price)
            else:
                self.sell(self.exchange, self.trading_pair, order.amount,
                          order.order_type, order.price)

    def cancel_all_orders(self):
        for order in self.get_active_orders(connector_name=self.exchange):
            self.cancel(self.exchange, order.trading_pair, order.client_order_id)

    # ------------------------------------------------------------ 約定記録
    def did_fill_order(self, event: OrderFilledEvent):
        side = "buy" if event.trade_type == TradeType.BUY else "sell"
        amount = float(event.amount)
        price = float(event.price)

        self.mm.on_fill(side, amount)
        if side == "buy":
            self._n_buys += 1
        else:
            self._n_sells += 1

        fee = 0.0
        try:
            fee = float(event.trade_fee.fee_amount_in_token(
                self.trading_pair, event.price, event.amount))
        except Exception:
            fee = price * amount * self.maker_fee_bps * 1e-4

        self._fees_paid += fee
        q = self.mm.quote
        self._append_csv([
            f"{event.timestamp:.3f}", side, f"{price:.2f}", f"{amount:.8f}",
            f"{fee:.8f}",
            f"{(q.fair if q else price):.2f}",
            f"{(q.half_spread_bps if q else 0.0):.3f}",
            f"{(q.vol_bps if q else 0.0):.3f}",
            f"{(q.skew_bps if q else 0.0):.3f}",
            f"{self.mm.inventory:.8f}",
            f"{self._fees_paid:.8f}",
            str(self._n_buys), str(self._n_sells),
        ])
        self.logger().info("約定 %s %.8f @ %.2f / 在庫 %.6f",
                           side, amount, price, self.mm.inventory)
        # 片側だけ当たったので、残っている反対側も置き直す
        self._requote()

    # ------------------------------------------------------------ CSV
    def _init_csv(self):
        path = self.csv_path
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("timestamp,side,price,amount,fee,fair_price,half_spread_bps,"
                         "vol_bps,skew_bps,inventory_after,fees_cum,n_buys,n_sells\n")

    def _append_csv(self, row: List[str]):
        try:
            with open(self.csv_path, "a", encoding="utf-8") as fh:
                fh.write(",".join(row) + "\n")
        except Exception as e:                       # pragma: no cover
            self.logger().warning("CSV に書けませんでした: %s", e)

    # ------------------------------------------------------------ 表示
    def format_status(self) -> str:
        if not self.ready_to_trade:
            return "市場データの接続待ちです。"
        q = self.mm.quote
        lines = [
            f"取引所      : {self.exchange} / {self.trading_pair}",
            f"適正価格    : {q.fair:.2f}" if q else "適正価格    : —",
            f"片側        : {q.half_spread_bps:.2f} bps"
            f" (ボラ {q.vol_bps:.2f} / スキュー {q.skew_bps:+.2f})" if q else "",
            f"板の片側幅  : {self.mm.market_half_spread_bps:.2f} bps（テープ推定）",
            f"在庫        : {self.mm.inventory:+.6f}"
            f"  [{self.inventory_floor_base:+g}, {self.inventory_cap_base:+g}]",
            f"約定        : 買 {self._n_buys} / 売 {self._n_sells}",
            f"手数料累計  : {self._fees_paid:.6f}",
            f"損益分岐    : 片側 "
            f"{self.params.fees.breakeven_half_spread_bps(self.fee_buffer_bps):.2f} bps 以上必要",
            f"CSV         : {self.csv_path}",
        ]
        if q and q.notes:
            lines.append("注記        : " + " / ".join(q.notes))
        lines.append("")
        lines.extend(self.active_orders_df().to_string(index=False).split("\n")
                     if self.get_active_orders(self.exchange) else ["（板に注文なし）"])
        return "\n".join(x for x in lines if x)
