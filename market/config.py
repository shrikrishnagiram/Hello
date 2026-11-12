"""Settings for the collector. Times are India time (IST, UTC+5:30, no daylight saving)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))

# Where the private data repository is cloned on the phone.
DATA_DIR = Path(os.environ.get("MARKET_DATA_DIR", str(Path.home() / "market-data")))
APP_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = APP_DIR / "data"

UNIVERSE_INDEX = "NIFTY 200"

# Collection schedule (IST, trading days). Snapshots are timed to land a few
# minutes before Claude's review checks (10:15, 13:00, 14:45).
SNAPSHOT_TIMES = ["09:20", "10:05", "11:05", "12:05", "12:50", "13:35", "14:35", "15:25", "15:35"]
DETAIL_TIMES = ["10:05", "12:50", "14:35"]       # option chains for watchlist + radar
EOD_TIME = "18:30"                                # NSE daily files (retried until EOD_GIVE_UP)
EOD_GIVE_UP = "22:30"
SYNC_PULL_MINUTES = 5                             # how often to look for Claude's verdicts (phase 3)

# Politeness towards NSE
REQUEST_GAP_SECONDS = 0.8
MAX_DETAIL_SYMBOLS = 45

# History back-fill defaults
BACKFILL_EQUITY_DAYS = 750      # ~3 years of trading days
BACKFILL_FO_DAYS = 125          # ~6 months of trading days (F&O files are large to download)


def now_ist() -> datetime:
    return datetime.now(IST)


def hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")
