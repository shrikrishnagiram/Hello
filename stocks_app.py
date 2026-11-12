#!/usr/bin/env python3
"""
Stocks app: swing-trading screens on top of the phone's collected data. READ-ONLY.

    python stocks_app.py            # real data from ~/market-data, opens http://127.0.0.1:8060
    python stocks_app.py --demo     # synthetic practice data (clearly labelled)

Shows verdicts, radar, watchlist and your recorded trades. It never places orders:
you trade in your broker app and record the trade here.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import tempfile
import threading
import time
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from market import analysis, config, store, sync

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "static" / "stocks.html"
FILES = {
    "/stocks.webmanifest": ("stocks.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}


class App:
    def __init__(self, root: Path, demo: bool):
        self.root, self.demo = root, demo
        self.lock = threading.Lock()
        self._cache = (None, None)

    def _stamp(self):
        """Changes whenever new data or list edits arrive."""
        parts = []
        for sub in ("eod/equity", "eod/fo", "snapshots", "meta"):
            p = self.root / sub
            if p.exists():
                fs = sorted(p.rglob("*.*"))
                parts.append((len(fs), max((f.stat().st_mtime for f in fs[-50:]), default=0)))
        return tuple(parts)

    def result(self):
        st = self._stamp()
        with self.lock:
            if self._cache[0] != st:
                self._cache = (st, analysis.run(self.root))
            return self._cache[1]

    def overview(self):
        r = self.result()
        out = {k: v for k, v in r.items() if k != "_by"}
        out["demo"] = self.demo
        out["status"] = self.status()
        return out

    def status(self):
        p = self.root / "meta" / "status.json"
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}

    # ---- edits (saved in the data repo so Claude sees them too) ----
    def _rw(self, name, fields, fn):
        p = self.root / "meta" / f"{name}.csv"
        rows = list(csv.DictReader(io.StringIO(p.read_text()))) if p.exists() else []
        rows = fn(rows)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(buf.getvalue())
        self._push(f"app: update {name}")

    def _push(self, msg):
        if self.demo or not sync.ready():
            return
        threading.Thread(target=lambda: _safe(lambda: sync.push(msg)), daemon=True).start()

    def watch(self, symbol, add, note=""):
        symbol = symbol.upper().strip()
        def fn(rows):
            rows = [r for r in rows if r["symbol"] != symbol]
            if add:
                rows.append({"symbol": symbol, "note": note})
            return rows
        self._rw("watchlist", ["symbol", "note"], fn)

    def add_position(self, d):
        sym = d["symbol"].upper().strip()
        qty, price, stop = int(d["qty"]), float(d["price"]), float(d["stop"])
        if qty <= 0 or price <= 0 or stop <= 0 or stop >= price:
            raise ValueError("Quantity and prices must be positive, and the stop-loss below your buy price.")
        self._rw("positions", ["symbol", "qty", "price", "stop", "date"],
                 lambda rows: [r for r in rows if r["symbol"] != sym] +
                 [{"symbol": sym, "qty": qty, "price": price, "stop": stop, "date": date.today().isoformat()}])

    def update_stop(self, sym, stop):
        def fn(rows):
            for r in rows:
                if r["symbol"] == sym:
                    if float(stop) >= float(r["price"]) * 3:
                        raise ValueError("That stop-loss looks wrong.")
                    r["stop"] = float(stop)
            return rows
        self._rw("positions", ["symbol", "qty", "price", "stop", "date"], fn)

    def close_position(self, sym, exit_price):
        closed = {}
        def fn(rows):
            keep = []
            for r in rows:
                (closed.update(r) if r["symbol"] == sym else keep.append(r))
            return keep
        self._rw("positions", ["symbol", "qty", "price", "stop", "date"], fn)
        if closed:
            pnl = (float(exit_price) - float(closed["price"])) * int(closed["qty"])
            self._rw("trades", ["symbol", "qty", "buy", "sell", "pnl", "opened", "closed"],
                     lambda rows: rows + [{"symbol": sym, "qty": closed["qty"], "buy": closed["price"], "sell": exit_price,
                                           "pnl": round(pnl, 2), "opened": closed["date"], "closed": date.today().isoformat()}])

    def trades(self):
        p = self.root / "meta" / "trades.csv"
        return list(csv.DictReader(io.StringIO(p.read_text()))) if p.exists() else []


def _safe(fn):
    try:
        fn()
    except Exception as e:  # upload failures are retried by the next push
        print("upload failed:", e)


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, default=str).encode(), "application/json")

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path in ("/", "/index.html"):
                    self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                elif u.path in FILES:
                    n, t = FILES[u.path]
                    self._send(200, (ROOT / "static" / n).read_bytes(), t)
                elif u.path == "/api/overview":
                    self._json(app.overview())
                elif u.path == "/api/stock":
                    r = analysis.stock_detail(app.root, q.get("symbol", "").upper(), app.result())
                    self._json(r or {"error": "No data for that stock yet."}, 200 if r else 404)
                elif u.path == "/api/search":
                    term = q.get("q", "").upper().strip()
                    uni = analysis.load_universe(app.root)
                    hits = [{"symbol": s, "company": m.get("company", ""), "industry": m.get("industry", "")}
                            for s, m in uni.items() if term and (term in s or term in (m.get("company") or "").upper())]
                    self._json(sorted(hits, key=lambda h: (not h["symbol"].startswith(term), h["symbol"]))[:20])
                elif u.path == "/api/trades":
                    self._json(app.trades())
                else:
                    self._send(404, b"Not found", "text/plain")
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)

        def do_POST(self):
            u = urlparse(self.path)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                d = json.loads(self.rfile.read(n) or b"{}")
                if u.path == "/api/watch":
                    app.watch(d["symbol"], bool(d.get("add", True)), d.get("note", ""))
                elif u.path == "/api/position":
                    act = d.get("action")
                    if act == "add":
                        app.add_position(d)
                    elif act == "stop":
                        app.update_stop(d["symbol"], d["stop"])
                    elif act == "close":
                        app.close_position(d["symbol"], d["price"])
                    else:
                        raise ValueError("unknown action")
                else:
                    return self._send(404, b"Not found", "text/plain")
                self._json({"ok": True})
            except (KeyError, ValueError) as e:
                self._json({"ok": False, "error": str(e)}, 400)
    return H


def main():
    ap = argparse.ArgumentParser(description="Stocks app (read-only).")
    ap.add_argument("--demo", action="store_true", help="synthetic practice data")
    ap.add_argument("--port", type=int, default=8060)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if a.demo:
        from market import demo
        root = Path(tempfile.mkdtemp(prefix="stocks-demo-"))
        config.DATA_DIR = root
        demo.generate(root)
    else:
        root = config.DATA_DIR
    app = App(root, a.demo)
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(app))
    url = f"http://127.0.0.1:{a.port}"
    print(f"Stocks app at {url}  ({'PRACTICE data' if a.demo else 'data: ' + str(root)})")
    if not a.no_browser:
        threading.Timer(0.8, lambda: _safe(lambda: webbrowser.open(url))).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
