"""
配布用の 1 枚 HTML（docs/index.html）を組み立てる。

web/ledger.html は body に置く断片なので、doctype と head を付けて
単体で成立する文書にする。GitHub Pages などにそのまま置ける。

    python scripts/build_page.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "web" / "ledger.html"
OUT = ROOT / "docs" / "index.html"

HEAD_TAGS = ("<title", "<link", "<meta")


def split_head(fragment: str) -> tuple[str, str]:
    """先頭に並ぶ <title>/<link>/<meta> を head 側に、残りを body 側に分ける。"""
    head, body = [], []
    for line in fragment.splitlines():
        target = head if not body and line.startswith(HEAD_TAGS) else body
        target.append(line)
    return "\n".join(head), "\n".join(body)


def build(fragment: str) -> str:
    head, body = split_head(fragment)
    return (
        '<!doctype html>\n<html lang="ja">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"{head}\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def main() -> int:
    if not SRC.exists():
        print(f"元ファイルがありません: {SRC}")
        return 1
    page = build(SRC.read_text(encoding="utf-8"))
    if "<script" not in page or 'id="state"' not in page:
        print("組み立て結果が壊れています。web/ledger.html を確認してください。")
        return 1
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)} を書き出しました（{len(page):,} bytes）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
