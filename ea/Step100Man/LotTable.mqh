//+------------------------------------------------------------------+
//| LotTable.mqh                                                     |
//| 画像「資金ごとのロット目安（40-50pips損切り値）」をそのまま持つ。    |
//|                                                                  |
//| 画像の表は 1万円以降おおむね「1万円あたり 0.003 ロット」で並ぶが、   |
//| 5千円(0.02)・7万円(0.20)・75万円(2.10) の3段だけその比率から外れる。 |
//| ここは直さず画像どおりに再現している（忠実再現が目的のため）。       |
//| 直線で揃えたい場合は LotTierVol[] を書き換えること。                |
//+------------------------------------------------------------------+
#ifndef STEP100MAN_LOTTABLE_MQH
#define STEP100MAN_LOTTABLE_MQH

#include "Common.mqh"

#define LOT_TIER_COUNT 15

const double LotTierJpy[LOT_TIER_COUNT] =
{
     5000,   10000,   20000,   30000,   50000,
    70000,  100000,  150000,  200000,  300000,
   500000,  600000,  750000,  900000, 1000000
};

const double LotTierVol[LOT_TIER_COUNT] =
{
     0.02,    0.03,    0.06,    0.09,    0.15,
     0.20,    0.30,    0.45,    0.60,    0.90,
     1.50,    1.80,    2.10,    2.70,    3.00
};

//+------------------------------------------------------------------+
//| 口座資金（円）→ 推奨ロット。                                      |
//| その資金以下で一番大きい段を採用する（表の「目安」の読み方）。      |
//| extrapolate=true なら 100万円超は同じ比率で伸ばす。                |
//+------------------------------------------------------------------+
double LotForBalanceJpy(const double balanceJpy, const bool extrapolate)
{
   if(balanceJpy < LotTierJpy[0])
      return(LotTierVol[0]);                       // 5千円未満も最小段で扱う

   for(int i = LOT_TIER_COUNT - 1; i >= 0; i--)
   {
      if(balanceJpy >= LotTierJpy[i])
      {
         if(i == LOT_TIER_COUNT - 1 && extrapolate && balanceJpy > LotTierJpy[i])
            return(LotTierVol[i] * (balanceJpy / LotTierJpy[i]));
         return(LotTierVol[i]);
      }
   }
   return(LotTierVol[0]);
}

//--- いまの段が表の何段目か（ログ表示用。見つからなければ -1）
int LotTierIndex(const double balanceJpy)
{
   for(int i = LOT_TIER_COUNT - 1; i >= 0; i--)
      if(balanceJpy >= LotTierJpy[i])
         return(i);
   return(-1);
}

//+------------------------------------------------------------------+
//| 口座通貨 → 円 のレート。                                          |
//| 円口座なら 1.0。それ以外は <通貨>JPY の気配を探し、               |
//| 見つからなければ manualRate を使う。                              |
//+------------------------------------------------------------------+
double AccountToJpyRate(const double manualRate)
{
   string cur = AccountInfoString(ACCOUNT_CURRENCY);
   if(cur == "JPY")
      return(1.0);

   string cand = cur + "JPY";
   if(SymbolSelect(cand, true))
   {
      double bid = SymbolInfoDouble(cand, SYMBOL_BID);
      if(bid > 0.0) return(bid);
   }

   // 業者のサフィックス付き（USDJPYm など）も試す
   string suffix = SymbolSuffix(_Symbol);
   if(suffix != "")
   {
      cand = cur + "JPY" + suffix;
      if(SymbolSelect(cand, true))
      {
         double bid2 = SymbolInfoDouble(cand, SYMBOL_BID);
         if(bid2 > 0.0) return(bid2);
      }
   }
   return(manualRate);
}

//+------------------------------------------------------------------+
//| 口座の 2% などを上限にロットを抑える（画像には無い安全装置）。      |
//| capPercent <= 0 なら何もしない＝表のまま。                        |
//+------------------------------------------------------------------+
double ApplyRiskCap(const string sym, const double lot, const double slPips,
                    const double capPercent, const double accountValue)
{
   if(capPercent <= 0.0 || slPips <= 0.0)
      return(lot);

   double pipVal = PipValuePerLot(sym);
   if(pipVal <= 0.0)
      return(lot);                                  // 値が取れないときは触らない

   double maxLoss = accountValue * capPercent / 100.0;
   double capLot  = maxLoss / (slPips * pipVal);
   return(MathMin(lot, capLot));
}

//--- そのロットで損切りに当たったときの損失額（口座通貨）
double LossAtStop(const string sym, const double lot, const double slPips)
{
   return(lot * slPips * PipValuePerLot(sym));
}

#endif // STEP100MAN_LOTTABLE_MQH
