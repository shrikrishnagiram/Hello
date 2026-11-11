#!/usr/bin/env python3
"""
Bank Nifty Strangle Planner -- local web app (READ-ONLY).

Scans every listed Bank Nifty expiry, rejects unrealistic / untradeable strikes,
proposes call+put combinations (near / mid / far) and lays out a catalyst plan:
when to enter, which expiry, when to exit.

    python app.py            # live NSE data, opens http://127.0.0.1:8050
    python app.py --demo     # synthetic data, to try the UI any time

No order-placement code exists here. Data fetch and display only.
"""

from __future__ import annotations

import argparse
import csv
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
import strategy as st
from demo_chain import demo_all

ROOT = Path(__file__).resolve().parent
INDEX_HTML = ROOT / "static" / "index.html"
STATIC_FILES = {  # files that make the page installable as a phone app (PWA)
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}
LOG_FIELDS = ["fetched_at", "nse_timestamp", "source", "expiry", "dte", "spot", "atm_iv",
              "one_sigma_pct", "near_cost", "mid_cost", "far_cost"]
CACHE_SECONDS = 60     # NSE is fetched at most once a minute
MAX_EXPIRIES = 4


class State:
    def __init__(self, demo: bool, out_dir: Path, replay: dict | None = None):
        self.demo = demo
        self.out_dir = out_dir
        self.replay = replay
        self.lock = threading.Lock()
        self.cache: tuple[float, list, str] | None = None

    def get_all(self, source: str, force: bool) -> list[tuple[str, dict, str]]:
        if self.replay is not None:
            rec = self.replay.get("records") or {}
            exps = rec.get("expiryDates") or sorted(
                {r.get("expiryDate") or r.get("expiryDates") for r in rec.get("data", [])} - {None})
            return [(e, self.replay, "replay file (NOT live data)") for e in exps]
        if self.demo:
            return demo_all(MAX_EXPIRIES)
        with self.lock:
            if self.cache and not force and self.cache[2] == source and time.time() - self.cache[0] < CACHE_SECONDS:
                return [(e, d, s + " (cached)") for e, d, s in self.cache[1]]
        res = bs.fetch_all_expiries(source, MAX_EXPIRIES)
        with self.lock:
            self.cache = (time.time(), res, source)
        return res


def _num(params: dict, key: str, default, cast=float):
    raw = (params.get(key) or [""])[0]
    if raw in ("", None):
        return default
    try:
        return cast(raw)
    except ValueError:
        raise bs.ChainError(f"Bad value for {key}: {raw!r}") from None


def rules_from(params: dict) -> st.Rules:
    r = st.Rules()
    r.lot_size = _num(params, "lot", r.lot_size, int)
    r.charges_flat = _num(params, "cflat", r.charges_flat)
    r.charges_pct = _num(params, "cpct", r.charges_pct)
    r.budget = _num(params, "budget", None) or None
    r.min_sigma = _num(params, "min_sigma", r.min_sigma)
    r.max_sigma = _num(params, "max_sigma", r.max_sigma)
    r.min_oi = _num(params, "min_oi", r.min_oi)
    if r.lot_size <= 0 or not (0 < r.min_sigma < r.max_sigma <= 4):
        raise bs.ChainError("Lot size must be positive and 0 < min sigma < max sigma <= 4.")
    return r


def chain_rows(chain: bs.Chain) -> list[dict]:
    by: dict[float, dict] = {}
    for q in chain.quotes:
        row = by.setdefault(q.strike, {"strike": q.strike})
        p = q.opt_type.lower()
        row.update({f"{p}_ltp": q.ltp, f"{p}_bid": q.bid, f"{p}_ask": q.ask,
                    f"{p}_iv": q.iv, f"{p}_oi": q.oi, f"{p}_vol": q.volume})
    return [by[k] for k in sorted(by)]


def propose(state: State, params: dict) -> dict:
    rules = rules_from(params)
    source = (params.get("source") or ["auto"])[0]
    if source not in ("auto", "nse", "nsepython"):
        source = "auto"
    raw = state.get_all(source, force=(params.get("force") or ["0"])[0] == "1")
    today = datetime.now().date()

    chains, problems = [], []
    for e, data, src in raw:
        try:
            chains.append(bs.parse_chain(data, e, src))
        except bs.ChainError as ex:
            problems.append(f"{e}: {ex}")
    if not chains:
        raise bs.ChainError("No expiry could be read.\n" + "\n".join(problems))

    plan = st.build_plan([c.expiry for c in chains], today)
    covers = st.expiry_catalysts(plan)
    expiries = []
    for ch in chains:
        ea = st.analyze_expiry(ch, today, rules)
        expiries.append({
            "expiry": ea.expiry, "dte": ea.dte, "atm_iv": ea.atm_iv, "one_sigma_pct": ea.one_sigma_pct,
            "catalysts": covers.get(ea.expiry, []), "skipped_reason": ea.skipped_reason,
            "calls_ok": ea.calls_ok, "puts_ok": ea.puts_ok, "n_combos_total": ea.n_combos_total,
            "excluded": ea.excluded, "excluded_examples": ea.excluded_examples, "zone": ea.zone,
            "bands": {k: [c.to_dict() for c in v] for k, v in ea.bands.items()},
            "chain": chain_rows(ch),
        })
    first = chains[0]
    result = {
        "ok": True, "demo": state.demo, "spot": first.spot, "timestamp": first.timestamp,
        "source": first.source, "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "today": today.isoformat(), "rules": asdict(rules), "plan": plan,
        "plan_rules": st.PLAN_RULES, "band_notes": st.BAND_NOTES,
        "bands_def": [{"name": n, "from": lo, "to": hi} for n, lo, hi in st.BANDS],
        "expiries": expiries, "problems": problems,
    }
    if (params.get("log") or ["0"])[0] == "1":
        append_log(state, result)
    return result


# --------------------------------------------------------------------------- #
# Scan log (tracks ATM IV and best cost per band over time -> "is vol cheap?")
# --------------------------------------------------------------------------- #
def log_path(state: State) -> Path:
    return state.out_dir / ("scan_log_demo.csv" if state.demo else "scan_log.csv")


def append_log(state: State, r: dict) -> None:
    path = log_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    with state.lock:
        if path.exists():
            with path.open(newline="") as fh:
                header = next(csv.reader(fh), [])
            if header != LOG_FIELDS:  # older format: keep it, start a fresh file
                path.rename(path.with_name(f"{path.stem}_old_{datetime.now():%Y%m%d_%H%M%S}.csv"))
        new = not path.exists()
        with path.open("a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
            if new:
                w.writeheader()
            for e in r["expiries"]:
                if e["skipped_reason"]:
                    continue
                best = {k: (v[0]["cost"] if v else "") for k, v in e["bands"].items()}
                w.writerow({
                    "fetched_at": r["fetched_at"], "nse_timestamp": r["timestamp"], "source": r["source"],
                    "expiry": e["expiry"], "dte": e["dte"], "spot": r["spot"],
                    "atm_iv": round(e["atm_iv"] * 100, 2) if e["atm_iv"] else "",
                    "one_sigma_pct": round(e["one_sigma_pct"], 2) if e["one_sigma_pct"] else "",
                    "near_cost": round(best["Near"], 0) if best.get("Near") else "",
                    "mid_cost": round(best["Mid"], 0) if best.get("Mid") else "",
                    "far_cost": round(best["Far"], 0) if best.get("Far") else "",
                })


def read_log(state: State, limit: int = 300) -> list[dict]:
    path = log_path(state)
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows[-limit:][::-1]


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
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
                self._send(200, (ROOT / "static" / name).read_bytes(), ctype)
            elif u.path == "/api/config":
                r = st.Rules()
                self._json({"demo": state.demo, "lot": r.lot_size, "cflat": r.charges_flat,
                            "cpct": r.charges_pct, "min_sigma": r.min_sigma, "max_sigma": r.max_sigma,
                            "min_oi": r.min_oi})
            elif u.path == "/api/proposals":
                try:
                    self._json(propose(state, q))
                except bs.ChainError as e:
                    self._json({"ok": False, "error": str(e)}, 502)
                except Exception as e:  # never show silent wrong data
                    self._json({"ok": False, "error": f"Unexpected error: {type(e).__name__}: {e}"}, 500)
            elif u.path == "/api/history":
                self._json({"rows": read_log(state)})
            else:
                self._send(404, b"Not found", "text/plain")

    return Handler


def _try_open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="Bank Nifty strangle planner (local web app, read-only).")
    ap.add_argument("--demo", action="store_true", help="use synthetic data (no NSE calls)")
    ap.add_argument("--replay", type=Path, help="serve a saved NSE JSON file instead of live data")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default %(default)s; local only)")
    ap.add_argument("--port", type=int, default=8050)
    ap.add_argument("--out-dir", type=Path, default=ROOT / bs.DEFAULT_OUT_DIR)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    replay = json.loads(args.replay.read_text()) if args.replay else None
    state = State(args.demo, args.out_dir, replay)
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{args.port}"
    mode = "DEMO data" if args.demo else ("REPLAY file" if replay else "live NSE")
    print(f"Bank Nifty strangle planner running at {url}  ({mode})")
    print("Read-only. Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.8, lambda: _try_open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
