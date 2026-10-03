//+------------------------------------------------------------------+
//| Structure.mqh                                                    |
//| スイング高値・安値と、そこから作る水平線（抵抗線・支持線）。        |
//| 画像の6パターンはどれも「何度か反発した線」が前提なので、           |
//| 線の作り方はここに1本化している。                                  |
//+------------------------------------------------------------------+
#ifndef STEP100MAN_STRUCTURE_MQH
#define STEP100MAN_STRUCTURE_MQH

#include "Common.mqh"

//--- 水平線1本。touches = その線に触れたスイングの本数。
struct SRLevel
{
   double price;
   int    touches;
   int    newestShift;    // 一番新しく触れた足（小さいほど最近）
   int    oldestShift;
};

//+------------------------------------------------------------------+
//| shift 本目が左右 width 本より高い（安い）か。                     |
//| 右側に未確定足しか無い位置は false（確定してから使う）。           |
//+------------------------------------------------------------------+
bool IsSwingHigh(const string sym, const ENUM_TIMEFRAMES tf, const int shift, const int width)
{
   if(shift - width < 1) return(false);
   double h = iHigh(sym, tf, shift);
   if(h <= 0.0) return(false);
   for(int i = 1; i <= width; i++)
   {
      if(iHigh(sym, tf, shift + i) >= h) return(false);
      if(iHigh(sym, tf, shift - i) >= h) return(false);
   }
   return(true);
}

bool IsSwingLow(const string sym, const ENUM_TIMEFRAMES tf, const int shift, const int width)
{
   if(shift - width < 1) return(false);
   double l = iLow(sym, tf, shift);
   if(l <= 0.0) return(false);
   for(int i = 1; i <= width; i++)
   {
      if(iLow(sym, tf, shift + i) <= l) return(false);
      if(iLow(sym, tf, shift - i) <= l) return(false);
   }
   return(true);
}

//--- 直近 lookback 本のスイング高値（安値）を、新しい順に集める
int CollectSwings(const string sym, const ENUM_TIMEFRAMES tf, const bool highs,
                  const int lookback, const int width,
                  double &prices[], int &shifts[])
{
   ArrayResize(prices, 0);
   ArrayResize(shifts, 0);
   int bars = iBars(sym, tf);
   int last = MathMin(lookback, bars - width - 2);

   for(int s = width + 1; s <= last; s++)
   {
      bool hit = highs ? IsSwingHigh(sym, tf, s, width) : IsSwingLow(sym, tf, s, width);
      if(!hit) continue;
      int n = ArraySize(prices);
      ArrayResize(prices, n + 1);
      ArrayResize(shifts, n + 1);
      prices[n] = highs ? iHigh(sym, tf, s) : iLow(sym, tf, s);
      shifts[n] = s;
   }
   return(ArraySize(prices));
}

//+------------------------------------------------------------------+
//| スイングを tolPips でまとめ、触れた回数の多い順に水平線を返す。    |
//| 同数なら新しい方を優先する。                                      |
//+------------------------------------------------------------------+
int BuildLevels(const string sym, const ENUM_TIMEFRAMES tf, const bool highs,
                const int lookback, const int width, const double tolPips,
                SRLevel &out[])
{
   double prices[]; int shifts[];
   int n = CollectSwings(sym, tf, highs, lookback, width, prices, shifts);
   ArrayResize(out, 0);
   if(n <= 0) return(0);

   double tol = PipsToPrice(sym, tolPips);
   bool used[];
   ArrayResize(used, n);
   for(int z = 0; z < n; z++) used[z] = false;

   for(int i = 0; i < n; i++)
   {
      if(used[i]) continue;
      double sum = prices[i];
      int    cnt = 1;
      int    newest = shifts[i];
      int    oldest = shifts[i];
      used[i] = true;

      for(int j = i + 1; j < n; j++)
      {
         if(used[j]) continue;
         if(MathAbs(prices[j] - prices[i]) > tol) continue;
         used[j] = true;
         sum += prices[j];
         cnt++;
         if(shifts[j] < newest) newest = shifts[j];
         if(shifts[j] > oldest) oldest = shifts[j];
      }

      int k = ArraySize(out);
      ArrayResize(out, k + 1);
      out[k].price       = sum / cnt;
      out[k].touches     = cnt;
      out[k].newestShift = newest;
      out[k].oldestShift = oldest;
   }

   // 触れた回数の多い順 → 新しい順（単純な選択ソート。本数は多くて数十本）
   int m = ArraySize(out);
   for(int a = 0; a < m - 1; a++)
   {
      int best = a;
      for(int b = a + 1; b < m; b++)
      {
         bool better = (out[b].touches > out[best].touches)
                    || (out[b].touches == out[best].touches && out[b].newestShift < out[best].newestShift);
         if(better) best = b;
      }
      if(best != a)
      {
         SRLevel t = out[a]; out[a] = out[best]; out[best] = t;
      }
   }
   return(m);
}

//+------------------------------------------------------------------+
//| いまの値より上（下）で、minTouches 回以上触れた一番近い線を返す。  |
//+------------------------------------------------------------------+
bool NearestLevel(const string sym, SRLevel &levels[], const bool above,
                  const double price, const int minTouches, double &outPrice, int &outTouches)
{
   bool   found = false;
   double best  = 0.0;
   int    bestT = 0;

   for(int i = 0; i < ArraySize(levels); i++)
   {
      if(levels[i].touches < minTouches) continue;
      double p = levels[i].price;
      if(above && p <= price) continue;
      if(!above && p >= price) continue;
      if(!found || MathAbs(p - price) < MathAbs(best - price))
      {
         found = true;
         best  = p;
         bestT = levels[i].touches;
      }
   }
   outPrice   = best;
   outTouches = bestT;
   return(found);
}

//--- 直近 n 本の最高値・最安値（確定足のみ）
double HighestHigh(const string sym, const ENUM_TIMEFRAMES tf, const int count, const int start)
{
   int idx = iHighest(sym, tf, MODE_HIGH, count, start);
   return(idx < 0 ? 0.0 : iHigh(sym, tf, idx));
}

double LowestLow(const string sym, const ENUM_TIMEFRAMES tf, const int count, const int start)
{
   int idx = iLowest(sym, tf, MODE_LOW, count, start);
   return(idx < 0 ? 0.0 : iLow(sym, tf, idx));
}

//--- 安値切り上げ / 高値切り下げ（ダマシ回避の確認に使う）
bool HasHigherLow(const string sym, const ENUM_TIMEFRAMES tf, const int lookback, const int width)
{
   double p[]; int s[];
   if(CollectSwings(sym, tf, false, lookback, width, p, s) < 2) return(false);
   return(p[0] > p[1]);     // p[0] が一番新しいスイング安値
}

bool HasLowerHigh(const string sym, const ENUM_TIMEFRAMES tf, const int lookback, const int width)
{
   double p[]; int s[];
   if(CollectSwings(sym, tf, true, lookback, width, p, s) < 2) return(false);
   return(p[0] < p[1]);
}

#endif // STEP100MAN_STRUCTURE_MQH
