#!/usr/bin/env python3
"""
Nifty 50 data downloader (READ-ONLY): index history, live option chain, and
historical option prices for every strike.

    python nifty_data.py index   --from 2020-01-01                 # daily OHLC of Nifty 50
    python nifty_data.py chain                                     # live chain, all expiries
    python nifty_data.py options --from 2026-10-01 --to 2026-11-10 # every Nifty option, every day
    python nifty_data.py options --from 2026-10-01 --expiry 2026-11-24 --near 10 --pivot
    python nifty_data.py contract --strike 24000 --type CE --expiry 2026-11-24 --from 2026-10-01

Sources (all official NSE, plus Yahoo for long index history):
  * index    : Yahoo Finance ^NSEI (one request for decades); fallback NSE's daily
               index-close archive (ind_close_all_DDMMYYYY.csv), one file per day.
  * chain    : NSE option-chain API (same code as the strangle scanner).
  * options  : NSE F&O end-of-day bhavcopy, one zip per trading day. It holds the
               open/high/low/close, settle price, OI and volume of EVERY strike of
               EVERY expiry. New UDiFF format from 8-Jul-2024, old format before.
               Downloaded zips are cached in data/bhav_cache/, so re-runs are fast.
  * contract : one strike's history -- NSE's foCPV API, falling back to bhavcopies.

Run it on your own computer or phone (Termux): NSE blocks most cloud servers.
Output goes to data/ (gitignored). Nothing here places orders.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import banknifty_strangle as bs

SYMBOL = "NIFTY"
UDIFF_START = date(2024, 7, 8)  # first day of the new F&O bhavcopy format

UDIFF_URLS = [
    "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip",
]
OLD_BHAV_URLS = [
    "https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{y}/{mon}/fo{d}{mon}{y}bhav.csv.zip",
    "https://archives.nseindia.com/content/historical/DERIVATIVES/{y}/{mon}/fo{d}{mon}{y}bhav.csv.zip",
]
IND_CLOSE_URLS = [
    "https://nsearchives.nseindia.com/content/indices/ind_close_all_{dmy}.csv",
    "https://archives.nseindia.com/content/indices/ind_close_all_{dmy}.csv",
]
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI"
FOCPV_URL = "https://www.nseindia.com/api/historical/foCPV"

OPTION_COLS = ["date", "symbol", "expiry", "strike", "type", "open", "high", "low", "close",
               "settle", "prev_close", "underlying", "oi", "oi_change", "volume", "contracts",
               "turnover"]
INDEX_COLS = ["date", "open", "high", "low", "close", "volume"]


class DataError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def parse_date(s: str) -> date:
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d%m%Y", "%Y%m%d", "%d-%B-%Y"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    raise DataError(f"Unrecognised date {s!r}. Use YYYY-MM-DD.")


def num(v) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "NA", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def write_rows(path: Path, cols: list[str], rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def _http():
    try:
        import requests
    except ImportError as e:  # pragma: no cover
        raise DataError("The 'requests' package is required: pip install requests") from e
    s = requests.Session()
    s.headers.update(bs.HEADERS)
    return s


def _download(session, urls: list[str], retries: int = 2) -> bytes | None:
    """Return the body of the first URL that answers 200, or None if all 404."""
    last = None
    for url in urls:
        for attempt in range(retries):
            try:
                r = session.get(url, timeout=30)
                if r.status_code == 200 and r.content:
                    return r.content
                if r.status_code == 404:
                    last = "404"
                    break  # holiday / not published -- no point retrying
                last = f"HTTP {r.status_code}"
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
            time.sleep(1.0 * (attempt + 1))
    if last == "404":
        return None
    raise DataError(f"Download failed ({last}): {urls[0]}")


# --------------------------------------------------------------------------- #
# Index history
# --------------------------------------------------------------------------- #
def parse_yahoo(data: dict) -> list[dict]:
    try:
        res = data["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError) as e:
        err = (data.get("chart") or {}).get("error") if isinstance(data, dict) else None
        raise DataError(f"Unexpected Yahoo response ({err or e}).") from None
    rows = []
    for i, t in enumerate(ts):
        c = q["close"][i]
        if c is None:
            continue
        # Yahoo timestamps are the session open in IST (UTC+5:30).
        d = (datetime.fromtimestamp(t, timezone.utc) + timedelta(hours=5, minutes=30)).date()
        rows.append({"date": d.isoformat(), "open": q["open"][i], "high": q["high"][i],
                     "low": q["low"][i], "close": c, "volume": q.get("volume", [None] * len(ts))[i]})
    # Yahoo sometimes repeats the live day; keep the last row per date.
    return list({r["date"]: r for r in rows}.values())


def index_from_yahoo(start: date, end: date) -> list[dict]:
    s = _http()
    p1 = int(datetime(start.year, start.month, start.day).timestamp())
    p2 = int(datetime(end.year, end.month, end.day).timestamp()) + 86400
    r = s.get(YAHOO_URL, params={"period1": p1, "period2": p2, "interval": "1d"}, timeout=30)
    if r.status_code != 200:
        raise DataError(f"Yahoo HTTP {r.status_code}")
    return [x for x in parse_yahoo(r.json()) if start.isoformat() <= x["date"] <= end.isoformat()]


def parse_ind_close(text: str, d: date, index_name: str = "Nifty 50") -> dict | None:
    for row in csv.DictReader(io.StringIO(text)):
        row = {k.strip(): v for k, v in row.items() if k}
        if (row.get("Index Name") or "").strip().lower() == index_name.lower():
            return {"date": d.isoformat(), "open": num(row.get("Open Index Value")),
                    "high": num(row.get("High Index Value")), "low": num(row.get("Low Index Value")),
                    "close": num(row.get("Closing Index Value")), "volume": num(row.get("Volume"))}
    return None


def index_from_nse(start: date, end: date, verbose=True) -> list[dict]:
    s = _http()
    rows = []
    for d in weekdays(start, end):
        dmy = d.strftime("%d%m%Y")
        body = _download(s, [u.format(dmy=dmy) for u in IND_CLOSE_URLS])
        if body is None:
            continue  # holiday
        row = parse_ind_close(body.decode("utf-8", "replace"), d)
        if row:
            rows.append(row)
            if verbose:
                print(f"  {d}  close {row['close']}", file=sys.stderr)
        time.sleep(0.3)
    return rows


def cmd_index(a) -> int:
    start, end = parse_date(a.date_from), parse_date(a.date_to)
    rows, src = None, None
    if a.source in ("auto", "yahoo"):
        try:
            rows, src = index_from_yahoo(start, end), "Yahoo ^NSEI"
        except Exception as e:
            print(f"Yahoo failed: {e}", file=sys.stderr)
            if a.source == "yahoo":
                return 2
    if not rows:
        print("Using NSE daily index archive (one file per day)...", file=sys.stderr)
        rows, src = index_from_nse(start, end), "NSE ind_close_all"
    if not rows:
        print("No index data returned.", file=sys.stderr)
        return 2
    path = write_rows(Path(a.out_dir) / f"nifty50_daily_{start}_{end}.csv", INDEX_COLS, rows)
    print(f"{len(rows)} days of Nifty 50 ({rows[0]['date']} .. {rows[-1]['date']}) from {src} -> {path}")
    return 0


# --------------------------------------------------------------------------- #
# Live option chain
# --------------------------------------------------------------------------- #
def cmd_chain(a) -> int:
    choose = (lambda exps: exps) if a.all_expiries else (lambda exps: exps[: a.expiries])
    try:
        got = bs.fetch_all_expiries(a.source, symbol=SYMBOL, choose=choose)
    except bs.ChainError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    rows = []
    fetched = datetime.now().isoformat(timespec="seconds")
    for expiry, data, source in got:
        try:
            chain = bs.parse_chain(data, expiry, source)
        except bs.ChainError as e:
            print(f"  {expiry}: {e}", file=sys.stderr)
            continue
        legs: dict[float, dict] = {}
        for q in chain.quotes:
            legs.setdefault(q.strike, {})[q.opt_type] = q
        for k in sorted(legs):
            row = {"fetched_at": fetched, "nse_timestamp": chain.timestamp, "expiry": expiry,
                   "spot": chain.spot, "strike": k}
            for side in ("CE", "PE"):
                q = legs[k].get(side)
                for c in ("ltp", "bid", "ask", "iv", "oi", "volume"):
                    row[f"{side.lower()}_{c}"] = getattr(q, c) if q else ""
            rows.append(row)
        print(f"  {expiry}: {len(legs)} strikes, spot {chain.spot}", file=sys.stderr)
    if not rows:
        print("ERROR: no chain rows parsed.", file=sys.stderr)
        return 2
    cols = ["fetched_at", "nse_timestamp", "expiry", "spot", "strike"] + [
        f"{s}_{c}" for s in ("ce", "pe") for c in ("ltp", "bid", "ask", "iv", "oi", "volume")]
    path = write_rows(Path(a.out_dir) / f"nifty_chain_{datetime.now():%Y%m%d_%H%M%S}.csv", cols, rows)
    print(f"{len(rows)} strike rows across {len(got)} expiries -> {path}")
    return 0


# --------------------------------------------------------------------------- #
# Historical options: F&O bhavcopy
# --------------------------------------------------------------------------- #
def bhav_urls(d: date) -> list[str]:
    if d >= UDIFF_START:
        return [u.format(ymd=d.strftime("%Y%m%d")) for u in UDIFF_URLS]
    mon = d.strftime("%b").upper()
    return [u.format(y=d.year, mon=mon, d=d.strftime("%d")) for u in OLD_BHAV_URLS]


def _iso(v) -> str:
    return parse_date(v).isoformat()


def parse_bhav_csv(text: str, d: date, symbol: str = SYMBOL) -> list[dict]:
    """Index-option rows for `symbol` from either bhavcopy format."""
    rdr = csv.DictReader(io.StringIO(text))
    rows = []
    fields = [f.strip() for f in (rdr.fieldnames or [])]
    if "TckrSymb" in fields:  # UDiFF (8-Jul-2024 onwards)
        for r in rdr:
            r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
            if r.get("TckrSymb") != symbol or r.get("FinInstrmTp") != "IDO":
                continue
            rows.append({
                "date": _iso(r.get("TradDt") or d.isoformat()), "symbol": symbol,
                "expiry": _iso(r["XpryDt"]), "strike": num(r.get("StrkPric")), "type": r.get("OptnTp"),
                "open": num(r.get("OpnPric")), "high": num(r.get("HghPric")), "low": num(r.get("LwPric")),
                "close": num(r.get("ClsPric")), "settle": num(r.get("SttlmPric")),
                "prev_close": num(r.get("PrvsClsgPric")), "underlying": num(r.get("UndrlygPric")),
                "oi": num(r.get("OpnIntrst")), "oi_change": num(r.get("ChngInOpnIntrst")),
                "volume": num(r.get("TtlTradgVol")), "contracts": num(r.get("TtlNbOfTxsExctd")),
                "turnover": num(r.get("TtlTrfVal")),
            })
    elif "INSTRUMENT" in fields:  # old format
        for r in rdr:
            r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
            if r.get("SYMBOL") != symbol or r.get("INSTRUMENT") != "OPTIDX":
                continue
            rows.append({
                "date": _iso(r.get("TIMESTAMP") or d.isoformat()), "symbol": symbol,
                "expiry": _iso(r["EXPIRY_DT"]), "strike": num(r.get("STRIKE_PR")), "type": r.get("OPTION_TYP"),
                "open": num(r.get("OPEN")), "high": num(r.get("HIGH")), "low": num(r.get("LOW")),
                "close": num(r.get("CLOSE")), "settle": num(r.get("SETTLE_PR")), "prev_close": None,
                "underlying": None, "oi": num(r.get("OPEN_INT")), "oi_change": num(r.get("CHG_IN_OI")),
                "volume": None, "contracts": num(r.get("CONTRACTS")),
                "turnover": (num(r.get("VAL_INLAKH")) or 0) * 1e5 or None,
            })
    else:
        raise DataError(f"Unrecognised bhavcopy columns for {d}: {fields[:8]}...")
    return rows


def read_zip_csv(body: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        name = next((n for n in z.namelist() if n.lower().endswith(".csv")), None)
        if not name:
            raise DataError("Bhavcopy zip has no CSV inside.")
        return z.read(name).decode("utf-8", "replace")


def load_bhav_day(session, d: date, cache: Path) -> str | None:
    """CSV text of the day's F&O bhavcopy (cached), or None for a holiday."""
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"fo_{d:%Y%m%d}.csv.zip"
    miss = cache / f"fo_{d:%Y%m%d}.none"
    if f.exists():
        return read_zip_csv(f.read_bytes())
    if miss.exists() and d < date.today() - timedelta(days=3):
        return None
    body = _download(session, bhav_urls(d))
    if body is None:
        if d < date.today() - timedelta(days=3):
            miss.touch()
        return None
    f.write_bytes(body)
    time.sleep(0.4)  # be polite to NSE
    return read_zip_csv(body)


def filter_rows(rows: list[dict], expiry: date | None = None, opt_type: str | None = None,
                strikes: set[float] | None = None, near: int | None = None,
                spot: float | None = None, min_strike=None, max_strike=None) -> list[dict]:
    out = [r for r in rows
           if (expiry is None or r["expiry"] == expiry.isoformat())
           and (opt_type in (None, "both") or r["type"] == opt_type)
           and (strikes is None or r["strike"] in strikes)
           and (min_strike is None or (r["strike"] or 0) >= min_strike)
           and (max_strike is None or (r["strike"] or 0) <= max_strike)]
    if near and out:
        ref = spot or next((r["underlying"] for r in out if r["underlying"]), None)
        if ref:
            # keep the `near` strikes on each side of the reference price, per expiry
            by_exp: dict[str, list[float]] = {}
            for r in out:
                by_exp.setdefault(r["expiry"], []).append(r["strike"])
            keep: dict[str, set] = {}
            for e, ks in by_exp.items():
                ks = sorted(set(ks), key=lambda k: abs(k - ref))
                keep[e] = set(ks[: 2 * near + 1])
            out = [r for r in out if r["strike"] in keep[r["expiry"]]]
    return out


def collect_options(start: date, end: date, cache: Path, verbose=True, **filters) -> list[dict]:
    session = _http()
    rows: list[dict] = []
    for d in weekdays(start, end):
        try:
            text = load_bhav_day(session, d, cache)
        except DataError as e:
            print(f"  {d}: {e}", file=sys.stderr)
            continue
        if text is None:
            if verbose:
                print(f"  {d}: no bhavcopy (holiday?)", file=sys.stderr)
            continue
        day = filter_rows(parse_bhav_csv(text, d), **filters)
        if verbose:
            print(f"  {d}: {len(day)} rows", file=sys.stderr)
        rows += day
    rows.sort(key=lambda r: (r["date"], r["expiry"], r["type"], r["strike"]))
    return rows


def pivot(rows: list[dict], value: str = "close") -> tuple[list[str], list[dict]]:
    """Wide table: one row per (date, expiry, type), one column per strike."""
    strikes = sorted({r["strike"] for r in rows})
    table: dict[tuple, dict] = {}
    for r in rows:
        key = (r["date"], r["expiry"], r["type"])
        t = table.setdefault(key, {"date": r["date"], "expiry": r["expiry"], "type": r["type"],
                                   "underlying": r["underlying"]})
        t[f"{r['strike']:g}"] = r[value]
    cols = ["date", "expiry", "type", "underlying"] + [f"{k:g}" for k in strikes]
    return cols, [table[k] for k in sorted(table)]


def _filters(a) -> dict:
    return {
        "expiry": parse_date(a.expiry) if a.expiry else None,
        "opt_type": a.type,
        "strikes": {float(x) for x in a.strikes.split(",")} if a.strikes else None,
        "near": a.near, "min_strike": a.min_strike, "max_strike": a.max_strike,
    }


def cmd_options(a) -> int:
    start, end = parse_date(a.date_from), parse_date(a.date_to)
    f = _filters(a)
    rows = collect_options(start, end, Path(a.out_dir) / "bhav_cache", **f)
    if not rows:
        print("No option rows (check dates, expiry and network).", file=sys.stderr)
        return 2
    tag = f"_{f['expiry']}" if f["expiry"] else ""
    out = Path(a.out_dir)
    path = write_rows(out / f"nifty_options_{start}_{end}{tag}.csv", OPTION_COLS, rows)
    print(f"{len(rows)} option rows, {len({r['date'] for r in rows})} days, "
          f"{len({r['expiry'] for r in rows})} expiries -> {path}")
    if a.pivot:
        cols, prow = pivot(rows, a.pivot_value)
        p = write_rows(out / f"nifty_options_{a.pivot_value}_matrix_{start}_{end}{tag}.csv", cols, prow)
        print(f"strike matrix ({a.pivot_value}) -> {p}")
    return 0


# --------------------------------------------------------------------------- #
# Single contract
# --------------------------------------------------------------------------- #
def parse_focpv(data: dict) -> list[dict]:
    rows = []
    for r in data.get("data") or []:
        rows.append({
            "date": _iso(r.get("FH_TIMESTAMP")), "symbol": r.get("FH_SYMBOL", SYMBOL),
            "expiry": _iso(r.get("FH_EXPIRY_DT")), "strike": num(r.get("FH_STRIKE_PRICE")),
            "type": r.get("FH_OPTION_TYPE"), "open": num(r.get("FH_OPENING_PRICE")),
            "high": num(r.get("FH_TRADE_HIGH_PRICE")), "low": num(r.get("FH_TRADE_LOW_PRICE")),
            "close": num(r.get("FH_CLOSING_PRICE")), "settle": num(r.get("FH_SETTLE_PRICE")),
            "prev_close": None, "underlying": num(r.get("FH_UNDERLYING_VALUE")),
            "oi": num(r.get("FH_OPEN_INT")), "oi_change": num(r.get("FH_CHANGE_IN_OI")),
            "volume": num(r.get("FH_TOT_TRADED_QTY")), "contracts": num(r.get("FH_TOT_TRADED_CONTRACTS")),
            "turnover": num(r.get("FH_TOT_TRADED_VAL")),
        })
    return sorted(rows, key=lambda r: r["date"])


def contract_from_api(strike: float, opt_type: str, expiry: date, start: date, end: date) -> list[dict]:
    s = bs._nse_session()
    data = bs._get_json(s, FOCPV_URL, {
        "from": start.strftime("%d-%m-%Y"), "to": end.strftime("%d-%m-%Y"),
        "instrumentType": "OPTIDX", "symbol": SYMBOL, "year": expiry.year,
        "expiryDate": expiry.strftime("%d-%b-%Y"), "optionType": opt_type,
        "strikePrice": f"{strike:.2f}"})
    return parse_focpv(data)


def cmd_contract(a) -> int:
    expiry = parse_date(a.expiry)
    end = min(parse_date(a.date_to), expiry)
    start = parse_date(a.date_from) if a.date_from else end - timedelta(days=90)
    rows = []
    if a.source in ("auto", "api"):
        try:
            rows = contract_from_api(a.strike, a.type, expiry, start, end)
        except Exception as e:
            print(f"NSE foCPV API failed ({e}); using bhavcopies.", file=sys.stderr)
    if not rows:
        rows = collect_options(start, end, Path(a.out_dir) / "bhav_cache", expiry=expiry,
                               opt_type=a.type, strikes={float(a.strike)})
    if not rows:
        print("No data for that contract (check strike/expiry/dates).", file=sys.stderr)
        return 2
    path = write_rows(Path(a.out_dir) / f"nifty_{expiry}_{a.strike:g}{a.type}_{start}_{end}.csv",
                      OPTION_COLS, rows)
    print(f"{len(rows)} days of NIFTY {expiry} {a.strike:g} {a.type} -> {path}")
    for r in rows[-5:]:
        print(f"  {r['date']}  close {r['close']}  settle {r['settle']}  OI {r['oi']}  spot {r['underlying']}")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    today = date.today().isoformat()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out-dir", default=bs.DEFAULT_OUT_DIR)
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("index", help="Nifty 50 daily OHLC")
    i.add_argument("--from", dest="date_from", default="2015-01-01")
    i.add_argument("--to", dest="date_to", default=today)
    i.add_argument("--source", choices=["auto", "yahoo", "nse"], default="auto")

    c = sub.add_parser("chain", help="live option chain snapshot")
    c.add_argument("--expiries", type=int, default=6, help="nearest N expiries (default 6)")
    c.add_argument("--all-expiries", action="store_true")
    c.add_argument("--source", choices=["auto", "nse", "nsepython"], default="auto")

    o = sub.add_parser("options", help="historical prices of Nifty options (bhavcopy)")
    o.add_argument("--from", dest="date_from", default=(date.today() - timedelta(days=30)).isoformat())
    o.add_argument("--to", dest="date_to", default=today)
    o.add_argument("--expiry", help="only this expiry (YYYY-MM-DD)")
    o.add_argument("--type", choices=["CE", "PE", "both"], default="both")
    o.add_argument("--strikes", help="comma-separated strikes, e.g. 23500,24000,24500")
    o.add_argument("--min-strike", type=float)
    o.add_argument("--max-strike", type=float)
    o.add_argument("--near", type=int, help="keep N strikes each side of that day's spot")
    o.add_argument("--pivot", action="store_true", help="also write a date x strike matrix")
    o.add_argument("--pivot-value", default="close", choices=["close", "settle", "oi", "volume"])

    k = sub.add_parser("contract", help="history of one option contract")
    k.add_argument("--strike", type=float, required=True)
    k.add_argument("--type", choices=["CE", "PE"], required=True)
    k.add_argument("--expiry", required=True)
    k.add_argument("--from", dest="date_from")
    k.add_argument("--to", dest="date_to", default=today)
    k.add_argument("--source", choices=["auto", "api", "bhav"], default="auto")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    try:
        return {"index": cmd_index, "chain": cmd_chain,
                "options": cmd_options, "contract": cmd_contract}[a.cmd](a)
    except DataError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
