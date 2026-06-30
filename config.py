"""
Configuration for the forex signal platform.
Fill in OANDA_API_TOKEN and OANDA_ACCOUNT_ID once you have practice (demo) credentials.
NEVER commit real tokens to a public repo -- use environment variables in production.
"""

import os

# --- OANDA connection ---
# Practice (demo) environment. Do NOT point this at api-fxtrade.oanda.com (live) for testing.
OANDA_API_URL = "https://api-fxpractice.oanda.com"
OANDA_API_TOKEN = os.environ.get("OANDA_API_TOKEN", "1bdbd0230f9a435ddb8d3aad4e24b7c2-1fde65296db1a2b6ca48109bb30878ad")
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID", "101-001-39693502-001")

# --- Watchlist ---
# Start with liquid majors -- tight spreads matter most for same-day/scalp trading.
WATCHLIST = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD"]

# --- Candle settings ---
CANDLE_GRANULARITY = "M15"   # 15-minute candles, matches same-day trading style
CANDLE_COUNT = 200           # how many candles to pull per check (enough history for 200-period calcs)

# --- Signal parameters (defaults; adjustable later via Settings screen) ---
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

STOCH_K_PERIOD = 14
STOCH_D_PERIOD = 3
STOCH_OVERBOUGHT = 80
STOCH_OVERSOLD = 20

CCI_PERIOD = 20
CCI_OVERBOUGHT = 100
CCI_OVERSOLD = -100

BBANDS_PERIOD = 20
BBANDS_STDDEV = 2

ATR_PERIOD = 14

SR_LOOKBACK = 50           # candles to scan for support/resistance levels

# --- Stop-loss defaults ---
ATR_STOP_MULTIPLIER = 1.5   # default stop = 1.5x ATR from entry
TRAILING_STOP_ENABLED_DEFAULT = True

# --- Session windows (Eastern Time, 24h) ---
# Highlight when liquidity/volatility is best for same-day trading
LONDON_SESSION = ("03:00", "12:00")
NEWYORK_SESSION = ("08:00", "17:00")
OVERLAP_SESSION = ("08:00", "12:00")  # best window for volume + movement

# --- Supabase (reuse existing project) ---
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
