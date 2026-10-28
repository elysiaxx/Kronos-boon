//+------------------------------------------------------------------+
//|                                               KronosForecast.mq5  |
//|   Draws a Kronos forecast on the chart by reading a CSV written   |
//|   by mt5_backtest/mt5_visualize.py.                               |
//|                                                                   |
//|   Pipeline:                                                       |
//|     Python (Kronos)  ->  kronos_forecast.csv  ->  THIS indicator  |
//|                                                                   |
//|   The CSV lives in the terminal's COMMON files sandbox            |
//|   (<data>/Terminal/Common/Files), opened here with FILE_COMMON.   |
//|   Format (epoch seconds, server time; first row = anchor):        |
//|     time,open,high,low,close                                      |
//|     1730000000,2630.5,2631.0,2630.1,2630.4                        |
//|     ...                                                           |
//+------------------------------------------------------------------+
#property copyright "Kronos"
#property version   "1.00"
#property indicator_chart_window
#property indicator_plots   0
#property indicator_buffers 0

input string InpFile        = "kronos_forecast.csv"; // file in Common\Files
input int    InpRefreshSec  = 5;                      // refresh interval (seconds)
input bool   InpUseCommon   = true;                   // read from Common\Files (match the exporter)
input color  InpCloseColor  = clrDodgerBlue;          // forecast close line
input int    InpLineWidth   = 2;                      // close line width
input bool   InpDrawBand    = true;                   // draw predicted high/low band
input color  InpBandColor   = clrSlateGray;           // band color
input bool   InpDrawCandles = true;                    // draw predicted candles
input color  InpUpColor     = clrSeaGreen;            // predicted bullish candle
input color  InpDownColor   = clrFireBrick;           // predicted bearish candle
input bool   InpMarkStart   = true;                    // vertical line at forecast start
input string InpPrefix      = "Kronos_";              // object name prefix

string g_sig = "";   // signature of the last drawn forecast (to avoid redrawing)

//+------------------------------------------------------------------+
int OnInit()
  {
   EventSetTimer(MathMax(1, InpRefreshSec));
   DrawForecast();
   return(INIT_SUCCEEDED);
  }
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   ObjectsDeleteAll(0, InpPrefix);
   ChartRedraw();
  }
//+------------------------------------------------------------------+
void OnTimer()
  {
   DrawForecast();
  }
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated,
                const datetime &time[], const double &open[],
                const double &high[], const double &low[],
                const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
  {
   // All drawing is done with graphical objects on a timer; nothing to plot.
   return(rates_total);
  }
//+------------------------------------------------------------------+
//| Read CSV + (re)draw if it changed                                |
//+------------------------------------------------------------------+
void DrawForecast()
  {
   int flags = FILE_READ | FILE_TXT | FILE_ANSI;
   if(InpUseCommon)
      flags |= FILE_COMMON;

   int h = FileOpen(InpFile, flags);
   if(h == INVALID_HANDLE)
      return; // file not there yet; try again on next timer

   datetime t[];
   double   o[], hi[], lo[], cl[];
   int n = 0;
   bool header = true;

   while(!FileIsEnding(h))
     {
      string line = FileReadString(h);
      if(StringLen(line) == 0)
         continue;
      string p[];
      int k = StringSplit(line, ',', p);
      if(k < 5)
         continue;
      if(header)
        {
         header = false;         // skip the "time,open,high,low,close" header
         continue;
        }
      ArrayResize(t,  n + 1); ArrayResize(o, n + 1); ArrayResize(hi, n + 1);
      ArrayResize(lo, n + 1); ArrayResize(cl, n + 1);
      t[n]  = (datetime)StringToInteger(p[0]);
      o[n]  = StringToDouble(p[1]);
      hi[n] = StringToDouble(p[2]);
      lo[n] = StringToDouble(p[3]);
      cl[n] = StringToDouble(p[4]);
      n++;
     }
   FileClose(h);

   if(n < 2)
      return;

   // Signature: anchor time + count + last close. Skip redraw if unchanged.
   string sig = (string)t[0] + "_" + (string)n + "_" + DoubleToString(cl[n - 1], 5);
   if(sig == g_sig)
      return;
   g_sig = sig;

   ObjectsDeleteAll(0, InpPrefix);

   // half candle width in seconds (for rectangle bodies)
   long period = (n >= 3) ? (long)(t[2] - t[1]) : (long)(t[1] - t[0]);
   long hw = (long)MathMax(1, period * 0.35);

   // predicted high/low band
   if(InpDrawBand)
     {
      for(int i = 0; i < n - 1; i++)
        {
         TrendSeg(InpPrefix + "band_hi_" + (string)i, t[i], hi[i], t[i + 1], hi[i + 1], InpBandColor, 1, STYLE_DOT);
         TrendSeg(InpPrefix + "band_lo_" + (string)i, t[i], lo[i], t[i + 1], lo[i + 1], InpBandColor, 1, STYLE_DOT);
        }
     }

   // predicted candles (skip anchor row i=0)
   if(InpDrawCandles)
     {
      for(int i = 1; i < n; i++)
        {
         color c = (cl[i] >= o[i]) ? InpUpColor : InpDownColor;
         // wick
         TrendSeg(InpPrefix + "wick_" + (string)i, t[i], hi[i], t[i], lo[i], c, 1, STYLE_SOLID);
         // body as a filled rectangle
         string rname = InpPrefix + "body_" + (string)i;
         ObjectCreate(0, rname, OBJ_RECTANGLE, 0, (datetime)(t[i] - hw), o[i], (datetime)(t[i] + hw), cl[i]);
         ObjectSetInteger(0, rname, OBJPROP_COLOR, c);
         ObjectSetInteger(0, rname, OBJPROP_FILL, true);
         ObjectSetInteger(0, rname, OBJPROP_BACK, true);
         ObjectSetInteger(0, rname, OBJPROP_SELECTABLE, false);
        }
     }

   // forecast close polyline (includes the anchor so it joins the real chart)
   for(int i = 0; i < n - 1; i++)
      TrendSeg(InpPrefix + "close_" + (string)i, t[i], cl[i], t[i + 1], cl[i + 1],
               InpCloseColor, InpLineWidth, STYLE_SOLID);

   // marker + label at the forecast start
   if(InpMarkStart)
     {
      string vname = InpPrefix + "start";
      ObjectCreate(0, vname, OBJ_VLINE, 0, t[0], 0);
      ObjectSetInteger(0, vname, OBJPROP_COLOR, InpCloseColor);
      ObjectSetInteger(0, vname, OBJPROP_STYLE, STYLE_DASH);
      ObjectSetInteger(0, vname, OBJPROP_BACK, true);
      ObjectSetInteger(0, vname, OBJPROP_SELECTABLE, false);
     }
   string lname = InpPrefix + "label";
   ObjectCreate(0, lname, OBJ_TEXT, 0, t[n - 1], cl[n - 1]);
   ObjectSetString(0, lname, OBJPROP_TEXT, "  Kronos +" + (string)(n - 1));
   ObjectSetInteger(0, lname, OBJPROP_COLOR, InpCloseColor);
   ObjectSetInteger(0, lname, OBJPROP_ANCHOR, ANCHOR_LEFT);
   ObjectSetInteger(0, lname, OBJPROP_SELECTABLE, false);

   ChartRedraw();
  }
//+------------------------------------------------------------------+
//| Create a styled 2-point trend segment (ray off)                  |
//+------------------------------------------------------------------+
void TrendSeg(string name, datetime t1, double p1, datetime t2, double p2,
              color clr, int width, ENUM_LINE_STYLE style)
  {
   ObjectCreate(0, name, OBJ_TREND, 0, t1, p1, t2, p2);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
  }
//+------------------------------------------------------------------+
