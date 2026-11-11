"""Synthetic NSE-shaped Bank Nifty chain for demo mode / offline testing.

NOT market data. Shape mirrors NSE's option-chain JSON so the same parser runs on it.
"""

from __future__ import annotations

import math
import random
from datetime import datetime


def demo_chain(expiry: str = "29-Dec-2026", spot: float | None = None) -> dict:
    spot = spot if spot is not None else 59500 + random.uniform(-250, 250)
    rows = []
    for strike in range(48000, 71100, 100):
        row = {"strikePrice": strike, "expiryDate": expiry}
        for opt in ("CE", "PE"):
            dist = (strike - spot) if opt == "CE" else (spot - strike)
            intrinsic = max(0.0, -dist)
            otm = max(dist, 0.0)
            # smile: OTM puts carry more IV than OTM calls
            iv = 14.5 + (otm / 1000) * (0.9 if opt == "PE" else 0.5)
            time_val = 380 * math.exp(-otm / (1350 if opt == "CE" else 1550))
            ltp = round(max(0.05, intrinsic + time_val * random.uniform(0.96, 1.04)) * 20) / 20
            vol = 0 if otm > 9000 and strike % 500 else int(random.uniform(200, 40000) * math.exp(-otm / 4000))
            spread = max(0.05, ltp * 0.04)
            row[opt] = {
                "strikePrice": strike, "expiryDate": expiry, "lastPrice": ltp,
                "bidprice": round(max(0.05, ltp - spread), 2), "askPrice": round(ltp + spread, 2),
                "impliedVolatility": round(iv, 2),
                "openInterest": int(5000 * math.exp(-abs(strike - spot) / 3000) + (800 if strike % 500 == 0 else 50)),
                "totalTradedVolume": vol, "underlyingValue": round(spot, 2),
            }
        rows.append(row)
    return {"records": {
        "timestamp": datetime.now().strftime("%d-%b-%Y %H:%M:%S") + " (DEMO)",
        "underlyingValue": round(spot, 2),
        "expiryDates": ["23-Nov-2026", expiry, "25-Jan-2027"],
        "data": rows,
    }}
