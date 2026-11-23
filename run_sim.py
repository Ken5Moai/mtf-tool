"""マーケットメイク戦略のシミュレータ CLI。

    python run_sim.py compare                   # 3つの地合いで比較
    python run_sim.py compare --maker-bps 2     # perp 想定の手数料で
    python run_sim.py sweep                     # どこなら成立するかの表
    python run_sim.py csv trades.csv --price-col p --size-col q --ts-col T --maker-col m

出てくる数字は約定モデルの前提ぶん上振れしている。mm/sim.py の冒頭を読むこと。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mm.core import Fees, Params
from mm.sim import compare_table, run_last_trade_mm, run_simple_pmm
from mm.tape import describe, load_csv, ranging_tape, synthetic_tape, trending_tape, volatile_tape

NOTIONAL_HINT = 100_000.0


def _params(a) -> Params:
    return Params(
        order_amount=a.amount,
        base_half_spread_bps=a.half_spread,
        max_half_spread_bps=a.max_half_spread,
        vol_widen_k=a.vol_k,
        inventory_cap_base=a.inv_cap,
        inventory_floor_base=-a.inv_cap,
        requote_threshold_bps=a.requote,
        fees=Fees(maker_bps=a.maker_bps, taker_bps=a.maker_bps),
        enforce_fee_floor=not a.no_fee_floor,
        debias_last_trade=not a.no_debias,
    )


def _report(name, tape, p, outdir: Path | None):
    print(f"\n### {name}")
    print(describe(tape))
    a = run_last_trade_mm(tape, p, label="直近約定MM")
    b = run_simple_pmm(tape, p, refresh_every=60, label="Simple PMM相当")
    print(compare_table([a, b]))
    if a.round_trips:
        gross = a.pnl_before_fees / a.round_trips / (p.order_amount * NOTIONAL_HINT) * 1e4
        print(f"1往復あたりの粗利 {gross:+.2f} bps / 往復手数料 "
              f"{p.fees.round_trip_maker_bps:.1f} bps → "
              f"{'成立' if gross > p.fees.round_trip_maker_bps else '不成立'}")
    if outdir:
        outdir.mkdir(parents=True, exist_ok=True)
        path = a.write_csv(outdir / f"fills_{name}.csv")
        print(f"約定ログ: {path}")
    return a, b


def cmd_compare(a) -> int:
    p = _params(a)
    print(f"手数料 maker {a.maker_bps}bps（往復 {p.fees.round_trip_maker_bps}bps）/ "
          f"片側 {a.half_spread}bps / 1回 {a.amount} BTC")
    out = Path(a.out) if a.out else None
    for name, tape in (("レンジ", ranging_tape(a.n)),
                       ("トレンド", trending_tape(a.n)),
                       ("急変あり", volatile_tape(a.n))):
        _report(name, tape, p, out)
    return 0


def cmd_sweep(a) -> int:
    """板の片側幅 S と1約定あたりのボラ V で、粗利がどう動くか。"""
    notional = a.amount * NOTIONAL_HINT
    vols = [0.1, 0.25, 0.5, 1.0, 2.0]
    spreads = [0.5, 1.0, 2.0, 4.0, 8.0]
    print("1往復あたりの粗利(bps)。手数料は含まない。")
    print("自分の片側スプレッド＝板の片側幅 S（＝最前列に置いた場合）\n")
    print("        " + "".join(f"{('V=' + str(v)):>11}" for v in vols))
    for S in spreads:
        row = f"S={S:<5}"
        for V in vols:
            tape = synthetic_tape(a.n, vol_bps=V, market_half_spread_bps=S, seed=7)
            p = Params(fees=Fees(maker_bps=0.0), base_half_spread_bps=S,
                       order_amount=a.amount, requote_threshold_bps=max(0.1, V / 2),
                       vol_widen_k=0.0, enforce_fee_floor=False,
                       inventory_cap_base=1e9, inventory_floor_base=-1e9)
            r = run_last_trade_mm(tape, p)
            g = (r.pnl_before_fees / r.round_trips / notional * 1e4) if r.round_trips else float("nan")
            row += f"{g:11.2f}"
        print(row)
    print("\n目安: 粗利 ≒ 2S − 3.8V。これが往復手数料を上回る場所だけが候補。")
    print(f"往復 maker 手数料: spot VIP0 ≒ 20bps / perp VIP0 ≒ 4bps（自分の口座で確認すること）")
    return 0


def cmd_csv(a) -> int:
    tape = load_csv(a.path, ts_col=a.ts_col, price_col=a.price_col,
                    size_col=a.size_col, buyer_maker_col=a.maker_col)
    p = _params(a)
    _report(Path(a.path).stem, tape, p, Path(a.out) if a.out else None)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--amount", type=float, default=0.001, help="1回の発注量")
    ap.add_argument("--half-spread", type=float, default=8.0, help="平常時の片側 bps")
    ap.add_argument("--max-half-spread", type=float, default=120.0)
    ap.add_argument("--vol-k", type=float, default=1.0, help="片側 += k × ボラ")
    ap.add_argument("--inv-cap", type=float, default=0.01, help="在庫の上限（±）")
    ap.add_argument("--requote", type=float, default=2.0, help="出し直すしきい値 bps")
    ap.add_argument("--maker-bps", type=float, default=10.0, help="片道 maker 手数料")
    ap.add_argument("--no-fee-floor", action="store_true", help="手数料フロアを外す")
    ap.add_argument("--no-debias", action="store_true", help="直近約定をそのまま使う")
    ap.add_argument("-n", type=int, default=20000, help="合成テープの約定数")
    ap.add_argument("--out", help="約定CSVの出力先ディレクトリ")

    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("compare", help="3つの地合いで Simple PMM と比べる").set_defaults(fn=cmd_compare)
    sub.add_parser("sweep", help="どこなら成立するかの表").set_defaults(fn=cmd_sweep)

    c = sub.add_parser("csv", help="手元の約定CSVで回す")
    c.add_argument("path")
    c.add_argument("--ts-col", default="timestamp")
    c.add_argument("--price-col", default="price")
    c.add_argument("--size-col", default="size")
    c.add_argument("--maker-col", default=None, help="isBuyerMaker の列名（Binance なら m）")
    c.set_defaults(fn=cmd_csv)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
