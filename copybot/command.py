"""
Natural-language task -> Config, like the "Grok Bot new task" chat in the video.

  "follow surgz and CXCinvest on fomo. copy every buy 30s after them,
   sell when they sell. take profit at 10x, lock the rest by 6:30am"

Japanese works too:
  "surgzとCXCinvestをフォロー。30秒後に全部コピー、売ったら売る。10倍で利確、6:30に全部ロック"
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Optional

from .engine import Config


@dataclass
class ParsedTask:
    config: Config
    lock_time: Optional[dt.time]
    reply: str
    warnings: list = field(default_factory=list)


_STOP = {"and", "on", "fomo", "the", "wallets", "wallet", "them", "copy", "every", "buy", "both"}


def _leaders(text: str) -> list[str]:
    m = re.search(r"follow\s+(.+?)(?:\s+on\s+\w+|[.\n]|,\s*copy|$)", text, re.I)
    if m:
        raw = re.split(r"\s*(?:,|&|\band\b|\s)\s*", m.group(1))
        return [w.lstrip("@") for w in raw if w and w.lower() not in _STOP]
    m = re.search(r"(.+?)\s*を\s*フォロー", text)
    if m:
        raw = re.split(r"\s*(?:と|、|,|&|\s)\s*", m.group(1))
        return [w.lstrip("@") for w in raw if w]
    return re.findall(r"@([A-Za-z0-9_]{2,32})", text)


def _delay(text: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds?|m|min|mins|minutes?|秒|分)\s*(?:after|later|後|遅れ)", text, re.I)
    if not m:
        return None
    v = float(m.group(1))
    return v * 60 if m.group(2).lower().startswith(("m", "分")) else v


def _tp(text: str) -> Optional[float]:
    m = re.search(r"(?:take\s*profit|tp)\s*(?:at|@)?\s*(\d+(?:\.\d+)?)\s*x", text, re.I)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|倍)\s*で?\s*(?:利確|利益確定|take)", text, re.I)
    return float(m.group(1)) if m else None


def _lock(text: str) -> Optional[dt.time]:
    m = re.search(r"(?:lock|ロック|by)[^0-9]{0,20}(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text, re.I)
    if not m:
        m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:に|まで)\s*(?:全部)?\s*ロック", text, re.I)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        return None
    return dt.time(h, mi)


def _mirror(text: str) -> bool:
    if re.search(r"(don'?t|do not|never)\s+sell when", text, re.I) or "売らない" in text:
        return False
    return True


def next_occurrence(t: dt.time, now: dt.datetime) -> dt.datetime:
    cand = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    return cand if cand > now else cand + dt.timedelta(days=1)


def parse_task(text: str, budget: float = 149.0, now: Optional[dt.datetime] = None) -> ParsedTask:
    now = now or dt.datetime.now()
    warnings = []
    leaders = _leaders(text)
    if not leaders:
        warnings.append("No traders found - write e.g. 'follow alice and bob'.")
    delay = _delay(text)
    if delay is None:
        delay = 30.0
        warnings.append("No delay given - using 30s.")
    tp = _tp(text)
    lock_t = _lock(text)
    lock_at = next_occurrence(lock_t, now).timestamp() if lock_t else None
    cfg = Config(leaders=leaders, budget=budget, delay_s=delay, mirror_exits=_mirror(text),
                 take_profit_x=tp, lock_at=lock_at)

    who = " and ".join(leaders) if leaders else "(nobody yet)"
    parts = [f"On it. Watching {who}.",
             f"Every buy copied {delay:g}s later with your ${budget:,.2f}"
             + (", same exits as them." if cfg.mirror_exits else ", exits only by TP / lock.")]
    if tp:
        parts.append(f"{tp:g}x = take profit.")
    if lock_t:
        parts.append(f"The rest locked at {lock_t.strftime('%H:%M')}.")
    return ParsedTask(cfg, lock_t, " ".join(parts), warnings)
