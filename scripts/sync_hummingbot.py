"""mm/core.py を Hummingbot 側のコピーへ同期する。

戦略の本体を2箇所で持たないための仕掛け。test_mm.py が一致を検査するので、
core.py を直したらこれを走らせること。

    python scripts/sync_hummingbot.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "mm" / "core.py"
DST = ROOT / "mm" / "hummingbot" / "last_trade_mm_core.py"


def main() -> int:
    if not SRC.exists():
        print(f"元ファイルがありません: {SRC}")
        return 1
    same = DST.exists() and DST.read_text(encoding="utf-8") == SRC.read_text(encoding="utf-8")
    if same:
        print("同期済みです（変更なし）")
        return 0
    DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SRC, DST)
    print(f"{DST.relative_to(ROOT)} を更新しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
