"""約定テープ上で戦略を回し、損益と在庫を測るシミュレータ。

■ 約定モデルについて（ここが一番の弱点なので先に書く）

    攻め手（aggressor）の向きで判定する。

      攻め手が売り（板のビッドを叩いた）で、約定価格 p のとき
          自分のビッド >= p なら、自分のビッドが先に食われていたはず → 買い約定
      攻め手が買い（板のアスクを取った）で、約定価格 p のとき
          自分のアスク <= p なら → 売り約定

    つまり「自分がその価格で板の先頭にいた」前提。実際には同じ価格に
    先客が並んでいるので、ここで出る約定回数と利益はどちらも上振れする。
    **この数字は上限であって、期待値ではない。**
    本当の数字は Hummingbot のペーパートレードで取ること。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .core import BPS, Fees, LastTradeMarketMaker, Params, Quote
from .tape import BUY, SELL, Trade


@dataclass
class Fill:
    ts: float
    side: str            # buy / sell
    price: float
    amount: float
    fee: float           # 支払った手数料（クォート通貨）
    fair: float          # そのときの適正価格
    half_spread_bps: float
    vol_bps: float
    inventory_after: float
    cash_after: float
    equity_after: float


@dataclass
class Result:
    label: str
    fills: list[Fill] = field(default_factory=list)
    equity_curve: list[tuple[float, float]] = field(default_factory=list)
    final_price: float = 0.0
    inventory: float = 0.0
    cash: float = 0.0
    fees_paid: float = 0.0
    requotes: int = 0
    blocked_buy_ticks: int = 0
    blocked_sell_ticks: int = 0

    # ------------------------------------------------------------ 指標
    @property
    def equity(self) -> float:
        return self.cash + self.inventory * self.final_price

    @property
    def total_pnl(self) -> float:
        return self.equity

    @property
    def n_buys(self) -> int:
        return sum(1 for f in self.fills if f.side == "buy")

    @property
    def n_sells(self) -> int:
        return sum(1 for f in self.fills if f.side == "sell")

    @property
    def round_trips(self) -> int:
        return min(self.n_buys, self.n_sells)

    @property
    def volume_quote(self) -> float:
        return sum(f.price * f.amount for f in self.fills)

    @property
    def max_drawdown(self) -> float:
        peak = float("-inf")
        worst = 0.0
        for _, eq in self.equity_curve:
            peak = max(peak, eq)
            worst = min(worst, eq - peak)
        return -worst

    @property
    def max_abs_inventory(self) -> float:
        return max((abs(f.inventory_after) for f in self.fills), default=0.0)

    @property
    def mean_abs_inventory(self) -> float:
        if not self.fills:
            return 0.0
        return sum(abs(f.inventory_after) for f in self.fills) / len(self.fills)

    @property
    def pnl_before_fees(self) -> float:
        return self.total_pnl + self.fees_paid

    def summary(self) -> dict:
        return {
            "戦略": self.label,
            "損益": round(self.total_pnl, 4),
            "手数料前": round(self.pnl_before_fees, 4),
            "手数料": round(self.fees_paid, 4),
            "最大DD": round(self.max_drawdown, 4),
            "約定": len(self.fills),
            "買/売": f"{self.n_buys}/{self.n_sells}",
            "往復": self.round_trips,
            "最大在庫": round(self.max_abs_inventory, 6),
            "平均在庫": round(self.mean_abs_inventory, 6),
            "最終在庫": round(self.inventory, 6),
            "出し直し": self.requotes,
        }

    def write_csv(self, path: str | Path) -> Path:
        """依頼どおり、いつ買っていつ売ったか・スプレッド・利益・在庫を残す。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow([
                "timestamp", "side", "price", "amount", "fee",
                "fair_price", "half_spread_bps", "vol_bps",
                "inventory_after", "cash_after", "equity_after",
            ])
            for f in self.fills:
                w.writerow([
                    f"{f.ts:.3f}", f.side, f"{f.price:.2f}", f"{f.amount:.8f}",
                    f"{f.fee:.6f}", f"{f.fair:.2f}", f"{f.half_spread_bps:.3f}",
                    f"{f.vol_bps:.3f}", f"{f.inventory_after:.8f}",
                    f"{f.cash_after:.4f}", f"{f.equity_after:.4f}",
                ])
        return path


class TimedPMM:
    """比較対象：Hummingbot の Simple PMM 相当。

    違いは3つだけにしてある（依頼の戦略の『何が効いたか』を切り分けるため）。
      - 値動きではなく一定間隔で出し直す
      - スプレッドは固定（ボラで広げない）
      - 在庫の上限もスキューも無い
    適正価格は同じく直近約定価格（この環境には板が無いため）。
    """

    def __init__(self, params: Params, refresh_every: int = 60) -> None:
        self.p = params
        self.refresh_every = max(1, refresh_every)
        self.fair: float | None = None
        self.inventory = 0.0
        self.quote: Quote | None = None
        self._n = 0

    def on_trade(self, price: float, side: str = "buy") -> bool:
        if not (price > 0):
            return False
        self.fair = price
        self._n += 1
        return self.quote is None or self._n % self.refresh_every == 0

    def on_fill(self, side: str, amount: float) -> None:
        self.inventory += amount if side == "buy" else -amount

    def build_quote(self) -> Quote | None:
        if self.fair is None:
            return None
        half = self.p.base_half_spread_bps
        q = Quote(
            fair=self.fair,
            bid=self.fair * (1.0 - half * BPS),
            ask=self.fair * (1.0 + half * BPS),
            bid_size=self.p.order_amount,
            ask_size=self.p.order_amount,
            half_spread_bps=half,
            vol_bps=0.0,
            skew_bps=0.0,
        )
        self.quote = q
        return q


# ---------------------------------------------------------------- 実行
def run(
    quoter,
    tape: list[Trade],
    fees: Fees,
    label: str,
    queue_factor: float = 1.0,
    equity_every: int = 50,
) -> Result:
    """テープを1本ずつ流し、指値に当たったら約定させる。

    1イベントの順番は「テープの約定が先、こちらの出し直しが後」。
    自分の板は、出した瞬間より前の約定には当たらない。
    """
    res = Result(label=label)
    cash = 0.0
    inventory = 0.0
    bid = ask = None
    bid_left = ask_left = 0.0
    cur: Quote | None = None

    for i, t in enumerate(tape):
        # 1) いま出ている指値がテープの約定に当たるか（攻め手の向きで判定）
        if bid is not None and bid_left > 0 and t.side == SELL and t.price <= bid:
            amt = min(bid_left, t.size * queue_factor)
            if amt > 0:
                fee = bid * amt * fees.maker_bps * BPS
                cash -= bid * amt + fee
                inventory += amt
                bid_left -= amt
                quoter.on_fill("buy", amt)
                res.fees_paid += fee
                res.fills.append(Fill(
                    t.ts, "buy", bid, amt, fee, cur.fair if cur else t.price,
                    cur.half_spread_bps if cur else 0.0, cur.vol_bps if cur else 0.0,
                    inventory, cash, cash + inventory * t.price,
                ))
                if bid_left <= 1e-12:
                    bid, bid_left = None, 0.0

        if ask is not None and ask_left > 0 and t.side == BUY and t.price >= ask:
            amt = min(ask_left, t.size * queue_factor)
            if amt > 0:
                fee = ask * amt * fees.maker_bps * BPS
                cash += ask * amt - fee
                inventory -= amt
                ask_left -= amt
                quoter.on_fill("sell", amt)
                res.fees_paid += fee
                res.fills.append(Fill(
                    t.ts, "sell", ask, amt, fee, cur.fair if cur else t.price,
                    cur.half_spread_bps if cur else 0.0, cur.vol_bps if cur else 0.0,
                    inventory, cash, cash + inventory * t.price,
                ))
                if ask_left <= 1e-12:
                    ask, ask_left = None, 0.0

        # 2) 新しい約定を取り込み、必要なら出し直す
        if quoter.on_trade(t.price, t.side):
            q = quoter.build_quote()
            if q is not None:
                cur = q
                res.requotes += 1
                bid, bid_left = (q.bid, q.bid_size) if q.bid_size > 0 else (None, 0.0)
                ask, ask_left = (q.ask, q.ask_size) if q.ask_size > 0 else (None, 0.0)
                if q.bid_size <= 0:
                    res.blocked_buy_ticks += 1
                if q.ask_size <= 0:
                    res.blocked_sell_ticks += 1

        if i % equity_every == 0:
            res.equity_curve.append((t.ts, cash + inventory * t.price))

    last = tape[-1].price if tape else 0.0
    res.equity_curve.append((tape[-1].ts if tape else 0.0, cash + inventory * last))
    res.final_price = last
    res.inventory = inventory
    res.cash = cash
    return res


def run_last_trade_mm(tape: list[Trade], params: Params, label: str = "直近約定MM",
                      queue_factor: float = 1.0) -> Result:
    return run(LastTradeMarketMaker(params), tape, params.fees, label, queue_factor)


def run_simple_pmm(tape: list[Trade], params: Params, refresh_every: int = 60,
                   label: str = "Simple PMM相当", queue_factor: float = 1.0) -> Result:
    return run(TimedPMM(params, refresh_every), tape, params.fees, label, queue_factor)


def compare_table(results: list[Result]) -> str:
    """並べて見るための素朴な表。"""
    if not results:
        return "（結果なし）"
    rows = [r.summary() for r in results]
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    line = " | ".join(c.ljust(widths[c]) for c in cols)
    sep = "-+-".join("-" * widths[c] for c in cols)
    body = "\n".join(" | ".join(str(r[c]).ljust(widths[c]) for c in cols) for r in rows)
    return f"{line}\n{sep}\n{body}"
