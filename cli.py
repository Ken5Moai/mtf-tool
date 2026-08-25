"""
コマンドラインから取引を記録する。

    python cli.py add "BTC/USD 買い +15ドル メモ：H1上昇の押し目"
    python cli.py add "今日の利益：15ドル" --date 2026-08-24
    python cli.py report            # 今日の日報
    python cli.py month             # 月次サマリー
    python cli.py list [--month YYYY-MM]
    python cli.py undo              # 直近1件を取り消し
    python cli.py rm t00007         # ID を指定して削除
    python cli.py config            # 現在の目標設定を表示
    python cli.py config usdjpy=150 monthly_goal_jpy=80000
"""
from __future__ import annotations

import sys
import unicodedata
from datetime import date

import report
import store
from stats import progress


def pad(text: str, width: int) -> str:
    """全角を2文字幅として数え、表示幅を揃える。"""
    w = sum(2 if unicodedata.east_asian_width(c) in "FWA" else 1 for c in text)
    return text + " " * max(width - w, 0)


def _month_arg(argv: list[str], default: str) -> str:
    for a in argv:
        if a.startswith("--month="):
            return a.split("=", 1)[1]
    return default


def _date_arg(argv: list[str]) -> date | None:
    for a in argv:
        if a.startswith("--date="):
            return date.fromisoformat(a.split("=", 1)[1])
    return None


def cmd_add(data: dict, argv: list[str]) -> int:
    text = " ".join(a for a in argv if not a.startswith("--"))
    if not text:
        print("記録する内容を指定してください。例: python cli.py add \"今日の利益：15ドル\"")
        return 1
    try:
        t = store.parse_line(text, on=_date_arg(argv))
    except ValueError as e:
        print(e)
        return 1
    store.add(data, t)
    store.save(data)
    print(f"記録: [{t.id}] {t.date} {t.symbol or '-'} {t.side or '-'} {t.net:+,.2f} ドル\n")
    return cmd_report(data, argv, memo=t.memo)


def cmd_report(data: dict, argv: list[str], memo: str = "") -> int:
    p = progress(data, _date_arg(argv))
    print(report.daily(p, store.month_of(data["trades"], p["month"]), memo))
    return 0


def cmd_month(data: dict, argv: list[str]) -> int:
    p = progress(data, _date_arg(argv))
    ym = _month_arg(argv, p["month"])
    print(report.monthly(p, store.month_of(data["trades"], ym)))
    return 0


def cmd_list(data: dict, argv: list[str]) -> int:
    ym = _month_arg(argv, date.today().strftime("%Y-%m"))
    rows = store.month_of(data["trades"], ym)
    if not rows:
        print(f"{ym} の記録はありません。")
        return 0
    print(pad("ID", 8) + pad("日付", 12) + pad("銘柄", 10) + pad("方向", 6) + f"{'損益($)':>9}  メモ")
    for t in rows:
        print(pad(t.id, 8) + pad(t.date, 12) + pad(t.symbol or "-", 10)
              + pad(t.side or "-", 6) + f"{t.net:>9,.2f}  {t.memo}")
    print(f"\n{len(rows)}件 / 合計 {sum(t.net for t in rows):+,.2f} ドル")
    return 0


def cmd_undo(data: dict, argv: list[str]) -> int:
    if not data["trades"]:
        print("記録がありません。")
        return 1
    t = sorted(data["trades"], key=lambda t: (t.date, t.id))[-1]
    store.remove(data, t.id)
    store.save(data)
    print(f"取り消しました: [{t.id}] {t.date} {t.net:+,.2f} ドル")
    return 0


def cmd_rm(data: dict, argv: list[str]) -> int:
    if not argv:
        print("削除する ID を指定してください。例: python cli.py rm t00007")
        return 1
    t = store.remove(data, argv[0])
    if t is None:
        print(f"ID {argv[0]} は見つかりません。")
        return 1
    store.save(data)
    print(f"削除しました: [{t.id}] {t.date} {t.net:+,.2f} ドル")
    return 0


def cmd_config(data: dict, argv: list[str]) -> int:
    changed = False
    for a in argv:
        if "=" not in a:
            continue
        k, v = a.split("=", 1)
        if k not in store.DEFAULT_CONFIG:
            print(f"未知の設定キー: {k}")
            return 1
        data["config"][k] = type(store.DEFAULT_CONFIG[k])(v)
        changed = True
    if changed:
        store.save(data)
    for k, v in data["config"].items():
        print(f"  {k:<20} {v}")
    return 0


COMMANDS = {"add": cmd_add, "report": cmd_report, "month": cmd_month,
            "list": cmd_list, "undo": cmd_undo, "rm": cmd_rm, "config": cmd_config}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    cmd = COMMANDS.get(argv[1])
    if cmd is None:
        print(f"未知のコマンド: {argv[1]}\n")
        print(__doc__.strip())
        return 1
    return cmd(store.load(), argv[2:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
