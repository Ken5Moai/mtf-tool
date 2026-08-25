"""
取引記録のデータモデルと永続化。

1 レコード = 1 トレード（決済済み）。損益はドル建てで保持し、
円換算は config の usdjpy レートで行う。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

DATA_PATH = Path(__file__).with_name("trades.json")

BUY, SELL = "BUY", "SELL"

DEFAULT_CONFIG: dict[str, Any] = {
    "monthly_goal_jpy": 100_000,   # 月間利益目標（円）
    "seed_jpy": 50_000,            # 維持したい種銭（円）
    "usdjpy": 155.0,               # 円換算レート
    "trading_days": 30,            # 月の稼働日数
    "risk_per_trade_pct": 2.0,     # 1トレードの許容リスク（口座に対する%）
    "max_drawdown_pct": 20.0,      # 種銭に対して許容する最大ドローダウン（%）
}


@dataclass
class Trade:
    """決済済みの 1 トレード。pnl 以外はすべて任意。"""

    date: str                                   # 決済日 YYYY-MM-DD
    pnl: float                                  # 損益（ドル、手数料を引く前）
    symbol: str = ""                            # BTC/USD, USD/JPY ...
    side: str = ""                              # BUY / SELL
    size: float | None = None                   # ロット / 数量
    entry: float | None = None
    exit: float | None = None
    sl: float | None = None
    tp: float | None = None
    fee: float = 0.0                            # 手数料・スワップ（ドル）
    memo: str = ""
    tags: list[str] = field(default_factory=list)
    id: str = ""

    # ---------------------------------------------------------- derived
    @property
    def net(self) -> float:
        """手数料控除後の損益。"""
        return round(self.pnl - self.fee, 2)

    @property
    def result(self) -> str:
        n = self.net
        return "win" if n > 0 else "loss" if n < 0 else "be"

    @property
    def risk_per_unit(self) -> float | None:
        """1単位あたりの想定リスク幅（entry と sl が揃っている場合のみ）。"""
        if self.entry is None or self.sl is None:
            return None
        d = abs(self.entry - self.sl)
        return d if d > 0 else None

    @property
    def planned_rr(self) -> float | None:
        """エントリー時に狙っていたリスクリワード比。"""
        r = self.risk_per_unit
        if r is None or self.tp is None or self.entry is None:
            return None
        return round(abs(self.tp - self.entry) / r, 2)

    @property
    def r_multiple(self) -> float | None:
        """結果を R 倍で表した値（+2.0 なら想定リスクの2倍を取れた）。"""
        r = self.risk_per_unit
        if r is None or self.exit is None or self.entry is None or not self.side:
            return None
        move = (self.exit - self.entry) if self.side == BUY else (self.entry - self.exit)
        return round(move / r, 2)

    # ------------------------------------------------------------- (de)serialize
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Trade":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


# ------------------------------------------------------------------ store
_ID_RE = re.compile(r"t(\d+)$")


def _new_id(existing: Iterable[str]) -> str:
    used = [int(m.group(1)) for m in (_ID_RE.fullmatch(x or "") for x in existing) if m]
    return "t{:05d}".format(max(used, default=0) + 1)


def load(path: Path | None = None) -> dict:
    """{'config': {...}, 'trades': [Trade, ...]} を返す。ファイルが無ければ空で作る。"""
    path = path or DATA_PATH          # 既定値は呼び出し時に解決する（差し替え可能にするため）
    cfg = dict(DEFAULT_CONFIG)
    trades: list[Trade] = []
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        cfg.update(raw.get("config") or {})
        trades = [Trade.from_dict(t) for t in raw.get("trades", [])]
    return {"config": cfg, "trades": trades}


def save(data: dict, path: Path | None = None) -> None:
    path = path or DATA_PATH
    payload = {
        "config": data["config"],
        "trades": [t.to_dict() for t in sorted(data["trades"], key=lambda t: (t.date, t.id))],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def add(data: dict, trade: Trade) -> Trade:
    if not trade.id:
        trade.id = _new_id(t.id for t in data["trades"])
    data["trades"].append(trade)
    return trade


def remove(data: dict, trade_id: str) -> Trade | None:
    for i, t in enumerate(data["trades"]):
        if t.id == trade_id:
            return data["trades"].pop(i)
    return None


def month_of(trades: list[Trade], ym: str) -> list[Trade]:
    return [t for t in trades if t.date.startswith(ym)]


def day_of(trades: list[Trade], day: str) -> list[Trade]:
    return [t for t in trades if t.date == day]


# ----------------------------------------------------------------- parsing
_NUM = r"[-+]?\d[\d,]*(?:\.\d+)?"
_SIDE = {
    "買": BUY, "買い": BUY, "ロング": BUY, "buy": BUY, "long": BUY,
    "売": SELL, "売り": SELL, "ショート": SELL, "sell": SELL, "short": SELL,
}

# ラベル一覧。数値ラベルは「直後に数字が来ること」を条件にして、
# メモ本文中の「利確が早すぎた」のような語を誤ってラベルと解釈しないようにする。
_AHEAD = r"\s*[：:＝=]?\s*(?=[-+]?\d)"
_LABELS = [
    ("memo", r"メモ\s*[：:]"),
    ("tags", r"(?:タグ|tags?)\s*[：:]"),
    ("fee", r"(?:手数料|コスト|(?<![A-Za-z])fee)" + _AHEAD),
    ("size", r"(?:ロット|数量|(?<![A-Za-z])lot|(?<![A-Za-z])size)" + _AHEAD),
    ("entry", r"(?:エントリー|建値|(?<![A-Za-z])entry)" + _AHEAD),
    ("exit", r"(?:決済|クローズ|(?<![A-Za-z])exit)" + _AHEAD),
    ("sl", r"(?:損切り?|ストップ|(?<![A-Za-z])sl)" + _AHEAD),
    ("tp", r"(?:利確|ターゲット|(?<![A-Za-z])tp)" + _AHEAD),
    ("pnl", r"(?:利益|損益|結果|(?<![A-Za-z])pnl)" + _AHEAD),
]
_LABEL_RE = re.compile("|".join("(?P<%s>%s)" % kv for kv in _LABELS), re.I)


def _split_labels(s: str) -> tuple[str, dict[str, str]]:
    """先頭の自由記述部と、{ラベル名: そのラベルが持つ文字列} に分解する。"""
    marks = list(_LABEL_RE.finditer(s))
    if not marks:
        return s, {}
    seg: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(s)
        seg.setdefault(m.lastgroup, s[m.end():end].strip())
    return s[: marks[0].start()], seg


def _num_and_rest(text: str | None) -> tuple[float | None, str]:
    """ラベル直後の数値と、その後ろに残った文字列を返す。"""
    if not text:
        return None, ""
    m = re.match(rf"\s*({_NUM})", text)
    if not m:
        return None, text
    return float(m.group(1).replace(",", "")), text[m.end():]


def _first_num(text: str | None) -> float | None:
    return _num_and_rest(text)[0]


def parse_line(text: str, on: date | None = None) -> Trade:
    """
    自由入力を Trade に変換する。

        "今日の利益：15ドル、メモ：押し目買い"
        "BTC/USD 買い +15ドル メモ：H1上昇の押し目"
        "USD/JPY 売り -8ドル 手数料0.4ドル タグ：早仕掛け 反省"
        "BTC/USD 買い エントリー64000 決済64500 SL63800 利益：25ドル"
    """
    s = text.replace("＋", "+").replace("－", "-").replace("　", " ").strip()
    s = re.sub(r"マイナス\s*", "-", s)
    s = re.sub(r"プラス\s*", "+", s)
    if not s:
        raise ValueError("入力が空です。例: 今日の利益：15ドル、メモ：〇〇")

    free, seg = _split_labels(s)

    nums: dict[str, float | None] = {}
    residual = [free]
    for key in ("fee", "size", "entry", "exit", "sl", "tp", "pnl"):
        nums[key], rest = _num_and_rest(seg.get(key))
        residual.append(rest)

    pnl = nums["pnl"]
    if pnl is None:
        # 「+25ドル」のように単独で置かれた金額を、ラベルが消費し残した部分から拾う
        m = re.search(rf"({_NUM})\s*(?:ドル|usd|\$)", " ".join(residual), re.I)
        if not m:
            raise ValueError("損益の金額が読み取れません。例: 今日の利益：15ドル、メモ：〇〇")
        pnl = float(m.group(1).replace(",", ""))

    m = re.search(r"([A-Za-z]{3,5}\s*/\s*[A-Za-z]{3,5})", free)
    symbol = re.sub(r"\s+", "", m.group(1)).upper() if m else ""

    side = ""
    for word, val in _SIDE.items():
        if re.search(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", free, re.I):
            side = val
            break

    memo = (seg.get("memo") or "").strip().rstrip("、,。").strip()
    tags = [x for x in re.split(r"[\s/、,]+", seg.get("tags") or "") if x]

    return Trade(
        date=(on or date.today()).isoformat(),
        pnl=round(pnl, 2), symbol=symbol, side=side,
        size=nums["size"], entry=nums["entry"], exit=nums["exit"],
        sl=nums["sl"], tp=nums["tp"], fee=round(nums["fee"] or 0.0, 2),
        memo=memo, tags=tags,
    )


def today_str() -> str:
    return datetime.now().date().isoformat()
