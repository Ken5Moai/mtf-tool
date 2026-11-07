import datetime as dt

from copybot.command import parse_task
from copybot.engine import Config, LeaderTrade, run
from copybot.live import trades_from_tx
from copybot.sim import monte_carlo, simulate

NOW = dt.datetime(2026, 11, 1, 19, 55)


def test_parse_video_prompt():
    p = parse_task("follow surgz and CXCinvest on fomo. copy every buy 30s after them, sell when they sell. "
                   "take profit at 10x, lock the rest by 6:30am", now=NOW)
    c = p.config
    assert c.leaders == ["surgz", "CXCinvest"]
    assert c.delay_s == 30 and c.take_profit_x == 10 and c.mirror_exits
    assert p.lock_time == dt.time(6, 30)
    assert dt.datetime.fromtimestamp(c.lock_at) == dt.datetime(2026, 11, 2, 6, 30)
    assert not p.warnings


def test_parse_japanese_and_missing():
    p = parse_task("surgzとCXCinvestをフォロー。1分後にコピー。5倍で利確、7:00に全部ロック", now=NOW)
    assert p.config.leaders == ["surgz", "CXCinvest"]
    assert p.config.delay_s == 60 and p.config.take_profit_x == 5
    assert p.lock_time == dt.time(7, 0)
    q = parse_task("make me money", now=NOW)          # must not crash
    assert q.config.leaders == [] and q.warnings


def _flat(prices):
    return lambda tok, t: prices[tok](t)


def test_delay_tp_and_lock():
    cfg = Config(leaders=["a"], budget=100, delay_s=30, take_profit_x=2, fee_bps=0, slippage_bps=0, lock_at=1000)
    px = {"X": lambda t: 1.0 if t < 30 else (1.5 if t < 300 else 3.0)}
    eng = run(cfg, _flat(px), [LeaderTrade(0, "a", "buy", "X")], 0, 2000, step_s=5)
    buy = next(a for a in eng.activity if a["kind"] == "buy")
    assert buy["t"] == 30 and buy["px"] == 1.5           # filled after the delay, at the pumped price
    tp = next(a for a in eng.activity if a["kind"] == "tp")
    assert tp["t"] == 300 and round(eng.cash, 6) == 200  # 100 -> 2x
    assert eng.locked and eng.equity[-1][0] == 1000


def test_mirror_sell_and_ignore_unknown_leader():
    cfg = Config(leaders=["a"], budget=100, delay_s=10, take_profit_x=None, fee_bps=0, slippage_bps=0)
    px = {"X": lambda t: 1.0 if t < 100 else 0.5}
    tr = [LeaderTrade(0, "a", "buy", "X"), LeaderTrade(0, "zzz", "buy", "X"), LeaderTrade(95, "a", "sell", "X")]
    eng = run(cfg, _flat(px), tr, 0, 300, step_s=5)
    assert eng.stats()["trades"] == 1
    assert round(eng.cash, 6) == 50 and not eng.positions


def test_sim_deterministic_and_bankroll_never_negative():
    cfg = parse_task("follow surgz and CXCinvest copy every buy 30s after, take profit at 10x, lock by 6:30am",
                     now=NOW).config
    e1, _ = simulate(cfg, 3)
    e2, _ = simulate(cfg, 3)
    assert e1.stats() == e2.stats()
    assert all(v >= -1e-9 for _, v in e1.equity) and e1.cash >= -1e-9
    r = monte_carlo(cfg, runs=20)
    assert len(r["multiples"]) == 20 and 0 <= r["p_loss"] <= 100


def test_live_tx_parsing():
    me = "Wallet111"
    def tx(pre, post):
        mk = lambda amt: [{"owner": me, "mint": "MEME", "uiTokenAmount": {"uiAmount": amt}},
                          {"owner": "other", "mint": "MEME", "uiTokenAmount": {"uiAmount": 999}}]
        return {"blockTime": 100, "meta": {"err": None, "preTokenBalances": mk(pre), "postTokenBalances": mk(post)}}
    held = {}
    b = trades_from_tx(tx(0, 1000), me, "a", held)
    assert [(x.side, x.token) for x in b] == [("buy", "MEME")] and held["MEME"] == 1000
    s = trades_from_tx(tx(1000, 750), me, "a", held)
    assert s[0].side == "sell" and abs(s[0].frac - 0.25) < 1e-9
    assert trades_from_tx({"meta": {"err": "x"}}, me, "a", held) == []


def test_stop_loss_and_chase_filter():
    cfg = Config(leaders=["a"], budget=100, delay_s=30, take_profit_x=None, fee_bps=0, slippage_bps=0,
                 stop_loss_x=0.6, size_frac=1.0)
    px = {"X": lambda t: 1.0 if t < 100 else 0.5}
    eng = run(cfg, _flat(px), [LeaderTrade(0, "a", "buy", "X")], 0, 300, step_s=5)
    assert any(a["kind"] == "stop" for a in eng.activity) and round(eng.cash, 6) == 50
    cfg2 = Config(leaders=["a"], budget=100, delay_s=30, max_chase_pct=15, fee_bps=0, slippage_bps=0)
    px2 = {"X": lambda t: 1.0 if t < 30 else 1.3}
    eng2 = run(cfg2, _flat(px2), [LeaderTrade(0, "a", "buy", "X")], 0, 100, step_s=5)
    assert eng2.stats()["trades"] == 0 and eng2.cash == 100


def test_score_leaders():
    from copybot.score import score
    rows = [{"kind": "sell", "leader": "good", "pnl": 5, "pct": 10}] * 20 + \
           [{"kind": "stop", "leader": "bad", "pnl": -5, "pct": -40}] * 20 + \
           [{"kind": "tp", "leader": "new", "pnl": 50, "pct": 900}] * 3
    v = {r["leader"]: r["verdict"] for r in score(rows)}
    assert v == {"good": "KEEP", "bad": "DROP", "new": "WAIT (3/20)"}
