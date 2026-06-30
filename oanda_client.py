"""
OANDA v20 API connector.
Handles pulling price candles and (later) placing/managing paper trades.
"""

import requests
import pandas as pd
import config


class OandaClient:
    def __init__(self):
        if not config.OANDA_API_TOKEN or not config.OANDA_ACCOUNT_ID:
            raise ValueError(
                "Missing OANDA credentials. Set OANDA_API_TOKEN and OANDA_ACCOUNT_ID "
                "in config.py or as environment variables before running."
            )
        self.base_url = config.OANDA_API_URL
        self.account_id = config.OANDA_ACCOUNT_ID
        self.headers = {
            "Authorization": f"Bearer {config.OANDA_API_TOKEN}",
            "Content-Type": "application/json",
        }

    def get_candles(self, instrument, granularity=None, count=None):
        """
        Pull historical candles for a given pair, e.g. 'EUR_USD'.
        Returns a pandas DataFrame with columns: time, open, high, low, close, volume
        """
        granularity = granularity or config.CANDLE_GRANULARITY
        count = count or config.CANDLE_COUNT

        url = f"{self.base_url}/v3/instruments/{instrument}/candles"
        params = {"granularity": granularity, "count": count, "price": "M"}  # M = midpoint price

        resp = requests.get(url, headers=self.headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        rows = []
        for c in data.get("candles", []):
            if not c.get("complete", False):
                continue  # skip the in-progress candle
            rows.append({
                "time": c["time"],
                "open": float(c["mid"]["o"]),
                "high": float(c["mid"]["h"]),
                "low": float(c["mid"]["l"]),
                "close": float(c["mid"]["c"]),
                "volume": int(c["volume"]),
            })

        df = pd.DataFrame(rows)
        if not df.empty:
            df["time"] = pd.to_datetime(df["time"])
        return df

    def get_current_price(self, instrument):
        """Get the latest bid/ask for an instrument."""
        url = f"{self.base_url}/v3/accounts/{self.account_id}/pricing"
        params = {"instruments": instrument}
        resp = requests.get(url, headers=self.headers, params=params, timeout=15)
        resp.raise_for_status()
        prices = resp.json().get("prices", [])
        if not prices:
            return None
        p = prices[0]
        return {
            "bid": float(p["bids"][0]["price"]),
            "ask": float(p["asks"][0]["price"]),
            "spread": round(float(p["asks"][0]["price"]) - float(p["bids"][0]["price"]), 5),
        }

    def get_account_summary(self):
        """Balance, NAV, margin used, etc."""
        url = f"{self.base_url}/v3/accounts/{self.account_id}/summary"
        resp = requests.get(url, headers=self.headers, timeout=15)
        resp.raise_for_status()
        return resp.json().get("account", {})

    def place_market_order(self, instrument, units, stop_loss_price=None, trailing_stop_distance=None):
        """
        Place a market order (paper trade, since this points at the practice environment).
        units: positive = buy, negative = sell
        stop_loss_price: absolute price level for fixed stop
        trailing_stop_distance: distance in price units (e.g. 0.0015 for 15 pips on EUR/USD)
        """
        order = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
            }
        }

        if stop_loss_price is not None:
            order["order"]["stopLossOnFill"] = {"price": f"{stop_loss_price:.5f}"}

        if trailing_stop_distance is not None:
            order["order"]["trailingStopLossOnFill"] = {"distance": f"{trailing_stop_distance:.5f}"}

        url = f"{self.base_url}/v3/accounts/{self.account_id}/orders"
        resp = requests.post(url, headers=self.headers, json=order, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_open_trades(self):
        url = f"{self.base_url}/v3/accounts/{self.account_id}/openTrades"
        resp = requests.get(url, headers=self.headers, timeout=15)
        resp.raise_for_status()
        return resp.json().get("trades", [])

    def close_trade(self, trade_id):
        url = f"{self.base_url}/v3/accounts/{self.account_id}/trades/{trade_id}/close"
        resp = requests.put(url, headers=self.headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
