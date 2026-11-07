import numpy as np
import pandas as pd

from research.backtest_mtf import backtest, summarize


def _series(n=4000, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    c = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.002, n)))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.001, n))
    l = np.minimum(o, c) * (1 - rng.uniform(0, 0.001, n))
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1.0}, index=idx)


def test_backtest_runs_and_trades_are_consistent():
    tr = backtest(_series(), cost_pct=0.1, step=3, warmup=260 * 12 // 4)
    for t in tr:
        assert t["result"] in ("SL", "TP")
        if t["result"] == "SL":
            assert t["r"] < 0
        assert t["exit_time"] > t["time"]
    s = summarize(tr)
    assert s["trades"] == len(tr)
