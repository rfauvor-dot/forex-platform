"""
Flask web server for the forex signal dashboard.
"""
from flask import Flask, jsonify, send_from_directory
import os
import sys
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import config
from oanda_client import OandaClient
from signals import compute_all_signals, suggested_stop
from supabase_client import log_signal
from datetime import datetime, timezone

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")
client = OandaClient()

_last_logged = {}
LOG_COOLDOWN_MINUTES = 15

@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")

def get_session_status():
    now_utc = datetime.now(timezone.utc)
    hour = now_utc.hour + now_utc.minute / 60
    hour_et = (hour - 4) % 24
    sessions = []
    if 3 <= hour_et < 12:
        sessions.append("London")
    if 8 <= hour_et < 17:
        sessions.append("New York")
    overlap = "London" in sessions and "New York" in sessions
    return {"active": sessions, "overlap": overlap, "time_et": f"{int(hour_et):02d}:{int((hour_et % 1) * 60):02d} ET"}

@app.route("/api/signals")
def signals():
    results = []
    session = get_session_status()

    try:
        account_balance = client.get_account_summary().get("balance")
    except Exception:
        account_balance = None

    for pair in config.WATCHLIST:
        try:
            df = client.get_candles(pair)
            flags = compute_all_signals(df)
            price_info = client.get_current_price(pair)
            if flags is None:
                results.append({"pair": pair, "error": "Not enough data"})
                continue
            direction = None
            if flags["composite_buy_signal"]:
                direction = "buy"
            elif flags["composite_sell_signal"]:
                direction = "sell"
            stop = suggested_stop(flags["price"], flags["atr_value"], direction) if direction else None

            if direction:
                now = datetime.now(timezone.utc)
                last = _last_logged.get(pair)
                should_log = (
                    last is None
                    or last["direction"] != direction
                    or (now - last["time"]).total_seconds() > LOG_COOLDOWN_MINUTES * 60
                )
                if should_log:
                    session_str = ",".join(session["active"]) if session["active"] else None
                    log_signal({
                        "pair": pair.replace("_", "/"),
                        "timeframe": config.CANDLE_GRANULARITY,
                        "session": session_str,
                        "signal_time": now.isoformat(),
                        "direction": direction,
                        "entry_price": flags["price"],
                        "stop_loss": stop,
                        "atr_value": flags["atr_value"],
                        "rsi": flags["rsi_value"],
                        "stochastic_k": flags["stoch_k"],
                        "cci": flags["cci_value"],
                        "bb_upper": flags["bb_upper"],
                        "bb_lower": flags["bb_lower"],
                        "nearest_support": flags["support"],
                        "nearest_resistance": flags["resistance"],
                        "account_balance": account_balance,
                    })
                    _last_logged[pair] = {"direction": direction, "time": now}

            results.append({"pair": pair.replace("_", "/"), "price": flags["price"], "spread": price_info["spread"] if price_info else None, "rsi": flags["rsi_value"], "stoch": flags["stoch_k"], "cci": flags["cci_value"], "atr": flags["atr_value"], "bb_upper": flags["bb_upper"], "bb_lower": flags["bb_lower"], "support": flags["support"], "resistance": flags["resistance"], "near_support": flags["near_support"], "near_resistance": flags["near_resistance"], "signal": direction, "suggested_stop": stop, "bb_breakout_upper": flags["bb_breakout_upper"], "bb_breakout_lower": flags["bb_breakout_lower"]})
        except Exception as e:
            results.append({"pair": pair.replace("_", "/"), "error": str(e)})
    return jsonify({"pairs": results, "session": session, "updated": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"), "granularity": config.CANDLE_GRANULARITY})

@app.route("/api/candles/<instrument>")
def candles(instrument):
    try:
        instr = instrument.replace("-", "_")
        df = client.get_candles(instr, count=100)
        data = [{"time": int(row["time"].timestamp()), "open": round(row["open"], 6), "high": round(row["high"], 6), "low": round(row["low"], 6), "close": round(row["close"], 6)} for _, row in df.iterrows()]
        return jsonify({"candles": data, "pair": instr})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/api/account")
def account():
    try:
        summary = client.get_account_summary()
        return jsonify({"balance": summary.get("balance"), "nav": summary.get("NAV"), "unrealized_pl": summary.get("unrealizedPL"), "realized_pl": summary.get("pl"), "open_trade_count": summary.get("openTradeCount", 0)})
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    app.run(debug=False, port=5000)
