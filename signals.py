"""
Signal calculation engine.
Takes a candle DataFrame (from OandaClient.get_candles) and computes:
RSI, Stochastic, CCI, Bollinger Bands, ATR, Support/Resistance, and a composite signal.
"""

import pandas as pd
import numpy as np
import config


def rsi(df, period=None):
    period = period or config.RSI_PERIOD
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    return rsi_series


def stochastic(df, k_period=None, d_period=None):
    k_period = k_period or config.STOCH_K_PERIOD
    d_period = d_period or config.STOCH_D_PERIOD
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    d = k.rolling(d_period).mean()
    return k, d


def cci(df, period=None):
    period = period or config.CCI_PERIOD
    tp = (df["high"] + df["low"] + df["close"]) / 3
    sma = tp.rolling(period).mean()
    mean_dev = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    cci_series = (tp - sma) / (0.015 * mean_dev.replace(0, np.nan))
    return cci_series


def bollinger_bands(df, period=None, stddev=None):
    period = period or config.BBANDS_PERIOD
    stddev = stddev or config.BBANDS_STDDEV
    mid = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std()
    upper = mid + stddev * std
    lower = mid - stddev * std
    return upper, mid, lower


def atr(df, period=None):
    period = period or config.ATR_PERIOD
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def support_resistance(df, lookback=None):
    """
    Simple swing-high/swing-low based S/R: returns the most recent
    significant high and low over the lookback window.
    """
    lookback = lookback or config.SR_LOOKBACK
    window = df.tail(lookback)
    resistance = window["high"].max()
    support = window["low"].min()
    return support, resistance


def compute_all_signals(df):
    """
    Run every indicator on the given candle DataFrame and return
    a dict with the latest values plus simple triggered/not-triggered flags.
    """
    if df.empty or len(df) < config.SR_LOOKBACK:
        return None

    df = df.copy()
    df["rsi"] = rsi(df)
    df["stoch_k"], df["stoch_d"] = stochastic(df)
    df["cci"] = cci(df)
    df["bb_upper"], df["bb_mid"], df["bb_lower"] = bollinger_bands(df)
    df["atr"] = atr(df)

    support, resistance = support_resistance(df)
    latest = df.iloc[-1]

    flags = {
        "rsi_value": round(latest["rsi"], 2) if pd.notna(latest["rsi"]) else None,
        "rsi_overbought": bool(latest["rsi"] >= config.RSI_OVERBOUGHT) if pd.notna(latest["rsi"]) else False,
        "rsi_oversold": bool(latest["rsi"] <= config.RSI_OVERSOLD) if pd.notna(latest["rsi"]) else False,

        "stoch_k": round(latest["stoch_k"], 2) if pd.notna(latest["stoch_k"]) else None,
        "stoch_overbought": bool(latest["stoch_k"] >= config.STOCH_OVERBOUGHT) if pd.notna(latest["stoch_k"]) else False,
        "stoch_oversold": bool(latest["stoch_k"] <= config.STOCH_OVERSOLD) if pd.notna(latest["stoch_k"]) else False,

        "cci_value": round(latest["cci"], 2) if pd.notna(latest["cci"]) else None,
        "cci_overbought": bool(latest["cci"] >= config.CCI_OVERBOUGHT) if pd.notna(latest["cci"]) else False,
        "cci_oversold": bool(latest["cci"] <= config.CCI_OVERSOLD) if pd.notna(latest["cci"]) else False,

        "price": round(latest["close"], 5),
        "bb_upper": round(latest["bb_upper"], 5) if pd.notna(latest["bb_upper"]) else None,
        "bb_lower": round(latest["bb_lower"], 5) if pd.notna(latest["bb_lower"]) else None,
        "bb_breakout_upper": bool(latest["close"] >= latest["bb_upper"]) if pd.notna(latest["bb_upper"]) else False,
        "bb_breakout_lower": bool(latest["close"] <= latest["bb_lower"]) if pd.notna(latest["bb_lower"]) else False,

        "atr_value": round(latest["atr"], 5) if pd.notna(latest["atr"]) else None,

        "support": round(support, 5),
        "resistance": round(resistance, 5),
        "near_support": bool(abs(latest["close"] - support) <= (latest["atr"] or 0) * 0.5) if pd.notna(latest["atr"]) else False,
        "near_resistance": bool(abs(latest["close"] - resistance) <= (latest["atr"] or 0) * 0.5) if pd.notna(latest["atr"]) else False,
    }

    # Composite confirmation: momentum oversold/overbought agreeing with each other
    oversold_votes = sum([flags["rsi_oversold"], flags["stoch_oversold"], flags["cci_oversold"]])
    overbought_votes = sum([flags["rsi_overbought"], flags["stoch_overbought"], flags["cci_overbought"]])

    flags["composite_buy_signal"] = oversold_votes >= 2
    flags["composite_sell_signal"] = overbought_votes >= 2

    return flags


def suggested_stop(entry_price, atr_value, direction="buy"):
    """
    ATR-based default stop-loss price.
    direction: 'buy' or 'sell'
    """
    if atr_value is None:
        return None
    distance = atr_value * config.ATR_STOP_MULTIPLIER
    if direction == "buy":
        return round(entry_price - distance, 5)
    else:
        return round(entry_price + distance, 5)
