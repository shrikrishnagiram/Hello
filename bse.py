"""
BSE (Sensex) option-chain reader. READ-ONLY.

BSE's public JSON API is undocumented and its field names change, so this module
detects columns by name pattern instead of hard-coding them, then converts the
result to the same shape as NSE's option chain so the rest of the app is shared.
If it cannot confidently recognise the format it raises ChainError showing what
it received -- it never guesses silently.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

from banknifty_strangle import ChainError, _get_json

BSE_HOME = "https://www.bseindia.com/"
BSE_EXPIRY_URL = "https://api.bseindia.com/BseIndiaAPI/api/ddlExpiry_IV/w"
BSE_CHAIN_URL = "https://api.bseindia.com/BseIndiaAPI/api/DerivOptionChain_IV/w"
SENSEX_SCRIP = "1"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": BSE_HOME,
    "Origin": "https://www.bseindia.com",
}
DATE_FORMATS = ("%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%d %b %y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")
SKIP_WORDS = ("qty", "chng", "chg", "change", "net", "pct", "percent", "time", "date", "expiry", "prev", "high", "low", "open_price", "close")


def _session():
    import requests
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(BSE_HOME, timeout=10)
    except Exception:
        pass
    return s


def _parse_date(v) -> datetime | None:
    if not isinstance(v, str):
        return None
    t = v.strip().split("T")[0]
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(t, f)
        except ValueError:
            continue
    return None


def find_expiries(obj) -> dict[str, str]:
    """All date-like strings anywhere in obj -> {NSE-format: raw string}."""
    out: dict[str, str] = {}

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        else:
            d = _parse_date(o)
            if d:
                out.setdefault(d.strftime("%d-%b-%Y"), o)
    walk(obj)
    return out


def _find_rows(obj) -> list[dict]:
    best: list[dict] = []

    def walk(o):
        nonlocal best
        if isinstance(o, list) and o and all(isinstance(x, dict) for x in o):
            if any("strike" in k.lower() for k in o[0]) and len(o) > len(best):
                best = o
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
    walk(obj)
    return best


def _num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).replace(",", "").strip()
    if t in ("", "-", "--", "NA", "null"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def map_columns(keys) -> dict:
    """Map BSE column names to (side, field). Returns {"strike": key, "spot": key|None, "CE": {...}, "PE": {...}}."""
    m = {"strike": None, "spot": None, "CE": {}, "PE": {}}
    for k in keys:
        kl = k.lower()
        toks = set(re.split(r"[^a-z0-9]+", kl))
        if kl.startswith(("c_", "call", "ce_", "ce")) and not kl.startswith("cur"):
            side = "CE"
        elif kl.startswith(("p_", "put", "pe_", "pe")) and not kl.startswith("prev"):
            side = "PE"
        else:
            side = None
        if side is None:
            if "strike" in kl and (m["strike"] is None or "price" in kl):
                m["strike"] = k
            elif any(w in kl for w in ("ula", "underlying", "spot")):
                m["spot"] = k
            continue
        if any(w in kl for w in SKIP_WORDS):
            continue
        if "open_int" in kl or "openint" in kl or "oi" in toks:
            f = "oi"
        elif "iv" in toks or "implied" in kl:
            f = "iv"
        elif "ltp" in kl or "last" in kl:
            f = "ltp"
        elif "bid" in kl or "buy" in kl:
            f = "bid"
        elif "ask" in kl or "sell" in kl or "offer" in kl:
            f = "ask"
        elif "vol" in kl:
            f = "volume"
        else:
            continue
        m[side].setdefault(f, k)
    return m


def to_nse_shape(raw: dict, expiry_nse: str) -> dict:
    rows = _find_rows(raw)
    if not rows:
        top = list(raw)[:20] if isinstance(raw, dict) else type(raw).__name__
        raise ChainError(f"BSE response has no option rows. Top-level keys: {top}. "
                         "Please send a screenshot of this message so the reader can be adjusted.")
    cols = map_columns(rows[0].keys())
    missing = [n for n, ok in (("strike", cols["strike"]), ("call LTP", cols["CE"].get("ltp")),
                               ("put LTP", cols["PE"].get("ltp"))) if not ok]
    if missing:
        raise ChainError(f"BSE format not recognised (missing {', '.join(missing)}). "
                         f"Columns received: {list(rows[0].keys())[:40]}. "
                         "Please send a screenshot of this message so the reader can be adjusted.")
    fields = {"ltp": "lastPrice", "bid": "bidprice", "ask": "askPrice", "iv": "impliedVolatility",
              "oi": "openInterest", "volume": "totalTradedVolume"}
    data, spot = [], None
    for r in rows:
        k = _num(r.get(cols["strike"]))
        if not k:
            continue
        if cols["spot"] and spot is None:
            spot = _num(r.get(cols["spot"]))
        row = {"strikePrice": k, "expiryDate": expiry_nse}
        for side in ("CE", "PE"):
            leg = {nse: _num(r.get(cols[side][f])) for f, nse in fields.items() if f in cols[side]}
            leg = {a: (b if b is not None else 0) for a, b in leg.items()}
            row[side] = leg
        data.append(row)
    if spot is None and isinstance(raw, dict):
        for key, v in raw.items():
            if any(w in key.lower() for w in ("ula", "underlying", "spot", "currvalue")) and _num(v):
                spot = _num(v)
                break
    if spot is None:  # put-call parity estimate at the strike where CE ~= PE
        priced = [d for d in data if d["CE"].get("lastPrice") and d["PE"].get("lastPrice")]
        if priced:
            atm = min(priced, key=lambda d: abs(d["CE"]["lastPrice"] - d["PE"]["lastPrice"]))
            spot = atm["strikePrice"] + atm["CE"]["lastPrice"] - atm["PE"]["lastPrice"]
    return {"records": {"underlyingValue": spot or 0,
                        "timestamp": datetime.now().strftime("%d-%b-%Y %H:%M:%S") + " (BSE)",
                        "data": data}}


def fetch_all_expiries_bse(choose=None) -> list[tuple[str, dict, str]]:
    choose = choose or (lambda exps: exps[:4])
    s = _session()
    info = _get_json(s, BSE_EXPIRY_URL, {"ProductType": "IO", "scrip_cd": SENSEX_SCRIP})
    found = find_expiries(info)
    today = datetime.now().date()
    upcoming = sorted((e for e in found if datetime.strptime(e, "%d-%b-%Y").date() >= today),
                      key=lambda e: datetime.strptime(e, "%d-%b-%Y"))
    if not upcoming:
        raise ChainError(f"BSE returned no upcoming Sensex expiries. Response started: {str(info)[:200]}")
    out, errors = [], []
    for e in choose(upcoming):
        raw_exp = found[e]
        try:
            raw = _get_json(s, BSE_CHAIN_URL, {"Expiry": raw_exp, "scrip_cd": SENSEX_SCRIP, "strprice": "0"})
            out.append((e, to_nse_shape(raw, e), "bse"))
        except ChainError as ex:
            errors.append(f"{e}: {ex}")
        time.sleep(0.6)
    if not out:
        raise ChainError("Could not fetch Sensex option chains from BSE:\n  - " + "\n  - ".join(errors))
    return out
