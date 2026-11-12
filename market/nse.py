"""Small, polite NSE client: JSON API + archive files, with clear errors. READ-ONLY."""

from __future__ import annotations

import io
import time
import zipfile
from urllib.parse import quote, urlencode

from . import config

HOME = "https://www.nseindia.com"
PRIMERS = ["https://www.nseindia.com/", "https://www.nseindia.com/market-data/live-equity-market"]
API = "https://www.nseindia.com/api"
ARCHIVES = "https://nsearchives.nseindia.com"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.nseindia.com/market-data/live-equity-market",
}


class NSEError(RuntimeError):
    pass


class NSEClient:
    def __init__(self, gap: float = config.REQUEST_GAP_SECONDS):
        import requests
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.gap = gap
        self._last = 0.0
        self._warm = False

    def _wait(self):
        dt = time.time() - self._last
        if dt < self.gap:
            time.sleep(self.gap - dt)
        self._last = time.time()

    def warm(self):
        for u in PRIMERS:
            try:
                self.s.get(u, timeout=12)
            except Exception:
                pass
        self._warm = True

    def json(self, path: str, params: dict | None = None, retries: int = 3):
        if not self._warm:
            self.warm()
        url = f"{API}/{path.lstrip('/')}"
        if params:  # NSE wants spaces as %20 (not '+'), e.g. index=NIFTY%20200
            url += "?" + urlencode(params, quote_via=quote)
        last = None
        for attempt in range(retries):
            self._wait()
            try:
                r = self.s.get(url, timeout=20)
                if r.status_code == 200 and r.text.strip():
                    data = r.json()
                    if data:
                        return data
                    last = "empty response"
                else:
                    last = f"HTTP {r.status_code}"
                    if r.status_code in (401, 403):
                        self.warm()
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
            time.sleep(2 * (attempt + 1))
        raise NSEError(f"{path} failed ({last})")

    def file(self, url: str, retries: int = 3) -> bytes | None:
        """Download an archive file. Returns None if it doesn't exist (holiday / not yet published)."""
        if not self._warm:
            self.warm()
        last = None
        for attempt in range(retries):
            self._wait()
            try:
                r = self.s.get(url, timeout=60)
                if r.status_code == 404:
                    return None
                if r.status_code == 200 and r.content:
                    return r.content
                last = f"HTTP {r.status_code}"
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
            time.sleep(3 * (attempt + 1))
        raise NSEError(f"download failed: {url} ({last})")


def unzip_first(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = z.namelist()[0]
        return z.read(name).decode("utf-8", errors="replace")


# --- URLs --------------------------------------------------------------------
def url_sec_bhav(d) -> str:
    """Equity daily file with price, volume AND delivery %."""
    return f"{ARCHIVES}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"


def url_fo_bhav(d) -> str:
    """F&O daily file (UDiFF format) with every contract's price and open interest."""
    return f"{ARCHIVES}/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


INDEX_LIST_FILES = {"NIFTY 200": "ind_nifty200list.csv", "NIFTY 100": "ind_nifty100list.csv",
                    "NIFTY MIDCAP 100": "ind_niftymidcap100list.csv", "NIFTY 50": "ind_nifty50list.csv"}


def url_index_list(index: str) -> str:
    """NSE's official constituents file for an index (symbol, company, industry)."""
    return f"{ARCHIVES}/content/indices/{INDEX_LIST_FILES[index]}"
