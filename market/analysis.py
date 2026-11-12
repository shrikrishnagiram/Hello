"""Analysis engine: turns collected numbers into radar scores, verdicts and trade plans.

Reads the private data folder (end-of-day history, F&O summaries, latest intraday
snapshot). Rules are explicit and every verdict carries the actual numbers behind it.
READ-ONLY: produces suggestions only, never orders.
"""

from __future__ import annotations

import csv
import gzip
import io
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import config

CAPITAL = 50_000
RISK_PER_TRADE = 500          # 1% of capital
MAX_POSITION = 12_500         # 25% of capital in any one stock
MAX_OPTION_COST = 5_000
HISTORY_DAYS = 260


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except ValueError:
        return None


def _read(path: Path) -> list[dict]:
    with gzip.open(path, "rt", newline="") as fh:
        return list(csv.DictReader(fh))


def _date_from_name(p: Path) -> str:
    s = p.name.split(".")[0]
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
@dataclass
class Series:
    symbol: str
    dates: list = field(default_factory=list)
    o: list = field(default_factory=list)
    h: list = field(default_factory=list)
    l: list = field(default_factory=list)
    c: list = field(default_factory=list)
    v: list = field(default_factory=list)
    dp: list = field(default_factory=list)     # delivery %


def load_universe(root: Path) -> dict[str, dict]:
    p = root / "meta" / "universe.csv"
    if not p.exists():
        return {}
    return {r["symbol"]: r for r in csv.DictReader(io.StringIO(p.read_text())) if r.get("symbol")}


def load_list(root: Path, name: str) -> list[dict]:
    p = root / "meta" / f"{name}.csv"
    if not p.exists():
        return []
    return [r for r in csv.DictReader(io.StringIO(p.read_text())) if r.get("symbol")]


def load_history(root: Path, symbols: set[str], days: int = HISTORY_DAYS) -> tuple[dict[str, Series], list[str]]:
    files = sorted((root / "eod" / "equity").glob("*/*.csv.gz"))[-days:]
    out: dict[str, Series] = {}
    dates = []
    for f in files:
        d = _date_from_name(f)
        dates.append(d)
        for r in _read(f):
            s = r.get("symbol")
            if s not in symbols:
                continue
            c = _f(r.get("close"))
            if c is None:
                continue
            ser = out.setdefault(s, Series(s))
            ser.dates.append(d)
            ser.o.append(_f(r.get("open")) or c)
            ser.h.append(_f(r.get("high")) or c)
            ser.l.append(_f(r.get("low")) or c)
            ser.c.append(c)
            ser.v.append(_f(r.get("volume")) or 0.0)
            ser.dp.append(_f(r.get("deliv_pct")))
    return out, dates


def load_fo(root: Path, n: int = 6) -> list[dict[str, dict]]:
    """Last n daily F&O summaries, oldest first: [{symbol: row}, ...]."""
    files = sorted((root / "eod" / "fo").glob("*/*.csv.gz"))[-n:]
    return [{r["symbol"]: r for r in _read(f)} for f in files]


def latest_snapshot(root: Path, kind: str) -> tuple[str | None, list[dict]]:
    files = sorted((root / "snapshots").glob(f"*/*/*/{kind}_*.csv.gz"))
    if not files:
        return None, []
    f = files[-1]
    parts = f.parts
    stamp = f"{parts[-4]}-{parts[-3]}-{parts[-2]} {f.stem.split('_')[1][:2]}:{f.stem.split('_')[1][2:4]}"
    return stamp, _read(f)


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #
def sma(x, n):
    return sum(x[-n:]) / n if len(x) >= n else None


def atr(s: Series, n=14):
    if len(s.c) < n + 1:
        return None
    trs = [max(s.h[i] - s.l[i], abs(s.h[i] - s.c[i - 1]), abs(s.l[i] - s.c[i - 1])) for i in range(len(s.c) - n, len(s.c))]
    return sum(trs) / n


def pct(a, b):
    return (a / b - 1) * 100 if (a is not None and b) else None


@dataclass
class Signal:
    name: str
    ok: bool
    detail: str
    weight: int


def analyze(s: Series, meta: dict, fo_days: list[dict], market_ret63: float | None,
            live: dict | None = None) -> dict | None:
    if len(s.c) < 60:
        return None
    c, h, l, v = list(s.c), list(s.h), list(s.l), list(s.v)
    live_note = None
    if live and _f(live.get("last")):
        # fold today's intraday snapshot in as a provisional bar
        lp = _f(live["last"])
        today_bar = (_f(live.get("high")) or lp, _f(live.get("low")) or lp, _f(live.get("volume")) or 0.0)
        c.append(lp); h.append(today_bar[0]); l.append(today_bar[1]); v.append(today_bar[2])
        live_note = live.get("updated")
    last = c[-1]
    prev = c[-2]
    s50, s200, s20 = sma(c, 50), sma(c, 200), sma(c, 20)
    s200_prev = sum(c[-220:-20]) / 200 if len(c) >= 220 else None
    a = atr(Series(s.symbol, [], [], h, l, c, v)) or (last * 0.02)
    hh20 = max(h[-21:-1]); ll20 = min(l[-21:-1])
    hi52 = max(h[-250:]); lo52 = min(l[-250:])
    vol20 = sum(v[-21:-1]) / 20 if len(v) >= 21 else None
    vol_ratio = (v[-1] / vol20) if vol20 else None
    rng10 = (max(h[-10:]) - min(l[-10:])) / last * 100
    rngs = [(max(h[i - 10:i]) - min(l[i - 10:i])) / c[i - 1] * 100 for i in range(max(10, len(c) - 120), len(c))]
    rng_med = sorted(rngs)[len(rngs) // 2] if rngs else None
    tight = (rng10 / rng_med) if rng_med else None
    dps = [d for d in s.dp[-60:] if d is not None]
    dp5 = [d for d in s.dp[-5:] if d is not None]
    deliv5 = sum(dp5) / len(dp5) if dp5 else None
    deliv60 = sum(dps) / len(dps) if dps else None
    upv = sum(v[i] for i in range(len(c) - 20, len(c)) if c[i] > c[i - 1])
    dnv = sum(v[i] for i in range(len(c) - 20, len(c)) if c[i] < c[i - 1])
    ret63 = pct(c[-1], c[-64]) if len(c) > 64 else None
    rs = (ret63 - market_ret63) if (ret63 is not None and market_ret63 is not None) else None
    ret5 = pct(c[-1], c[-6])

    fo = None
    if fo_days and s.symbol in fo_days[-1]:
        now, old = fo_days[-1][s.symbol], fo_days[0].get(s.symbol)
        oi_now, oi_old = _f(now.get("fut_oi_all")) or _f(now.get("fut_oi")), (_f(old.get("fut_oi_all")) or _f(old.get("fut_oi"))) if old else None
        oi_chg = pct(oi_now, oi_old) if oi_old else None
        px_chg = ret5
        if oi_chg is not None and px_chg is not None:
            if oi_chg > 3 and px_chg > 0: build = "Long build-up"
            elif oi_chg > 3 and px_chg < 0: build = "Short build-up"
            elif oi_chg < -3 and px_chg > 0: build = "Short covering"
            elif oi_chg < -3 and px_chg < 0: build = "Long unwinding"
            else: build = "No clear build-up"
        else:
            build = None
        fo = {"oi": oi_now, "oi_chg5_pct": oi_chg, "buildup": build, "pcr": _f(now.get("pcr")),
              "max_call": _f(now.get("max_ce_strike")), "max_put": _f(now.get("max_pe_strike")),
              "lot": _f(now.get("lot_size")), "opt_expiry": now.get("opt_expiry"), "fut_expiry": now.get("fut_expiry")}

    near_hi = pct(hh20, last)          # % to break the 20-day high
    near_lo = pct(last, ll20)          # % above the 20-day low
    trend_up = bool(s50 and s200 and last > s50 > s200 and (s200_prev is None or s200 >= s200_prev))
    trend_dn = bool(s50 and s200 and last < s50 < s200)
    fmt = lambda x, d=1: "—" if x is None else f"{x:,.{d}f}"

    up = [
        Signal("Uptrend", trend_up, f"Price ₹{fmt(last)} vs 50-day avg ₹{fmt(s50, 0)} and 200-day avg ₹{fmt(s200, 0)}", 20),
        Signal("Near breakout", near_hi is not None and near_hi <= 3, f"{fmt(near_hi)}% below its 20-day high ₹{fmt(hh20)}" if near_hi and near_hi > 0 else f"Above its 20-day high ₹{fmt(hh20)}", 20),
        Signal("Coiling", tight is not None and tight < 0.75, f"10-day range {fmt(rng10)}% vs usual {fmt(rng_med)}%", 15),
        Signal("Buyers taking delivery", (deliv5 and deliv60 and deliv5 > deliv60 * 1.12) or upv > dnv * 1.3,
               f"Delivery {fmt(deliv5)}% (5-day) vs {fmt(deliv60)}% usual; up-day volume {fmt(upv / dnv if dnv else None, 1)}× down-day", 15),
        Signal("Stronger than market", rs is not None and rs > 0, f"3-month {fmt(ret63)}% vs market {fmt(market_ret63)}%", 15),
        Signal("Futures: fresh buying", bool(fo and fo["buildup"] == "Long build-up"),
               (f"{fo['buildup']}: futures positions {fmt(fo['oi_chg5_pct'])}% in 5 days, price {fmt(ret5)}%" if fo and fo['buildup'] else "Not in F&O / no data"), 15),
    ]
    dn = [
        Signal("Downtrend", trend_dn, f"Price ₹{fmt(last)} vs 50-day ₹{fmt(s50, 0)}, 200-day ₹{fmt(s200, 0)}", 20),
        Signal("Near breakdown", near_lo is not None and near_lo <= 3, f"{fmt(near_lo)}% above its 20-day low ₹{fmt(ll20)}" if near_lo and near_lo > 0 else f"Below its 20-day low ₹{fmt(ll20)}", 20),
        Signal("Coiling", tight is not None and tight < 0.75, f"10-day range {fmt(rng10)}% vs usual {fmt(rng_med)}%", 15),
        Signal("Sellers in control", dnv > upv * 1.3, f"Down-day volume {fmt(dnv / upv if upv else None, 1)}× up-day volume", 15),
        Signal("Weaker than market", rs is not None and rs < 0, f"3-month {fmt(ret63)}% vs market {fmt(market_ret63)}%", 15),
        Signal("Futures: fresh selling", bool(fo and fo["buildup"] == "Short build-up"),
               (f"{fo['buildup']}: futures positions {fmt(fo['oi_chg5_pct'])}% in 5 days, price {fmt(ret5)}%" if fo and fo['buildup'] else "Not in F&O / no data"), 15),
    ]
    up_score = sum(x.weight for x in up if x.ok)
    dn_score = sum(x.weight for x in dn if x.ok)
    direction = "up" if up_score >= dn_score else "down"
    score = max(up_score, dn_score)

    broke_up = last > hh20 and (vol_ratio or 0) >= 1.5
    broke_dn = last < ll20 and (vol_ratio or 0) >= 1.5
    extended = s20 and last > s20 * 1.08
    if direction == "up":
        if broke_up and score >= 55 and not extended:
            stage = "breakout"
        elif extended:
            stage = "extended"
        elif score >= 55 and near_hi is not None and near_hi <= 3:
            stage = "setting-up"
        else:
            stage = "neutral"
    else:
        if broke_dn and score >= 55:
            stage = "breakdown"
        elif score >= 55 and near_lo is not None and near_lo <= 3:
            stage = "weakening"
        else:
            stage = "neutral"

    plan = None
    if direction == "up":
        if stage == "breakout":
            # A breakout fails if price falls back into its old range: stop just below the level it broke.
            entry = last
            stop = min(hh20 - 0.5 * a, l[-1])
        else:
            entry = round(hh20 * 1.002, 2)
            stop = max(min(l[-10:]), entry - 3 * a)          # bottom of the recent range
        if entry - stop < a:                                  # never tighter than one normal day's move
            stop = entry - a
        stop = round(stop, 2)
        risk_ps = entry - stop
        qty = int(min(RISK_PER_TRADE / risk_ps if risk_ps > 0 else 0, MAX_POSITION / entry))
        fits = qty >= 1
        plan = {"side": "buy", "fits": fits,
                "fit_note": None if fits else f"One share costs ₹{entry:,.0f}, more than your ₹{MAX_POSITION:,} limit per stock",
                "entry": round(entry, 2), "stop": stop, "target": round(entry + 2 * risk_ps, 2),
                "qty": qty, "cost": round(qty * entry), "risk": round(qty * risk_ps),
                "risk_pct_price": round(risk_ps / entry * 100, 1),
                "trigger": "now" if stage == "breakout" else f"if it closes above ₹{fmt(entry)} on strong volume"}
    if fo and fo.get("lot"):
        sigma_d = a / last / 1.25
        est = 0.4 * last * sigma_d * math.sqrt(21)       # rough near-the-money monthly premium
        lot_cost = est * fo["lot"]
        plan = plan or {}
        plan["option"] = {"type": "CE" if direction == "up" else "PE", "lot": fo["lot"], "est_premium": round(est, 1),
                          "est_lot_cost": round(lot_cost), "affordable": lot_cost <= MAX_OPTION_COST,
                          "expiry": fo.get("opt_expiry")}

    reasons = [x for x in (up if direction == "up" else dn) if x.ok]
    return {
        "symbol": s.symbol, "company": meta.get("company") or s.symbol, "industry": meta.get("industry") or "",
        "last": round(last, 2), "chg_pct": round(pct(last, prev) or 0, 2), "live": live_note,
        "direction": direction, "score": score, "up_score": up_score, "down_score": dn_score, "stage": stage,
        "signals_up": [x.__dict__ for x in up], "signals_down": [x.__dict__ for x in dn],
        "tags": [x.name for x in reasons][:4],
        "headline": (reasons[0].detail if reasons else "No strong signals"),
        "metrics": {"sma20": s20, "sma50": s50, "sma200": s200, "atr": a, "hh20": hh20, "ll20": ll20,
                    "hi52": hi52, "lo52": lo52, "vol_ratio": vol_ratio, "vol20": vol20, "volume": v[-1],
                    "deliv5": deliv5, "deliv60": deliv60, "tight": tight, "ret63": ret63, "rs": rs, "ret5": ret5,
                    "near_hi_pct": near_hi, "near_lo_pct": near_lo},
        "fo": fo, "plan": plan,
        "spark": [round(x, 2) for x in c[-60:]],
    }


def market_state(results: list[dict], indices: list[dict]) -> dict:
    above50 = [r for r in results if r["metrics"]["sma50"]]
    breadth = (sum(1 for r in above50 if r["last"] > r["metrics"]["sma50"]) / len(above50) * 100) if above50 else None
    ix = {r["index"]: r for r in indices}
    nifty, bank, vix = ix.get("NIFTY 50"), ix.get("NIFTY BANK"), ix.get("INDIA VIX")
    if breadth is None:
        mood, note = "Unknown", "Not enough data yet."
    elif breadth >= 60:
        mood, note = "Healthy", "Most stocks are above their 50-day average. Good conditions for buying breakouts."
    elif breadth >= 40:
        mood, note = "Mixed", "About half the market is in an uptrend. Be selective; smaller positions."
    else:
        mood, note = "Weak", "Most stocks are below their 50-day average. Avoid new buys; protect what you hold."
    g = lambda r, k: _f(r.get(k)) if r else None
    return {"mood": mood, "note": note, "breadth": breadth,
            "nifty": {"last": g(nifty, "last"), "chg": g(nifty, "change_pct")},
            "bank": {"last": g(bank, "last"), "chg": g(bank, "change_pct")},
            "vix": {"last": g(vix, "last"), "chg": g(vix, "change_pct")}}


def run(root: Path | None = None) -> dict:
    root = root or config.DATA_DIR
    uni = load_universe(root)
    watch = load_list(root, "watchlist")
    positions = load_list(root, "positions")
    syms = set(uni) | {w["symbol"] for w in watch} | {p["symbol"] for p in positions}
    hist, dates = load_history(root, syms)
    fo_days = load_fo(root)
    snap_at, snap = latest_snapshot(root, "quotes")
    idx_at, indices = latest_snapshot(root, "indices")
    live = {r["symbol"]: r for r in snap} if (snap_at and dates and snap_at[:10] > dates[-1]) else {}
    rets = sorted(pct(sr.c[-1], sr.c[-64]) for sr in hist.values() if len(sr.c) > 64)
    mret = rets[len(rets) // 2] if rets else None
    results = []
    for sym, ser in hist.items():
        r = analyze(ser, uni.get(sym, {}), fo_days, mret, live.get(sym))
        if r:
            results.append(r)
    by = {r["symbol"]: r for r in results}
    mk = market_state(results, indices)
    weak = mk["mood"] == "Weak"

    radar_up = sorted([r for r in results if r["direction"] == "up" and r["score"] >= 55 and r["stage"] in ("breakout", "setting-up")],
                      key=lambda r: (-r["score"], r["metrics"]["near_hi_pct"] or 9))[:15]
    radar_dn = sorted([r for r in results if r["direction"] == "down" and r["score"] >= 55 and r["stage"] in ("breakdown", "weakening")],
                      key=lambda r: (-r["score"], r["metrics"]["near_lo_pct"] or 9))[:15]

    actions = []
    for p in positions:
        r = by.get(p["symbol"])
        if not r:
            continue
        stop = _f(p.get("stop"))
        if stop and r["last"] <= stop:
            actions.append({"kind": "SELL", "symbol": r["symbol"], "why": f"Stop-loss hit: ₹{r['last']:,.2f} is at or below your stop ₹{stop:,.2f}."})
        elif r["direction"] == "down" and r["stage"] == "breakdown":
            actions.append({"kind": "SELL", "symbol": r["symbol"], "why": "Broke below its 20-day low on heavy volume while you hold it."})
    fits = lambda r: bool(r["plan"] and r["plan"].get("fits", True))
    for r in radar_up:
        if r["stage"] == "breakout" and not fits(r):
            actions.append({"kind": "WAIT", "symbol": r["symbol"], "why": f"Breakout, but {r['plan']['fit_note']}."})
        elif r["stage"] == "breakout":
            actions.append({"kind": "WAIT" if weak else "BUY", "symbol": r["symbol"],
                            "why": ("Breakout, but the market mood is weak. Wait." if weak else
                                    f"Broke above ₹{r['metrics']['hh20']:,.2f} on {r['metrics']['vol_ratio']:.1f}× normal volume.")})
    for r in radar_up:
        if r["stage"] == "setting-up" and fits(r):
            m = r["metrics"]
            why = (f"{m['near_hi_pct']:.1f}% below its breakout level ₹{m['hh20']:,.2f}." if m["near_hi_pct"] > 0 else
                   f"Above its breakout level ₹{m['hh20']:,.2f} but volume only {m['vol_ratio']:.1f}× normal. Needs confirmation.")
            actions.append({"kind": "WATCH", "symbol": r["symbol"], "why": why})
    held = {p["symbol"] for p in positions}
    for r in radar_dn:
        if r["stage"] == "breakdown":
            actions.append({"kind": "AVOID", "symbol": r["symbol"],
                            "why": "Breaking down. Avoid buying" + ("" if r["symbol"] in held else "; a put option would be the way to profit (practice only at your size).")})

    def card(r):
        return {k: r[k] for k in ("symbol", "company", "industry", "last", "chg_pct", "direction", "score", "stage", "tags", "headline", "spark", "plan")} | {"vol_ratio": r["metrics"]["vol_ratio"]}

    return {
        "asof": dates[-1] if dates else None, "history_days": len(dates), "snapshot_at": snap_at, "indices_at": idx_at,
        "universe": len(uni), "analyzed": len(results), "market": mk,
        "actions": [a | {"card": card(by[a["symbol"]])} for a in actions][:12],
        "radar_up": [card(r) for r in radar_up], "radar_down": [card(r) for r in radar_dn],
        "watchlist": [card(by[w["symbol"]]) | {"note": w.get("note", "")} for w in watch if w["symbol"] in by],
        "watch_missing": [w["symbol"] for w in watch if w["symbol"] not in by],
        "positions": [p | {"card": card(by[p["symbol"]]) if p["symbol"] in by else None} for p in positions],
        "capital": {"total": CAPITAL, "risk_per_trade": RISK_PER_TRADE, "max_position": MAX_POSITION},
        "_by": by,
    }


def stock_detail(root: Path, symbol: str, result: dict) -> dict | None:
    r = result["_by"].get(symbol)
    if not r:
        return None
    hist, _ = load_history(root, {symbol}, 130)
    s = hist.get(symbol)
    bars = [{"d": s.dates[i], "o": s.o[i], "h": s.h[i], "l": s.l[i], "c": s.c[i], "v": s.v[i], "dp": s.dp[i]}
            for i in range(len(s.c))] if s else []
    return r | {"bars": bars}
