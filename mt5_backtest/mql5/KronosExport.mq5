//+------------------------------------------------------------------+
//|                                                 KronosExport.mq5  |
//|   Exports the chart's recent bars to a CSV so a NATIVE (non-Wine) |
//|   Python/Kronos process can read them. This removes any need for  |
//|   the Windows-only MetaTrader5 Python package, so the whole bridge |
//|   works on macOS (MT5 under Wine) and Linux too.                  |
//|                                                                   |
//|   Pair it with KronosForecast.mq5 on the same chart:              |
//|     KronosExport   : bars   -> kronos_bars.csv                    |
//|     Python (Kronos): reads bars.csv -> writes kronos_forecast.csv |
//|     KronosForecast : reads forecast.csv -> draws on the chart     |
//|                                                                   |
//|   Writes into the COMMON files sandbox by default                 |
//|   (<data>/Terminal/Common/Files), where the Python side reads it. |
//|   CSV (epoch seconds, server time, oldest -> newest):             |
//|     time,open,high,low,close,tick_volume                          |
//+------------------------------------------------------------------+
#property copyright "Kronos"
#property version   "1.00"
#property indicator_chart_window
#property indicator_plots   0
#property indicator_buffers 0

input int    InpBars      = 512;                  // number of closed bars to export
input string InpFile      = "kronos_bars.csv";    // output file in Common\Files
input bool   InpUseCommon = true;                 // write to Common\Files

datetime g_last_bar = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   g_last_bar = 0;
   return(INIT_SUCCEEDED);
  }
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated,
                const datetime &time[], const double &open[],
                const double &high[], const double &low[],
                const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
  {
   // Arrays here are plain-indexed (index 0 = oldest). The last element is the
   // still-forming bar, so the last CLOSED bar is at rates_total-2.
   if(rates_total < 2)
      return(rates_total);

   datetime newest_closed = time[rates_total - 2];
   if(newest_closed == g_last_bar)
      return(rates_total); // no new bar closed since the last export
   g_last_bar = newest_closed;

   WriteBars(rates_total, time, open, high, low, close, tick_volume);
   return(rates_total);
  }
//+------------------------------------------------------------------+
void WriteBars(const int rates_total, const datetime &time[],
               const double &open[], const double &high[],
               const double &low[], const double &close[], const long &tv[])
  {
   int flags = FILE_WRITE | FILE_TXT | FILE_ANSI;
   if(InpUseCommon)
      flags |= FILE_COMMON;

   int h = FileOpen(InpFile, flags);
   if(h == INVALID_HANDLE)
     {
      Print("KronosExport: cannot open ", InpFile, " err=", GetLastError());
      return;
     }

   FileWriteString(h, "time,open,high,low,close,tick_volume\n");

   int last = rates_total - 2;                 // last closed bar
   int first = MathMax(0, last - InpBars + 1); // oldest bar to export
   for(int i = first; i <= last; i++)
     {
      string row = (string)(long)time[i] + "," +
                   DoubleToString(open[i], _Digits) + "," +
                   DoubleToString(high[i], _Digits) + "," +
                   DoubleToString(low[i], _Digits) + "," +
                   DoubleToString(close[i], _Digits) + "," +
                   (string)tv[i] + "\n";
      FileWriteString(h, row);
     }
   FileClose(h);
  }
//+------------------------------------------------------------------+
