# mtf-tool

## MTF signal tool
`streamlit run app.py` — H1/M15/M5 multi-timeframe BUY/SELL/NO-TRADE.

## Copy Bot（コピートレードBot：fomo「Grok Bot」動画の再現）
指定トレーダーの売買を N 秒遅れでコピーし、相手が売ったら売る・N倍で利確・指定時刻に全決済する Bot。
**全部ペーパー（仮想資金）。実資金・秘密鍵・実注文は一切扱いません。**

| 使い方 | コマンド |
|---|---|
| 画面で再現（シミュレーション） | `streamlit run copybot_app.py` |
| 実在ウォレットをペーパー追跡 | `python -m copybot.live --leader surgz=<SOLANAアドレス> --leader cxc=<アドレス> --budget 149 --delay 30 --tp 10 --lock 06:30` |
| テスト | `python -m pytest -q tests` |

指示文は英語/日本語どちらでも可：
`follow surgz and CXCinvest on fomo. copy every buy 30s after them, sell when they sell. take profit at 10x, lock the rest by 6:30am`
`surgzとCXCinvestをフォロー。30秒後にコピー。10倍で利確、6:30に全部ロック`

構成: `copybot/engine.py`（売買ロジック）, `command.py`（指示文パーサ）, `sim.py`（仮想市場・モンテカルロ）, `live.py`（Solana RPC + DexScreener ペーパー追跡）。
「Reality check」タブで同じルールを数百晩回し、動画のような13倍がどれくらいの確率かを確認できます。

### 増やす方向に寄せる設定（シミュレーション上の検証結果は Reality check タブで再現可）
- `--size 0.2 --stop 40 --max-chase 15`（1回20%・損切り-40%・相手より15%以上高ければ見送り）
- 追う相手は `python -m copybot.score copybot_paper_log.jsonl` で「自分がコピーした場合の損益」で選別（20回以上・合計プラス・中央値プラスのみKEEP）
