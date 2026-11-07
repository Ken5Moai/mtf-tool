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


def test_ops_selection_and_report(tmp_path):
    from copybot import ops
    st = ops.load_state(tmp_path)
    assert st["bankroll"] == 149.0
    st["leaders"] = {"good": {"status": "candidate", "added": 1}, "bad": {"status": "candidate", "added": 2},
                     "new": {"status": "candidate", "added": 3}, "old": {"status": "drop", "added": 0}}
    log = [{"kind": "sell", "leader": "good", "pnl": 5, "pct": 10}] * 20 + \
          [{"kind": "stop", "leader": "bad", "pnl": -5, "pct": -40}] * 20
    sc = ops.update_statuses(st, log, 20)
    assert st["leaders"]["good"]["status"] == "keep" and st["leaders"]["bad"]["status"] == "drop"
    assert ops.choose(st, 5) == ["good", "new"]
    ops.save_state(tmp_path, st)
    txt = ops.write_report(tmp_path, ops.load_state(tmp_path), sc, log)
    assert "残高: $149.00" in txt and (tmp_path / "REPORT.md").exists()


def test_cancel_copy_when_leader_exits_first_and_scalper_drop():
    cfg = Config(leaders=["a"], budget=100, delay_s=10, fee_bps=0, slippage_bps=0)
    px = {"X": lambda t: 1.0}
    eng = run(cfg, _flat(px), [LeaderTrade(0, "a", "buy", "X"), LeaderTrade(2, "a", "sell", "X")], 0, 60, step_s=1)
    assert eng.stats()["trades"] == 0 and eng.cash == 100
    from copybot import ops
    st = {"leaders": {"a": {"status": "candidate", "added": 0}}}
    log = []
    for i in range(6):
        log += [{"kind": "watch", "leader": "a", "token": f"T{i}", "side": "buy", "t": i * 100},
                {"kind": "watch", "leader": "a", "token": f"T{i}", "side": "sell", "t": i * 100 + 3}]
    ops.update_statuses(st, log, 20)
    assert st["leaders"]["a"]["status"] == "drop"


def test_fill_price_from_sol_swap_and_chase_uses_it():
    me = "W1"
    tx = {"blockTime": 10,
          "transaction": {"message": {"accountKeys": [{"pubkey": me}, {"pubkey": "pool"}]}},
          "meta": {"err": None, "fee": 5000,
                   "preBalances": [2_000_000_000, 0], "postBalances": [1_000_000_000 - 5000, 0],
                   "preTokenBalances": [], "postTokenBalances": [
                       {"owner": me, "mint": "MEME", "uiTokenAmount": {"uiAmount": 1_000_000}}]}}
    tr = trades_from_tx(tx, me, "a", sol_usd=150.0)        # spent 1 SOL = $150 for 1M tokens
    assert tr[0].side == "buy" and abs(tr[0].px - 0.00015) < 1e-12
    cfg = Config(leaders=["a"], budget=100, delay_s=0, max_chase_pct=15, fee_bps=0, slippage_bps=0)
    px = {"MEME": lambda t: 0.0002}                         # already +33% vs leader fill
    tr[0].t = 0
    eng = run(cfg, _flat(px), tr, 0, 10, step_s=1)
    assert eng.stats()["trades"] == 0
    px2 = {"MEME": lambda t: 0.00016}                       # +6.7%: copy, and record the cost
    eng2 = run(cfg, _flat(px2), tr, 0, 10, step_s=1)
    b = next(a for a in eng2.activity if a["kind"] == "buy")
    assert abs(b["chase_pct"] - 6.67) < 0.01


def test_sell_fraction_from_pre_balance_and_buy_filter():
    me = "W1"
    mk = lambda amt: [{"owner": me, "mint": "MEME", "uiTokenAmount": {"uiAmount": amt}}]
    tx = {"blockTime": 1, "meta": {"err": None, "preTokenBalances": mk(400), "postTokenBalances": mk(100)}}
    s = trades_from_tx(tx, me, "a")                         # no history: fraction from pre-balance
    assert s[0].side == "sell" and abs(s[0].frac - 0.75) < 1e-9
    from copybot.engine import CopyEngine
    eng = CopyEngine(Config(leaders=["a"], budget=100, delay_s=0), lambda tok, t: 1.0)
    eng.buy_filter = lambda tok: "liquidity $900 < $15,000"
    eng.on_leader_trade(LeaderTrade(0, "a", "buy", "X")); eng.step(0)
    assert eng.stats()["trades"] == 0 and any(a["kind"] == "skip" for a in eng.activity)


def test_restore_positions(tmp_path):
    from copybot import ops
    from copybot.engine import CopyEngine

    class FakeSol:
        def token_balance(self, owner, mint):
            return {"A": 0.0, "B": 10.0}[mint]

    st = {"open": [{"token": "A", "leader": "L", "qty": 10, "cost": 10, "opened": 0},     # leader sold offline
                   {"token": "B", "leader": "L", "qty": 10, "cost": 10, "opened": 0},     # still held -> keep
                   {"token": "C", "leader": "L", "qty": 10, "cost": 10, "opened": 0},     # no price -> $0
                   {"token": "B", "leader": "gone", "qty": 5, "cost": 5, "opened": 0}]}   # unfollowed -> close
    prices = {"A": 2.0, "B": 3.0}
    eng = CopyEngine(Config(leaders=["L"], budget=50, fee_bps=0, slippage_bps=0), lambda t, _: prices.get(t))
    ops.restore_positions(eng, st, FakeSol(), ["L"])
    assert list(eng.positions) == [("L", "B")]
    assert round(eng.cash, 6) == 50 + 20 + 15
    assert round(eng.mark(0), 6) == 85 + 30


def test_backtest_replay_uses_leader_fills_plus_cost():
    from copybot.backtest import replay, median_hold
    h = {"L": [{"t": 0, "side": "buy", "token": "X", "px": 1.0, "frac": 1.0},
               {"t": 3600, "side": "sell", "token": "X", "px": 2.0, "frac": 1.0}]}
    r0 = replay(h, 0.0, 0, 3600, budget=100, size=1.0)
    assert abs(r0["end"] - 100 * 2 * 0.99 / 1.01) < 1e-6          # only the 1% fee each side
    r10 = replay(h, 10.0, 0, 3600, budget=100, size=1.0)
    assert abs(r10["end"] - 100 * 2 * 0.89 / 1.11) < 1e-6         # +10% copy cost each side
    assert median_hold(h["L"] * 3) == 3600
