"""
Daily trading journal (pure logic + CLI).

Input : "今日の利益：15ドル、メモ：BTC 押し目買いで勝ち"
Output: 結論 -> 根拠 -> 次アクション の3部構成レポート。

Usage:
    python journal.py "今日の利益：15ドル、メモ：BTC押し目買い"
    python journal.py --show          # 集計だけ表示
    python journal.py --undo          # 直近1件を取り消し
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

LOG_PATH = Path(__file__).with_name("daily_log.json")

# ---- 目標設定 (円建てが正、ドルは為替レートから算出) -------------------
DEFAULT_CONFIG = {
    "monthly_goal_jpy": 100_000,   # 月間利益目標
    "seed_jpy": 50_000,            # 維持したい種銭
    "usdjpy": 155.0,               # 換算レート
    "trading_days": 30,            # 月の稼働日数
}


# ---------------------------------------------------------------- parsing
_NUM = r"[-+]?\d[\d,]*(?:\.\d+)?"


def parse_entry(text: str) -> tuple[float, str]:
    """'今日の利益：15ドル、メモ：〇〇' -> (15.0, '〇〇')"""
    s = text.replace("＋", "+").replace("－", "-").replace("　", " ")
    s = re.sub(r"マイナス\s*", "-", s)
    s = re.sub(r"プラス\s*", "+", s)

    m = re.search(rf"利益\s*[：:＝=]?\s*({_NUM})", s) or re.search(rf"({_NUM})\s*(?:ドル|USD|\$)", s, re.I)
    if not m:
        raise ValueError("利益の金額が読み取れません。例: 今日の利益：15ドル、メモ：〇〇")
    profit = float(m.group(1).replace(",", ""))

    memo = ""
    mm = re.search(r"メモ\s*[：:]\s*(.+)$", s, re.S)
    if mm:
        memo = mm.group(1).strip().rstrip("。").strip()
    return profit, memo


# ------------------------------------------------------------------ store
@dataclass
class Entry:
    date: str
    profit_usd: float
    memo: str = ""


def load(path: Path = LOG_PATH) -> dict:
    if not path.exists():
        return {"config": dict(DEFAULT_CONFIG), "entries": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(data.get("config") or {})
    return {"config": cfg, "entries": data.get("entries", [])}


def save(data: dict, path: Path = LOG_PATH) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def add_entry(data: dict, profit: float, memo: str, on: date | None = None) -> Entry:
    e = Entry((on or date.today()).isoformat(), round(profit, 2), memo)
    data["entries"].append(asdict(e))
    return e


# -------------------------------------------------------------- aggregate
def summarize(data: dict, today: date | None = None) -> dict:
    cfg = data["config"]
    today = today or date.today()
    ym = today.strftime("%Y-%m")
    rate = float(cfg["usdjpy"])

    month = [e for e in data["entries"] if str(e["date"]).startswith(ym)]
    cum = round(sum(float(e["profit_usd"]) for e in month), 2)
    days_done = len({e["date"] for e in month})
    days_left = max(cfg["trading_days"] - days_done, 0)

    goal_usd = cfg["monthly_goal_jpy"] / rate
    seed_usd = cfg["seed_jpy"] / rate
    achieved = cum / goal_usd if goal_usd else 0.0

    # 本来この日までに積み上がっているべきライン
    on_track = goal_usd * (days_done / cfg["trading_days"]) if cfg["trading_days"] else 0.0
    pace = cum / on_track if on_track > 0 else 0.0
    need_per_day = (goal_usd - cum) / days_left if days_left else 0.0

    wins = [e for e in month if float(e["profit_usd"]) > 0]
    win_rate = len(wins) / len(month) if month else 0.0

    if pace >= 1.0:
        status = "順調"
    elif pace >= 0.8:
        status = "ややビハインド"
    else:
        status = "要巻き返し"

    return {
        "month": ym,
        "today_profit": float(month[-1]["profit_usd"]) if month else 0.0,
        "cum_usd": cum,
        "cum_jpy": round(cum * rate),
        "goal_usd": round(goal_usd, 2),
        "goal_jpy": cfg["monthly_goal_jpy"],
        "achieved": achieved,
        "days_done": days_done,
        "days_left": days_left,
        "on_track_usd": round(on_track, 2),
        "gap_usd": round(cum - on_track, 2),
        "pace": pace,
        "need_per_day": round(need_per_day, 2),
        "equity_usd": round(seed_usd + cum, 2),
        "seed_usd": round(seed_usd, 2),
        "seed_ok": (seed_usd + cum) >= seed_usd,
        "win_rate": win_rate,
        "trades": len(month),
        "status": status,
        "rate": rate,
    }


# ----------------------------------------------------------------- report
def _bar(ratio: float, width: int = 20) -> str:
    n = max(0, min(width, round(ratio * width)))
    return "█" * n + "░" * (width - n)


def _praise(s: dict, today_profit: float) -> str:
    if today_profit > 0 and s["pace"] >= 1.0:
        return "ナイストレード。ペースも貯金付き、この型を明日もそのまま繰り返そう。"
    if today_profit > 0:
        return "小さくてもプラスで終えた日は前進。積み上げは裏切らない、明日も同じ手順で。"
    if today_profit == 0:
        return "見送れたのは実力。ノーポジも立派な1日、資金を減らさなかった自分を褒めよう。"
    return "損切りできた時点で及第点。1日の負けは30日の中の1マス、明日リセットして淡々といこう。"


def report(s: dict, memo: str = "") -> str:
    tp = s["today_profit"]
    sign = "+" if tp >= 0 else ""
    L: list[str] = []

    L.append("【結論】")
    L.append(f"  本日 {sign}{tp:,.2f} ドル / 累計 {s['cum_usd']:,.2f} ドル（約 {s['cum_jpy']:,} 円）")
    L.append(f"  月間達成率 {s['achieved']*100:5.1f}%  {_bar(s['achieved'])}  目標 {s['goal_usd']:,.2f} ドル")
    if memo:
        L.append(f"  メモ: {memo}")

    L.append("")
    L.append("【根拠】")
    gap = s["gap_usd"]
    gs = "貯金" if gap >= 0 else "ビハインド"
    L.append(f"  ① 進捗 {s['days_done']}/{s['days_done']+s['days_left']} 日目・想定ライン {s['on_track_usd']:,.2f} ドルに対し {abs(gap):,.2f} ドルの{gs} → {s['status']}")
    if s["days_left"]:
        L.append(f"  ② 残り {s['days_left']} 日、必要ペースは 1日あたり {s['need_per_day']:,.2f} ドル（約 {round(s['need_per_day']*s['rate']):,} 円）")
    else:
        L.append("  ② 稼働日は消化済み。今月の結果を確定して来月の設定に反映しよう")
    L.append(f"  ③ 勝率 {s['win_rate']*100:.0f}%（{s['trades']}件中）／ 口座 {s['equity_usd']:,.2f} ドル ＝ 種銭 {s['seed_usd']:,.2f} ドル "
             + ("維持OK" if s["seed_ok"] else "**割れ・要リスク縮小**"))

    L.append("")
    L.append("【次のアクション】")
    if not s["seed_ok"]:
        L.append("  ・種銭割れ。ロットを半分に落とし、MTF が完全一致した日だけエントリー")
    elif s["days_left"] and s["need_per_day"] > s["goal_usd"] / max(s["days_done"] + s["days_left"], 1) * 1.5:
        L.append("  ・必要ペースが平常の1.5倍超。狙いは増やさず、RR 2.0 以上の場面だけに絞る")
    else:
        L.append(f"  ・明日も {s['goal_usd'] / max(s['days_done'] + s['days_left'], 1):,.2f} ドルを淡々と。ツールが NO-TRADE なら休むのも仕事")
    L.append(f"  ・{_praise(s, tp)}")
    return "\n".join(L)


# -------------------------------------------------------------------- cli
def main(argv: list[str]) -> int:
    data = load()

    if "--undo" in argv:
        if not data["entries"]:
            print("記録がありません。")
            return 1
        gone = data["entries"].pop()
        save(data)
        print(f"取り消しました: {gone['date']} {gone['profit_usd']:+.2f} ドル")
        return 0

    if "--show" in argv or len(argv) < 2:
        print(report(summarize(data)))
        return 0

    try:
        profit, memo = parse_entry(" ".join(argv[1:]))
    except ValueError as e:
        print(e)
        return 1

    add_entry(data, profit, memo)
    save(data)
    print(report(summarize(data), memo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
