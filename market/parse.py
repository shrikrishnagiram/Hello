"""Turn NSE responses into clean rows of actual numbers.

Every parser checks that the columns it needs exist and raises ParseError naming
what's missing (so a format change shows up as a clear message, never as silent
wrong data).
"""

from __future__ import annotations

import csv
import io
import math
from collections import defaultdict


class ParseError(ValueError):
    pass


def num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and math.isnan(v)) else float(v)
    t = str(v).replace(",", "").strip()
    if t in ("", "-", "--", "NA", "null", "None"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Live (JSON API)
# --------------------------------------------------------------------------- #
QUOTE_FIELDS = ["symbol", "open", "high", "low", "last", "prev_close", "change_pct",
                "volume", "value_cr", "year_high", "year_low", "updated"]


def index_quotes(js: dict) -> tuple[list[dict], dict | None]:
    """/api/equity-stockIndices?index=NIFTY 200 -> (stock rows, index row)."""
    rows = js.get("data") if isinstance(js, dict) else None
    if not rows:
        raise ParseError(f"equity-stockIndices: no 'data' rows (keys: {list(js)[:10] if isinstance(js, dict) else type(js)})")
    need = {"symbol", "lastPrice", "totalTradedVolume"}
    missing = need - set(rows[-1])
    if missing:
        raise ParseError(f"equity-stockIndices: missing {sorted(missing)}; got {sorted(rows[-1])[:25]}")
    out, idx = [], None
    for r in rows:
        row = {
            "symbol": r.get("symbol"), "open": num(r.get("open")), "high": num(r.get("dayHigh")),
            "low": num(r.get("dayLow")), "last": num(r.get("lastPrice")),
            "prev_close": num(r.get("previousClose")), "change_pct": num(r.get("pChange")),
            "volume": num(r.get("totalTradedVolume")),
            "value_cr": (num(r.get("totalTradedValue")) or 0) / 1e7 or None,
            "year_high": num(r.get("yearHigh")), "year_low": num(r.get("yearLow")),
            "updated": r.get("lastUpdateTime") or js.get("timestamp"),
        }
        if r.get("priority") == 1 or (r.get("symbol") or "").upper().startswith("NIFTY"):
            idx = row
        else:
            out.append(row)
    return out, idx


INDEX_FIELDS = ["index", "last", "change_pct", "open", "high", "low", "prev_close",
                "advances", "declines", "unchanged"]
INDICES_WANTED = {"NIFTY 50", "NIFTY BANK", "NIFTY 200", "INDIA VIX", "NIFTY IT", "NIFTY AUTO",
                  "NIFTY FMCG", "NIFTY METAL", "NIFTY PHARMA", "NIFTY REALTY", "NIFTY ENERGY",
                  "NIFTY FINANCIAL SERVICES", "NIFTY PSU BANK", "NIFTY MIDCAP 100",
                  "NIFTY SMALLCAP 100", "NIFTY CONSUMER DURABLES", "NIFTY MEDIA", "NIFTY OIL & GAS",
                  "NIFTY HEALTHCARE INDEX", "NIFTY PRIVATE BANK", "NIFTY CPSE", "NIFTY INFRASTRUCTURE"}


def all_indices(js: dict) -> list[dict]:
    rows = js.get("data") if isinstance(js, dict) else None
    if not rows or "index" not in rows[0]:
        raise ParseError("allIndices: unexpected format")
    out = []
    for r in rows:
        name = (r.get("index") or "").upper()
        if name in INDICES_WANTED:
            out.append({"index": name, "last": num(r.get("last")), "change_pct": num(r.get("percentChange")),
                        "open": num(r.get("open")), "high": num(r.get("high")), "low": num(r.get("low")),
                        "prev_close": num(r.get("previousClose")), "advances": num(r.get("advances")),
                        "declines": num(r.get("declines")), "unchanged": num(r.get("unchanged"))})
    return out


OI_FIELDS = ["symbol", "latest_oi", "prev_oi", "change_oi", "change_oi_pct", "volume", "underlying"]


def oi_spurts(js) -> list[dict]:
    """/api/live-analysis-oi-spurts-underlyings -> futures+options open interest per stock."""
    rows = js.get("data") if isinstance(js, dict) else js
    if not rows or not isinstance(rows, list):
        raise ParseError("oi-spurts: no rows")
    k = {key.lower(): key for key in rows[0]}
    def pick(r, *names):
        for n in names:
            if n.lower() in k:
                return num(r.get(k[n.lower()]))
        return None
    if "symbol" not in k:
        raise ParseError(f"oi-spurts: no symbol column; got {list(rows[0])[:20]}")
    out = []
    for r in rows:
        latest, prev = pick(r, "latestOI"), pick(r, "prevOI")
        ch = pick(r, "changeInOI")
        if ch is None and latest is not None and prev is not None:
            ch = latest - prev
        out.append({"symbol": r.get(k["symbol"]), "latest_oi": latest, "prev_oi": prev, "change_oi": ch,
                    "change_oi_pct": (ch / prev * 100) if (ch is not None and prev) else None,
                    "volume": pick(r, "volume"), "underlying": pick(r, "underlyingValue")})
    return out


CHAIN_FIELDS = ["symbol", "expiry", "spot", "ce_oi", "pe_oi", "ce_chg_oi", "pe_chg_oi", "pcr",
                "max_ce_strike", "max_pe_strike", "atm_strike", "atm_ce_iv", "atm_pe_iv", "atm_ce", "atm_pe"]


def chain_summary(symbol: str, js: dict, expiry: str) -> dict:
    """Summarise one option chain (NSE v3 shape): where positions sit, put/call ratio, ATM IV."""
    rec = js.get("records") or {}
    rows = rec.get("data") or []
    if not rows:
        raise ParseError(f"{symbol}: option chain has no rows")
    spot = num(rec.get("underlyingValue"))
    if not spot:
        for r in rows:
            for side in ("CE", "PE"):
                if isinstance(r.get(side), dict) and num(r[side].get("underlyingValue")):
                    spot = num(r[side]["underlyingValue"])
                    break
            if spot:
                break
    ce_oi = pe_oi = ce_ch = pe_ch = 0.0
    max_ce = (0.0, None)
    max_pe = (0.0, None)
    strikes = {}
    for r in rows:
        k = num(r.get("strikePrice"))
        if k is None:
            continue
        ce, pe = r.get("CE") or {}, r.get("PE") or {}
        co, po = num(ce.get("openInterest")) or 0, num(pe.get("openInterest")) or 0
        ce_oi += co
        pe_oi += po
        ce_ch += num(ce.get("changeinOpenInterest")) or 0
        pe_ch += num(pe.get("changeinOpenInterest")) or 0
        if co > max_ce[0]:
            max_ce = (co, k)
        if po > max_pe[0]:
            max_pe = (po, k)
        strikes[k] = (ce, pe)
    atm = min(strikes, key=lambda x: abs(x - spot)) if (strikes and spot) else None
    ce_a, pe_a = strikes.get(atm, ({}, {}))
    return {"symbol": symbol, "expiry": expiry, "spot": spot, "ce_oi": ce_oi, "pe_oi": pe_oi,
            "ce_chg_oi": ce_ch, "pe_chg_oi": pe_ch, "pcr": (pe_oi / ce_oi) if ce_oi else None,
            "max_ce_strike": max_ce[1], "max_pe_strike": max_pe[1], "atm_strike": atm,
            "atm_ce_iv": num(ce_a.get("impliedVolatility")), "atm_pe_iv": num(pe_a.get("impliedVolatility")),
            "atm_ce": num(ce_a.get("lastPrice")), "atm_pe": num(pe_a.get("lastPrice"))}


# --------------------------------------------------------------------------- #
# End-of-day archive files
# --------------------------------------------------------------------------- #
EQ_FIELDS = ["date", "symbol", "prev_close", "open", "high", "low", "close", "avg_price",
             "volume", "turnover_lacs", "trades", "deliv_qty", "deliv_pct"]


def sec_bhav(text: str, symbols: set[str] | None = None) -> list[dict]:
    """sec_bhavdata_full_DDMMYYYY.csv -> equity (series EQ/BE) rows incl. delivery %."""
    rdr = csv.DictReader(io.StringIO(text))
    if not rdr.fieldnames:
        raise ParseError("equity daily file is empty")
    cols = {c.strip().upper(): c for c in rdr.fieldnames}
    need = ["SYMBOL", "SERIES", "DATE1", "CLOSE_PRICE", "TTL_TRD_QNTY", "DELIV_PER"]
    missing = [c for c in need if c not in cols]
    if missing:
        raise ParseError(f"equity daily file: missing {missing}; got {list(cols)[:20]}")
    g = lambda r, c: r.get(cols[c]) if c in cols else None
    out = []
    for r in rdr:
        sym = (g(r, "SYMBOL") or "").strip()
        if (g(r, "SERIES") or "").strip() not in ("EQ", "BE", "BZ"):
            continue
        if symbols and sym not in symbols:
            continue
        out.append({"date": (g(r, "DATE1") or "").strip(), "symbol": sym,
                    "prev_close": num(g(r, "PREV_CLOSE")), "open": num(g(r, "OPEN_PRICE")),
                    "high": num(g(r, "HIGH_PRICE")), "low": num(g(r, "LOW_PRICE")),
                    "close": num(g(r, "CLOSE_PRICE")), "avg_price": num(g(r, "AVG_PRICE")),
                    "volume": num(g(r, "TTL_TRD_QNTY")), "turnover_lacs": num(g(r, "TURNOVER_LACS")),
                    "trades": num(g(r, "NO_OF_TRADES")), "deliv_qty": num(g(r, "DELIV_QTY")),
                    "deliv_pct": num(g(r, "DELIV_PER"))})
    return out


FO_FIELDS = ["date", "symbol", "underlying", "fut_expiry", "fut_close", "fut_oi", "fut_chg_oi", "fut_volume",
             "fut_oi_all", "fut_chg_oi_all", "opt_expiry", "ce_oi", "pe_oi", "ce_chg_oi", "pe_chg_oi", "pcr",
             "max_ce_strike", "max_pe_strike", "opt_volume", "lot_size"]


def fo_bhav_summary(text: str, symbols: set[str] | None = None) -> list[dict]:
    """UDiFF F&O daily file -> one summary row per underlying (futures + nearest-expiry options)."""
    rdr = csv.DictReader(io.StringIO(text))
    if not rdr.fieldnames:
        raise ParseError("F&O daily file is empty")
    f = set(rdr.fieldnames)
    need = {"TradDt", "FinInstrmTp", "TckrSymb", "XpryDt", "OpnIntrst", "ClsPric"}
    if need - f:
        raise ParseError(f"F&O daily file: missing {sorted(need - f)}; got {sorted(f)[:25]}")
    futs = defaultdict(list)
    opts = defaultdict(lambda: defaultdict(list))
    date = None
    for r in rdr:
        tp = (r.get("FinInstrmTp") or "").strip()
        sym = (r.get("TckrSymb") or "").strip()
        if symbols and sym not in symbols:
            continue
        date = date or (r.get("TradDt") or "").strip()
        if tp in ("STF", "IDF"):
            futs[sym].append(r)
        elif tp in ("STO", "IDO"):
            opts[sym][(r.get("XpryDt") or "").strip()].append(r)
    out = []
    for sym in sorted(set(futs) | set(opts)):
        row = {k: None for k in FO_FIELDS}
        row.update({"date": date, "symbol": sym})
        fl = sorted(futs.get(sym, []), key=lambda r: r.get("XpryDt") or "")
        if fl:
            near = fl[0]
            row.update({"fut_expiry": near.get("XpryDt"), "fut_close": num(near.get("ClsPric")),
                        "fut_oi": num(near.get("OpnIntrst")), "fut_chg_oi": num(near.get("ChngInOpnIntrst")),
                        "fut_volume": num(near.get("TtlTradgVol")), "underlying": num(near.get("UndrlygPric")),
                        "lot_size": num(near.get("NewBrdLotQty")),
                        "fut_oi_all": sum(num(x.get("OpnIntrst")) or 0 for x in fl),
                        "fut_chg_oi_all": sum(num(x.get("ChngInOpnIntrst")) or 0 for x in fl)})
        ex = sorted(opts.get(sym, {}))
        if ex:
            rows = opts[sym][ex[0]]
            ce = [r for r in rows if (r.get("OptnTp") or "").strip() == "CE"]
            pe = [r for r in rows if (r.get("OptnTp") or "").strip() == "PE"]
            s = lambda rs, c: sum(num(x.get(c)) or 0 for x in rs)
            mx = lambda rs: max(rs, key=lambda x: num(x.get("OpnIntrst")) or 0) if rs else None
            mce, mpe = mx(ce), mx(pe)
            row.update({"opt_expiry": ex[0], "ce_oi": s(ce, "OpnIntrst"), "pe_oi": s(pe, "OpnIntrst"),
                        "ce_chg_oi": s(ce, "ChngInOpnIntrst"), "pe_chg_oi": s(pe, "ChngInOpnIntrst"),
                        "max_ce_strike": num(mce.get("StrkPric")) if mce else None,
                        "max_pe_strike": num(mpe.get("StrkPric")) if mpe else None,
                        "opt_volume": s(rows, "TtlTradgVol")})
            row["pcr"] = (row["pe_oi"] / row["ce_oi"]) if row["ce_oi"] else None
            if row["underlying"] is None and rows:
                row["underlying"] = num(rows[0].get("UndrlygPric"))
            if row["lot_size"] is None and rows:
                row["lot_size"] = num(rows[0].get("NewBrdLotQty"))
        out.append(row)
    return out
