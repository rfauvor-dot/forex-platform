"""
Supabase client and signal logging.
"""
import os
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

_client = None

def get_client():
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            print("[Supabase] Missing SUPABASE_URL or SUPABASE_SERVICE_KEY env vars")
            return None
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def log_signal(signal_data: dict):
    """
    Insert a new row into trade_signals. Returns the new row's id, or None on failure.
    """
    client = get_client()
    if client is None:
        return None

    row = {
        "pair": signal_data["pair"],
        "timeframe": signal_data["timeframe"],
        "session": signal_data.get("session"),
        "signal_time": signal_data["signal_time"],
        "direction": signal_data["direction"],
        "entry_price": signal_data["entry_price"],
        "stop_loss": signal_data["stop_loss"],
        "atr_value": signal_data.get("atr_value"),
        "rsi": signal_data.get("rsi"),
        "stochastic_k": signal_data.get("stochastic_k"),
        "cci": signal_data.get("cci"),
        "bb_upper": signal_data.get("bb_upper"),
        "bb_lower": signal_data.get("bb_lower"),
        "nearest_support": signal_data.get("nearest_support"),
        "nearest_resistance": signal_data.get("nearest_resistance"),
        "account_balance": signal_data.get("account_balance"),
        "taken": False,
    }

    try:
        result = client.table("trade_signals").insert(row).execute()
        return result.data[0]["id"]
    except Exception as e:
        print(f"[Supabase] Failed to log signal: {e}")
        return None
