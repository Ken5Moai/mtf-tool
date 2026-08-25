# mtf-tool

## 1. MTF シグナルツール

```bash
streamlit run app.py
```

H1 / M15 / M5 を自動取得し、BUY / SELL / NO-TRADE とエントリー・SL・TP を提示します。

## 2. 日報（毎日の積み上げ管理）

毎日の損益を記録して、月間目標に対する達成率・必要ペース・種銭の維持状況を集計します。

```bash
# 記録する
python journal.py "今日の利益：15ドル、メモ：BTC 押し目買いで勝ち"

# 集計だけ見る / 直前の記録を取り消す
python journal.py --show
python journal.py --undo
```

Streamlit の「日報」ページ（`streamlit run app.py` → サイドバーの日報）でも同じ操作ができます。

出力は 3 部構成です。

1. **結論** — 本日の損益、今月の累計、達成率
2. **根拠** — 想定ラインとの差、残り日数と必要ペース、勝率と種銭の維持状況
3. **次のアクション** — 明日の行動案とひとこと

### 目標設定

`journal.py` の `DEFAULT_CONFIG`、または日報ページのサイドバーから変更できます。
円建てを正とし、ドル建ての目標は `usdjpy` レートから自動換算します。

| 項目 | 既定値 |
| --- | --- |
| `monthly_goal_jpy` | 100,000 円（≒ 645 ドル @155円） |
| `seed_jpy` | 50,000 円（≒ 323 ドル @155円） |
| `usdjpy` | 155.0 |
| `trading_days` | 30 日 |

記録は `daily_log.json` に保存されます（`.gitignore` 済み。履歴を端末間で共有したい場合は
`git add -f daily_log.json` してください）。

### テスト

```bash
python -m pytest test_journal.py -q
```
