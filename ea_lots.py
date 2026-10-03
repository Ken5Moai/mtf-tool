"""EA のロット表を Python 側から読み、検算するための小さな道具。

ロット表の正本は ea/Step100Man/LotTable.mqh。ここで値を持ち直すと二重管理に
なるので、MQL のソースをそのまま読み取る。テストが「出荷するファイル」を
直接見ることになる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

LOT_TABLE_MQH = Path(__file__).resolve().parent / "ea" / "Step100Man" / "LotTable.mqh"

_ARRAY_RE = r"const\s+double\s+{name}\s*\[[^\]]*\]\s*=\s*\{{(.*?)\}}\s*;"


def _read_array(src: str, name: str) -> list[float]:
    m = re.search(_ARRAY_RE.format(name=name), src, re.S)
    if not m:
        raise ValueError(f"{name} が LotTable.mqh に見つかりません")
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", body)]


@dataclass(frozen=True)
class LotTable:
    jpy: tuple[float, ...]
    lot: tuple[float, ...]

    @classmethod
    def load(cls, path: Path | None = None) -> "LotTable":
        src = (path or LOT_TABLE_MQH).read_text(encoding="utf-8")
        jpy = _read_array(src, "LotTierJpy")
        lot = _read_array(src, "LotTierVol")
        if len(jpy) != len(lot):
            raise ValueError("資金とロットの段数が合っていません")
        return cls(tuple(jpy), tuple(lot))

    def __len__(self) -> int:
        return len(self.jpy)

    def lot_for(self, balance_jpy: float, extrapolate: bool = False) -> float:
        """その資金以下で一番大きい段のロット。MQL の LotForBalanceJpy と同じ規則。"""
        if balance_jpy < self.jpy[0]:
            return self.lot[0]
        for i in range(len(self.jpy) - 1, -1, -1):
            if balance_jpy >= self.jpy[i]:
                top = i == len(self.jpy) - 1
                if top and extrapolate and balance_jpy > self.jpy[i]:
                    return self.lot[i] * (balance_jpy / self.jpy[i])
                return self.lot[i]
        return self.lot[0]

    def tier_index(self, balance_jpy: float) -> int:
        for i in range(len(self.jpy) - 1, -1, -1):
            if balance_jpy >= self.jpy[i]:
                return i
        return -1


def loss_at_stop(lot: float, sl_pips: float, pip_value_per_lot: float) -> float:
    """損切りに当たったときの損失額（口座通貨）。"""
    return lot * sl_pips * pip_value_per_lot


def apply_risk_cap(
    lot: float, sl_pips: float, cap_percent: float, account_value: float,
    pip_value_per_lot: float,
) -> float:
    """資金の cap_percent% を上限にロットを抑える。0 以下なら素通し。"""
    if cap_percent <= 0 or sl_pips <= 0 or pip_value_per_lot <= 0:
        return lot
    max_loss = account_value * cap_percent / 100.0
    return min(lot, max_loss / (sl_pips * pip_value_per_lot))


def risk_percent(lot: float, sl_pips: float, pip_value_per_lot: float, account_value: float) -> float:
    """1トレードが資金の何％を賭けているか。"""
    if account_value <= 0:
        return 0.0
    return loss_at_stop(lot, sl_pips, pip_value_per_lot) / account_value * 100.0
