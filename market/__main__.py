"""Command line: python -m market <command>

  run                 all-day collector loop (what market-start launches)
  selftest            check every NSE source once and report (saves nothing)
  snapshot            take one price/volume/OI snapshot now and upload it
  detail [SYM ...]    option-chain summary now (default: watchlist + radar)
  eod [YYYY-MM-DD]    save NSE's daily files for a date (default: today)
  backfill [EQ] [FO]  save past daily files: EQ equity days, FO F&O days
  status              show the last result of each job
"""

from __future__ import annotations

import json
import sys
from datetime import date

from . import config, daemon, jobs, store, sync
from .nse import NSEClient


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "run":
        daemon.run()
        return 0
    if cmd == "selftest":
        print("Checking NSE sources from this phone (takes ~30 seconds)...\n")
        res = jobs.selftest(NSEClient())
        for name, ok, msg in res:
            print(f"{'✅' if ok else '❌'} {name}\n     {msg}\n")
        good = sum(ok for _, ok, _ in res)
        print(f"{good}/{len(res)} sources working.")
        print("Data repository:", "ready" if sync.ready() else "NOT SET UP (run market-setup)")
        return 0 if good == len(res) else 1
    if cmd == "snapshot":
        c = NSEClient()
        print(jobs.snapshot(c))
        print("upload:", sync.push("manual snapshot"))
        return 0
    if cmd == "detail":
        print(jobs.detail(NSEClient(), symbols=[s.upper() for s in argv[1:]] or None))
        print("upload:", sync.push("manual option chains"))
        return 0
    if cmd == "eod":
        d = date.fromisoformat(argv[1]) if len(argv) > 1 else config.now_ist().date()
        ok, msg = jobs.eod(NSEClient(), d)
        print(msg)
        print("upload:", sync.push(f"eod {d}"))
        return 0 if ok else 1
    if cmd == "backfill":
        eq = int(argv[1]) if len(argv) > 1 else config.BACKFILL_EQUITY_DAYS
        fo = int(argv[2]) if len(argv) > 2 else config.BACKFILL_FO_DAYS
        print(f"Back-filling {eq} days of equity and {fo} days of F&O files. Keep the phone on Wi-Fi and charging.\n")
        print(jobs.backfill(NSEClient(), eq, fo))
        return 0
    if cmd == "status":
        p = store.root() / "meta" / "status.json"
        print("Data repository:", config.DATA_DIR, "(ready)" if sync.ready() else "(NOT SET UP — run market-setup)")
        if p.exists():
            for job, st in json.loads(p.read_text()).items():
                print(f"{'✅' if st['ok'] else '❌'} {job:9s} {st['at']}  {st['detail']}")
        else:
            print("No jobs have run yet.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
