"""
Synthetic memecoin night for the copy bot (offline, deterministic by seed).

Model (deliberately simple, but it keeps the two things that decide the
outcome of a delayed copy trade):
  * most new tokens bleed out or rug; a few run 5-50x
  * a leader's buy pushes the price up before the copier fills (copy delay
    cost), and a leader's sell pushes it down before the copier exits
Leaders have an "edge": the probability that a token they pick is a runner.

monte_carlo() replays many nights so you can see the real distribution
behind a single "$149 -> $1,947" screenshot.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np

from .engine import Config, LeaderTrade, run

STEP = 5  # seconds per price bar

NAMES = ["CHAD", "FATCOIN", "Anakin", "DARKSIDE", "FLOAT", "CPU", "PNUT", "WIF2", "BONKER", "MOODENG2",
         "GIGA", "POPCAT2", "SIGMA", "RIZZ", "NEIRO2", "TURBO2", "MEW2", "BRETT2", "LUCE2", "GOAT2",
         "FWOG", "MICHI", "SCF", "PONKE", "MANEKI", "RETARDIO", "SELFIE", "HARAMBE", "ZEREBRO", "AI16Z2"]


@dataclass
class Night:
    t0: float
    t1: float
    paths: dict                      # token -> np.ndarray of prices (STEP spacing from t0)
    trades: list = field(default_factory=list)
    leaders: list = field(default_factory=list)
    mcap_supply: float = 1e9

    def price(self, token: str, t: float):
        p = self.paths.get(token)
        if p is None:
            return None
        i = int((t - self.t0) // STEP)
        if i < 0:
            return None
        return float(p[min(i, len(p) - 1)])


def _ramp(path: np.ndarray, i: int, steps: int, k: float) -> None:
    """Multiply path from bar i onward by k, ramping linearly over `steps` bars."""
    n = len(path)
    for j in range(steps):
        if i + j < n:
            path[i + j] *= 1 + (k - 1) * (j + 1) / steps
    if i + steps < n:
        path[i + steps:] *= k


def make_night(seed: int, leaders=("surgz", "CXCinvest"), edge: float = 0.30,
               start: dt.datetime | None = None, hours: float = 10.6,
               picks_per_leader: int = 6, copy_lag_s: float = 30.0) -> Night:
    rng = np.random.default_rng(seed)
    start = start or dt.datetime(2026, 11, 1, 19, 55)
    t0 = start.timestamp()
    n = int(hours * 3600 // STEP)
    t1 = t0 + n * STEP
    lag = max(1, int(copy_lag_s // STEP))
    paths, trades = {}, []
    names = list(rng.permutation(NAMES))
    k = 0
    for leader in leaders:
        for _ in range(picks_per_leader):
            if k >= len(names):
                break
            tok = names[k]; k += 1
            launch = int(rng.uniform(0, n * 0.75))
            runner = rng.random() < edge
            p0 = rng.uniform(2e-5, 2e-4)                       # ~$20k-200k mcap at 1e9 supply
            sigma = 0.02
            drift = rng.uniform(0.0006, 0.0025) if runner else rng.uniform(-0.0012, 0.0)
            peak_bar = launch + int(rng.uniform(200, 2500))     # when a runner tops out
            steps = rng.normal(drift, sigma, n)
            steps[:launch] = 0
            after = np.arange(n) > peak_bar
            steps[after] = rng.normal(-0.0015, sigma, after.sum())
            if not runner and rng.random() < 0.55:              # rug pull
                rug = launch + int(rng.uniform(60, 1500))
                if rug < n:
                    steps[rug] += np.log(rng.uniform(0.03, 0.2))
            path = p0 * np.exp(np.cumsum(steps))
            path[:launch] = p0
            # leader buys shortly after launch; their buy + followers pump it
            b = min(n - 1, launch + int(rng.uniform(2, 24)))
            entry = path[b]
            _ramp(path, b, lag, rng.uniform(1.04, 1.30))
            trades.append(LeaderTrade(t0 + b * STEP, leader, "buy", tok))
            # leader exits at target, stop, or after a holding time
            target = rng.uniform(2.0, 15.0)
            stop = rng.uniform(0.45, 0.75)
            max_hold = int(rng.uniform(300, 3000))
            s = None
            for i in range(b + 1, min(n, b + max_hold)):
                r = path[i] / entry
                if r >= target or r <= stop:
                    s = i
                    break
            if s is None:
                s = min(n - 1, b + max_hold)
            if s < n - 1:
                trades.append(LeaderTrade(t0 + s * STEP, leader, "sell", tok))
                _ramp(path, s, lag, rng.uniform(0.70, 0.95))
            paths[tok] = path
    return Night(t0, t1, paths, trades, list(leaders))


def simulate(cfg: Config, seed: int, edge: float = 0.30, start: dt.datetime | None = None):
    """One night. Returns (engine, night)."""
    start = start or dt.datetime(2026, 11, 1, 19, 55)
    night = make_night(seed, leaders=tuple(cfg.leaders) or ("surgz", "CXCinvest"), edge=edge,
                       start=start, copy_lag_s=30.0)
    if cfg.lock_at is None or not (night.t0 < cfg.lock_at <= night.t1 + 86400):
        lock = night.t1
    else:
        lock = min(cfg.lock_at, night.t1)
    c = Config(**{**cfg.__dict__, "lock_at": lock})
    eng = run(c, night.price, night.trades, night.t0, night.t1, step_s=STEP)
    return eng, night


def monte_carlo(cfg: Config, runs: int = 300, edge: float = 0.30, seed0: int = 0) -> dict:
    mults, best = [], (None, -1.0)
    for s in range(seed0, seed0 + runs):
        eng, _ = simulate(cfg, s, edge=edge)
        m = eng.stats()["multiple"]
        mults.append(m)
        if m > best[1]:
            best = (s, m)
    a = np.array(mults)
    return {
        "multiples": a,
        "median": float(np.median(a)),
        "mean": float(a.mean()),
        "p_loss": float((a < 1).mean() * 100),
        "p_2x": float((a >= 2).mean() * 100),
        "p_10x": float((a >= 10).mean() * 100),
        "p_13x": float((a >= 13).mean() * 100),
        "best_seed": best[0],
        "best_multiple": best[1],
        "worst_multiple": float(a.min()),
    }
