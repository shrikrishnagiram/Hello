"""Collector jobs. Each returns a short human-readable result line."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from . import config, parse, store
from .nse import NSEClient, NSEError, unzip_first, url_fo_bhav, url_sec_bhav


# --------------------------------------------------------------------------- #
# Trading calendar
# --------------------------------------------------------------------------- #
def holidays(client: NSEClient | None = None) -> set[date]:
    """NSE trading holidays for this year (cached in meta/holidays.json; weekends always closed)."""
    p = store.root() / "meta" / "holidays.json"
    year = config.now_ist().year
    try:
        cached = json.loads(p.read_text())
        if cached.get("year") == year:
            return {date.fromisoformat(d) for d in cached["dates"]}
    except Exception:
        pass
    if client is None:
        return set()
    try:
        js = client.json("holiday-master", {"type": "trading"})
        rows = js.get("CM") or js.get("FO") or []
        ds = set()
        for r in rows:
            try:
                ds.add(datetime.strptime(r["tradingDate"], "%d-%b-%Y").date())
            except Exception:
                continue
        if ds:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"year": year, "dates": sorted(d.isoformat() for d in ds)}))
        return ds
    except NSEError:
        return set()


def is_trading_day(d: date, hol: set[date]) -> bool:
    return d.weekday() < 5 and d not in hol


# --------------------------------------------------------------------------- #
# Intraday
# --------------------------------------------------------------------------- #
def universe_quotes(client: NSEClient) -> tuple[list[dict], str]:
    """Nifty 200 quotes; falls back to Nifty 100 + Nifty Midcap 100 if NSE refuses NIFTY 200."""
    try:
        rows, _ = parse.index_quotes(client.json("equity-stockIndices", {"index": config.UNIVERSE_INDEX}))
        return rows, config.UNIVERSE_INDEX
    except (NSEError, parse.ParseError) as first:
        rows, seen = [], set()
        for part in config.UNIVERSE_PARTS:
            try:
                part_rows, _ = parse.index_quotes(client.json("equity-stockIndices", {"index": part}))
            except (NSEError, parse.ParseError) as e:
                raise NSEError(f"{config.UNIVERSE_INDEX} failed ({first}); backup {part} also failed ({e})") from None
            for r in part_rows:
                if r["symbol"] not in seen:
                    seen.add(r["symbol"])
                    rows.append(r)
        return rows, " + ".join(config.UNIVERSE_PARTS)


def snapshot(client: NSEClient, when: datetime | None = None) -> str:
    """Nifty 200 prices & volume + indices + F&O open interest. ~3-4 requests."""
    when = when or config.now_ist()
    rows, _src = universe_quotes(client)
    store.write_rows(store.snap_path("quotes", when), rows, parse.QUOTE_FIELDS)
    store.write_universe([r["symbol"] for r in rows if r["symbol"]])
    msg = [f"{len(rows)} stocks"]
    try:
        ind = parse.all_indices(client.json("allIndices"))
        store.write_rows(store.snap_path("indices", when), ind, parse.INDEX_FIELDS)
        msg.append(f"{len(ind)} indices")
    except (NSEError, parse.ParseError) as e:
        msg.append(f"indices failed: {e}")
    try:
        oi = parse.oi_spurts(client.json("live-analysis-oi-spurts-underlyings"))
        store.write_rows(store.snap_path("oi", when), oi, parse.OI_FIELDS)
        msg.append(f"{len(oi)} F&O open-interest rows")
    except (NSEError, parse.ParseError) as e:
        msg.append(f"F&O open interest failed: {e}")
    return "snapshot: " + ", ".join(msg)


def detail_symbols(limit: int = config.MAX_DETAIL_SYMBOLS) -> list[str]:
    """Watchlist + radar; before those exist, the stocks with the biggest F&O position changes."""
    syms = []
    for s in store.read_list("watchlist") + store.read_list("radar"):
        if s not in syms:
            syms.append(s)
    if len(syms) < 10:
        day = store.root() / "snapshots" / f"{config.now_ist():%Y/%m/%d}"
        files = sorted(day.glob("oi_*.csv.gz")) if day.exists() else []
        if files:
            rows = store.read_rows(files[-1])
            rows.sort(key=lambda r: abs(float(r["change_oi_pct"] or 0)), reverse=True)
            for r in rows:
                if r["symbol"] not in syms:
                    syms.append(r["symbol"])
                if len(syms) >= 20:
                    break
    return syms[:limit]


def _nearest_expiry(exps: list[str], today: date, min_days: int = 3) -> str | None:
    ds = []
    for e in exps or []:
        try:
            ds.append((datetime.strptime(e, "%d-%b-%Y").date(), e))
        except ValueError:
            continue
    ds = sorted(x for x in ds if (x[0] - today).days >= min_days)
    return ds[0][1] if ds else None


def detail(client: NSEClient, when: datetime | None = None, symbols: list[str] | None = None) -> str:
    """Option-chain summary for watchlist + radar stocks (2 requests per stock)."""
    when = when or config.now_ist()
    syms = symbols if symbols is not None else detail_symbols()
    out, fails = [], []
    for s in syms:
        try:
            info = client.json("option-chain-contract-info", {"symbol": s})
            exp = _nearest_expiry(info.get("expiryDates"), when.date())
            if not exp:
                fails.append(f"{s}: no expiry")
                continue
            js = client.json("option-chain-v3", {"type": "Equity", "symbol": s, "expiry": exp})
            out.append(parse.chain_summary(s, js, exp))
        except (NSEError, parse.ParseError) as e:
            fails.append(f"{s}: {e}")
    if out:
        store.write_rows(store.snap_path("chains", when), out, parse.CHAIN_FIELDS)
    msg = f"option chains: {len(out)}/{len(syms)} stocks"
    if fails:
        msg += f" ({len(fails)} failed, e.g. {fails[0]})"
    return msg


# --------------------------------------------------------------------------- #
# End of day + history
# --------------------------------------------------------------------------- #
def eod(client: NSEClient, d: date, kinds=("equity", "fo")) -> tuple[bool, str]:
    """Download NSE's daily files for date d. Returns (complete, message)."""
    done, msgs = True, []
    if "equity" in kinds and not store.eod_path("equity", d).exists():
        blob = client.file(url_sec_bhav(d))
        if blob is None:
            done = False
            msgs.append("equity file not published")
        else:
            rows = parse.sec_bhav(blob.decode("utf-8", errors="replace"))
            store.write_rows(store.eod_path("equity", d), rows, parse.EQ_FIELDS)
            msgs.append(f"equity {len(rows)} stocks")
    if "fo" in kinds and not store.eod_path("fo", d).exists():
        blob = client.file(url_fo_bhav(d))
        if blob is None:
            done = False
            msgs.append("F&O file not published")
        else:
            rows = parse.fo_bhav_summary(unzip_first(blob))
            store.write_rows(store.eod_path("fo", d), rows, parse.FO_FIELDS)
            msgs.append(f"F&O {len(rows)} underlyings")
    return done, f"eod {d}: " + (", ".join(msgs) or "already saved")


def backfill(client: NSEClient, equity_days: int = config.BACKFILL_EQUITY_DAYS,
             fo_days: int = config.BACKFILL_FO_DAYS, progress=print, push_every: int = 60) -> str:
    """Walk back through past trading days and save missing daily files."""
    from . import sync
    hol = holidays(client)
    d = config.now_ist().date() - timedelta(days=1)
    eq_left, fo_left, saved, n = equity_days, fo_days, 0, 0
    misses = 0
    while (eq_left > 0 or fo_left > 0) and misses < 40:
        if d.weekday() < 5 and d not in hol:
            kinds = tuple(k for k, left in (("equity", eq_left), ("fo", fo_left)) if left > 0)
            try:
                ok, msg = eod(client, d, kinds)
                misses = 0 if ok else misses + 1
                if "equity" in kinds:
                    eq_left -= 1
                if "fo" in kinds:
                    fo_left -= 1
                saved += 1
                n += 1
                progress(f"  {msg}   (left: {eq_left} equity, {fo_left} F&O days)")
                if n % push_every == 0 and sync.ready():
                    progress("  uploading... " + sync.push(f"backfill up to {d}"))
            except Exception as e:
                misses += 1
                progress(f"  {d}: {e}")
        d -= timedelta(days=1)
    if sync.ready():
        progress("  uploading... " + sync.push("backfill"))
    return f"backfill: {saved} days processed"


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
def selftest(client: NSEClient) -> list[tuple[str, bool, str]]:
    """Try each NSE source once and report in plain words. Saves nothing."""
    res = []

    def t(name, fn):
        try:
            res.append((name, True, fn()))
        except Exception as e:
            res.append((name, False, f"{type(e).__name__}: {e}"[:300]))

    t("Nifty 200 prices", lambda: (lambda r: f"{len(r[0])} stocks via {r[1]}, e.g. {r[0][0]['symbol']} ₹{r[0][0]['last']}")(
        universe_quotes(client)))
    t("Indices & VIX", lambda: (lambda r: ", ".join(f"{x['index']} {x['last']}" for x in r[:3]))(
        parse.all_indices(client.json("allIndices"))))
    t("F&O open interest", lambda: (lambda r: f"{len(r)} stocks, e.g. {r[0]['symbol']} OI {r[0]['latest_oi']}")(
        parse.oi_spurts(client.json("live-analysis-oi-spurts-underlyings"))))

    def chain():
        info = client.json("option-chain-contract-info", {"symbol": "RELIANCE"})
        exp = _nearest_expiry(info.get("expiryDates"), config.now_ist().date(), 0)
        s = parse.chain_summary("RELIANCE", client.json("option-chain-v3", {"type": "Equity", "symbol": "RELIANCE", "expiry": exp}), exp)
        return f"RELIANCE {exp}: put/call ratio {s['pcr'] and round(s['pcr'], 2)}, most calls at {s['max_ce_strike']}, most puts at {s['max_pe_strike']}"
    t("Option chain (RELIANCE)", chain)

    def last_day():
        d = config.now_ist().date()
        for _ in range(7):
            d -= timedelta(days=1)
            if d.weekday() < 5:
                return d
    d = last_day()
    t(f"Daily equity file {d}", lambda: (lambda b: "not published" if b is None else
        (lambda r: f"{len(r)} stocks, e.g. {r[0]['symbol']} delivery {r[0]['deliv_pct']}%")(parse.sec_bhav(b.decode())))(
        client.file(url_sec_bhav(d))))
    t(f"Daily F&O file {d}", lambda: (lambda b: "not published" if b is None else
        (lambda r: f"{len(r)} underlyings, e.g. {r[0]['symbol']} fut OI {r[0]['fut_oi']}")(parse.fo_bhav_summary(unzip_first(b))))(
        client.file(url_fo_bhav(d))))
    return res
