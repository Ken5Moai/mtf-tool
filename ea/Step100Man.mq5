//+------------------------------------------------------------------+
//|                                                  Step100Man.mq5  |
//|                                                                  |
//|  「1万円から100万円を目指す戦略とロット目安」をそのまま実装した   |
//|  MT5 用 EA。元画像の5つのブロックが、そのまま5つの入力グループに   |
//|  対応している。                                                    |
//|                                                                  |
//|   (1) 全体の戦略        → 利確/損切り・分割・建値SL・追加エントリー |
//|   (2) 資金ごとのロット目安 → LotTable.mqh の表（画像の数値のまま）  |
//|   (3) エントリーポイント  → Signals.mqh の6パターン                |
//|   (4) 利確・損切りのルール → Manager.mqh                           |
//|   (5) 注意点            → Filters.mqh ＋ ロット据え置きの保証       |
//|                                                                  |
//|  ■ 初期値は「シグナル通知のみ」。自動売買は EaMode を変えること。  |
//|  ■ 画像の表は損切りも 40-50pips なので、1回の損失も「1回の利益    |
//|     目安」と同額になる。1万円・0.03lot なら 1回 1,500〜2,000円＝   |
//|     口座の 15〜20%。RiskCapPercent で上限を掛けられる（既定=無効）。|
//+------------------------------------------------------------------+
#property copyright "Step100Man"
#property version   "1.00"
#property description "画像『1万円から100万円を目指す戦略とロット目安』の忠実実装（MT5）"

#include <Trade\Trade.mqh>
#include "Step100Man/Common.mqh"
#include "Step100Man/LotTable.mqh"
#include "Step100Man/Structure.mqh"
#include "Step100Man/Signals.mqh"
#include "Step100Man/Filters.mqh"
#include "Step100Man/Manager.mqh"

enum ENUM_EA_MODE
{
   EA_SIGNAL_ONLY,   // シグナル通知のみ（発注しない）
   EA_AUTO           // 自動売買
};

enum ENUM_LOT_MODE
{
   LOTM_TABLE,       // 画像の表どおり（資金 → ロット）
   LOTM_FIXED,       // 固定ロット
   LOTM_RISK         // 1トレードのリスク％から逆算
};

enum ENUM_BASIS
{
   BASIS_BALANCE,    // 残高
   BASIS_EQUITY      // 有効証拠金（ボーナス込みの業者向け）
};

//--- (0) 動作 -------------------------------------------------------
input group "=== 動作 ==="
input ENUM_EA_MODE    EaMode               = EA_SIGNAL_ONLY; // 動作モード
input long            MagicNumber          = 100260914;      // マジックナンバー
input ENUM_TIMEFRAMES TradeTimeframe       = PERIOD_H1;      // 判定する時間足
input int             SlippagePoints       = 20;             // 許容スリッページ(point)
input string          TradeComment         = "Step100Man";   // 注文コメント

//--- (2) ロット -----------------------------------------------------
input group "=== (2) 資金ごとのロット目安 ==="
input ENUM_LOT_MODE   LotMode              = LOTM_TABLE;     // ロットの決め方
input ENUM_BASIS      TableBasis           = BASIS_BALANCE;  // 表に当てる資金
input double          FixedLot             = 0.03;           // 固定ロット(LOTM_FIXED)
input double          RiskPercentPerTrade  = 2.0;            // リスク％(LOTM_RISK)
input double          ManualUsdJpy         = 155.0;          // 円換算レート(自動取得できない時)
input bool            ExtrapolateAbove1M   = false;          // 100万円超もロットを伸ばす
input double          RiskCapPercent       = 0.0;            // ロットの上限を資金の何％の損失に抑えるか(0=無効)

//--- (1)(4) 利確・損切り --------------------------------------------
input group "=== (1)(4) 利確・損切りのルール ==="
input double          StopLossPips         = 45.0;           // 損切り(pips) 画像:40-50
input double          TakeProfitPips       = 45.0;           // 利確(pips)   画像:40-50
input bool            UsePartialClose      = true;           // 分割利確する
input double          PartialAtPips        = 25.0;           // 分割する利益(pips)
input double          PartialPercent       = 50.0;           // 分割で決済する割合(%)
input bool            UseBreakEven         = true;           // 建値へSL移動する
input double          BreakEvenAtPips      = 30.0;           // 建値に移す利益(pips) 画像:30
input double          BreakEvenBufferPips  = 1.0;            // 建値+α(pips)
input bool            UseTrailing          = true;           // 残りをトレーリングで伸ばす
input double          TrailStartPips       = 35.0;           // トレール開始(pips)
input double          TrailStepPips        = 20.0;           // トレール幅(pips)
input double          InvalidationPips     = 10.0;           // 根拠が崩れたら撤退(pips, 0=無効)

//--- (1) 追加エントリー ---------------------------------------------
input group "=== (1) 追加エントリー ==="
input bool            UsePyramiding        = true;           // 抵抗線ブレイクで追加を狙う
input int             MaxAddPositions      = 2;              // 追加の上限(本)
input bool            AddOnlyAfterBreakEven= true;           // 既存が建値に移ってからのみ追加

//--- (3) エントリーパターン -----------------------------------------
input group "=== (3) エントリーポイントの例 ==="
input bool            UsePattern1          = true;           // 1 ブレイクの逆指値狙い
input bool            UsePattern2          = true;           // 2 キリ番での反発狙い
input bool            UsePattern3          = true;           // 3 レジサポ転換の押し目買い
input bool            UsePattern4          = true;           // 4 チャートパターン形成後のブレイク
input bool            UsePattern5          = true;           // 5 ダマシ回避の確認後エントリー
input bool            UsePattern6          = true;           // 6 逆行後の反発狙い
input int             LookbackBars         = 150;            // 線を探す足数
input int             SwingWidth           = 2;              // スイング判定の左右本数
input double          LevelTolerancePips   = 8.0;            // 同じ線とみなす幅(pips)
input int             MinTouches           = 2;              // 「複数回反発した線」の回数
input double          BreakBufferPips      = 2.0;            // 抜けたと認める余白(pips)
input bool            RequireCloseConfirm  = true;           // 終値で抜けを確認する
input double          WickRatio            = 1.5;            // 反発足のヒゲ／実体比
input int             FakeOutBars          = 12;             // ダマシを探す範囲(本)
input int             RoleReversalBars     = 20;             // レジサポ転換を待つ範囲(本)
input double          RoundTolerancePips   = 6.0;            // キリ番とみなす幅(pips)
input int             PatternMinSeparation = 5;              // ダブルボトム/トップの最小間隔(本)

//--- (5) 注意点 ------------------------------------------------------
input group "=== (5) 注意点（門番） ==="
input double          MaxSpreadPips        = 2.5;            // これより広ければ入らない
input bool            UseNewsFilter        = true;           // 経済指標前後は入らない
input int             NewsMinutesBefore    = 30;             // 指標の何分前から止めるか
input int             NewsMinutesAfter     = 30;             // 指標の何分後まで止めるか
input int             NewsMinImportance    = 3;              // 1=低 2=中 3=高
input bool            BlockIfNoCalendar    = false;          // カレンダーが使えない時も止める
input string          AvoidHoursCsv        = "";             // 入らない時間帯 例:"21:30-23:00"
input int             MaxPositionsTotal    = 3;              // 同時保有の上限
input int             MaxTradesPerDay      = 3;              // 1日の新規回数の上限(0=無制限)
input double          MaxDailyLossPercent  = 10.0;           // 1日の損失上限(%) 0=無効
input bool            CloseAllOnDailyLoss  = true;           // 上限に当たったら全決済

//--- 通知 ------------------------------------------------------------
input group "=== 通知 ==="
input bool            AlertPopup           = true;           // ポップアップ
input bool            AlertPush            = false;          // スマホへプッシュ
input bool            AlertMail            = false;          // メール
input bool            DrawArrows           = true;           // チャートに矢印を置く
input bool            VerboseLog           = true;           // 判断の理由をログに出す

//--- 内部状態 --------------------------------------------------------
CTrade     g_trade;
SignalCfg  g_sig;
ManageCfg  g_man;
datetime   g_lastBarTime  = 0;
datetime   g_haltedDay    = 0;     // 損失上限で止めた日
int        g_arrowSeq     = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   if(StopLossPips <= 0.0 || TakeProfitPips <= 0.0)
   {
      Print("[Step100Man] 損切り・利確は 0 より大きい値にしてください。");
      return(INIT_PARAMETERS_INCORRECT);
   }
   if(UsePartialClose && PartialAtPips >= TakeProfitPips)
      Print("[Step100Man] 注意: 分割利確の pips が利確以上です。分割が働きません。");
   if(UseBreakEven && BreakEvenAtPips >= TakeProfitPips)
      Print("[Step100Man] 注意: 建値移動の pips が利確以上です。建値移動が働きません。");
   if(PipSize(_Symbol) <= 0.0)
   {
      Print("[Step100Man] この銘柄の pip を計算できません。");
      return(INIT_FAILED);
   }

   g_trade.SetExpertMagicNumber((ulong)MagicNumber);
   g_trade.SetDeviationInPoints(SlippagePoints);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_trade.SetAsyncMode(false);

   g_sig.tf                  = TradeTimeframe;
   g_sig.lookback            = LookbackBars;
   g_sig.swingWidth          = SwingWidth;
   g_sig.tolPips             = LevelTolerancePips;
   g_sig.minTouches          = MinTouches;
   g_sig.breakBufferPips     = BreakBufferPips;
   g_sig.wickRatio           = WickRatio;
   g_sig.requireCloseConfirm = RequireCloseConfirm;
   g_sig.fakeOutBars         = FakeOutBars;
   g_sig.roleReversalBars    = RoleReversalBars;
   g_sig.roundTolPips        = RoundTolerancePips;
   g_sig.patternMinSep       = PatternMinSeparation;
   g_sig.use[0] = false;
   g_sig.use[1] = UsePattern1;
   g_sig.use[2] = UsePattern2;
   g_sig.use[3] = UsePattern3;
   g_sig.use[4] = UsePattern4;
   g_sig.use[5] = UsePattern5;
   g_sig.use[6] = UsePattern6;

   g_man.slPips              = StopLossPips;
   g_man.tpPips              = TakeProfitPips;
   g_man.usePartial          = UsePartialClose;
   g_man.partialAtPips       = PartialAtPips;
   g_man.partialPercent      = PartialPercent;
   g_man.useBreakEven        = UseBreakEven;
   g_man.breakEvenAtPips     = BreakEvenAtPips;
   g_man.breakEvenBufferPips = BreakEvenBufferPips;
   g_man.useTrailing         = UseTrailing;
   g_man.trailStartPips      = TrailStartPips;
   g_man.trailStepPips       = TrailStepPips;
   g_man.invalidationPips    = InvalidationPips;

   PrintStartupReport();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) { }

//+------------------------------------------------------------------+
//| 起動時に「この口座・この銘柄で実際いくら動くのか」を出す。        |
//| 画像の『1回の利益目安』は 1pip≒1,500円/lot（ドル建て通貨ペア）の  |
//| 前提なので、クロス円では金額が変わる。実測値を出して突き合わせる。|
//+------------------------------------------------------------------+
void PrintStartupReport()
{
   string cur    = AccountInfoString(ACCOUNT_CURRENCY);
   double rate   = AccountToJpyRate(ManualUsdJpy);
   double basis  = (TableBasis == BASIS_EQUITY) ? AccountInfoDouble(ACCOUNT_EQUITY)
                                                : AccountInfoDouble(ACCOUNT_BALANCE);
   double jpy    = basis * rate;
   double pipVal = PipValuePerLot(_Symbol);
   double lot    = CurrentLot();

   Print("==================================================");
   PrintFormat("[Step100Man] %s / %s / モード=%s",
               _Symbol, EnumToString(TradeTimeframe),
               (EaMode == EA_AUTO ? "自動売買" : "シグナル通知のみ"));
   PrintFormat("  口座 %.2f %s × %.2f = 約 %s 円  → 表の段: %s",
               basis, cur, rate, DoubleToString(jpy, 0), TierLabel(jpy));
   PrintFormat("  採用ロット %.2f / 1pip あたり %.2f %s（1ロット）",
               lot, pipVal, cur);

   if(pipVal > 0.0)
   {
      double win  = lot * TakeProfitPips * pipVal;
      double loss = lot * StopLossPips   * pipVal;
      PrintFormat("  → 利確 %.0fpips = +%.2f %s（約 %s 円）",
                  TakeProfitPips, win, cur, DoubleToString(win * rate, 0));
      PrintFormat("  → 損切り %.0fpips = -%.2f %s（約 %s 円）",
                  StopLossPips, loss, cur, DoubleToString(loss * rate, 0));
      if(basis > 0.0)
         PrintFormat("  → 1回の損失は資金の %.1f%%", loss / basis * 100.0);
   }
   Print("  ※画像の表は損切りも40-50pipsなので、利益目安＝損失目安です。");
   Print("==================================================");
}

string TierLabel(const double jpy)
{
   int i = LotTierIndex(jpy);
   if(i < 0) return("5千円未満");
   return(StringFormat("%s円 → %.2f lot", DoubleToString(LotTierJpy[i], 0), LotTierVol[i]));
}

//+------------------------------------------------------------------+
//| いま使うロット。画像の表が基本、上限キャップは任意。              |
//| 「1回の負けで大きくロットを上げない」ため、直前の勝敗は一切見ない。|
//+------------------------------------------------------------------+
double CurrentLot()
{
   double basis = (TableBasis == BASIS_EQUITY) ? AccountInfoDouble(ACCOUNT_EQUITY)
                                               : AccountInfoDouble(ACCOUNT_BALANCE);
   double lot   = 0.0;

   if(LotMode == LOTM_FIXED)
   {
      lot = FixedLot;
   }
   else if(LotMode == LOTM_RISK)
   {
      double pipVal = PipValuePerLot(_Symbol);
      if(pipVal <= 0.0 || StopLossPips <= 0.0) return(0.0);
      lot = (basis * RiskPercentPerTrade / 100.0) / (StopLossPips * pipVal);
   }
   else
   {
      double jpy = basis * AccountToJpyRate(ManualUsdJpy);
      lot = LotForBalanceJpy(jpy, ExtrapolateAbove1M);
   }

   lot = ApplyRiskCap(_Symbol, lot, StopLossPips, RiskCapPercent, basis);
   return(NormalizeLot(_Symbol, lot));
}

//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime t = iTime(_Symbol, TradeTimeframe, 0);
   if(t == 0 || t == g_lastBarTime) return(false);
   g_lastBarTime = t;
   return(true);
}

//+------------------------------------------------------------------+
//| 門番。入れない理由があれば why に入れて false を返す。            |
//+------------------------------------------------------------------+
bool EntryAllowed(string &why)
{
   why = "";

   double spread = CurrentSpreadPips(_Symbol);
   if(MaxSpreadPips > 0.0 && spread > MaxSpreadPips)
   { why = StringFormat("スプレッドが広い (%.1f > %.1f pips)", spread, MaxSpreadPips); return(false); }

   if(InBlockedHours(AvoidHoursCsv))
   { why = "入らない時間帯"; return(false); }

   if(MaxPositionsTotal > 0 && CountPositions(_Symbol, MagicNumber) >= MaxPositionsTotal)
   { why = "同時保有の上限"; return(false); }

   if(MaxTradesPerDay > 0 && TodayEntryCount(_Symbol, MagicNumber) >= MaxTradesPerDay)
   { why = "1日の新規回数の上限（感情エントリー防止）"; return(false); }

   if(g_haltedDay == StartOfDay(TimeCurrent()))
   { why = "1日の損失上限に当たったため今日は停止"; return(false); }

   if(UseNewsFilter)
   {
      bool   available = false;
      string ev        = "";
      bool   blocked   = NewsBlackout(_Symbol, NewsMinutesBefore, NewsMinutesAfter,
                                      NewsMinImportance, available, ev);
      if(blocked)      { why = "経済指標の前後: " + ev; return(false); }
      if(!available && BlockIfNoCalendar)
      { why = "経済指標カレンダーを参照できない"; return(false); }
   }
   return(true);
}

//+------------------------------------------------------------------+
//| 1日の損失上限。当たったら今日はもう入らない。                     |
//+------------------------------------------------------------------+
void CheckDailyLoss()
{
   if(MaxDailyLossPercent <= 0.0) return;
   if(g_haltedDay == StartOfDay(TimeCurrent())) return;

   double profit; int deals;
   if(!TodayStats(_Symbol, MagicNumber, profit, deals)) return;

   double base = AccountInfoDouble(ACCOUNT_BALANCE) - profit;   // 今日の開始残高の目安
   if(base <= 0.0) return;

   double lossPct = (profit < 0.0) ? (-profit / base * 100.0) : 0.0;
   if(lossPct < MaxDailyLossPercent) return;

   g_haltedDay = StartOfDay(TimeCurrent());
   string msg = StringFormat("[Step100Man] 1日の損失上限 %.1f%% に到達（%.2f）。今日は新規停止。",
                             lossPct, profit);
   Print(msg);
   Notify(msg);
   if(CloseAllOnDailyLoss && EaMode == EA_AUTO)
      CloseAll(g_trade, _Symbol, MagicNumber);
}

//+------------------------------------------------------------------+
void Notify(const string text)
{
   if(AlertPopup) Alert(text);
   if(AlertPush)  SendNotification(text);
   if(AlertMail)  SendMail("Step100Man", text);
}

void PutArrow(const int dir, const double price, const string tip)
{
   if(!DrawArrows) return;
   string name = StringFormat("S100M_%d_%d", (int)TimeCurrent(), g_arrowSeq++);
   ENUM_OBJECT kind = (dir > 0) ? OBJ_ARROW_BUY : OBJ_ARROW_SELL;
   if(!ObjectCreate(0, name, kind, 0, TimeCurrent(), price))
      return;
   ObjectSetInteger(0, name, OBJPROP_COLOR, dir > 0 ? clrDodgerBlue : clrTomato);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
   ObjectSetString (0, name, OBJPROP_TOOLTIP, tip);
}

//+------------------------------------------------------------------+
void OnTick()
{
   // 建玉の面倒は毎ティック見る（分割・建値・トレール・根拠崩れ）
   if(EaMode == EA_AUTO)
      ManagePositions(g_trade, _Symbol, MagicNumber, g_man);

   CheckDailyLoss();

   if(!IsNewBar()) return;

   SignalInfo sig;
   if(!EvaluateSignals(_Symbol, g_sig, sig)) return;

   string why;
   if(!EntryAllowed(why))
   {
      if(VerboseLog)
         PrintFormat("[Step100Man] 見送り（%s）: %d %s / %s",
                     why, sig.pattern, sig.name, sig.reason);
      return;
   }

   int    held   = CountPositionsDir(_Symbol, MagicNumber, sig.dir);
   bool   isAdd  = (held > 0);
   double price  = (sig.dir > 0) ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                                 : SymbolInfoDouble(_Symbol, SYMBOL_BID);

   if(isAdd)
   {
      // 「抵抗線のブレイクが確認できれば追加エントリーも狙う」
      if(!UsePyramiding)
      {
         if(VerboseLog) Print("[Step100Man] 既に同じ向きを保有（追加は無効）");
         return;
      }
      if(held > MaxAddPositions)
      {
         if(VerboseLog) Print("[Step100Man] 追加の上限に到達");
         return;
      }
      if(sig.pattern != 1 && sig.pattern != 3 && sig.pattern != 4 && sig.pattern != 5)
      {
         if(VerboseLog) Print("[Step100Man] 追加はブレイク／レジサポ転換系のみ");
         return;
      }
      if(AddOnlyAfterBreakEven && !AllInProfitOrBreakEven(_Symbol, MagicNumber, sig.dir))
      {
         if(VerboseLog) Print("[Step100Man] 既存が建値に移っていないため追加しない");
         return;
      }
   }
   // 逆向きを持っているときは入らない（ドテンしない）
   if(CountPositionsDir(_Symbol, MagicNumber, -sig.dir) > 0)
   {
      if(VerboseLog) Print("[Step100Man] 逆向きを保有中のため見送り");
      return;
   }

   double lot = CurrentLot();
   if(lot <= 0.0)
   {
      Print("[Step100Man] ロットを決められませんでした。");
      return;
   }

   double pipVal = PipValuePerLot(_Symbol);
   string head   = StringFormat("%s %s %s  %s",
                                _Symbol,
                                (sig.dir > 0 ? "買い" : "売り"),
                                sig.name, sig.reason);
   string body   = StringFormat("%s / %.2f lot / SL %.0f・TP %.0f pips / 想定損失 %.2f %s%s",
                                head, lot, StopLossPips, TakeProfitPips,
                                lot * StopLossPips * pipVal,
                                AccountInfoString(ACCOUNT_CURRENCY),
                                (isAdd ? " / 追加エントリー" : ""));

   PutArrow(sig.dir, price, body);
   Print("[Step100Man] " + body);

   if(EaMode == EA_SIGNAL_ONLY)
   {
      Notify("[シグナル] " + body);
      return;
   }

   ulong ticket = 0;
   if(OpenTrade(g_trade, _Symbol, MagicNumber, sig.dir, lot, g_man, sig.level, isAdd,
                TradeComment, ticket))
   {
      Notify("[約定] " + body);
   }
}
//+------------------------------------------------------------------+
