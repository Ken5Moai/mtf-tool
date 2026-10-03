//+------------------------------------------------------------------+
//| Filters.mqh                                                      |
//| 画像「注意点」をそのまま門番にしたもの。                           |
//|   ・経済指標前後は無理に入らない                                   |
//|   ・感情でエントリーしない（＝1日の回数と損失に上限を置く）        |
//|   ・1回の負けで大きくロットを上げない（Manager 側で保証）          |
//| スプレッド上限と時間帯フィルターは実運用のための追加。             |
//+------------------------------------------------------------------+
#ifndef STEP100MAN_FILTERS_MQH
#define STEP100MAN_FILTERS_MQH

#include "Common.mqh"

//--- その日の 00:00（サーバー時刻）
datetime StartOfDay(const datetime t)
{
   MqlDateTime d;
   TimeToStruct(t, d);
   d.hour = 0; d.min = 0; d.sec = 0;
   return(StructToTime(d));
}

//+------------------------------------------------------------------+
//| 今日の確定損益と約定回数（この EA のマジックナンバーぶんだけ）     |
//+------------------------------------------------------------------+
bool TodayStats(const string sym, const long magic, double &profit, int &closedDeals)
{
   profit = 0.0; closedDeals = 0;
   datetime from = StartOfDay(TimeCurrent());
   if(!HistorySelect(from, TimeCurrent() + 1))
      return(false);

   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      if(HistoryDealGetInteger(ticket, DEAL_MAGIC) != magic) continue;
      if(HistoryDealGetString(ticket, DEAL_SYMBOL) != sym) continue;

      long entry = HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_INOUT && entry != DEAL_ENTRY_OUT_BY)
         continue;

      profit += HistoryDealGetDouble(ticket, DEAL_PROFIT)
              + HistoryDealGetDouble(ticket, DEAL_SWAP)
              + HistoryDealGetDouble(ticket, DEAL_COMMISSION);
      closedDeals++;
   }
   return(true);
}

//--- 今日この EA が新規で入った回数
int TodayEntryCount(const string sym, const long magic)
{
   datetime from = StartOfDay(TimeCurrent());
   if(!HistorySelect(from, TimeCurrent() + 1))
      return(0);

   int n = 0, total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      if(HistoryDealGetInteger(ticket, DEAL_MAGIC) != magic) continue;
      if(HistoryDealGetString(ticket, DEAL_SYMBOL) != sym) continue;
      if(HistoryDealGetInteger(ticket, DEAL_ENTRY) == DEAL_ENTRY_IN) n++;
   }
   return(n);
}

//+------------------------------------------------------------------+
//| 時間帯フィルター。"21:30-23:00,08:00-08:30" のような指定。        |
//| 日またぎ（"23:00-01:00"）も通る。すべてサーバー時刻。             |
//+------------------------------------------------------------------+
bool InBlockedHours(const string csv)
{
   if(StringLen(csv) == 0) return(false);

   MqlDateTime now;
   TimeToStruct(TimeCurrent(), now);
   int cur = now.hour * 60 + now.min;

   string ranges[];
   int n = StringSplit(csv, ',', ranges);
   for(int i = 0; i < n; i++)
   {
      string r = ranges[i];
      StringTrimLeft(r); StringTrimRight(r);
      if(StringLen(r) == 0) continue;

      string parts[];
      if(StringSplit(r, '-', parts) != 2) continue;

      string a[], b[];
      if(StringSplit(parts[0], ':', a) != 2) continue;
      if(StringSplit(parts[1], ':', b) != 2) continue;

      int from = (int)StringToInteger(a[0]) * 60 + (int)StringToInteger(a[1]);
      int to   = (int)StringToInteger(b[0]) * 60 + (int)StringToInteger(b[1]);

      if(from <= to) { if(cur >= from && cur < to) return(true); }
      else           { if(cur >= from || cur < to) return(true); }   // 日またぎ
   }
   return(false);
}

//+------------------------------------------------------------------+
//| 経済指標フィルター（MT5 の内蔵カレンダー）。                      |
//| 取引銘柄の2通貨に関係する指標だけを見る。                         |
//| カレンダーが使えない環境では available=false を返すので、         |
//| 呼び出し側が「止める／通す」を決める。                            |
//+------------------------------------------------------------------+
bool NewsBlackout(const string sym, const int minsBefore, const int minsAfter,
                  const int minImportance, bool &available, string &why)
{
   available = false;
   why       = "";

   string base  = SymbolInfoString(sym, SYMBOL_CURRENCY_BASE);
   string prof  = SymbolInfoString(sym, SYMBOL_CURRENCY_PROFIT);

   datetime now  = TimeCurrent();
   datetime from = now - (datetime)(minsAfter  * 60);
   datetime to   = now + (datetime)(minsBefore * 60);

   MqlCalendarValue values[];
   int n = CalendarValueHistory(values, from, to, NULL, NULL);
   if(n <= 0)
   {
      // 0件なのか、カレンダーが無いのか区別する
      available = (GetLastError() == 0);
      ResetLastError();
      return(false);
   }
   available = true;

   for(int i = 0; i < n; i++)
   {
      MqlCalendarEvent ev;
      if(!CalendarEventById(values[i].event_id, ev)) continue;
      if((int)ev.importance < minImportance) continue;

      MqlCalendarCountry country;
      if(!CalendarCountryById(ev.country_id, country)) continue;
      if(country.currency != base && country.currency != prof) continue;

      why = StringFormat("%s %s（%s）",
                         TimeToString(values[i].time, TIME_MINUTES), ev.name, country.currency);
      return(true);
   }
   return(false);
}

//--- この EA が持っているポジション数
int CountPositions(const string sym, const long magic)
{
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != sym) continue;
      if(PositionGetInteger(POSITION_MAGIC) != magic) continue;
      n++;
   }
   return(n);
}

//--- 同じ向きのポジション数（追加エントリーの上限判定に使う）
int CountPositionsDir(const string sym, const long magic, const int dir)
{
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != sym) continue;
      if(PositionGetInteger(POSITION_MAGIC) != magic) continue;
      long type = PositionGetInteger(POSITION_TYPE);
      if((dir > 0 && type == POSITION_TYPE_BUY) || (dir < 0 && type == POSITION_TYPE_SELL)) n++;
   }
   return(n);
}

#endif // STEP100MAN_FILTERS_MQH
