"""Where the numbers live inside the private data repository.

Layout (all CSV, gzip-compressed):
  snapshots/YYYY/MM/DD/quotes_HHMM.csv.gz    Nifty 200 prices & volume (hourly)
  snapshots/YYYY/MM/DD/indices_HHMM.csv.gz   Nifty, Bank Nifty, VIX, sectors (hourly)
  snapshots/YYYY/MM/DD/oi_HHMM.csv.gz        F&O open interest per stock (hourly)
  snapshots/YYYY/MM/DD/chains_HHMM.csv.gz    option-chain summary, watchlist + radar (3x a day)
  eod/equity/YYYY/YYYYMMDD.csv.gz            daily price, volume, delivery % (back-filled ~3 years)
  eod/fo/YYYY/YYYYMMDD.csv.gz                daily futures & options summary per stock
  meta/universe.csv                          current Nifty 200 list
  meta/status.json                           last run of each job, errors
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from datetime import date, datetime
from pathlib import Path

from . import config


def root() -> Path:
    return config.DATA_DIR


def snap_path(kind: str, when: datetime) -> Path:
    return root() / "snapshots" / f"{when:%Y/%m/%d}" / f"{kind}_{when:%H%M}.csv.gz"


def eod_path(kind: str, d: date) -> Path:
    return root() / "eod" / kind / f"{d:%Y}" / f"{d:%Y%m%d}.csv.gz"


def write_rows(path: Path, rows: list[dict], fields: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else (round(r[k], 4) if isinstance(r.get(k), float) else r[k]))
                    for k in fields})
    # mtime=0 -> identical content gives identical bytes (no pointless git changes)
    with open(path, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
        gz.write(buf.getvalue().encode())
    return path


def read_rows(path: Path) -> list[dict]:
    with gzip.open(path, "rt", newline="") as fh:
        return list(csv.DictReader(fh))


def write_universe(rows) -> None:
    """rows: list of symbols, or of {symbol, company, industry} dicts."""
    rows = [r if isinstance(r, dict) else {"symbol": r} for r in rows]
    p = root() / "meta" / "universe.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["symbol", "company", "industry"], extrasaction="ignore")
    w.writeheader()
    for r in sorted(rows, key=lambda r: r["symbol"]):
        w.writerow(r)
    p.write_text(buf.getvalue())


def read_universe() -> list[str]:
    p = root() / "meta" / "universe.csv"
    if not p.exists():
        return []
    return [r["symbol"].strip() for r in csv.DictReader(io.StringIO(p.read_text())) if r.get("symbol")]


def read_list(name: str) -> list[str]:
    """Symbols from meta/<name>.csv (watchlist / radar); first column, header 'symbol'."""
    p = root() / "meta" / f"{name}.csv"
    if not p.exists():
        return []
    rows = list(csv.DictReader(io.StringIO(p.read_text())))
    return [r["symbol"].strip().upper() for r in rows if r.get("symbol")]


def status_update(job: str, ok: bool, detail: str) -> None:
    p = root() / "meta" / "status.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        st = json.loads(p.read_text())
    except Exception:
        st = {}
    st[job] = {"at": config.now_ist().isoformat(timespec="seconds"), "ok": ok, "detail": detail[:500]}
    p.write_text(json.dumps(st, indent=1, sort_keys=True))
