"""Yahoo Finance quotes for NSE stocks (price + volume). Used because NSE retired its
index-constituents quote feed (equity-stockIndices returns 404 since ~Oct 2026). READ-ONLY."""

from __future__ import annotations

import time
from urllib.parse import quote

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"
HEADERS = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36",
           "Accept": "application/json"}


class YahooError(RuntimeError):
    pass


def ysym(nse_symbol: str) -> str:
    return f"{nse_symbol}.NS"


def parse_chart(nse_symbol: str, js: dict) -> dict:
    """v8 chart JSON -> one quote row (today's open/high/low/last/volume)."""
    try:
        res = js["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        err = (js.get("chart") or {}).get("error") if isinstance(js, dict) else None
        raise YahooError(f"{nse_symbol}: no data ({err})")
    m = res.get("meta") or {}
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    ts = res.get("timestamp") or []
    last_i = len(ts) - 1
    pick = lambda k: (q.get(k) or [None] * len(ts))[last_i] if last_i >= 0 else None
    closes = [c for c in (q.get("close") or []) if c is not None]
    prev = closes[-2] if len(closes) >= 2 else m.get("chartPreviousClose")
    last = m.get("regularMarketPrice") or pick("close")
    if last is None:
        raise YahooError(f"{nse_symbol}: no price in response")
    return {"symbol": nse_symbol, "open": pick("open"), "high": m.get("regularMarketDayHigh") or pick("high"),
            "low": m.get("regularMarketDayLow") or pick("low"), "last": last, "prev_close": prev,
            "change_pct": ((last - prev) / prev * 100) if prev else None,
            "volume": m.get("regularMarketVolume") or pick("volume"),
            "value_cr": None, "year_high": m.get("fiftyTwoWeekHigh"), "year_low": m.get("fiftyTwoWeekLow"),
            "updated": m.get("regularMarketTime")}


class YahooClient:
    def __init__(self, gap: float = 0.25):
        import requests
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.gap, self._last = gap, 0.0

    def quote(self, nse_symbol: str, retries: int = 2) -> dict:
        """One stock. Short timeout; a timeout or connection failure is not retried."""
        last = None
        for attempt in range(retries):
            dt = time.time() - self._last
            if dt < self.gap:
                time.sleep(self.gap - dt)
            self._last = time.time()
            try:
                r = self.s.get(CHART.format(sym=quote(ysym(nse_symbol), safe="")), timeout=8)
                if r.status_code == 200:
                    return parse_chart(nse_symbol, r.json())
                last = f"HTTP {r.status_code}"
                if r.status_code == 404:
                    break
            except YahooError:
                raise
            except Exception as e:
                raise YahooError(f"{nse_symbol}: {type(e).__name__}: {str(e)[:120]}") from None
            if r.status_code == 429:
                time.sleep(2)
        raise YahooError(f"{nse_symbol}: {last}")
