"""
Backtest for the Opening Range Breakout (ORB) Pyramid strategy.

Ruleset simulated (see orb_pyramid_strategy_ruleset.md for full description):
  1. Range = high/low of the first 15-min window after NY session open (8:30 AM ET).
  2. Watch 5-min candles for a CLOSE beyond that range, within 30 min of session open.
     No breakout in that window -> no trade for the day.
  3. Position 1: 10 lots, stop-loss 10 pips from entry.
  4. If price moves +20 pips favorable -> open Position 2 (10 lots) at that price.
     Move the single combined stop to 5 pips below/above Position 2's entry
     (locks +15 pips on Position 1, risks 5 pips on Position 2).
  5. Repeat: every further +20 pip move triggers another 10-lot add, and the
     combined stop moves to 5 pips beyond the newest position's entry.
  6. When the stop is hit, ALL open positions close simultaneously at that price.

CONFIGURABLE FILTERS (all optional, default to original/simplest behavior):
  --breakout-buffer PIPS    Require the close to clear the range by at least this
                             many pips (default 0 = any close beyond the range).
  --min-range PIPS          Skip the day if the 15-min opening range is narrower
                             than this many pips (default 0 = no filter).
  --confirm-candles N       Require N CONSECUTIVE 5-min candles to close beyond
                             the range (same direction) before entering (default 1
                             = original behavior, enter on the first qualifying close).
                             Setting this to 2 requires two consecutive closes beyond
                             the range in the same direction; entry is taken on the
                             close of the confirming (Nth) candle. This still must
                             happen within the 30-minute breakout window.

IMPORTANT MODELING ASSUMPTIONS (read before trusting the output):
  - Entry/management is simulated on 5-minute candles. If both a stop-hit and an
    add-trigger fall inside the same candle, the stop is assumed to hit FIRST
    (conservative assumption - real intrabar path is unknown from OHLC data alone).
  - No spread or slippage is modeled. Fills are assumed at the exact trigger price.
  - Only one trade attempt per instrument per day (no re-entry after a stop-out
    or after a no-trade day).
  - Each add is a flat 10 lots. P&L is reported in pips per position, summed
    across the stack (NOT converted to dollars - pip value depends on lot size
    and account currency, which this script does not model).
  - A trade's management window is capped at 48 hours after entry. If the stop
    still hasn't been hit by then, the trade is marked "still_open_at_cutoff"
    and excluded from win/loss stats (shown separately in the summary).
  - Weekday date range is used as a proxy for trading days (approximate -
    doesn't account for holidays).

Usage:
    python3 backtest_orb_pyramid.py --instruments EUR_USD,GBP_USD --start 2026-01-01 --end 2026-06-30 --confirm-candles 2
"""

import os
import sys
import csv
import time
import argparse
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import config
from oanda_client import OandaClient

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# --- Strategy parameters (match the agreed ruleset; override via CLI if needed) ---
INITIAL_STOP_PIPS = 10
ADD_TRIGGER_PIPS = 20
ADD_STOP_BUFFER_PIPS = 5
MAX_ADDS_SAFETY_CAP = 50  # runaway-loop safety valve, not a strategy rule
BREAKOUT_WINDOW_MINUTES = 30  # includes the 15-min range-forming period
RANGE_MINUTES = 15
MANAGEMENT_CAP_HOURS = 48


def pip_size(instrument):
    return 0.01 if instrument.endswith("_JPY") else 0.0001


def fetch_range_candles(client, instrument, granularity, from_dt_utc, to_dt_utc):
    """
    Pull candles for an explicit UTC time window (OANDA v20 supports from/to).
    Mirrors OandaClient.get_candles but with a date range instead of count.
    """
    url = f"{client.base_url}/v3/instruments/{instrument}/candles"
    params = {
        "granularity": granularity,
        "price": "M",
        "from": from_dt_utc.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
        "to": to_dt_utc.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
    }
    resp = requests.get(url, headers=client.headers, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    rows = []
    for c in data.get("candles", []):
        if not c.get("complete", False):
            continue
        rows.append({
            "time": c["time"],
            "open": float(c["mid"]["o"]),
