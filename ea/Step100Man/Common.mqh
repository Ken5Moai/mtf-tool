//+------------------------------------------------------------------+
//| Common.mqh                                                       |
//| pip 換算・ロット丸め・ローソク足の小道具。                          |
//| どのファイルからも使うので、ここには状態を持たせない。               |
//+------------------------------------------------------------------+
#ifndef STEP100MAN_COMMON_MQH
#define STEP100MAN_COMMON_MQH

//--- 5桁/3桁業者では 1pip = 10point。4桁/2桁ならそのまま 1point。
double PipSize(const string sym)
{
   int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double point  = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(point <= 0.0)
      return(0.0);
   return((digits == 3 || digits == 5) ? point * 10.0 : point);
}

double PipsToPrice(const string sym, const double pips) { return(pips * PipSize(sym)); }

double PriceToPips(const string sym, const double diff)
{
   double p = PipSize(sym);
   return(p > 0.0 ? diff / p : 0.0);
}

double NormalizePrice(const string sym, const double price)
{
   return(NormalizeDouble(price, (int)SymbolInfoInteger(sym, SYMBOL_DIGITS)));
}

//--- 業者のロット刻みに合わせて切り下げ、最小・最大の範囲に収める。
double NormalizeLot(const string sym, const double lot)
{
   double mn   = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double mx   = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   if(step <= 0.0) step = 0.01;
   if(mn   <= 0.0) mn   = step;

   double v = MathFloor(lot / step + 1e-8) * step;
   if(v < mn) v = mn;
   if(v > mx && mx > 0.0) v = mx;

   // step の小数桁で丸め、浮動小数の端数を落とす（0.30000000004 対策）
   int    d = 0;
   double s = step;
   while(s < 1.0 && d < 8) { s *= 10.0; d++; }
   return(NormalizeDouble(v, d));
}

//--- いまのスプレッド（pips）
double CurrentSpreadPips(const string sym)
{
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   return(PriceToPips(sym, ask - bid));
}

//--- 業者が決めている「現値からこれ以上離さないと置けない」距離（pips）
double MinStopPips(const string sym)
{
   long   lvl   = SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL);
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   return(PriceToPips(sym, (double)lvl * point));
}

//--- 1ロット・1pip あたりの損益（口座通貨）。損失額の計算に使う。
double PipValuePerLot(const string sym)
{
   double tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   if(tickSize <= 0.0 || tickValue <= 0.0)
      return(0.0);
   return(tickValue * (PipSize(sym) / tickSize));
}

//--- ローソク足まわり（shift は 1 以上＝確定足のみを見る）
double BarOpen (const string s, const ENUM_TIMEFRAMES tf, const int i) { return(iOpen (s, tf, i)); }
double BarHigh (const string s, const ENUM_TIMEFRAMES tf, const int i) { return(iHigh (s, tf, i)); }
double BarLow  (const string s, const ENUM_TIMEFRAMES tf, const int i) { return(iLow  (s, tf, i)); }
double BarClose(const string s, const ENUM_TIMEFRAMES tf, const int i) { return(iClose(s, tf, i)); }

double BodySize(const string s, const ENUM_TIMEFRAMES tf, const int i)
{
   return(MathAbs(iClose(s, tf, i) - iOpen(s, tf, i)));
}

double BarRange(const string s, const ENUM_TIMEFRAMES tf, const int i)
{
   return(iHigh(s, tf, i) - iLow(s, tf, i));
}

double UpperWick(const string s, const ENUM_TIMEFRAMES tf, const int i)
{
   return(iHigh(s, tf, i) - MathMax(iOpen(s, tf, i), iClose(s, tf, i)));
}

double LowerWick(const string s, const ENUM_TIMEFRAMES tf, const int i)
{
   return(MathMin(iOpen(s, tf, i), iClose(s, tf, i)) - iLow(s, tf, i));
}

bool IsBullBar(const string s, const ENUM_TIMEFRAMES tf, const int i)
{
   return(iClose(s, tf, i) > iOpen(s, tf, i));
}

bool IsBearBar(const string s, const ENUM_TIMEFRAMES tf, const int i)
{
   return(iClose(s, tf, i) < iOpen(s, tf, i));
}

//--- 下ヒゲで否定して上位で引けた足（＝逆行ターンの反発）
bool IsBullRejection(const string s, const ENUM_TIMEFRAMES tf, const int i, const double wickRatio)
{
   double rng = BarRange(s, tf, i);
   if(rng <= 0.0) return(false);
   double body = BodySize(s, tf, i);
   double wick = LowerWick(s, tf, i);
   if(wick < wickRatio * MathMax(body, rng * 0.05)) return(false);
   // 終値が足の上から 1/3 以内にある
   return(iClose(s, tf, i) >= iLow(s, tf, i) + rng * 0.6);
}

bool IsBearRejection(const string s, const ENUM_TIMEFRAMES tf, const int i, const double wickRatio)
{
   double rng = BarRange(s, tf, i);
   if(rng <= 0.0) return(false);
   double body = BodySize(s, tf, i);
   double wick = UpperWick(s, tf, i);
   if(wick < wickRatio * MathMax(body, rng * 0.05)) return(false);
   return(iClose(s, tf, i) <= iHigh(s, tf, i) - rng * 0.6);
}

//--- 包み足
bool IsBullEngulf(const string s, const ENUM_TIMEFRAMES tf, const int i)
{
   return(IsBullBar(s, tf, i) && IsBearBar(s, tf, i + 1)
          && iClose(s, tf, i) >= iOpen(s, tf, i + 1)
          && iOpen (s, tf, i) <= iClose(s, tf, i + 1));
}

bool IsBearEngulf(const string s, const ENUM_TIMEFRAMES tf, const int i)
{
   return(IsBearBar(s, tf, i) && IsBullBar(s, tf, i + 1)
          && iClose(s, tf, i) <= iOpen(s, tf, i + 1)
          && iOpen (s, tf, i) >= iClose(s, tf, i + 1));
}

//--- 銘柄サフィックス（USDJPYm の "m"）。別銘柄を探すときに借りる。
string SymbolSuffix(const string sym)
{
   if(StringLen(sym) > 6)
      return(StringSubstr(sym, 6));
   return("");
}

#endif // STEP100MAN_COMMON_MQH
