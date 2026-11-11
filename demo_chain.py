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
# per-index demo settings: spot, strike step, ATM IV, weekly expiry weekday (None = monthly only)
DEMO = {
    "BANKNIFTY": {"spot": 60850.0, "step": 100, "atm": 0.155, "weekday": None, "monthly_weekday": 1},
    "NIFTY": {"spot": 26450.0, "step": 50, "atm": 0.125, "weekday": 1, "monthly_weekday": 1},
    "SENSEX": {"spot": 86900.0, "step": 100, "atm": 0.13, "weekday": 3, "monthly_weekday": 3},
}


def _last_weekday(y: int, m: int, wd: int = 1) -> date:
    d = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    while d.weekday() != wd:
        d -= timedelta(days=1)
    return d


_last_tuesday = _last_weekday


def demo_expiries(today: date | None = None, n: int = 4, index: str = "BANKNIFTY") -> list[str]:
    """Monthly expiries (and weeklies for indices that have them) over ~4 months."""
    cfg = DEMO[index]
    today = today or datetime.now().date()
    out = set()
    y, m = today.year, today.month
    for _ in range(n):
        d = _last_weekday(y, m, cfg["monthly_weekday"])
        if d >= today:
            out.add(d)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    if cfg["weekday"] is not None:
        d = today
        while d.weekday() != cfg["weekday"]:
            d += timedelta(days=1)
        for _ in range(6):
            out.add(d)
            d += timedelta(days=7)
    return [d.strftime("%d-%b-%Y") for d in sorted(out)]


def demo_chain(expiry: str = "29-Dec-2026", spot: float | None = None, seed: int | None = None,
               index: str = "BANKNIFTY") -> dict:
    cfg = DEMO[index]
    rng = random.Random(seed)
    spot = spot if spot is not None else cfg["spot"] * (1 + rng.uniform(-0.0025, 0.0025))
    exp_d = datetime.strptime(expiry, "%d-%b-%Y").date()
    dte = max(1, (exp_d - datetime.now().date()).days)
    T = dte / 365.0
    atm, step = cfg["atm"], cfg["step"]
    rows = []
    for strike in range(int(spot * 0.70) // step * step, int(spot * 1.30), step):
        x = math.log(strike / spot)
        row = {"strikePrice": strike, "expiryDate": expiry}
        for opt in ("CE", "PE"):
            iv = atm + 0.45 * max(-x, 0) + 0.10 * max(x, 0)   # put skew
            fair = bs_price(spot, strike, T, iv, 0.06, opt)
            otm = (opt == "CE" and strike > spot) or (opt == "PE" and strike < spot)
            dist = abs(x)
            liq = math.exp(-dist / 0.05)                       # fades with distance
            round_k = strike % (step * 5) == 0
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
        "expiryDates": demo_expiries(index=index),
        "data": rows,
    }}


def demo_all(index: str = "BANKNIFTY", choose=None) -> list[tuple[str, dict, str]]:
    choose = choose or (lambda exps: exps[:4])
    seed = random.randint(0, 10**6)
    spot = DEMO[index]["spot"] * (1 + random.uniform(-0.0025, 0.0025))
    return [(e, demo_chain(e, spot=spot, seed=seed + i, index=index), "demo (synthetic -- NOT market data)")
            for i, e in enumerate(choose(demo_expiries(index=index)))]
