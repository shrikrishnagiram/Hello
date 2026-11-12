"""All-day loop on the phone: runs each job at its time on trading days, uploads results."""

from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timedelta

from . import config, jobs, store, sync
from .nse import NSEClient

STATE = config.LOG_DIR / "collector_state.json"
LOG = config.LOG_DIR / "collector.log"


def log(msg: str) -> None:
    line = f"{config.now_ist():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        fh.write(line + "\n")


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st))


def _due(now: datetime, slot: str, window_min: int = 40) -> bool:
    h, m = map(int, slot.split(":"))
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return t <= now < t + timedelta(minutes=window_min)


def tick(client: NSEClient, st: dict, hol: set) -> None:
    now = config.now_ist()
    today = now.date().isoformat()
    day = st.setdefault(today, {"done": []})
    for k in list(st):
        if k < (now.date() - timedelta(days=5)).isoformat():
            del st[k]
    if not jobs.is_trading_day(now.date(), hol):
        return
    changed = []
    for slot in config.SNAPSHOT_TIMES:
        key = f"snap {slot}"
        if key not in day["done"] and _due(now, slot):
            try:
                changed.append(jobs.snapshot(client, now))
                if slot in config.DETAIL_TIMES:
                    changed.append(jobs.detail(client, now))
                store.status_update("snapshot", True, " | ".join(changed))
            except Exception as e:
                store.status_update("snapshot", False, str(e))
                log(f"{key} FAILED: {e}")
            day["done"].append(key)
    if "eod" not in day["done"] and config.hhmm(now) >= config.EOD_TIME:
        last_try = day.get("eod_try")
        if not last_try or (now - datetime.fromisoformat(last_try)) >= timedelta(minutes=20):
            day["eod_try"] = now.isoformat()
            try:
                ok, msg = jobs.eod(client, now.date())
                changed.append(msg)
                store.status_update("eod", ok, msg)
                if ok or config.hhmm(now) >= config.EOD_GIVE_UP:
                    day["done"].append("eod")
            except Exception as e:
                store.status_update("eod", False, str(e))
                log(f"eod FAILED: {e}")
    if changed:
        for c in changed:
            log(c)
        try:
            log("upload: " + sync.push(f"{now:%Y-%m-%d %H:%M} " + changed[0][:60]))
        except Exception as e:
            log(f"upload FAILED (will retry next run): {e}")
    _save(st)


def run() -> None:
    log("collector started (Ctrl+C or market-stop to end)")
    client = NSEClient()
    hol = jobs.holidays(client)
    st = _load()
    last_hol = config.now_ist().date()
    while True:
        try:
            if config.now_ist().date() != last_hol:
                hol, last_hol = jobs.holidays(client), config.now_ist().date()
                client = NSEClient()           # fresh cookies each day
            tick(client, st, hol)
        except Exception:
            log("unexpected error:\n" + traceback.format_exc()[-1500:])
        time.sleep(30)
