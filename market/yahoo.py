"""Alternative live-price sources for NSE stocks (price + volume). READ-ONLY.

NSE retired its index quote feed (equity-stockIndices: 404 since ~Oct 2026), so
the hourly Nifty 200 prices come from one of these, whichever answers:
  YahooClient  - Yahoo Finance chart API, with the cookie/crumb handshake browsers do
  GrowwClient  - Groww website's public live-price data (no login)
"""

from __future__ import annotations

import time
from urllib.parse import quote

UA = ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36")


class YahooError(RuntimeError):
    pass


QuoteError = YahooError   # common error type for all alternative sources


def _num(v):
    try:
        return None if v is None else float(str(v).replace(",", ""))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Yahoo
# --------------------------------------------------------------------------- #
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


class _Base:
    name = "?"

    def __init__(self, gap: float = 0.3):
        import requests
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                               "Accept-Language": "en-IN,en;q=0.9"})
        self.gap, self._last, self._ready = gap, 0.0, False

    def _pace(self):
        dt = time.time() - self._last
        if dt < self.gap:
            time.sleep(self.gap - dt)
        self._last = time.time()

    def _get(self, url: str, sym: str, timeout: int = 8):
        self._pace()
        try:
            return self.s.get(url, timeout=timeout)
        except Exception as e:
            raise YahooError(f"{sym}: {type(e).__name__}: {str(e)[:120]}") from None


class YahooClient(_Base):
    name = "Yahoo Finance"
    HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]

    def _handshake(self):
        """Get the cookie (and crumb) a browser would have; Yahoo answers 429 without them."""
        self._ready = True
        for url in ("https://fc.yahoo.com/", "https://finance.yahoo.com/"):
            try:
                self.s.get(url, timeout=8, allow_redirects=True)
            except Exception:
                pass
        try:
            r = self.s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=8)
            self.crumb = r.text.strip() if r.status_code == 200 and len(r.text) < 40 else None
        except Exception:
            self.crumb = None

    def quote(self, nse_symbol: str) -> dict:
        if not self._ready:
            self._handshake()
        last = None
        for host in self.HOSTS:
            url = f"https://{host}/v8/finance/chart/{quote(ysym(nse_symbol), safe='')}?interval=1d&range=5d"
            if getattr(self, "crumb", None):
                url += f"&crumb={quote(self.crumb)}"
            r = self._get(url, nse_symbol)
            if r.status_code == 200:
                return parse_chart(nse_symbol, r.json())
            last = f"HTTP {r.status_code}"
            if r.status_code == 404:
                break
        raise YahooError(f"{nse_symbol}: {last}")


# --------------------------------------------------------------------------- #
# Groww (public website data, no login)
# --------------------------------------------------------------------------- #
GROWW_LIVE = "https://groww.in/v1/api/stocks_data/v1/tr_live_prices/exchange/NSE/segment/CASH/{sym}/latest"


def parse_groww(nse_symbol: str, js: dict) -> dict:
    if not isinstance(js, dict):
        raise YahooError(f"{nse_symbol}: unexpected Groww response")
    k = {key.lower(): key for key in js}
    g = lambda *names: next((_num(js[k[n.lower()]]) for n in names if n.lower() in k), None)
    last = g("ltp", "lastPrice", "price")
    if last is None:
        raise YahooError(f"{nse_symbol}: Groww response has no price (fields: {list(js)[:12]})")
    change = g("dayChange")
    prev = g("prevClose", "previousClose")
    if prev is None and change is not None:
        prev = round(last - change, 2)
    if prev is None:
        prev = g("close")
    return {"symbol": nse_symbol, "open": g("open"), "high": g("high"), "low": g("low"), "last": last,
            "prev_close": prev, "change_pct": g("dayChangePerc") if g("dayChangePerc") is not None else
            (((last - prev) / prev * 100) if prev else None),
            "volume": g("volume", "totalTradedVolume"), "value_cr": None,
            "year_high": g("yearHighPrice", "high52"), "year_low": g("yearLowPrice", "low52"),
            "updated": js.get(k["tsinmillis"]) if "tsinmillis" in k else None}


class GrowwClient(_Base):
    name = "Groww"

    def quote(self, nse_symbol: str) -> dict:
        if not self._ready:
            self._ready = True
            try:
                self.s.get("https://groww.in/", timeout=8)
            except Exception:
                pass
        r = self._get(GROWW_LIVE.format(sym=quote(nse_symbol, safe="")), nse_symbol)
        if r.status_code != 200:
            raise YahooError(f"{nse_symbol}: HTTP {r.status_code}")
        try:
            return parse_groww(nse_symbol, r.json())
        except ValueError:
            raise YahooError(f"{nse_symbol}: Groww sent a non-JSON page") from None
