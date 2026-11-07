"""
Copy-trading engine (pure logic, no I/O).

Reproduces the "Grok Bot" mechanic from the fomo screen recording:
  - watch N leader wallets
  - copy every leader BUY `delay_s` seconds later with a slice of the bankroll
  - mirror leader SELLs (same delay, same fraction)
  - take profit when a position reaches `take_profit_x` times cost
  - at `lock_at`, sell everything that is still open ("lock the rest")

Prices come from a callable price_fn(token, t) -> float | None, so the same
engine runs on the simulator (copybot.sim) and on live paper data (copybot.live).
This engine never touches real money.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

PriceFn = Callable[[str, float], Optional[float]]


@dataclass
class Config:
    leaders: list[str]
    budget: float = 149.0
    delay_s: float = 30.0
    mirror_exits: bool = True
    take_profit_x: Optional[float] = 10.0
    lock_at: Optional[float] = None          # epoch seconds; None = never
    size_frac: Optional[float] = None        # of current equity per copy; None = 1/len(leaders)
    fee_bps: float = 100.0                   # per side (platform fee)
    slippage_bps: float = 150.0              # per side (memecoin slippage)
    min_order: float = 1.0
    # --- risk controls (all off by default = behaviour shown in the video) ---
    stop_loss_x: Optional[float] = None      # sell all if value <= cost * x (e.g. 0.6 = -40%)
    take_initial_x: Optional[float] = None   # at this multiple, sell enough to get the stake back
    max_chase_pct: Optional[float] = None    # skip copy if price already ran > N% since leader's buy
    max_hold_s: Optional[float] = None       # sell if held longer than this

    def slice_frac(self) -> float:
        if self.size_frac:
            return self.size_frac
        return 1.0 / max(1, len(self.leaders))


@dataclass
class LeaderTrade:
    t: float
    leader: str
    side: str            # "buy" | "sell"
    token: str
    frac: float = 1.0    # for sells: fraction of leader's position sold
    usd: Optional[float] = None


@dataclass
class Position:
    token: str
    leader: str
    qty: float
    cost: float          # USD spent incl. fees
    opened: float
    initial_out: bool = False

    def value(self, px: float) -> float:
        return self.qty * px


@dataclass
class _Pending:
    due: float
    trade: LeaderTrade


@dataclass
class CopyEngine:
    cfg: Config
    price_fn: PriceFn
    cash: float = field(init=False)
    positions: dict = field(init=False, default_factory=dict)   # (leader, token) -> Position
    pending: list = field(init=False, default_factory=list)
    activity: list = field(init=False, default_factory=list)
    equity: list = field(init=False, default_factory=list)      # (t, equity)
    realized: float = field(init=False, default=0.0)
    locked: bool = field(init=False, default=False)
    last_px: dict = field(init=False, default_factory=dict)

    def __post_init__(self):
        self.cash = float(self.cfg.budget)

    # ---------- inputs ----------
    def on_leader_trade(self, tr: LeaderTrade) -> None:
        if self.locked or tr.leader not in self.cfg.leaders:
            return
        if tr.side == "sell" and not self.cfg.mirror_exits:
            return
        self._log(tr.t, "watch", f"{tr.leader} {'bought' if tr.side == 'buy' else 'sold'} {tr.token}",
                  leader=tr.leader, token=tr.token)
        self.pending.append(_Pending(tr.t + self.cfg.delay_s, tr))

    def step(self, now: float) -> None:
        due = [p for p in self.pending if p.due <= now]
        self.pending = [p for p in self.pending if p.due > now]
        for p in sorted(due, key=lambda p: p.due):
            if p.trade.side == "buy":
                self._copy_buy(p.due, p.trade)
            else:
                self._copy_sell(p.due, p.trade)
        self._check_tp(now)
        if self.cfg.lock_at is not None and now >= self.cfg.lock_at and not self.locked:
            self._lock(now)
        self.equity.append((now, self.mark(now)))

    # ---------- helpers ----------
    def _px(self, token: str, t: float) -> Optional[float]:
        px = self.price_fn(token, t)
        if px is not None and px > 0:
            self.last_px[token] = px
            return px
        return self.last_px.get(token)

    def mark(self, t: float) -> float:
        v = self.cash
        for pos in self.positions.values():
            px = self._px(pos.token, t)
            if px:
                v += pos.value(px)
        return v

    def _side_cost(self) -> float:
        return (self.cfg.fee_bps + self.cfg.slippage_bps) / 10_000

    def _copy_buy(self, t: float, tr: LeaderTrade) -> None:
        if self.locked:
            return
        px = self._px(tr.token, t)
        if not px:
            self._log(t, "skip", f"no price for {tr.token}", token=tr.token)
            return
        usd = min(self.cash, self.mark(t) * self.cfg.slice_frac())
        if usd < self.cfg.min_order:
            self._log(t, "skip", f"not enough cash to copy {tr.token}", token=tr.token)
            return
        if self.cfg.max_chase_pct is not None:
            ref = self.price_fn(tr.token, tr.t)
            if ref and px > ref * (1 + self.cfg.max_chase_pct / 100):
                self._log(t, "skip", f"skipped {tr.token}: already +{(px / ref - 1) * 100:.0f}% since {tr.leader} bought",
                          token=tr.token, leader=tr.leader)
                return
        fill = px * (1 + self._side_cost())
        qty = usd / fill
        key = (tr.leader, tr.token)
        if key in self.positions:
            p = self.positions[key]
            p.qty += qty
            p.cost += usd
        else:
            self.positions[key] = Position(tr.token, tr.leader, qty, usd, t)
        self.cash -= usd
        self._log(t, "buy", f"Bought {tr.token}", usd=-usd, px=px, leader=tr.leader,
                  token=tr.token, note=f"copied {tr.leader} {int(self.cfg.delay_s)}s later")

    def _close(self, t: float, key, frac: float, kind: str, note: str) -> None:
        pos = self.positions.get(key)
        if pos is None:
            return
        px = self._px(pos.token, t)
        if not px:
            return
        frac = max(0.0, min(1.0, frac))
        qty = pos.qty * frac
        cost = pos.cost * frac
        proceeds = qty * px * (1 - self._side_cost())
        pnl = proceeds - cost
        self.cash += proceeds
        self.realized += pnl
        pos.qty -= qty
        pos.cost -= cost
        if pos.qty <= 1e-12 or frac >= 0.999:
            del self.positions[key]
        pct = pnl / cost * 100 if cost else 0.0
        self._log(t, kind, f"Sold {pos.token}", usd=proceeds, pnl=pnl, pct=pct, px=px,
                  leader=pos.leader, token=pos.token, note=note)

    def _copy_sell(self, t: float, tr: LeaderTrade) -> None:
        key = (tr.leader, tr.token)
        if key not in self.positions:
            return
        self._close(t, key, tr.frac, "sell", f"{tr.leader} sold - same exit")

    def _check_tp(self, t: float) -> None:
        c = self.cfg
        for key, pos in list(self.positions.items()):
            px = self._px(pos.token, t)
            if not px or pos.cost <= 0:
                continue
            m = pos.value(px) / pos.cost
            if c.take_profit_x and m >= c.take_profit_x:
                self._close(t, key, 1.0, "tp", f"take profit {c.take_profit_x:g}x")
            elif c.stop_loss_x and m <= c.stop_loss_x:
                self._close(t, key, 1.0, "stop", f"stop loss {(c.stop_loss_x - 1) * 100:.0f}%")
            elif c.max_hold_s and t - pos.opened >= c.max_hold_s:
                self._close(t, key, 1.0, "sell", f"max hold {c.max_hold_s / 60:.0f}min")
            elif c.take_initial_x and not pos.initial_out and m >= c.take_initial_x:
                pos.initial_out = True
                frac = 1.0 / (m * (1 - self._side_cost()))     # sell enough to recover the stake
                self._close(t, key, min(1.0, frac), "tp", f"took initial out at {c.take_initial_x:g}x")

    def _lock(self, t: float) -> None:
        for key in list(self.positions):
            self._close(t, key, 1.0, "lock", "locked at deadline")
        self.pending.clear()
        self.locked = True
        self._log(t, "lock", f"Locked. balance ${self.cash:,.2f}", usd=self.cash)

    def _log(self, t: float, kind: str, text: str, **kw) -> None:
        self.activity.append({"t": t, "kind": kind, "text": text, **kw})

    # ---------- summary ----------
    def stats(self) -> dict:
        start = self.cfg.budget
        end = self.equity[-1][1] if self.equity else self.cash
        sells = [a for a in self.activity if a["kind"] in ("sell", "tp", "stop", "lock") and "pnl" in a]
        wins = [a for a in sells if a["pnl"] > 0]
        return {
            "start": start,
            "end": end,
            "pnl": end - start,
            "pct": (end - start) / start * 100 if start else 0.0,
            "multiple": end / start if start else 0.0,
            "trades": sum(1 for a in self.activity if a["kind"] == "buy"),
            "exits": len(sells),
            "win_rate": len(wins) / len(sells) * 100 if sells else 0.0,
            "open": len(self.positions),
        }


def run(cfg: Config, price_fn: PriceFn, trades: list[LeaderTrade],
        t_start: float, t_end: float, step_s: float = 5.0) -> CopyEngine:
    """Replay a list of leader trades through the engine on a fixed clock."""
    eng = CopyEngine(cfg, price_fn)
    trades = sorted(trades, key=lambda x: x.t)
    i = 0
    t = t_start
    while t <= t_end:
        while i < len(trades) and trades[i].t <= t:
            eng.on_leader_trade(trades[i])
            i += 1
        eng.step(t)
        if eng.locked:
            break
        t += step_s
    return eng
