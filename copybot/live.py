"""
Live PAPER copy trading on Solana.

Watches real leader wallets via Solana JSON-RPC, detects their token buys/sells
from balance changes, prices tokens via DexScreener, and runs the same
CopyEngine on a fake bankroll. No keys, no signing, no real orders.

  python -m copybot.live --leader surgz=<WALLET_ADDRESS> --leader cxc=<WALLET_ADDRESS> \
      --budget 149 --delay 30 --tp 10 --lock 06:30

Note: fomo usernames are not wallets. Open the trader's profile on fomo /
Solscan and copy their Solana address.
Hosts used: api.mainnet-beta.solana.com (or --rpc), api.dexscreener.com.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from typing import Optional

import requests

from .command import next_occurrence
from .engine import Config, CopyEngine, LeaderTrade

SOL_MINT = "So11111111111111111111111111111111111111112"
STABLES = {SOL_MINT,
           "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",   # USDC
           "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCE8BenwNYB"}   # USDT


def token_deltas(tx: dict, owner: str) -> dict:
    """mint -> change in UI amount held by `owner` within one parsed transaction."""
    meta = (tx or {}).get("meta") or {}
    if meta.get("err"):
        return {}

    def bal(key):
        out = {}
        for b in meta.get(key) or []:
            if b.get("owner") != owner:
                continue
            amt = (b.get("uiTokenAmount") or {}).get("uiAmount") or 0.0
            out[b["mint"]] = out.get(b["mint"], 0.0) + float(amt)
        return out

    pre, post = bal("preTokenBalances"), bal("postTokenBalances")
    return {m: post.get(m, 0.0) - pre.get(m, 0.0)
            for m in set(pre) | set(post) if abs(post.get(m, 0.0) - pre.get(m, 0.0)) > 0}


def sol_delta(tx: dict, owner: str) -> float:
    """Native SOL change of `owner` in the tx (fee added back when owner paid it)."""
    meta = (tx or {}).get("meta") or {}
    try:
        keys = tx["transaction"]["message"]["accountKeys"]
        keys = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
        i = keys.index(owner)
        d = (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9
        if i == 0:
            d += (meta.get("fee") or 0) / 1e9
        return d
    except (KeyError, ValueError, IndexError, TypeError):
        return 0.0


def _pre_amount(tx: dict, owner: str, mint: str) -> float:
    meta = (tx or {}).get("meta") or {}
    return sum(float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
               for b in meta.get("preTokenBalances") or [] if b.get("owner") == owner and b.get("mint") == mint)


def trades_from_tx(tx: dict, owner: str, leader: str, held: Optional[dict] = None,
                   sol_usd: Optional[float] = None) -> list[LeaderTrade]:
    """Turn a parsed tx into buy/sell events with the leader's fill price when it can be derived.
    Sell fraction comes from the leader's balance before the tx (no history needed)."""
    out = []
    held = {} if held is None else held
    t = float(tx.get("blockTime") or time.time())
    deltas = token_deltas(tx, owner)
    quote_usd = 0.0                                   # what the leader paid (+) / received (-) in USD
    for m, d in deltas.items():
        if m in ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCE8BenwNYB"):
            quote_usd -= d
        elif m == SOL_MINT and sol_usd:
            quote_usd -= d * sol_usd
    if sol_usd:
        quote_usd -= sol_delta(tx, owner) * sol_usd
    traded = {m: d for m, d in deltas.items() if m not in STABLES}
    for mint, d in traded.items():
        px = abs(quote_usd) / abs(d) if quote_usd and len(traded) == 1 and d else None
        if px is not None and ((d > 0) != (quote_usd > 0)):
            px = None                                 # not a simple swap (transfer, airdrop...)
        if d > 0:
            out.append(LeaderTrade(t, leader, "buy", mint, px=px))
            held[mint] = held.get(mint, 0.0) + d
        else:
            pre = _pre_amount(tx, owner, mint) or held.get(mint, 0.0)
            frac = 1.0 if pre <= 0 else min(1.0, -d / pre)
            out.append(LeaderTrade(t, leader, "sell", mint, frac=frac, px=px))
            held[mint] = max(0.0, held.get(mint, 0.0) + d)
    return out


class Solana:
    """JSON-RPC client that respects the public node's per-method limit (~1 call/s) and retries 429s."""

    def __init__(self, rpc: str, min_interval: float = 1.1):
        self.rpc = rpc
        self.s = requests.Session()
        self.min_interval = min_interval
        self.last: dict = {}
        self.calls = 0

    def call(self, method, params, retries: int = 5):
        for i in range(retries):
            wait = self.last.get(method, 0) + self.min_interval - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last[method] = time.time()
            self.calls += 1
            r = self.s.post(self.rpc, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(30, 2 ** (i + 1)))
                continue
            r.raise_for_status()
            j = r.json()
            if "error" in j:
                raise RuntimeError(j["error"])
            return j["result"]
        raise RuntimeError(f"{method}: rate limited after {retries} retries")

    def signatures(self, addr: str, until: Optional[str] = None, limit: int = 20, before: Optional[str] = None):
        p = {"limit": limit}
        if until:
            p["until"] = until
        if before:
            p["before"] = before
        return self.call("getSignaturesForAddress", [addr, p])

    def tx(self, sig: str, tries: int = 3):
        """Parsed tx at 'confirmed'. A just-notified tx can be null for a moment: retry briefly."""
        for i in range(tries):
            r = self.call("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1,
                                                   "commitment": "confirmed"}])
            if r is not None or i == tries - 1:
                return r
            time.sleep(1.0)

    def token_balance(self, owner: str, mint: str) -> float:
        r = self.call("getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed"}])
        return sum(float(a["account"]["data"]["parsed"]["info"]["tokenAmount"].get("uiAmount") or 0)
                   for a in (r or {}).get("value", []))

    @property
    def ws_url(self) -> str:
        return self.rpc.replace("https://", "wss://").replace("http://", "ws://")


class LogWatcher:
    """Push notifications (logsSubscribe) for wallets -> queue of (name, address, signature).
    Runs in a background thread; reconnects forever. Public node allows ~5 subscriptions per socket."""

    PER_SOCKET = 5

    def __init__(self, ws_url: str, wallets: dict):
        import queue
        import threading
        self.url, self.wallets = ws_url, wallets
        self.q = queue.Queue()
        self.connected = 0
        self.stop = False
        self.t = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.t.start()
        return self

    def _run(self):
        import asyncio
        asyncio.run(self._main())

    async def _main(self):
        import asyncio
        items = list(self.wallets.items())
        groups = [items[i:i + self.PER_SOCKET] for i in range(0, len(items), self.PER_SOCKET)]
        await asyncio.gather(*(self._socket(g) for g in groups))

    async def _socket(self, group):
        import asyncio
        import websockets
        backoff = 2
        while not self.stop:
            try:
                async with websockets.connect(self.url, open_timeout=20, ping_interval=20, max_size=2 ** 22) as ws:
                    subs = {}
                    for i, (name, addr) in enumerate(group):
                        await ws.send(json.dumps({"jsonrpc": "2.0", "id": i, "method": "logsSubscribe",
                                                  "params": [{"mentions": [addr]}, {"commitment": "confirmed"}]}))
                    while len(subs) < len(group):
                        m = json.loads(await asyncio.wait_for(ws.recv(), 20))
                        if "id" in m and "result" in m:
                            subs[m["result"]] = group[m["id"]]
                    self.connected += 1
                    backoff = 2
                    try:
                        async for raw in ws:
                            m = json.loads(raw)
                            p = m.get("params") or {}
                            v = (p.get("result") or {}).get("value") or {}
                            if v.get("signature") and not v.get("err") and p.get("subscription") in subs:
                                name, addr = subs[p["subscription"]]
                                self.q.put((name, addr, v["signature"], time.time()))
                            if self.stop:
                                break
                    finally:
                        self.connected -= 1
            except Exception as e:
                print(f"ws reconnect ({e.__class__.__name__}: {str(e)[:80]})", file=sys.stderr)
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)


def fee_payer(tx: dict) -> Optional[str]:
    try:
        k = tx["transaction"]["message"]["accountKeys"][0]
        return k["pubkey"] if isinstance(k, dict) else k
    except (KeyError, IndexError, TypeError):
        return None


class DexPrices:
    """USD price per token from DexScreener (deepest pool). Also keeps liquidity / age for filters."""

    def __init__(self, ttl: float = 5.0):
        self.cache: dict = {}
        self.info: dict = {}
        self.ttl = ttl
        self.s = requests.Session()

    def __call__(self, mint: str, t: float = 0) -> Optional[float]:
        hit = self.cache.get(mint)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        try:
            r = self.s.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=15)
            pairs = [p for p in ((r.json() or {}).get("pairs") or []) if p.get("priceUsd")]
            if not pairs:
                return hit[1] if hit else None
            best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
            px = float(best["priceUsd"])
            self.cache[mint] = (time.time(), px)
            self.info[mint] = {"liq": float((best.get("liquidity") or {}).get("usd") or 0),
                               "created": (best.get("pairCreatedAt") or 0) / 1000,
                               "dex": best.get("dexId")}
            return px
        except Exception as e:  # network hiccup: keep last price
            print(f"price error {mint[:6]}: {e}", file=sys.stderr)
            return hit[1] if hit else None

    def sol_usd(self) -> Optional[float]:
        return self(SOL_MINT)

    def liquidity_filter(self, min_liq: float = 15000):
        def f(mint: str) -> Optional[str]:
            if mint not in self.info:
                self(mint)
            liq = (self.info.get(mint) or {}).get("liq", 0)
            if liq < min_liq:
                return f"liquidity ${liq:,.0f} < ${min_liq:,.0f}"
            return None
        return f


def paper_loop(eng: CopyEngine, wallets: dict, sol: "Solana", until: float, log=None,
               poll: float = 1.0, echo: bool = True, use_ws: bool = True, backfill_s: float = 45.0) -> CopyEngine:
    """Follow `wallets` (name -> address) live until `until` (epoch) or the engine locks.

    Detection: WebSocket push (fast) + a slow polling sweep as safety net (catches anything
    the socket missed). Each 'watch' row gets `lag_s` = our detection time - on-chain block time."""
    last, held, done = {}, {name: {} for name in wallets}, set()
    for name, addr in wallets.items():                 # start from "now": ignore history
        try:
            sigs = sol.signatures(addr, limit=1)
            last[name] = sigs[0]["signature"] if sigs else None
        except Exception as e:
            print(f"rpc error {name}: {e}", file=sys.stderr)
            last[name] = None
    watcher = LogWatcher(sol.ws_url, wallets).start() if use_ws else None
    names = list(wallets)
    sweep_i, next_sweep = 0, time.time() + backfill_s / max(1, len(names))
    seen = 0

    def handle(name, addr, sig):
        if sig in done:
            return
        done.add(sig)
        try:
            tx = sol.tx(sig)
            if not tx:
                return
            sol_usd = eng.price_fn.sol_usd() if hasattr(eng.price_fn, "sol_usd") else None
            for tr in trades_from_tx(tx, addr, name, held[name], sol_usd=sol_usd):
                now = time.time()
                lag = now - tr.t
                tr.t = now                             # copy delay counts from when WE saw it
                n0 = len(eng.activity)
                eng.on_leader_trade(tr)
                for a in eng.activity[n0:]:
                    if a["kind"] == "watch":
                        a["lag_s"] = round(lag, 1)
                        a["via"] = "ws" if from_ws else "poll"
        except Exception as e:
            print(f"tx error {sig[:8]}: {e}", file=sys.stderr)

    while not eng.locked and time.time() < until:
        from_ws = True
        while watcher and not watcher.q.empty():       # fast path
            name, addr, sig, _ = watcher.q.get()
            handle(name, addr, sig)
            eng.step(time.time())
        if time.time() >= next_sweep or not watcher or not watcher.connected:   # safety net, one wallet at a time
            from_ws = False
            name = names[sweep_i % len(names)]; sweep_i += 1
            addr = wallets[name]
            next_sweep = time.time() + (backfill_s / len(names) if watcher and watcher.connected else 0)
            try:
                sigs = sol.signatures(addr, until=last[name]) if last[name] else sol.signatures(addr, limit=1)
                if sigs:
                    first = last[name] is None
                    last[name] = sigs[0]["signature"]
                    if first:
                        sigs = []
                for s in reversed(sigs[:10]):          # a burst of >10 txs is a bot; only look at the newest
                    if not s.get("err"):
                        handle(name, addr, s["signature"])
            except Exception as e:
                print(f"rpc error {name}: {e}", file=sys.stderr)
        eng.step(time.time())
        for act in eng.activity[seen:]:
            if echo:
                stamp = dt.datetime.fromtimestamp(act["t"]).strftime("%H:%M:%S")
                print(f"[{stamp}] {act['kind']:5} {act['text']}  {act.get('note') or ''}", flush=True)
            if log:
                log.write(json.dumps(act) + "\n"); log.flush()
        seen = len(eng.activity)
        if len(done) > 50000:
            done.clear()
        time.sleep(poll)
    if watcher:
        watcher.stop = True
    return eng


def main(argv=None):
    ap = argparse.ArgumentParser(description="Paper copy-trade real Solana wallets.")
    ap.add_argument("--leader", action="append", required=True, help="name=WALLET_ADDRESS (repeatable)")
    ap.add_argument("--budget", type=float, default=149.0)
    ap.add_argument("--delay", type=float, default=30.0)
    ap.add_argument("--tp", type=float, default=10.0)
    ap.add_argument("--lock", default=None, help="HH:MM local time to close everything")
    ap.add_argument("--no-mirror", action="store_true")
    ap.add_argument("--size", type=float, default=None, help="fraction of equity per copy, e.g. 0.2")
    ap.add_argument("--stop", type=float, default=None, help="stop loss in %%, e.g. 40")
    ap.add_argument("--max-chase", type=float, default=None, help="skip if price ran more than N%% since leader bought")
    ap.add_argument("--rpc", default=os.environ.get("SOLANA_RPC", "https://api.mainnet-beta.solana.com"))
    ap.add_argument("--poll", type=float, default=5.0)
    ap.add_argument("--log", default="copybot_paper_log.jsonl")
    a = ap.parse_args(argv)

    wallets = dict(x.split("=", 1) for x in a.leader)
    lock_at = None
    if a.lock:
        h, m = map(int, a.lock.split(":"))
        lock_at = next_occurrence(dt.time(h, m), dt.datetime.now()).timestamp()
    cfg = Config(leaders=list(wallets), budget=a.budget, delay_s=a.delay, take_profit_x=a.tp,
                 lock_at=lock_at, mirror_exits=not a.no_mirror, size_frac=a.size,
                 stop_loss_x=(1 - a.stop / 100) if a.stop else None, max_chase_pct=a.max_chase)
    sol, px = Solana(a.rpc), DexPrices()
    eng = CopyEngine(cfg, px)
    print(f"PAPER mode. Watching {', '.join(wallets)}. budget ${a.budget:,.2f}, delay {a.delay:g}s, TP {a.tp:g}x")
    with open(a.log, "a") as f:
        paper_loop(eng, wallets, sol, until=lock_at or float("inf"), log=f, poll=a.poll)
    st = eng.stats()
    print(f"Locked. ${st['start']:,.2f} -> ${st['end']:,.2f} ({st['pct']:+.1f}%, {st['multiple']:.2f}x)")


if __name__ == "__main__":
    main()
