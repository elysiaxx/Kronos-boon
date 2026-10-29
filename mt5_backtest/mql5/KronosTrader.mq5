//+------------------------------------------------------------------+
//|                                                 KronosTrader.mq5  |
//|   Places the orders a NATIVE (non-Wine) Python/Kronos process     |
//|   asks for, so the whole loop runs on macOS where the Windows-only|
//|   MetaTrader5 Python package is unavailable.                      |
//|                                                                   |
//|   Full bridge, all three files in Common\Files:                   |
//|     KronosExport  : bars          -> kronos_bars.csv              |
//|     Python(Kronos): reads bars    -> kronos_signal.csv            |
//|     THIS EA       : reads signal  -> OrderSend(), and writes back |
//|                     kronos_status.csv (account, position, fills)  |
//|     Python        : reads status  -> logs equity and real PnL     |
//|                                                                   |
//|   The signal is a TARGET NET POSITION, not an order: +1 long,     |
//|   -1 short, 0 flat. The EA moves the position to that target, so  |
//|   a duplicated or re-read signal can never double up.             |
//|                                                                   |
//|   Signal CSV (written atomically by Python):                      |
//|     id,time,symbol,target,volume                                  |
//|     7,1730200000,XAUUSD,-1,0.01                                   |
//|                                                                   |
//|   Safety: refuses REAL accounts unless InpAllowReal, caps volume, |
//|   ignores stale signals, and only ever touches its own magic.     |
//+------------------------------------------------------------------+
#property copyright "Kronos"
#property version   "1.00"

#include <Trade\Trade.mqh>

input string InpSignalFile      = "kronos_signal.csv";  // signal file in Common\Files
input string InpStatusFile      = "kronos_status.csv";  // status file in Common\Files
input bool   InpUseCommon       = true;                 // use Common\Files (match Python)
input int    InpPollSec         = 2;                    // how often to check the signal
input double InpMaxVolume       = 0.10;                 // hard cap on lots per position
input int    InpMaxSignalAgeSec = 1800;                 // ignore signals older than this
input int    InpMaxRetries      = 3;                    // attempts per signal before giving up
input long   InpMagic           = 20250826;             // only touch positions with this magic
input int    InpDeviation       = 20;                   // max slippage in points
input bool   InpAllowReal       = false;                // permit a REAL account (danger)

CTrade   g_trade;
long     g_last_id      = -1;      // signal id already acted on
int      g_attempts     = 0;       // attempts spent on g_last_id
bool     g_blocked      = false;   // refusing to trade (e.g. real account)
string   g_block_reason = "";
string   g_last_error   = "";

// last executed action, reported back to Python
string   g_last_action  = "none";
double   g_last_price   = 0.0;
datetime g_last_time    = 0;
double   g_close_profit = 0.0;     // broker PnL of the last close, incl. swap/commission
double   g_close_entry  = 0.0;
datetime g_close_opened = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpDeviation);
   g_trade.SetTypeFillingBySymbol(_Symbol);

   long mode = AccountInfoInteger(ACCOUNT_TRADE_MODE);
   if(mode == ACCOUNT_TRADE_MODE_REAL && !InpAllowReal)
     {
      g_blocked = true;
      g_block_reason = "REAL account refused; this EA is meant for demo. Set InpAllowReal to override.";
      Print("KronosTrader: ", g_block_reason);
     }
   else
     {
      PrintFormat("KronosTrader: account=%I64d mode=%s symbol=%s magic=%I64d",
                  AccountInfoInteger(ACCOUNT_LOGIN), TradeModeName(mode), _Symbol, InpMagic);
     }

   EventSetTimer(MathMax(1, InpPollSec));
   WriteStatus();
   return(INIT_SUCCEEDED);
  }
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   g_last_error = "EA removed from chart";
   WriteStatus();
  }
//+------------------------------------------------------------------+
void OnTimer()
  {
   ProcessSignal();
   WriteStatus();
  }
//+------------------------------------------------------------------+
//| Ticks react faster than the timer, but reading the signal file on |
//| every tick of a busy symbol is wasteful - throttle to ~1/second.  |
//+------------------------------------------------------------------+
void OnTick()
  {
   static uint last_check = 0;
   uint now = GetTickCount();
   if(now - last_check < 1000)
      return;
   last_check = now;
   ProcessSignal();
  }
//+------------------------------------------------------------------+
string TradeModeName(const long mode)
  {
   if(mode == ACCOUNT_TRADE_MODE_DEMO)    return("DEMO");
   if(mode == ACCOUNT_TRADE_MODE_CONTEST) return("CONTEST");
   if(mode == ACCOUNT_TRADE_MODE_REAL)    return("REAL");
   return("UNKNOWN");
  }
//+------------------------------------------------------------------+
//| Find this EA's position in the chart symbol. Returns the ticket,  |
//| or 0 when flat; side/volume/price/opened are filled in.           |
//+------------------------------------------------------------------+
ulong FindPosition(int &side, double &volume, double &price, datetime &opened)
  {
   side = 0; volume = 0.0; price = 0.0; opened = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol)
         continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      side   = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
      volume = PositionGetDouble(POSITION_VOLUME);
      price  = PositionGetDouble(POSITION_PRICE_OPEN);
      opened = (datetime)PositionGetInteger(POSITION_TIME);
      return(ticket);
     }
   return(0);
  }
//+------------------------------------------------------------------+
double NormalizeVolume(double volume)
  {
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(step <= 0.0) step = 0.01;
   if(vmax <= 0.0) vmax = InpMaxVolume;
   double v = MathMin(MathMin(volume, InpMaxVolume), vmax);
   v = MathRound(v / step) * step;
   if(v < vmin) v = vmin;
   return(NormalizeDouble(v, 2));
  }
//+------------------------------------------------------------------+
//| Realized PnL of a close, taken from the broker's own deal record  |
//| so swap and commission are included.                              |
//+------------------------------------------------------------------+
double DealProfit(const ulong deal_ticket)
  {
   if(deal_ticket == 0)
      return(0.0);
   if(!HistorySelect(TimeCurrent() - 86400, TimeCurrent() + 60))
      return(0.0);
   if(!HistoryDealSelect(deal_ticket))
      return(0.0);
   return(HistoryDealGetDouble(deal_ticket, DEAL_PROFIT)
        + HistoryDealGetDouble(deal_ticket, DEAL_SWAP)
        + HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION));
  }
//+------------------------------------------------------------------+
//| Read the signal file. Returns false when there is nothing usable. |
//+------------------------------------------------------------------+
bool ReadSignal(long &id, datetime &stamp, string &symbol, int &target, double &volume)
  {
   int flags = FILE_READ | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE;
   if(InpUseCommon)
      flags |= FILE_COMMON;

   if(!FileIsExist(InpSignalFile, InpUseCommon ? FILE_COMMON : 0))
      return(false);
   int h = FileOpen(InpSignalFile, flags);
   if(h == INVALID_HANDLE)
      return(false);

   string last = "";
   while(!FileIsEnding(h))
     {
      string line = FileReadString(h);
      StringTrimLeft(line);
      StringTrimRight(line);
      if(StringLen(line) > 0 && StringFind(line, "id,") != 0)
         last = line;              // keep the last non-header row
     }
   FileClose(h);
   if(StringLen(last) == 0)
      return(false);

   string parts[];
   if(StringSplit(last, ',', parts) < 5)
      return(false);
   id     = (long)StringToInteger(parts[0]);
   stamp  = (datetime)StringToInteger(parts[1]);
   symbol = parts[2];
   target = (int)StringToInteger(parts[3]);
   volume = StringToDouble(parts[4]);
   return(true);
  }
//+------------------------------------------------------------------+
void ProcessSignal()
  {
   if(g_blocked)
      return;

   long     id = 0, nowsec = (long)TimeCurrent();
   datetime stamp = 0;
   string   symbol = "";
   int      target = 0;
   double   volume = 0.0;
   if(!ReadSignal(id, stamp, symbol, target, volume))
      return;

   if(symbol != _Symbol)
     {
      g_last_error = StringFormat("signal is for %s, this chart is %s", symbol, _Symbol);
      return;
     }
   if(InpMaxSignalAgeSec > 0 && nowsec - (long)stamp > InpMaxSignalAgeSec)
     {
      g_last_error = StringFormat("signal %I64d is %I64d s old; ignored", id, nowsec - (long)stamp);
      return;
     }
   if(id == g_last_id && g_attempts >= InpMaxRetries)
      return;                       // already handled (or given up on) this signal
   if(id != g_last_id)
     {
      g_last_id = id;
      g_attempts = 0;
     }

   int      side = 0;
   double   pos_vol = 0.0, pos_price = 0.0;
   datetime opened = 0;
   ulong    ticket = FindPosition(side, pos_vol, pos_price, opened);

   if(side == target)
     {
      g_attempts = InpMaxRetries;   // nothing to do; stop re-checking this signal
      g_last_error = "";
      return;
     }
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED) || !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
     {
      g_last_error = "AutoTrading is off (toolbar button / EA properties)";
      return;
     }

   g_attempts++;
   bool ok = true;

   // Close whatever is open first, so a flip is always close-then-open.
   if(side != 0)
     {
      g_close_entry  = pos_price;
      g_close_opened = opened;
      ok = g_trade.PositionClose(ticket, InpDeviation);
      if(ok)
        {
         g_close_profit = DealProfit(g_trade.ResultDeal());
         g_last_action  = "close";
         g_last_price   = g_trade.ResultPrice();
         g_last_time    = TimeCurrent();
         PrintFormat("KronosTrader: closed %s %.2f @ %.5f  pnl=%.2f",
                     side > 0 ? "long" : "short", pos_vol, g_last_price, g_close_profit);
        }
      else
        {
         g_last_error = StringFormat("close failed: %d %s", g_trade.ResultRetcode(),
                                     g_trade.ResultRetcodeDescription());
         Print("KronosTrader: ", g_last_error);
         return;
        }
     }

   if(target != 0)
     {
      double vol = NormalizeVolume(volume);
      ok = (target > 0) ? g_trade.Buy(vol, _Symbol, 0.0, 0.0, 0.0, "kronos")
                        : g_trade.Sell(vol, _Symbol, 0.0, 0.0, 0.0, "kronos");
      if(ok)
        {
         g_last_action = (target > 0) ? "open_long" : "open_short";
         g_last_price  = g_trade.ResultPrice();
         g_last_time   = TimeCurrent();
         PrintFormat("KronosTrader: opened %s %.2f @ %.5f",
                     target > 0 ? "long" : "short", vol, g_last_price);
        }
      else
        {
         g_last_error = StringFormat("open failed: %d %s", g_trade.ResultRetcode(),
                                     g_trade.ResultRetcodeDescription());
         Print("KronosTrader: ", g_last_error);
         return;
        }
     }

   g_attempts = InpMaxRetries;      // signal satisfied
   g_last_error = "";
  }
//+------------------------------------------------------------------+
//| Status is written to a temp file and moved into place, so Python  |
//| never reads a half-written file.                                  |
//+------------------------------------------------------------------+
void WriteStatus()
  {
   int flags = FILE_WRITE | FILE_TXT | FILE_ANSI;
   if(InpUseCommon)
      flags |= FILE_COMMON;

   string tmp = InpStatusFile + ".tmp";
   int h = FileOpen(tmp, flags);
   if(h == INVALID_HANDLE)
      return;

   int      side = 0;
   double   pos_vol = 0.0, pos_price = 0.0;
   datetime opened = 0;
   ulong    ticket = FindPosition(side, pos_vol, pos_price, opened);
   double   pos_profit = 0.0;
   if(ticket != 0 && PositionSelectByTicket(ticket))
      pos_profit = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);

   string blocked_note = g_blocked ? g_block_reason : "";
   string err = StringLen(blocked_note) > 0 ? blocked_note : g_last_error;

   FileWriteString(h, StringFormat("time=%I64d\n", (long)TimeCurrent()));
   FileWriteString(h, StringFormat("symbol=%s\n", _Symbol));
   FileWriteString(h, StringFormat("account=%I64d\n", AccountInfoInteger(ACCOUNT_LOGIN)));
   FileWriteString(h, StringFormat("server=%s\n", AccountInfoString(ACCOUNT_SERVER)));
   FileWriteString(h, StringFormat("trade_mode=%s\n", TradeModeName(AccountInfoInteger(ACCOUNT_TRADE_MODE))));
   FileWriteString(h, StringFormat("currency=%s\n", AccountInfoString(ACCOUNT_CURRENCY)));
   FileWriteString(h, StringFormat("balance=%.2f\n", AccountInfoDouble(ACCOUNT_BALANCE)));
   FileWriteString(h, StringFormat("equity=%.2f\n", AccountInfoDouble(ACCOUNT_EQUITY)));
   FileWriteString(h, StringFormat("margin_free=%.2f\n", AccountInfoDouble(ACCOUNT_MARGIN_FREE)));
   FileWriteString(h, StringFormat("autotrading=%d\n",
                   (MQLInfoInteger(MQL_TRADE_ALLOWED) && TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) ? 1 : 0));
   FileWriteString(h, StringFormat("blocked=%d\n", g_blocked ? 1 : 0));
   FileWriteString(h, StringFormat("pos_side=%d\n", side));
   FileWriteString(h, StringFormat("pos_volume=%.2f\n", pos_vol));
   FileWriteString(h, StringFormat("pos_price=%.5f\n", pos_price));
   FileWriteString(h, StringFormat("pos_time=%I64d\n", (long)opened));
   FileWriteString(h, StringFormat("pos_profit=%.2f\n", pos_profit));
   FileWriteString(h, StringFormat("last_signal_id=%I64d\n", g_last_id));
   FileWriteString(h, StringFormat("last_action=%s\n", g_last_action));
   FileWriteString(h, StringFormat("last_price=%.5f\n", g_last_price));
   FileWriteString(h, StringFormat("last_time=%I64d\n", (long)g_last_time));
   FileWriteString(h, StringFormat("close_profit=%.2f\n", g_close_profit));
   FileWriteString(h, StringFormat("close_entry=%.5f\n", g_close_entry));
   FileWriteString(h, StringFormat("close_opened=%I64d\n", (long)g_close_opened));
   FileWriteString(h, StringFormat("error=%s\n", err));
   FileClose(h);

   int common = InpUseCommon ? FILE_COMMON : 0;
   FileDelete(InpStatusFile, common);
   FileMove(tmp, common, InpStatusFile, common | FILE_REWRITE);
  }
//+------------------------------------------------------------------+
