//+------------------------------------------------------------------+
//| Manager.mqh                                                      |
//| 画像「利確・損切りのルール」の実行部分。                           |
//|   ・損切りは必ず置く（置けなければ即撤退）                         |
//|   ・分割利確（半分決済→残りを伸ばす）                             |
//|   ・30pips 程度の利益後に建値へ SL 移動                           |
//|   ・EN値の根拠が崩れたらすぐ撤退                                   |
//|   ・残りはトレーリングで伸ばす                                     |
//|                                                                  |
//| 建玉ごとの進捗（分割済み／建値済み／根拠の線）はターミナルの        |
//| グローバル変数に持たせる。EA を入れ直しても続きから動かせる。       |
//+------------------------------------------------------------------+
#ifndef STEP100MAN_MANAGER_MQH
#define STEP100MAN_MANAGER_MQH

#include <Trade\Trade.mqh>
#include "Common.mqh"

#define S100M_GV "S100M_"

string GvKey(const string tag, const ulong ticket)
{
   return(S100M_GV + tag + "_" + (string)ticket);
}

void GvSet(const string tag, const ulong ticket, const double v)
{
   GlobalVariableSet(GvKey(tag, ticket), v);
}

double GvGet(const string tag, const ulong ticket, const double def)
{
   string k = GvKey(tag, ticket);
   if(!GlobalVariableCheck(k)) return(def);
   return(GlobalVariableGet(k));
}

void GvDelTicket(const ulong ticket)
{
   string tags[4] = {"P", "B", "L", "A"};
   for(int i = 0; i < 4; i++)
   {
      string k = GvKey(tags[i], ticket);
      if(GlobalVariableCheck(k)) GlobalVariableDel(k);
   }
}

//--- 閉じた建玉ぶんのグローバル変数を片付ける
void GvCleanup()
{
   for(int i = GlobalVariablesTotal() - 1; i >= 0; i--)
   {
      string name = GlobalVariableName(i);
      if(StringFind(name, S100M_GV) != 0) continue;

      string parts[];
      if(StringSplit(name, '_', parts) < 3) continue;
      ulong ticket = (ulong)StringToInteger(parts[ArraySize(parts) - 1]);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket))
         GlobalVariableDel(name);
   }
}

//+------------------------------------------------------------------+
//| 管理の設定                                                        |
//+------------------------------------------------------------------+
struct ManageCfg
{
   double slPips;
   double tpPips;
   bool   usePartial;
   double partialAtPips;
   double partialPercent;
   bool   useBreakEven;
   double breakEvenAtPips;
   double breakEvenBufferPips;
   bool   useTrailing;
   double trailStartPips;
   double trailStepPips;
   double invalidationPips;    // 0 なら根拠崩れでの撤退をしない
};

//--- SL/TP が業者の最小距離を満たすように押し広げる
double ClampStop(const string sym, const double price, const double stop, const bool isStopLoss, const int dir)
{
   double minDist = PipsToPrice(sym, MinStopPips(sym));
   if(minDist <= 0.0) return(stop);

   double s = stop;
   if((isStopLoss && dir > 0) || (!isStopLoss && dir < 0))
   {
      if(price - s < minDist) s = price - minDist;      // 現値より下に置くもの
   }
   else
   {
      if(s - price < minDist) s = price + minDist;      // 現値より上に置くもの
   }
   return(NormalizePrice(sym, s));
}

//+------------------------------------------------------------------+
//| 新規エントリー。SL が付かなければ建玉を残さない。                 |
//+------------------------------------------------------------------+
bool OpenTrade(CTrade &trade, const string sym, const long magic, const int dir,
               const double lot, const ManageCfg &cfg, const double level, const bool isAddOn,
               const string comment, ulong &ticketOut)
{
   ticketOut = 0;
   if(lot <= 0.0) return(false);

   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double entry = (dir > 0) ? ask : bid;
   if(entry <= 0.0) return(false);

   double slRaw = (dir > 0) ? entry - PipsToPrice(sym, cfg.slPips)
                            : entry + PipsToPrice(sym, cfg.slPips);
   double tpRaw = (dir > 0) ? entry + PipsToPrice(sym, cfg.tpPips)
                            : entry - PipsToPrice(sym, cfg.tpPips);

   double sl = ClampStop(sym, entry, slRaw, true,  dir);
   double tp = ClampStop(sym, entry, tpRaw, false, dir);

   bool ok = (dir > 0) ? trade.Buy (lot, sym, 0.0, sl, tp, comment)
                       : trade.Sell(lot, sym, 0.0, sl, tp, comment);
   if(!ok)
   {
      PrintFormat("[Step100Man] 発注失敗 retcode=%d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
      return(false);
   }

   // 約定(deal)から position id を引くのが一番確実。駄目なら order→走査の順に落とす。
   ulong ticket = 0;
   ulong deal   = trade.ResultDeal();
   if(deal != 0 && HistoryDealSelect(deal))
      ticket = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
   if(ticket == 0) ticket = trade.ResultOrder();
   if(!PositionSelectByTicket(ticket))
   {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
      {
         ulong t = PositionGetTicket(i);
         if(t == 0) continue;
         if(PositionGetString(POSITION_SYMBOL) != sym) continue;
         if(PositionGetInteger(POSITION_MAGIC) != magic) continue;
         ticket = t;
         break;
      }
   }

   // 「必ず損切りを設定しておく」— 付いていなければ付け直し、それも駄目なら閉じる
   if(PositionSelectByTicket(ticket))
   {
      if(PositionGetDouble(POSITION_SL) == 0.0)
      {
         if(!trade.PositionModify(ticket, sl, tp))
         {
            PrintFormat("[Step100Man] SL を設定できなかったため建玉を閉じる ticket=%I64u", ticket);
            trade.PositionClose(ticket);
            return(false);
         }
      }
   }

   ticketOut = ticket;
   GvSet("P", ticket, 0.0);
   GvSet("B", ticket, 0.0);
   GvSet("L", ticket, level);
   GvSet("A", ticket, isAddOn ? 1.0 : 0.0);
   return(true);
}

//+------------------------------------------------------------------+
//| 建玉の面倒を見る。毎ティック呼ぶ。                                |
//+------------------------------------------------------------------+
void ManagePositions(CTrade &trade, const string sym, const long magic, const ManageCfg &cfg)
{
   double pip = PipSize(sym);
   if(pip <= 0.0) return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != sym) continue;
      if(PositionGetInteger(POSITION_MAGIC) != magic) continue;

      long   type   = PositionGetInteger(POSITION_TYPE);
      int    dir    = (type == POSITION_TYPE_BUY) ? 1 : -1;
      double open   = PositionGetDouble(POSITION_PRICE_OPEN);
      double vol    = PositionGetDouble(POSITION_VOLUME);
      double sl     = PositionGetDouble(POSITION_SL);
      double tp     = PositionGetDouble(POSITION_TP);
      double price  = (dir > 0) ? SymbolInfoDouble(sym, SYMBOL_BID)
                                : SymbolInfoDouble(sym, SYMBOL_ASK);
      double gain   = (dir > 0) ? (price - open) : (open - price);
      double gainP  = gain / pip;

      //--- 損切りが外れていたら付け直す（最優先）
      if(sl == 0.0)
      {
         double fix = (dir > 0) ? open - PipsToPrice(sym, cfg.slPips)
                                : open + PipsToPrice(sym, cfg.slPips);
         fix = ClampStop(sym, price, fix, true, dir);
         if(trade.PositionModify(ticket, fix, tp)) sl = fix;
         else continue;
      }

      //--- EN値の根拠が崩れたら即撤退
      if(cfg.invalidationPips > 0.0)
      {
         double level = GvGet("L", ticket, 0.0);
         if(level > 0.0)
         {
            double bust = PipsToPrice(sym, cfg.invalidationPips);
            bool broken = (dir > 0) ? (price < level - bust) : (price > level + bust);
            if(broken && gainP < 0.0)
            {
               PrintFormat("[Step100Man] 根拠崩れで撤退 ticket=%I64u level=%s",
                           ticket, DoubleToString(level, _Digits));
               if(trade.PositionClose(ticket)) { GvDelTicket(ticket); continue; }
            }
         }
      }

      //--- 分割利確（半分決済 → 残りを伸ばす）
      if(cfg.usePartial && GvGet("P", ticket, 0.0) < 0.5 && gainP >= cfg.partialAtPips)
      {
         double minLot = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
         double part   = NormalizeLot(sym, vol * cfg.partialPercent / 100.0);
         double rest   = NormalizeLot(sym, vol - part);
         if(part >= minLot && rest >= minLot)
         {
            if(trade.PositionClosePartial(ticket, part))
            {
               GvSet("P", ticket, 1.0);
               PrintFormat("[Step100Man] 分割利確 %.2f lot (+%.1f pips) ticket=%I64u",
                           part, gainP, ticket);
               if(!PositionSelectByTicket(ticket)) { GvDelTicket(ticket); continue; }
               vol = PositionGetDouble(POSITION_VOLUME);
            }
         }
         else
         {
            GvSet("P", ticket, 1.0);   // 割れないロットなので分割は飛ばす
         }
      }

      //--- 建値へ SL 移動
      if(cfg.useBreakEven && GvGet("B", ticket, 0.0) < 0.5 && gainP >= cfg.breakEvenAtPips)
      {
         double be = (dir > 0) ? open + PipsToPrice(sym, cfg.breakEvenBufferPips)
                               : open - PipsToPrice(sym, cfg.breakEvenBufferPips);
         be = NormalizePrice(sym, be);
         bool better = (dir > 0) ? (be > sl) : (be < sl);
         if(better)
         {
            double safe = ClampStop(sym, price, be, true, dir);
            bool stillBetter = (dir > 0) ? (safe > sl) : (safe < sl);
            if(stillBetter && trade.PositionModify(ticket, safe, tp))
            {
               sl = safe;
               GvSet("B", ticket, 1.0);
               PrintFormat("[Step100Man] 建値へSL移動 (+%.1f pips) ticket=%I64u", gainP, ticket);
            }
         }
      }

      //--- 残りをトレーリングで伸ばす
      if(cfg.useTrailing && gainP >= cfg.trailStartPips)
      {
         double trail = (dir > 0) ? price - PipsToPrice(sym, cfg.trailStepPips)
                                  : price + PipsToPrice(sym, cfg.trailStepPips);
         trail = NormalizePrice(sym, trail);
         bool better = (dir > 0) ? (trail > sl) : (trail < sl);
         if(better)
         {
            double safe = ClampStop(sym, price, trail, true, dir);
            bool stillBetter = (dir > 0) ? (safe > sl) : (safe < sl);
            if(stillBetter) trade.PositionModify(ticket, safe, tp);
         }
      }
   }

   GvCleanup();
}

//--- この EA の建玉をすべて閉じる（1日の損失上限に当たったとき）
int CloseAll(CTrade &trade, const string sym, const long magic)
{
   int closed = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != sym) continue;
      if(PositionGetInteger(POSITION_MAGIC) != magic) continue;
      if(trade.PositionClose(ticket)) { GvDelTicket(ticket); closed++; }
   }
   return(closed);
}

//--- 同じ向きの建玉がすべて建値以上に進んでいるか（追加エントリーの条件）
bool AllInProfitOrBreakEven(const string sym, const long magic, const int dir)
{
   bool any = false;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != sym) continue;
      if(PositionGetInteger(POSITION_MAGIC) != magic) continue;

      long type = PositionGetInteger(POSITION_TYPE);
      int  pdir = (type == POSITION_TYPE_BUY) ? 1 : -1;
      if(pdir != dir) continue;
      any = true;
      if(GvGet("B", ticket, 0.0) < 0.5) return(false);   // まだ建値に移していない
   }
   return(any);
}

#endif // STEP100MAN_MANAGER_MQH
