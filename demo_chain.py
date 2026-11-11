"""Synthetic NSE-shaped Bank Nifty chains for demo mode / offline testing.

NOT market data. Prices come from Black-Scholes with a put skew; liquidity fades
far from spot so the filters have something real to do. The JSON shape mirrors
NSE's option-chain response so the normal parser runs on it.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta

from strategy import bs_price

DEMO_SPOT = 60850.0


def _last_tuesday(y: int, m: int) -> date:
    d = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    while d.weekday() != 1:
        d -= timedelta(days=1)
    return d


def demo_expiries(today: date | None = None, n: int = 4) -> list[str]:
    today = today or datetime.now().date()
    out, y, m = [], today.year, today.month
    while len(out) < n:
        d = _last_tuesday(y, m)
        if d >= today:
            out.append(d.strftime("%d-%b-%Y"))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def demo_chain(expiry: str = "29-Dec-2026", spot: float | None = None, seed: int | None = None) -> dict:
    rng = random.Random(seed)
    spot = spot if spot is not None else DEMO_SPOT + rng.uniform(-150, 150)
    exp_d = datetime.strptime(expiry, "%d-%b-%Y").date()
    dte = max(1, (exp_d - datetime.now().date()).days)
    T = dte / 365.0
    atm = 0.155
    rows = []
    for strike in range(int(spot * 0.70) // 100 * 100, int(spot * 1.30), 100):
        x = math.log(strike / spot)
        row = {"strikePrice": strike, "expiryDate": expiry}
        for opt in ("CE", "PE"):
            iv = atm + 0.45 * max(-x, 0) + 0.10 * max(x, 0)   # put skew
            fair = bs_price(spot, strike, T, iv, 0.06, opt)
            otm = (opt == "CE" and strike > spot) or (opt == "PE" and strike < spot)
            dist = abs(x)
            liq = math.exp(-dist / 0.05)                       # fades with distance
            round_k = strike % 500 == 0
            oi = int(9000 * liq * (2.5 if round_k else 0.6) + rng.uniform(0, 60))
            vol = int(30000 * liq * (2.0 if round_k else 0.5) * rng.uniform(0.6, 1.4))
            half = max(0.05, fair * (0.012 + 1.2 * dist * dist))
            ltp = max(0.05, round(fair * rng.uniform(0.985, 1.015) * 20) / 20)
            bid = max(0.0, round((fair - half) * 20) / 20)
            ask = round((fair + half) * 20) / 20
            if otm and oi < 120:          # thin far strikes: often no seller / no trade
                ask = 0.0 if rng.random() < 0.6 else ask
                vol = 0 if rng.random() < 0.7 else vol
                if vol == 0:
                    ltp = 0.0 if rng.random() < 0.5 else ltp
            row[opt] = {
                "strikePrice": strike, "expiryDate": expiry, "lastPrice": ltp,
                "bidprice": bid, "askPrice": ask,
                "impliedVolatility": round(iv * 100, 2) if ltp > 0 else 0,
                "openInterest": oi, "totalTradedVolume": vol, "underlyingValue": round(spot, 2),
            }
        rows.append(row)
    return {"records": {
        "timestamp": datetime.now().strftime("%d-%b-%Y %H:%M:%S") + " (DEMO)",
        "underlyingValue": round(spot, 2),
        "expiryDates": demo_expiries(),
        "data": rows,
    }}


def demo_all(n: int = 4) -> list[tuple[str, dict, str]]:
    seed = random.randint(0, 10**6)
    spot = DEMO_SPOT + random.uniform(-150, 150)
    return [(e, demo_chain(e, spot=spot, seed=seed + i), "demo (synthetic -- NOT market data)")
            for i, e in enumerate(demo_expiries(n=n))]
