#!/usr/bin/env python3
"""
Bank Nifty Cheap-Strangle Scanner -- local web app (READ-ONLY).

Run it on your own computer (NSE blocks most cloud servers):

    python app.py            # live NSE data, opens http://127.0.0.1:8050
    python app.py --demo     # synthetic data, to try the UI any time

No order-placement code exists here. Data fetch and display only.
Uses only the Python standard library + `requests` (already required by the scanner).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import threading
import time
import webbrowser
from dataclasses import asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import banknifty_strangle as bs
from demo_chain import demo_chain

ROOT = Path(__file__).resolve().parent
INDEX_HTML = ROOT / "static" / "index.html"
# Files that make the page installable as a phone app (PWA).
STATIC_FILES = {
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}
LOG_FIELDS = ["fetched_at", "nse_timestamp", "source", "expiry", "spot", "target", "matched",
              "ce_strike", "ce_price", "ce_iv", "pe_strike", "pe_price", "pe_iv",
              "pair_premium", "total_cost"]

# Catalyst calendar from the project brief -- dates must be re-verified.
CATALYSTS = [
    {"date": "2026-12-04", "name": "RBI MPC decision", "note": "Meeting 2-4 Dec. Oct hike to 5.50%, 'calibrated tightening'. Next clean setup."},
    {"date": "2026-12-16", "name": "US Fed FOMC (approx.)", "note": "Mid-Dec. Confirm exact date."},
    {"date": "2026-12-29", "name": "Dec monthly expiry", "note": "Last Tuesday."},
    {"date": "2027-01-12", "name": "Q3 bank earnings begin (approx.)", "note": "HDFC, ICICI, SBI, Axis, Kotak through mid/late Jan."},
    {"date": "2027-01-25", "name": "Jan monthly expiry", "note": "Last Tuesday -- verify."},
    {"date": "2027-02-01", "name": "Union Budget", "note": "Sunday; reaction lands next trading session."},
    {"date": "2027-02-05", "name": "RBI MPC decision", "note": "Back-to-back with Budget."},
]

CACHE_SECONDS = 20  # don't hit NSE more often than this per expiry


class State:
    def __init__(self, demo: bool, out_dir: Path):
        self.demo = demo
        self.out_dir = out_dir
        self.lock = threading.Lock()
        self.raw_cache: dict[tuple, tuple[float, dict, str]] = {}
        self.last_chain: bs.Chain | None = None

    def get_raw(self, expiry_nse: str, source: str, force: bool) -> tuple[dict, str]:
        if getattr(self, "replay", None):
            return self.replay, "replay file (NOT live data)"
        if self.demo:
            return demo_chain(expiry_nse), "demo (synthetic -- NOT market data)"
        key = (expiry_nse, source)
        with self.lock:
            hit = self.raw_cache.get(key)
            if hit and not force and time.time() - hit[0] < CACHE_SECONDS:
                return hit[1], hit[2] + " (cached)"
        data, src = bs.fetch(source, expiry_nse)
        with self.lock:
            self.raw_cache[key] = (time.time(), data, src)
        return data, src


def wing_dict(w: bs.Wing, spot: float, be: float, target: float) -> dict:
    q = w.quote
    d = asdict(q)
    d.update(price=w.price, basis=w.basis, matched=w.matched,
             reason=None if w.matched else bs.no_match_reason(w, target),
             farthest_listed=w.farthest_listed, farthest_traded=w.farthest_traded,
             farthest_quoted=w.farthest_quoted,
             dist=q.strike - spot, dist_pct=bs.pct(q.strike, spot),
             be=be, move=be - spot, move_pct=bs.pct(be, spot))
    return d


def scan(state: State, params: dict) -> dict:
    g = lambda k, d: (params.get(k) or [d])[0]
    expiry_nse = bs.to_nse_expiry(g("expiry", bs.DEFAULT_EXPIRY))
    try:
        target = float(g("target", bs.DEFAULT_TARGET))
        lot = int(g("lot", bs.DEFAULT_LOT_SIZE))
        cflat = float(g("cflat", bs.DEFAULT_CHARGES_FLAT))
        cpct = float(g("cpct", bs.DEFAULT_CHARGES_PCT))
    except ValueError as e:
        raise bs.ChainError(f"Bad numeric input: {e}") from None
    if target <= 0 or lot <= 0:
        raise bs.ChainError("Target premium and lot size must be positive.")
    source = g("source", "auto")
    if source not in ("auto", "nse", "nsepython"):
        source = "auto"

    data, src = state.get_raw(expiry_nse, source, force=g("force", "0") == "1")
    chain = bs.parse_chain(data, expiry_nse, src)
    rep = bs.build_report(chain, target, lot, cflat, cpct)
    state.last_chain = chain

    by_strike: dict[float, dict] = {}
    for q in chain.quotes:
        r = by_strike.setdefault(q.strike, {"strike": q.strike})
        p = q.opt_type.lower()
        r.update({f"{p}_ltp": q.ltp, f"{p}_iv": q.iv, f"{p}_oi": q.oi,
                  f"{p}_vol": q.volume, f"{p}_bid": q.bid, f"{p}_ask": q.ask})

    try:
        dte = (datetime.strptime(chain.expiry, "%d-%b-%Y").date() - datetime.now().date()).days
    except ValueError:
        dte = None

    result = {
        "ok": True, "demo": state.demo, "expiry": chain.expiry, "spot": chain.spot,
        "timestamp": chain.timestamp, "source": chain.source, "dte": dte, "target": target,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "call": wing_dict(rep.call_wing, rep.spot, rep.call_be, target),
        "put": wing_dict(rep.put_wing, rep.spot, rep.put_be, target),
        "matched": rep.matched, "tolerance": bs.tolerance_for(target),
        "pair_premium": rep.pair_premium, "lot_size": rep.lot_size,
        "premium_cost": rep.premium_cost, "charges": rep.charges, "total_cost": rep.total_cost,
        "warnings": bs.wing_warnings(rep, target),
        "chain": [by_strike[k] for k in sorted(by_strike)],
    }
    if g("log", "0") == "1":
        append_log(state, result)
    return result


def log_path(state: State) -> Path:
    return state.out_dir / ("scan_log_demo.csv" if state.demo else "scan_log.csv")


def append_log(state: State, r: dict) -> None:
    path = log_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with path.open(newline="") as fh:
            header = next(csv.reader(fh), [])
        if header != LOG_FIELDS:  # older format: keep it, start a fresh file
            path.rename(path.with_name(f"{path.stem}_old_{datetime.now():%Y%m%d_%H%M%S}.csv"))
    new = not path.exists()
    with state.lock, path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow({
            "fetched_at": r["fetched_at"], "nse_timestamp": r["timestamp"], "source": r["source"],
            "expiry": r["expiry"], "spot": r["spot"], "target": r["target"],
            "matched": "yes" if r["matched"] else "no",
            "ce_strike": r["call"]["strike"], "ce_price": r["call"]["price"], "ce_iv": r["call"]["iv"],
            "pe_strike": r["put"]["strike"], "pe_price": r["put"]["price"], "pe_iv": r["put"]["iv"],
            "pair_premium": round(r["pair_premium"], 2), "total_cost": round(r["total_cost"], 2),
        })


def read_log(state: State, limit: int = 200) -> list[dict]:
    path = log_path(state)
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows[-limit:][::-1]


def chain_csv(chain: bs.Chain) -> str:
    buf = io.StringIO()
    cols = ["ltp", "bid", "ask", "iv", "oi", "volume"]
    w = csv.writer(buf)
    w.writerow(["nse_timestamp", "expiry", "spot", "strike"]
               + [f"ce_{c}" for c in cols] + [f"pe_{c}" for c in cols])
    by: dict[float, dict] = {}
    for q in chain.quotes:
        by.setdefault(q.strike, {})[q.opt_type] = q
    for k in sorted(by):
        row = [chain.timestamp, chain.expiry, chain.spot, k]
        for side in ("CE", "PE"):
            q = by[k].get(side)
            row += [getattr(q, c) if q else "" for c in cols]
        w.writerow(row)
    return buf.getvalue()


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter console
            if "/api/" in (args[0] if args else ""):
                return
            super().log_message(fmt, *args)

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                self._send(200, INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
            elif u.path in STATIC_FILES:
                name, ctype = STATIC_FILES[u.path]
                extra = {"Service-Worker-Allowed": "/"} if name == "sw.js" else None
                self._send(200, (ROOT / "static" / name).read_bytes(), ctype, extra)
            elif u.path == "/api/config":
                self._json({
                    "demo": state.demo, "expiry": bs.DEFAULT_EXPIRY, "target": bs.DEFAULT_TARGET,
                    "lot": bs.DEFAULT_LOT_SIZE, "cflat": bs.DEFAULT_CHARGES_FLAT,
                    "cpct": bs.DEFAULT_CHARGES_PCT, "catalysts": CATALYSTS,
                    "today": datetime.now().date().isoformat(),
                })
            elif u.path == "/api/scan":
                try:
                    self._json(scan(state, q))
                except bs.ChainError as e:
                    self._json({"ok": False, "error": str(e)}, 502)
                except Exception as e:  # never show silent wrong data
                    self._json({"ok": False, "error": f"Unexpected error: {type(e).__name__}: {e}"}, 500)
            elif u.path == "/api/history":
                self._json({"rows": read_log(state)})
            elif u.path == "/api/chain.csv":
                if not state.last_chain:
                    self._json({"ok": False, "error": "Run a scan first."}, 404)
                    return
                c = state.last_chain
                name = f"banknifty_{c.expiry}_{datetime.now():%Y%m%d_%H%M%S}.csv"
                self._send(200, chain_csv(c).encode(), "text/csv",
                           {"Content-Disposition": f'attachment; filename="{name}"'})
            else:
                self._send(404, b"Not found", "text/plain")

    return Handler


def _try_open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="Bank Nifty cheap-strangle scanner (local web app, read-only).")
    ap.add_argument("--demo", action="store_true", help="use synthetic data (no NSE calls)")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default %(default)s; local only)")
    ap.add_argument("--port", type=int, default=8050)
    ap.add_argument("--out-dir", type=Path, default=ROOT / bs.DEFAULT_OUT_DIR)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--replay", type=Path, help="serve a saved NSE JSON file instead of live data (testing)")
    args = ap.parse_args()

    state = State(args.demo, args.out_dir)
    state.replay = json.loads(args.replay.read_text()) if args.replay else None
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{args.port}"
    print(f"Bank Nifty strangle scanner running at {url}  ({'DEMO data' if args.demo else 'live NSE'})")
    print("Read-only. Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.8, lambda: _try_open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
