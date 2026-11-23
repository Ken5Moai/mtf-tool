"""約定テープ（timestamp, price, size, 攻め手の向き）。

取引所 API はこの環境から見えないので、検証用の合成テープを作れるように
してある。手元で落とした実データの CSV も同じ形で読める。

■ テープの作り方で気をつけたこと

    最初は「約定価格そのものをランダムウォークさせる」テープを作ったが、
    それだと **ビッド・アスク間の往復が存在しない**。マーケットメイクが
    利益を得る源泉はまさにその往復なので、どんな設定でも必ず負ける
    （＝モデルの性質であって、戦略の性質ではない）テープになってしまう。

    そこで、見えない中値（mid）をランダムウォークさせ、約定はその上下
    market_half_spread_bps のところで、買い手か売り手のどちらかが
    取りに来た結果として起きる、という形にしてある。
"""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass
from pathlib import Path

BUY = "buy"    # 攻め手が買い＝板のアスクを取っていった
SELL = "sell"  # 攻め手が売り＝板のビッドを叩いた


@dataclass(frozen=True)
class Trade:
    ts: float
    price: float
    size: float
    side: str = BUY     # 攻め手（aggressor）の向き
    mid: float = 0.0    # 合成テープのときだけ入る、答え合わせ用の中値


def synthetic_tape(
    n: int = 20_000,
    start: float = 100_000.0,
    vol_bps: float = 1.0,              # 中値の1約定あたり標準偏差
    drift_bps: float = 0.0,            # 中値のドリフト（トレンド）
    market_half_spread_bps: float = 1.0,  # 板の気配幅（片側）
    buy_ratio: float = 0.5,            # 攻め手が買いになる割合
    jump_prob: float = 0.0,
    jump_bps: float = 50.0,
    size_mean: float = 0.01,
    dt: float = 0.25,
    seed: int = 0,
) -> list[Trade]:
    """見えない中値＋その上下で起きる約定、という形の素朴なテープ。

    板の厚みも待ち行列も再現していないので、ここで出た損益は参考値でしかない。
    目的は『ロジックが意図どおり動くか』の確認であって、収益予測ではない。
    """
    rng = random.Random(seed)
    mid = start
    ts = 0.0
    out: list[Trade] = []
    half = market_half_spread_bps * 1e-4
    for _ in range(n):
        shock = rng.gauss(drift_bps, vol_bps) * 1e-4
        if jump_prob > 0 and rng.random() < jump_prob:
            shock += rng.choice((-1.0, 1.0)) * jump_bps * 1e-4
        mid *= math.exp(shock)
        ts += rng.expovariate(1.0 / dt) if dt > 0 else 1.0
        size = max(1e-6, rng.expovariate(1.0 / size_mean))
        if rng.random() < buy_ratio:
            out.append(Trade(ts, mid * (1.0 + half), size, BUY, mid))
        else:
            out.append(Trade(ts, mid * (1.0 - half), size, SELL, mid))
    return out


def ranging_tape(n: int = 20_000, seed: int = 0, **kw) -> list[Trade]:
    """レンジ。マーケットメイクが一番効くはずの地合い。"""
    return synthetic_tape(n=n, vol_bps=1.0, drift_bps=0.0, seed=seed, **kw)


def trending_tape(n: int = 20_000, seed: int = 1, **kw) -> list[Trade]:
    """片方向。買いばかり約定して在庫が偏る、MM が一番苦手な地合い。"""
    kw.setdefault("buy_ratio", 0.58)
    return synthetic_tape(n=n, vol_bps=1.0, drift_bps=0.35, seed=seed, **kw)


def volatile_tape(n: int = 20_000, seed: int = 2, **kw) -> list[Trade]:
    """急変あり。スプレッドを広げる仕組みが効くかどうかを見る。"""
    return synthetic_tape(n=n, vol_bps=3.0, jump_prob=0.002, jump_bps=80.0,
                          market_half_spread_bps=2.0, seed=seed, **kw)


def load_csv(
    path: str | Path,
    ts_col: str = "timestamp",
    price_col: str = "price",
    size_col: str = "size",
    side_col: str | None = None,
    buyer_maker_col: str | None = None,
) -> list[Trade]:
    """手元で落とした約定履歴を読む。

    Binance の aggTrades CSV なら:
        ts_col="T", price_col="p", size_col="q", buyer_maker_col="m"
    m（isBuyerMaker）が真なら買い手が指値＝攻め手は売り。
    攻め手の向きが無い場合は、前回価格との比較で推定する。
    """
    out: list[Trade] = []
    prev = 0.0
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                price = float(row[price_col])
                size = float(row[size_col])
                ts = float(row[ts_col])
            except (KeyError, TypeError, ValueError):
                continue
            if not (price > 0 and size > 0):
                continue

            if buyer_maker_col and buyer_maker_col in row:
                raw = str(row[buyer_maker_col]).strip().lower()
                side = SELL if raw in ("true", "1", "t", "yes") else BUY
            elif side_col and side_col in row:
                side = BUY if str(row[side_col]).strip().lower().startswith("b") else SELL
            else:
                side = BUY if price >= prev else SELL     # ティックルールで推定
            out.append(Trade(ts, price, size, side))
            prev = price
    if not out:
        raise ValueError(f"{path} から約定を1件も読めませんでした（列名を確認）")
    return out


def describe(tape: list[Trade]) -> str:
    if not tape:
        return "（空）"
    prices = [t.price for t in tape]
    span = tape[-1].ts - tape[0].ts
    move = (prices[-1] / prices[0] - 1.0) * 100.0
    buys = sum(1 for t in tape if t.side == BUY)
    return (
        f"{len(tape):,}件 / {span/3600:.1f}時間 / "
        f"始値 {prices[0]:,.2f} → 終値 {prices[-1]:,.2f} ({move:+.2f}%) / "
        f"高安 {min(prices):,.2f}-{max(prices):,.2f} / 攻め手の買い比率 {buys/len(tape):.1%}"
    )
