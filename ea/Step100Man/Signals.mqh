//+------------------------------------------------------------------+
//| Signals.mqh                                                      |
//| 画像「エントリーポイントの例」の6パターンをコードにしたもの。      |
//|                                                                  |
//|   1 ブレイクの逆指値狙い                                          |
//|   2 キリ番での反発狙い                                            |
//|   3 レジサポ転換の押し目買い                                      |
//|   4 チャートパターン形成後のブレイク                              |
//|   5 ダマシ回避の確認後エントリー                                  |
//|   6 逆行後の反発狙い                                              |
//|                                                                  |
//| 元画像は手書きのチャート図なので、ここでの定義は「その図を素直に   |
//| 数式にしたらこうなる」という解釈。判定の閾値はすべて入力で動かせる。|
//| 判定は確定足だけで行う（shift>=1）。形成中の足は見ない。           |
//+------------------------------------------------------------------+
#ifndef STEP100MAN_SIGNALS_MQH
#define STEP100MAN_SIGNALS_MQH

#include "Common.mqh"
#include "Structure.mqh"

struct SignalCfg
{
   ENUM_TIMEFRAMES tf;
   int    lookback;              // 線を探す足数
   int    swingWidth;            // スイング判定の左右本数
   double tolPips;               // 同じ線とみなす幅
   int    minTouches;            // 「複数回反発した線」の回数
   double breakBufferPips;       // 抜けたと認める余白
   double wickRatio;             // ヒゲ／実体の比（反発足の判定）
   bool   requireCloseConfirm;   // true=終値で抜けを判定 / false=高値安値でも可
   int    fakeOutBars;           // ダマシを探す範囲
   int    roleReversalBars;      // レジサポ転換を待つ範囲
   double roundTolPips;          // キリ番とみなす幅
   int    patternMinSep;         // ダブルボトム/トップの最小間隔（本）
   bool   use[7];                // use[1]..use[6]
};

struct SignalInfo
{
   int    dir;        // +1 買い / -1 売り / 0 なし
   int    pattern;    // 1..6
   string name;
   string reason;
   double level;      // 根拠にした水平線
   double stopRef;    // 構造上の損切り候補（直近安値/高値）
};

void ResetSignal(SignalInfo &s)
{
   s.dir = 0; s.pattern = 0; s.name = ""; s.reason = ""; s.level = 0.0; s.stopRef = 0.0;
}

//--- 抜けの判定に使う値（終値確認か、ヒゲ込みか）
double BreakValueUp(const string sym, const SignalCfg &cfg, const int i)
{
   return(cfg.requireCloseConfirm ? iClose(sym, cfg.tf, i) : iHigh(sym, cfg.tf, i));
}
double BreakValueDown(const string sym, const SignalCfg &cfg, const int i)
{
   return(cfg.requireCloseConfirm ? iClose(sym, cfg.tf, i) : iLow(sym, cfg.tf, i));
}

//+------------------------------------------------------------------+
//| 1 ブレイクの逆指値狙い                                            |
//|   複数回反発した線を、確定足の終値で抜けた直後に入る。            |
//+------------------------------------------------------------------+
bool Detect1_Break(const string sym, const SignalCfg &cfg, SignalInfo &out)
{
   int dg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double buf = PipsToPrice(sym, cfg.breakBufferPips);

   SRLevel hi[];
   BuildLevels(sym, cfg.tf, true, cfg.lookback, cfg.swingWidth, cfg.tolPips, hi);
   for(int i = 0; i < ArraySize(hi); i++)
   {
      if(hi[i].touches < cfg.minTouches) continue;
      if(hi[i].newestShift < 2) continue;                       // 線は前から在ること
      double lv = hi[i].price;
      if(BreakValueUp(sym, cfg, 1) > lv + buf && iClose(sym, cfg.tf, 2) <= lv + buf)
      {
         out.dir     = 1;
         out.pattern = 1;
         out.name    = "ブレイクの逆指値狙い";
         out.reason  = StringFormat("%d回反発した抵抗線 %s を終値で上抜け",
                                    hi[i].touches, DoubleToString(lv, dg));
         out.level   = lv;
         out.stopRef = LowestLow(sym, cfg.tf, cfg.swingWidth * 3 + 2, 1);
         return(true);
      }
   }

   SRLevel lo[];
   BuildLevels(sym, cfg.tf, false, cfg.lookback, cfg.swingWidth, cfg.tolPips, lo);
   for(int j = 0; j < ArraySize(lo); j++)
   {
      if(lo[j].touches < cfg.minTouches) continue;
      if(lo[j].newestShift < 2) continue;
      double lv = lo[j].price;
      if(BreakValueDown(sym, cfg, 1) < lv - buf && iClose(sym, cfg.tf, 2) >= lv - buf)
      {
         out.dir     = -1;
         out.pattern = 1;
         out.name    = "ブレイクの逆指値狙い";
         out.reason  = StringFormat("%d回反発した支持線 %s を終値で下抜け",
                                    lo[j].touches, DoubleToString(lv, dg));
         out.level   = lv;
         out.stopRef = HighestHigh(sym, cfg.tf, cfg.swingWidth * 3 + 2, 1);
         return(true);
      }
   }
   return(false);
}

//+------------------------------------------------------------------+
//| 2 キリ番での反発狙い                                              |
//|   キリ番（JPY系=0.50刻み／他=50pips刻み）に触れて戻した足で入る。 |
//+------------------------------------------------------------------+
double RoundStep(const string sym)
{
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   return((digits <= 3) ? 0.50 : 0.0050);
}

bool Detect2_RoundNumber(const string sym, const SignalCfg &cfg, SignalInfo &out)
{
   int dg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double step = RoundStep(sym);
   double tol  = PipsToPrice(sym, cfg.roundTolPips);
   if(step <= 0.0) return(false);

   double lo1 = iLow (sym, cfg.tf, 1);
   double hi1 = iHigh(sym, cfg.tf, 1);
   double cl1 = iClose(sym, cfg.tf, 1);

   // 買い：安値がキリ番に触れ、終値はキリ番の上、形は反発足
   double rLow = MathRound(lo1 / step) * step;
   if(MathAbs(lo1 - rLow) <= tol && cl1 > rLow
      && (IsBullRejection(sym, cfg.tf, 1, cfg.wickRatio) || IsBullEngulf(sym, cfg.tf, 1)))
   {
      out.dir     = 1;
      out.pattern = 2;
      out.name    = "キリ番での反発狙い";
      out.reason  = StringFormat("キリ番 %s で反発、終値で回復", DoubleToString(rLow, dg));
      out.level   = rLow;
      out.stopRef = lo1;
      return(true);
   }

   // 売り
   double rHigh = MathRound(hi1 / step) * step;
   if(MathAbs(hi1 - rHigh) <= tol && cl1 < rHigh
      && (IsBearRejection(sym, cfg.tf, 1, cfg.wickRatio) || IsBearEngulf(sym, cfg.tf, 1)))
   {
      out.dir     = -1;
      out.pattern = 2;
      out.name    = "キリ番での反発狙い";
      out.reason  = StringFormat("キリ番 %s で上抑え、終値で押し戻し", DoubleToString(rHigh, dg));
      out.level   = rHigh;
      out.stopRef = hi1;
      return(true);
   }
   return(false);
}

//+------------------------------------------------------------------+
//| 3 レジサポ転換の押し目買い                                        |
//|   抵抗線を抜けたあと、その線まで戻って支えられたら入る。          |
//+------------------------------------------------------------------+
bool Detect3_RoleReversal(const string sym, const SignalCfg &cfg, SignalInfo &out)
{
   int dg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double buf = PipsToPrice(sym, cfg.breakBufferPips);
   double tol = PipsToPrice(sym, cfg.tolPips);

   // 買い：抵抗線 → 支持線への転換
   SRLevel hi[];
   BuildLevels(sym, cfg.tf, true, cfg.lookback, cfg.swingWidth, cfg.tolPips, hi);
   for(int i = 0; i < ArraySize(hi); i++)
   {
      if(hi[i].touches < cfg.minTouches) continue;
      double lv = hi[i].price;

      // 直近 roleReversalBars 本のどこかで上抜けていること
      bool broke = false;
      for(int k = 2; k <= cfg.roleReversalBars; k++)
         if(iClose(sym, cfg.tf, k) > lv + buf && iClose(sym, cfg.tf, k + 1) <= lv + buf)
         { broke = true; break; }
      if(!broke) continue;

      // いまその線まで押して、終値は線の上で耐えている
      if(iLow(sym, cfg.tf, 1) <= lv + tol && iClose(sym, cfg.tf, 1) > lv
         && IsBullBar(sym, cfg.tf, 1))
      {
         out.dir     = 1;
         out.pattern = 3;
         out.name    = "レジサポ転換の押し目買い";
         out.reason  = StringFormat("抵抗線 %s を抜けた後、同じ線で支えられた",
                                    DoubleToString(lv, dg));
         out.level   = lv;
         out.stopRef = iLow(sym, cfg.tf, 1);
         return(true);
      }
   }

   // 売り：支持線 → 抵抗線への転換
   SRLevel lo[];
   BuildLevels(sym, cfg.tf, false, cfg.lookback, cfg.swingWidth, cfg.tolPips, lo);
   for(int j = 0; j < ArraySize(lo); j++)
   {
      if(lo[j].touches < cfg.minTouches) continue;
      double lv = lo[j].price;

      bool broke = false;
      for(int k = 2; k <= cfg.roleReversalBars; k++)
         if(iClose(sym, cfg.tf, k) < lv - buf && iClose(sym, cfg.tf, k + 1) >= lv - buf)
         { broke = true; break; }
      if(!broke) continue;

      if(iHigh(sym, cfg.tf, 1) >= lv - tol && iClose(sym, cfg.tf, 1) < lv
         && IsBearBar(sym, cfg.tf, 1))
      {
         out.dir     = -1;
         out.pattern = 3;
         out.name    = "レジサポ転換の戻り売り";
         out.reason  = StringFormat("支持線 %s を割った後、同じ線で抑えられた",
                                    DoubleToString(lv, dg));
         out.level   = lv;
         out.stopRef = iHigh(sym, cfg.tf, 1);
         return(true);
      }
   }
   return(false);
}

//+------------------------------------------------------------------+
//| 4 チャートパターン形成後のブレイク                                |
//|   (a) ダブルボトム／ダブルトップのネックライン抜け                |
//|   (b) 三角持ち合い（高値切り下げ＋安値切り上げ）の抜け            |
//+------------------------------------------------------------------+
bool Detect4_PatternBreak(const string sym, const SignalCfg &cfg, SignalInfo &out)
{
   int dg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double buf = PipsToPrice(sym, cfg.breakBufferPips);
   double tol = PipsToPrice(sym, cfg.tolPips);

   double lowP[], highP[]; int lowS[], highS[];
   int nl = CollectSwings(sym, cfg.tf, false, cfg.lookback, cfg.swingWidth, lowP, lowS);
   int nh = CollectSwings(sym, cfg.tf, true,  cfg.lookback, cfg.swingWidth, highP, highS);

   //--- (a) ダブルボトム：同じ高さの安値2つ＋間の高値がネックライン
   if(nl >= 2 && nh >= 1)
   {
      for(int i = 0; i < nl - 1; i++)
      {
         for(int j = i + 1; j < nl; j++)
         {
            if(MathAbs(lowP[i] - lowP[j]) > tol) continue;
            if(lowS[j] - lowS[i] < cfg.patternMinSep) continue;

            // 2つの安値の「間」にある一番高いスイング高値＝ネックライン
            double neck = 0.0;
            for(int k = 0; k < nh; k++)
               if(highS[k] > lowS[i] && highS[k] < lowS[j] && highP[k] > neck)
                  neck = highP[k];
            if(neck <= 0.0) continue;

            if(BreakValueUp(sym, cfg, 1) > neck + buf && iClose(sym, cfg.tf, 2) <= neck + buf)
            {
               out.dir     = 1;
               out.pattern = 4;
               out.name    = "チャートパターン形成後のブレイク";
               out.reason  = StringFormat("ダブルボトムのネックライン %s を上抜け",
                                          DoubleToString(neck, dg));
               out.level   = neck;
               out.stopRef = MathMin(lowP[i], lowP[j]);
               return(true);
            }
         }
      }
   }

   //--- (a') ダブルトップ
   if(nh >= 2 && nl >= 1)
   {
      for(int i = 0; i < nh - 1; i++)
      {
         for(int j = i + 1; j < nh; j++)
         {
            if(MathAbs(highP[i] - highP[j]) > tol) continue;
            if(highS[j] - highS[i] < cfg.patternMinSep) continue;

            double neck = 0.0; bool got = false;
            for(int k = 0; k < nl; k++)
               if(lowS[k] > highS[i] && lowS[k] < highS[j] && (!got || lowP[k] < neck))
               { neck = lowP[k]; got = true; }
            if(!got) continue;

            if(BreakValueDown(sym, cfg, 1) < neck - buf && iClose(sym, cfg.tf, 2) >= neck - buf)
            {
               out.dir     = -1;
               out.pattern = 4;
               out.name    = "チャートパターン形成後のブレイク";
               out.reason  = StringFormat("ダブルトップのネックライン %s を下抜け",
                                          DoubleToString(neck, dg));
               out.level   = neck;
               out.stopRef = MathMax(highP[i], highP[j]);
               return(true);
            }
         }
      }
   }

   //--- (b) 三角持ち合い：高値は切り下げ、安値は切り上げ → 直近高値抜けで買い
   if(nh >= 2 && nl >= 2)
   {
      bool squeeze = (highP[0] < highP[1]) && (lowP[0] > lowP[1]);
      if(squeeze)
      {
         if(BreakValueUp(sym, cfg, 1) > highP[0] + buf && iClose(sym, cfg.tf, 2) <= highP[0] + buf)
         {
            out.dir     = 1;
            out.pattern = 4;
            out.name    = "チャートパターン形成後のブレイク";
            out.reason  = "三角持ち合いの上辺を上抜け";
            out.level   = highP[0];
            out.stopRef = lowP[0];
            return(true);
         }
         if(BreakValueDown(sym, cfg, 1) < lowP[0] - buf && iClose(sym, cfg.tf, 2) >= lowP[0] - buf)
         {
            out.dir     = -1;
            out.pattern = 4;
            out.name    = "チャートパターン形成後のブレイク";
            out.reason  = "三角持ち合いの下辺を下抜け";
            out.level   = lowP[0];
            out.stopRef = highP[0];
            return(true);
         }
      }
   }
   return(false);
}

//+------------------------------------------------------------------+
//| 5 ダマシ回避の確認後エントリー                                    |
//|   一度抜けて戻された（ダマシ）あと、もう一度抜けた確定足で入る。  |
//+------------------------------------------------------------------+
bool Detect5_AfterFakeOut(const string sym, const SignalCfg &cfg, SignalInfo &out)
{
   int dg = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double buf = PipsToPrice(sym, cfg.breakBufferPips);

   SRLevel hi[];
   BuildLevels(sym, cfg.tf, true, cfg.lookback, cfg.swingWidth, cfg.tolPips, hi);
   for(int i = 0; i < ArraySize(hi); i++)
   {
      if(hi[i].touches < cfg.minTouches) continue;
      double lv = hi[i].price;
      if(!(BreakValueUp(sym, cfg, 1) > lv + buf)) continue;     // いま抜けていること

      // 手前に「抜けて戻された」足があるか
      bool fake = false;
      for(int k = 2; k <= cfg.fakeOutBars; k++)
      {
         if(iClose(sym, cfg.tf, k) >= lv) continue;             // 戻っている足を探す
         for(int m = k + 1; m <= cfg.fakeOutBars + 2; m++)
            if(iHigh(sym, cfg.tf, m) > lv + buf && iClose(sym, cfg.tf, m) < lv)
            { fake = true; break; }
         if(fake) break;
      }
      if(!fake) continue;
      if(!HasHigherLow(sym, cfg.tf, cfg.lookback, cfg.swingWidth)) continue;  // 安値切り上げ

      out.dir     = 1;
      out.pattern = 5;
      out.name    = "ダマシ回避の確認後エントリー";
      out.reason  = StringFormat("%s をダマシで一度戻したあと、再度上抜け＋安値切り上げ",
                                 DoubleToString(lv, dg));
      out.level   = lv;
      out.stopRef = LowestLow(sym, cfg.tf, cfg.fakeOutBars, 1);
      return(true);
   }

   SRLevel lo[];
   BuildLevels(sym, cfg.tf, false, cfg.lookback, cfg.swingWidth, cfg.tolPips, lo);
   for(int j = 0; j < ArraySize(lo); j++)
   {
      if(lo[j].touches < cfg.minTouches) continue;
      double lv = lo[j].price;
      if(!(BreakValueDown(sym, cfg, 1) < lv - buf)) continue;

      bool fake = false;
      for(int k = 2; k <= cfg.fakeOutBars; k++)
      {
         if(iClose(sym, cfg.tf, k) <= lv) continue;
         for(int m = k + 1; m <= cfg.fakeOutBars + 2; m++)
            if(iLow(sym, cfg.tf, m) < lv - buf && iClose(sym, cfg.tf, m) > lv)
            { fake = true; break; }
         if(fake) break;
      }
      if(!fake) continue;
      if(!HasLowerHigh(sym, cfg.tf, cfg.lookback, cfg.swingWidth)) continue;

      out.dir     = -1;
      out.pattern = 5;
      out.name    = "ダマシ回避の確認後エントリー";
      out.reason  = StringFormat("%s をダマシで一度戻したあと、再度下抜け＋高値切り下げ",
                                 DoubleToString(lv, dg));
      out.level   = lv;
      out.stopRef = HighestHigh(sym, cfg.tf, cfg.fakeOutBars, 1);
      return(true);
   }
   return(false);
}

//+------------------------------------------------------------------+
//| 6 逆行後の反発狙い                                                |
//|   下げたあとの安値圏で、ヒゲ／実体が安値を否定した足で入る。      |
//+------------------------------------------------------------------+
bool Detect6_Rebound(const string sym, const SignalCfg &cfg, SignalInfo &out)
{
   int    win = MathMax(cfg.swingWidth * 4, 10);
   double tol = PipsToPrice(sym, cfg.tolPips);

   double ll = LowestLow(sym, cfg.tf, win, 1);
   double hh = HighestHigh(sym, cfg.tf, win, 1);

   // 買い：直近の安値圏にいて、下ヒゲで否定した足
   if(iLow(sym, cfg.tf, 1) <= ll + tol
      && IsBullRejection(sym, cfg.tf, 1, cfg.wickRatio)
      && iClose(sym, cfg.tf, win) > iClose(sym, cfg.tf, 1))        // そこまでは下げてきた
   {
      out.dir     = 1;
      out.pattern = 6;
      out.name    = "逆行後の反発狙い";
      out.reason  = "安値圏でヒゲ／実体が安値を否定";
      out.level   = ll;
      out.stopRef = iLow(sym, cfg.tf, 1);
      return(true);
   }

   // 売り
   if(iHigh(sym, cfg.tf, 1) >= hh - tol
      && IsBearRejection(sym, cfg.tf, 1, cfg.wickRatio)
      && iClose(sym, cfg.tf, win) < iClose(sym, cfg.tf, 1))
   {
      out.dir     = -1;
      out.pattern = 6;
      out.name    = "逆行後の反発狙い";
      out.reason  = "高値圏でヒゲ／実体が高値を否定";
      out.level   = hh;
      out.stopRef = iHigh(sym, cfg.tf, 1);
      return(true);
   }
   return(false);
}

//+------------------------------------------------------------------+
//| 6つを 1→6 の順に見て、最初に当たったものを返す。                  |
//| 画像の並び順＝優先順位として扱っている。                          |
//+------------------------------------------------------------------+
bool EvaluateSignals(const string sym, const SignalCfg &cfg, SignalInfo &out)
{
   ResetSignal(out);
   if(iBars(sym, cfg.tf) < cfg.lookback + cfg.swingWidth + 5)
      return(false);

   if(cfg.use[1] && Detect1_Break        (sym, cfg, out)) return(true);
   if(cfg.use[2] && Detect2_RoundNumber  (sym, cfg, out)) return(true);
   if(cfg.use[3] && Detect3_RoleReversal (sym, cfg, out)) return(true);
   if(cfg.use[4] && Detect4_PatternBreak (sym, cfg, out)) return(true);
   if(cfg.use[5] && Detect5_AfterFakeOut (sym, cfg, out)) return(true);
   if(cfg.use[6] && Detect6_Rebound      (sym, cfg, out)) return(true);

   ResetSignal(out);
   return(false);
}

#endif // STEP100MAN_SIGNALS_MQH
