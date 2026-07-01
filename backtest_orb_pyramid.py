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
    python3 backtest_orb_pyramid.py --instruments EUR_USD,GBP_USD --start 2026-03-01 --end 2026-06-30

Requires SUPABASE_URL / SUPABASE_SERVICE_KEY are NOT needed here - this script
only talks to OANDA via your existing oanda_client.py and config.py.
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
            "high": float(c["mid"]["h"]),
            "low": float(c["mid"]["l"]),
            "close": float(c["mid"]["c"]),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def ny_session_open_utc(date_obj):
    """Return the UTC datetime for 8:30 AM ET on the given date (DST-aware)."""
    ny_open_naive = datetime.combine(date_obj, dtime(8, 30), tzinfo=NY)
    return ny_open_naive.astimezone(UTC)


def simulate_day(df_m5, session_open_utc, instrument):
    """
    Run the ORB pyramid ruleset for a single day's candle data.
    Returns a dict describing the outcome.
    """
    pip = pip_size(instrument)

    range_start = session_open_utc
    range_end = session_open_utc + timedelta(minutes=RANGE_MINUTES)
    breakout_deadline = session_open_utc + timedelta(minutes=BREAKOUT_WINDOW_MINUTES)

    range_candles = df_m5[(df_m5["time"] >= range_start) & (df_m5["time"] < range_end)]
    if range_candles.empty:
        return {"date": session_open_utc.date(), "result": "no_data"}

    range_high = range_candles["high"].max()
    range_low = range_candles["low"].min()

    entry_candles = df_m5[(df_m5["time"] >= range_end) & (df_m5["time"] < breakout_deadline)]

    direction = None
    entry_price = None
    entry_time = None

    for _, candle in entry_candles.iterrows():
        if candle["close"] > range_high:
            direction = "buy"
            entry_price = candle["close"]
            entry_time = candle["time"]
            break
        elif candle["close"] < range_low:
            direction = "sell"
            entry_price = candle["close"]
            entry_time = candle["time"]
            break

    if direction is None:
        return {
            "date": session_open_utc.date(),
            "result": "no_trade",
            "range_high": range_high,
            "range_low": range_low,
        }

    sign = 1 if direction == "buy" else -1
    stop_price = entry_price - sign * INITIAL_STOP_PIPS * pip
    add_trigger_price = entry_price + sign * ADD_TRIGGER_PIPS * pip
    positions = [{"entry": entry_price, "lots": 10}]

    management_candles = df_m5[df_m5["time"] > entry_time]
    cutoff = entry_time + timedelta(hours=MANAGEMENT_CAP_HOURS)

    exit_price = None
    exit_time = None
    result = "still_open_at_cutoff"

    for _, candle in management_candles.iterrows():
        if candle["time"] > cutoff:
            break

        stop_hit = (candle["low"] <= stop_price) if direction == "buy" else (candle["high"] >= stop_price)
        if stop_hit:
            exit_price = stop_price
            exit_time = candle["time"]
            result = "closed"
            break

        add_hit = (candle["high"] >= add_trigger_price) if direction == "buy" else (candle["low"] <= add_trigger_price)
        if add_hit and len(positions) < MAX_ADDS_SAFETY_CAP:
            new_entry = add_trigger_price
            positions.append({"entry": new_entry, "lots": 10})
            stop_price = new_entry - sign * ADD_STOP_BUFFER_PIPS * pip
            add_trigger_price = new_entry + sign * ADD_TRIGGER_PIPS * pip

    if result == "closed":
        for p in positions:
            p["exit"] = exit_price
            p["pnl_pips"] = round((exit_price - p["entry"]) / pip * sign, 1)
        total_pips = sum(p["pnl_pips"] for p in positions)
    else:
        # Still open at cutoff - report unrealized pips at last known close for reference only
        last_close = management_candles.iloc[-1]["close"] if not management_candles.empty else entry_price
        for p in positions:
            p["exit"] = None
            p["pnl_pips"] = round((last_close - p["entry"]) / pip * sign, 1)
        total_pips = None

    return {
        "date": session_open_utc.date(),
        "result": result,
        "direction": direction,
        "range_high": round(range_high, 5),
        "range_low": round(range_low, 5),
        "entry_price": round(entry_price, 5),
        "entry_time": entry_time,
        "exit_price": round(exit_price, 5) if exit_price else None,
        "exit_time": exit_time,
        "num_positions": len(positions),
        "total_pips": total_pips,
        "positions": positions,
    }


def run_backtest(instrument, start_date, end_date, client):
    results = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:  # Mon-Fri only (approximation, ignores holidays)
            session_open = ny_session_open_utc(current)
            fetch_from = session_open - timedelta(minutes=5)
            fetch_to = session_open + timedelta(hours=MANAGEMENT_CAP_HOURS + 1)
            try:
                df = fetch_range_candles(client, instrument, "M5", fetch_from, fetch_to)
                day_result = simulate_day(df, session_open, instrument)
                results.append(day_result)
            except Exception as e:
                results.append({"date": current, "result": "error", "error": str(e)})
            time.sleep(0.2)  # be polite to OANDA's API
        current += timedelta(days=1)
    return results


def summarize(results, instrument):
    no_data = [r for r in results if r["result"] == "no_data"]
    no_trade = [r for r in results if r["result"] == "no_trade"]
    closed = [r for r in results if r["result"] == "closed"]
    still_open = [r for r in results if r["result"] == "still_open_at_cutoff"]
    errors = [r for r in results if r["result"] == "error"]

    wins = [r for r in closed if r["total_pips"] > 0]
    losses = [r for r in closed if r["total_pips"] <= 0]
    total_pips = sum(r["total_pips"] for r in closed)

    print(f"\n=== Backtest Summary: {instrument} ===")
    print(f"Trading days scanned: {len(results)}")
    print(f"No data / holidays:   {len(no_data)}")
    print(f"No trade (no breakout in 30 min): {len(no_trade)}")
    print(f"Trades taken & closed: {len(closed)}")
    print(f"Still open at 48h cutoff: {len(still_open)}")
    print(f"Errors: {len(errors)}")
    print("---")
    if closed:
        win_rate = len(wins) / len(closed) * 100
        avg_win = sum(r["total_pips"] for r in wins) / len(wins) if wins else 0
        avg_loss = sum(r["total_pips"] for r in losses) / len(losses) if losses else 0
        print(f"Win rate: {win_rate:.1f}%  ({len(wins)}W / {len(losses)}L)")
        print(f"Average winning day: {avg_win:+.1f} pips")
        print(f"Average losing day:  {avg_loss:+.1f} pips")
        print(f"Total pips (summed across all positions, all days): {total_pips:+.1f}")
        max_positions = max(r["num_positions"] for r in closed)
        print(f"Largest pyramid stack reached: {max_positions} positions")
    else:
        print("No closed trades in this window - nothing to summarize.")


def write_csv(results, instrument, output_path):
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "date", "result", "direction", "range_high", "range_low",
            "entry_price", "entry_time", "exit_price", "exit_time",
            "num_positions", "total_pips"
        ])
        for r in results:
            writer.writerow([
                r.get("date"), r.get("result"), r.get("direction"),
                r.get("range_high"), r.get("range_low"),
                r.get("entry_price"), r.get("entry_time"),
                r.get("exit_price"), r.get("exit_time"),
                r.get("num_positions"), r.get("total_pips"),
            ])
    print(f"\nDetailed results written to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ORB Pyramid strategy backtest")
    parser.add_argument("--instruments", default=",".join(config.WATCHLIST),
                         help="Comma-separated OANDA instruments, e.g. EUR_USD,GBP_USD")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--output-dir", default=".", help="Directory to write CSV results")
    args = parser.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    instruments = [i.strip() for i in args.instruments.split(",")]

    client = OandaClient()

    for instrument in instruments:
        print(f"\nRunning backtest for {instrument} from {start_date} to {end_date}...")
        results = run_backtest(instrument, start_date, end_date, client)
        summarize(results, instrument)
        output_path = os.path.join(args.output_dir, f"backtest_{instrument}_{args.start}_{args.end}.csv")
        write_csv(results, instrument, output_path)
