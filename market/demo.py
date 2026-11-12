"""Synthetic practice data in the same folder layout as the real collector (NOT market data).

Lets the stocks app run before real history exists, with a few designed patterns:
a breakout, stocks coiling just under their highs, and breakdowns.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta
from pathlib import Path

from . import parse, store

STOCKS = [  # symbol, company, industry, start price, lot, pattern
    ("KALYANKJIL", "Kalyan Jewellers India", "Consumer Durables", 480, 1175, "breakout"),
    ("TITAN", "Titan Company", "Consumer Durables", 3300, 175, "setup"),
    ("BEL", "Bharat Electronics", "Capital Goods", 380, 1425, "setup"),
    ("DIXON", "Dixon Technologies", "Consumer Durables", 15500, 50, "setup"),
    ("PERSISTENT", "Persistent Systems", "Information Technology", 5600, 100, "setup"),
    ("HAL", "Hindustan Aeronautics", "Capital Goods", 4300, 150, "uptrend"),
    ("TRENT", "Trent", "Consumer Services", 5200, 100, "uptrend"),
    ("RELIANCE", "Reliance Industries", "Oil Gas & Fuels", 1300, 500, "flat"),
    ("TCS", "Tata Consultancy Services", "Information Technology", 3500, 175, "flat"),
    ("INFY", "Infosys", "Information Technology", 1550, 400, "flat"),
    ("HDFCBANK", "HDFC Bank", "Financial Services", 1900, 550, "uptrend"),
    ("ICICIBANK", "ICICI Bank", "Financial Services", 1350, 700, "uptrend"),
    ("SBIN", "State Bank of India", "Financial Services", 820, 750, "flat"),
    ("AXISBANK", "Axis Bank", "Financial Services", 1150, 625, "flat"),
    ("KOTAKBANK", "Kotak Mahindra Bank", "Financial Services", 2050, 400, "flat"),
    ("LT", "Larsen & Toubro", "Construction", 3600, 175, "uptrend"),
    ("ITC", "ITC", "FMCG", 420, 1600, "flat"),
    ("BAJFINANCE", "Bajaj Finance", "Financial Services", 900, 750, "uptrend"),
    ("MARUTI", "Maruti Suzuki", "Automobile", 12500, 50, "flat"),
    ("SUNPHARMA", "Sun Pharma", "Healthcare", 1700, 350, "flat"),
    ("POLYCAB", "Polycab India", "Capital Goods", 6800, 125, "setup"),
    ("COFORGE", "Coforge", "Information Technology", 1750, 375, "uptrend"),
    ("TATASTEEL", "Tata Steel", "Metals & Mining", 160, 5500, "flat"),
    ("HINDALCO", "Hindalco", "Metals & Mining", 700, 1400, "uptrend"),
    ("JSWSTEEL", "JSW Steel", "Metals & Mining", 1050, 675, "flat"),
    ("ONGC", "ONGC", "Oil Gas & Fuels", 250, 2250, "weak"),
    ("NTPC", "NTPC", "Power", 340, 1500, "flat"),
    ("POWERGRID", "Power Grid", "Power", 300, 1900, "weak"),
    ("COALINDIA", "Coal India", "Oil Gas & Fuels", 390, 1350, "weak"),
    ("INDIGO", "InterGlobe Aviation", "Services", 5600, 150, "breakdown"),
    ("ASIANPAINT", "Asian Paints", "Consumer Durables", 2400, 250, "breakdown"),
    ("HINDUNILVR", "Hindustan Unilever", "FMCG", 2400, 300, "weak"),
    ("NESTLEIND", "Nestle India", "FMCG", 1200, 500, "flat"),
    ("BHARTIARTL", "Bharti Airtel", "Telecommunication", 1950, 475, "uptrend"),
    ("M&M", "Mahindra & Mahindra", "Automobile", 3300, 200, "uptrend"),
    ("EICHERMOT", "Eicher Motors", "Automobile", 6500, 100, "flat"),
    ("IRCTC", "IRCTC", "Consumer Services", 760, 875, "weak"),
    ("VBL", "Varun Beverages", "FMCG", 520, 1025, "weakening"),
    ("PAGEIND", "Page Industries", "Textiles", 44000, 15, "flat"),
    ("BDL", "Bharat Dynamics", "Capital Goods", 1500, 350, "uptrend"),
]


def _days(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def generate(root: Path, end: date | None = None, n: int = 200, seed: int = 7) -> None:
    rng = random.Random(seed)
    end = end or date.today() - timedelta(days=1)
    days = _days(end, n)
    series = {}
    for sym, comp, ind, p0, lot, pat in STOCKS:
        drift = {"breakout": 0.0016, "setup": 0.0014, "uptrend": 0.0011, "flat": 0.0001, "weak": -0.0008,
                 "breakdown": -0.0006, "weakening": -0.0009}[pat]
        vol = 0.015 if pat != "flat" else 0.012
        p, rows = p0, []
        for i, d in enumerate(days):
            left = n - 1 - i
            dv = vol
            if pat in ("setup", "breakout") and 2 <= left <= 14:
                dv, ret = vol * 0.35, 0.0002
            elif pat == "breakout" and left == 1:
                dv, ret = vol * 0.5, 0.004
            elif pat == "breakout" and left == 0:
                dv, ret = vol * 0.3, 0.062
            elif pat == "breakdown" and 1 <= left <= 12:
                dv, ret = vol * 0.4, -0.0004
            elif pat == "breakdown" and left == 0:
                dv, ret = vol * 0.3, -0.048
            else:
                ret = drift + rng.gauss(0, dv)
            if pat in ("setup",) and 2 <= left <= 14:
                ret += rng.gauss(0, dv)
            o = p * (1 + rng.gauss(0, dv * 0.3))
            c = p * (1 + ret)
            hi = max(o, c) * (1 + abs(rng.gauss(0, dv * 0.5)))
            lo = min(o, c) * (1 - abs(rng.gauss(0, dv * 0.5)))
            base_v = 2.2e9 / p0 * (0.7 + 0.6 * rng.random())
            v = base_v * (3.1 if (pat in ("breakout", "breakdown") and left == 0) else (0.6 if pat in ("setup",) and left < 14 else 1))
            dp = 42 + rng.gauss(0, 6) + (12 if pat in ("setup", "breakout") and left < 6 else 0) - (8 if pat == "breakdown" and left < 6 else 0)
            rows.append((d, p, o, hi, lo, c, v, max(5, min(95, dp))))
            p = c
        series[sym] = rows
    for i, d in enumerate(days):
        eq = []
        for sym, rows in series.items():
            dd, prev, o, hi, lo, c, v, dp = rows[i]
            eq.append({"date": d.strftime("%d-%b-%Y"), "symbol": sym, "prev_close": prev, "open": o, "high": hi, "low": lo,
                       "close": c, "avg_price": (hi + lo + c) / 3, "volume": round(v), "turnover_lacs": v * c / 1e5,
                       "trades": round(v / 50), "deliv_qty": round(v * dp / 100), "deliv_pct": dp})
        store.write_rows(store.eod_path("equity", d), eq, parse.EQ_FIELDS)
    meta = {s[0]: s for s in STOCKS}
    for j, d in enumerate(days[-6:]):
        fo = []
        for sym, rows in series.items():
            pat, lot = meta[sym][5], meta[sym][4]
            c = rows[len(days) - 6 + j][5]
            k = (j + 1) / 6
            oi = 1e7 / lot * (1 + {"breakout": 0.25, "setup": 0.08, "breakdown": 0.22, "weakening": 0.12}.get(pat, 0.0) * k)
            step = round(c * 0.025 / 5) * 5 or 1
            fo.append({"date": d.isoformat(), "symbol": sym, "underlying": c, "fut_expiry": "2026-11-24", "fut_close": c * 1.003,
                       "fut_oi": oi, "fut_chg_oi": oi * 0.02, "fut_volume": oi * 0.3, "fut_oi_all": oi * 1.1,
                       "fut_chg_oi_all": oi * 0.02, "opt_expiry": "2026-11-24", "ce_oi": oi * 2, "pe_oi": oi * 1.6,
                       "ce_chg_oi": 0, "pe_chg_oi": 0, "pcr": 0.8, "max_ce_strike": round(c / step) * step + 4 * step,
                       "max_pe_strike": round(c / step) * step - 4 * step, "opt_volume": oi, "lot_size": lot})
        store.write_rows(store.eod_path("fo", d), fo, parse.FO_FIELDS)
    store.write_universe([{"symbol": s, "company": c, "industry": i} for s, c, i, *_ in STOCKS])
    (root / "meta").mkdir(parents=True, exist_ok=True)
    (root / "meta" / "watchlist.csv").write_text("symbol,note\nKALYANKJIL,starting example\nTITAN,\nRELIANCE,\n")
    (root / "meta" / "positions.csv").write_text(
        "symbol,qty,price,stop,date\nHAL,2,%.2f,%.2f,%s\n" % (series["HAL"][-8][5], series["HAL"][-8][5] * 0.93, days[-8].isoformat()))
    ind = [{"index": "NIFTY 50", "last": 25299.35, "change_pct": 0.42}, {"index": "NIFTY BANK", "last": 61518.5, "change_pct": 0.55},
           {"index": "INDIA VIX", "last": 12.4, "change_pct": -3.1}]
    when = __import__("datetime").datetime.combine(end, __import__("datetime").time(15, 35))
    store.write_rows(store.snap_path("indices", when), ind, parse.INDEX_FIELDS)
