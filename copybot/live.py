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


def trades_from_tx(tx: dict, owner: str, leader: str, held: dict) -> list[LeaderTrade]:
    """Turn a parsed tx into buy/sell events. `held` tracks leader balances (mint -> amount)."""
    out = []
    t = float(tx.get("blockTime") or time.time())
    for mint, d in token_deltas(tx, owner).items():
        if mint in STABLES:
            continue
        before = held.get(mint, 0.0)
        if d > 0:
            out.append(LeaderTrade(t, leader, "buy", mint))
            held[mint] = before + d
        else:
            frac = 1.0 if before <= 0 else min(1.0, -d / before)
            out.append(LeaderTrade(t, leader, "sell", mint, frac=frac))
            held[mint] = max(0.0, before + d)
    return out


class Solana:
    def __init__(self, rpc: str):
        self.rpc = rpc
        self.s = requests.Session()

    def call(self, method, params):
        r = self.s.post(self.rpc, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20)
        r.raise_for_status()
        j = r.json()
        if "error" in j:
            raise RuntimeError(j["error"])
        return j["result"]

    def signatures(self, addr: str, until: Optional[str] = None, limit: int = 20):
        p = {"limit": limit}
        if until:
            p["until"] = until
        return self.call("getSignaturesForAddress", [addr, p])

    def tx(self, sig: str):
        return self.call("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])


class DexPrices:
    def __init__(self, ttl: float = 5.0):
        self.cache: dict = {}
        self.ttl = ttl
        self.s = requests.Session()

    def __call__(self, mint: str, t: float) -> Optional[float]:
        hit = self.cache.get(mint)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        try:
            r = self.s.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=15)
            pairs = (r.json() or {}).get("pairs") or []
            pairs = [p for p in pairs if p.get("priceUsd")]
            if not pairs:
                return None
            best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
            px = float(best["priceUsd"])
            self.cache[mint] = (time.time(), px)
            return px
        except Exception as e:  # network hiccup: keep last price
            print(f"price error {mint[:6]}: {e}", file=sys.stderr)
            return hit[1] if hit else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Paper copy-trade real Solana wallets.")
    ap.add_argument("--leader", action="append", required=True, help="name=WALLET_ADDRESS (repeatable)")
    ap.add_argument("--budget", type=float, default=149.0)
    ap.add_argument("--delay", type=float, default=30.0)
    ap.add_argument("--tp", type=float, default=10.0)
    ap.add_argument("--lock", default=None, help="HH:MM local time to close everything")
    ap.add_argument("--no-mirror", action="store_true")
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
                 lock_at=lock_at, mirror_exits=not a.no_mirror)
    sol, px = Solana(a.rpc), DexPrices()
    eng = CopyEngine(cfg, px)
    last = {}
    held = {name: {} for name in wallets}
    for name, addr in wallets.items():                 # start from "now": ignore history
        sigs = sol.signatures(addr, limit=1)
        last[name] = sigs[0]["signature"] if sigs else None
    print(f"PAPER mode. Watching {', '.join(wallets)}. budget ${a.budget:,.2f}, delay {a.delay:g}s, TP {a.tp:g}x")
    seen = 0
    with open(a.log, "a") as f:
        while not eng.locked:
            for name, addr in wallets.items():
                try:
                    sigs = sol.signatures(addr, until=last[name])
                except Exception as e:
                    print(f"rpc error {name}: {e}", file=sys.stderr)
                    continue
                if sigs:
                    last[name] = sigs[0]["signature"]
                for s in reversed(sigs):
                    if s.get("err"):
                        continue
                    try:
                        for tr in trades_from_tx(sol.tx(s["signature"]), addr, name, held[name]):
                            tr.t = max(tr.t, time.time() - 1)
                            eng.on_leader_trade(tr)
                    except Exception as e:
                        print(f"tx error {s['signature'][:8]}: {e}", file=sys.stderr)
            eng.step(time.time())
            for act in eng.activity[seen:]:
                stamp = dt.datetime.fromtimestamp(act["t"]).strftime("%H:%M:%S")
                print(f"[{stamp}] {act['kind']:5} {act['text']}  {act.get('note') or ''}")
                f.write(json.dumps(act) + "\n"); f.flush()
            seen = len(eng.activity)
            time.sleep(a.poll)
    st = eng.stats()
    print(f"Locked. ${st['start']:,.2f} -> ${st['end']:,.2f} ({st['pct']:+.1f}%, {st['multiple']:.2f}x)")


if __name__ == "__main__":
    main()
