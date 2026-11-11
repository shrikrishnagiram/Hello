"""
Index registry: which indices the planner supports and how to fetch each one.

Lot sizes and expiry patterns change from time to time -- VERIFY in your broker.
"""

from __future__ import annotations

from datetime import date, datetime

INDICES = {
    "BANKNIFTY": {"name": "Bank Nifty", "exchange": "NSE", "symbol": "BANKNIFTY", "lot": 30,
                  "expiries": "monthly (last Tuesday)"},
    "NIFTY": {"name": "Nifty 50", "exchange": "NSE", "symbol": "NIFTY", "lot": 65,
              "expiries": "weekly (Tuesday) + monthly"},
    "SENSEX": {"name": "Sensex", "exchange": "BSE", "symbol": "SENSEX", "lot": 20,
               "expiries": "weekly (Thursday) + monthly"},
}
DEFAULT_INDEX = "BANKNIFTY"


def get_index(key: str | None) -> dict:
    k = (key or DEFAULT_INDEX).upper()
    if k not in INDICES:
        raise ValueError(f"Unknown index {key!r}. Choose from {', '.join(INDICES)}.")
    return {"key": k, **INDICES[k]}


def choose_expiries(expiries: list[str], today: date | None = None,
                    nearest: int = 2, months: int = 4) -> list[str]:
    """Pick the expiries worth analysing from a full list (NSE DD-Mon-YYYY format).

    Weekly-expiry indices list a dozen dates; a catalyst plan needs ones a few weeks
    out. Keep the `nearest` upcoming expiries plus the last (monthly) expiry of each
    of the next `months` calendar months.
    """
    today = today or datetime.now().date()
    ds = sorted({datetime.strptime(e, "%d-%b-%Y").date() for e in expiries})
    ds = [d for d in ds if d >= today]
    keep = set(ds[:nearest])
    monthly: dict[tuple, date] = {}
    for d in ds:
        monthly[(d.year, d.month)] = max(d, monthly.get((d.year, d.month), d))
    for ym in sorted(monthly)[:months]:
        keep.add(monthly[ym])
    return [d.strftime("%d-%b-%Y") for d in sorted(keep)]
