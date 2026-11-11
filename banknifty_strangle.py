#!/usr/bin/env python3
"""
Bank Nifty cheap-strangle chain reader (READ-ONLY).

Fetches the Bank Nifty option chain for one expiry, finds the far-OTM call and
put whose LTP is nearest a target premium (default Rs 8), and reports distance
from spot, breakevens, and the cost of one lot of the pair.

GUARDRAIL: this script only reads market data. It contains no order-placement
code and must not be extended to place orders until the logic is fully trusted.

Every number it prints is a live snapshot -- re-verify in your broker before
acting on anything.

Examples:
    python banknifty_strangle.py
    python banknifty_strangle.py --expiry 2026-12-29 --target 8 --csv
    python banknifty_strangle.py --from-json data/snapshot.json   # offline replay
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
# Config defaults (override via CLI)
# --------------------------------------------------------------------------- #
SYMBOL = "BANKNIFTY"
DEFAULT_EXPIRY = "2026-12-29"   # last Tuesday of Dec 2026 -- verify
DEFAULT_TARGET = 8.0            # target premium per leg, Rs
DEFAULT_LOT_SIZE = 30           # units per lot (35 -> 30 from Jan 2026) -- verify
DEFAULT_CHARGES_FLAT = 40.0     # Rs, e.g. 2 orders x Rs 20 brokerage
DEFAULT_CHARGES_PCT = 2.0       # % of premium for STT/exchange/GST/stamp (rough)
DEFAULT_OUT_DIR = "data"

NSE_HOME = "https://www.nseindia.com"
NSE_PRIMER = "https://www.nseindia.com/option-chain"
NSE_V3_URL = "https://www.nseindia.com/api/option-chain-v3"
NSE_LEGACY_URL = "https://www.nseindia.com/api/option-chain-indices"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE_PRIMER,
    "Connection": "keep-alive",
}


class ChainError(RuntimeError):
    """Raised with a human-readable reason whenever data is missing or suspect."""


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class Quote:
    strike: float
    opt_type: str          # "CE" or "PE"
    ltp: float
    bid: float | None
    ask: float | None
    iv: float | None
    oi: float | None
    volume: float | None

    @classmethod
    def from_nse(cls, strike: float, opt_type: str, d: dict) -> "Quote":
        def num(*keys):
            for k in keys:
                v = d.get(k)
                if v not in (None, "", "-"):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
            return None

        return cls(
            strike=float(strike),
            opt_type=opt_type,
            ltp=num("lastPrice") or 0.0,
            bid=num("bidprice", "buyPrice1"),
            ask=num("askPrice", "sellPrice1"),
            iv=num("impliedVolatility"),
            oi=num("openInterest"),
            volume=num("totalTradedVolume"),
        )


@dataclass
class Chain:
    expiry: str            # NSE format, e.g. 29-Dec-2026
    spot: float
    timestamp: str
    quotes: list[Quote]
    source: str


# --------------------------------------------------------------------------- #
# Expiry helpers
# --------------------------------------------------------------------------- #
def to_nse_expiry(s: str) -> str:
    """Accept YYYY-MM-DD or DD-Mon-YYYY; return NSE's DD-Mon-YYYY."""
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%d-%b-%Y")
        except ValueError:
            continue
    raise ChainError(f"Unrecognised expiry '{s}'. Use YYYY-MM-DD, e.g. 2026-12-29.")


def same_expiry(a: str, b: str) -> bool:
    try:
        return to_nse_expiry(a) == to_nse_expiry(b)
    except ChainError:
        return a.strip().lower() == b.strip().lower()


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def _nse_session():
    try:
        import requests
    except ImportError as e:  # pragma: no cover
        raise ChainError("The 'requests' package is required: pip install requests") from e
    s = requests.Session()
    s.headers.update(HEADERS)
    # NSE sets anti-bot cookies on the HTML pages; the API rejects requests without them.
    for url in (NSE_HOME, NSE_PRIMER):
        try:
            s.get(url, timeout=10)
        except Exception:
            pass
    return s


def _get_json(session, url: str, params: dict, retries: int = 3) -> dict:
    last = None
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=15)
            if r.status_code == 200 and r.text.strip():
                data = r.json()
                if data:
                    return data
                last = "empty JSON body"
            else:
                last = f"HTTP {r.status_code}"
            if r.status_code in (401, 403):
                session.get(NSE_PRIMER, timeout=10)  # refresh cookies
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(1.5 * (attempt + 1))
    raise ChainError(f"NSE request to {url} failed after {retries} tries ({last}).")


def fetch_raw_nse(expiry_nse: str) -> tuple[dict, str]:
    """Try NSE's current (v3) endpoint, then the legacy one. Returns (json, source)."""
    session = _nse_session()
    errors = []
    try:
        data = _get_json(session, NSE_V3_URL,
                         {"type": "Indices", "symbol": SYMBOL, "expiry": expiry_nse})
        return data, "nse-v3"
    except ChainError as e:
        errors.append(str(e))
    try:
        data = _get_json(session, NSE_LEGACY_URL, {"symbol": SYMBOL})
        return data, "nse-legacy"
    except ChainError as e:
        errors.append(str(e))
    raise ChainError("Could not fetch from NSE:\n  - " + "\n  - ".join(errors))


def fetch_nsepython() -> tuple[dict, str]:
    try:
        from nsepython import nse_optionchain_scrapper  # type: ignore
    except ImportError as e:
        raise ChainError("nsepython not installed (pip install nsepython).") from e
    try:
        data = nse_optionchain_scrapper(SYMBOL)
    except Exception as e:
        raise ChainError(f"nsepython failed: {type(e).__name__}: {e}") from e
    if not data:
        raise ChainError("nsepython returned empty data.")
    return data, "nsepython"


def fetch(source: str, expiry_nse: str) -> tuple[dict, str]:
    if source == "nse":
        return fetch_raw_nse(expiry_nse)
    if source == "nsepython":
        return fetch_nsepython()
    # auto: raw NSE first, nsepython as fallback
    try:
        return fetch_raw_nse(expiry_nse)
    except ChainError as e1:
        try:
            return fetch_nsepython()
        except ChainError as e2:
            raise ChainError(f"{e1}\n{e2}") from None


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def parse_chain(data: dict, expiry_nse: str, source: str) -> Chain:
    """Parse NSE option-chain JSON (v3 or legacy shape) for a single expiry."""
    records = data.get("records") or {}
    rows = records.get("data") or []
    if not rows:
        raise ChainError(
            "NSE response has no option rows (records.data empty). "
            "Market data may be unavailable or the API format changed."
        )

    spot = records.get("underlyingValue")
    if not spot:
        for row in rows:
            for side in ("CE", "PE"):
                if isinstance(row.get(side), dict) and row[side].get("underlyingValue"):
                    spot = row[side]["underlyingValue"]
                    break
            if spot:
                break
    try:
        spot = float(spot)
    except (TypeError, ValueError):
        spot = 0.0
    if spot <= 0:
        raise ChainError("Could not determine Bank Nifty spot (underlyingValue missing/zero).")

    quotes: list[Quote] = []
    seen_expiries: set[str] = set()
    for row in rows:
        row_exp = row.get("expiryDate") or row.get("expiryDates") or ""
        if row_exp:
            seen_expiries.add(row_exp)
            if not same_expiry(row_exp, expiry_nse):
                continue
        strike = row.get("strikePrice")
        if strike is None:
            continue
        for side in ("CE", "PE"):
            leg = row.get(side)
            if isinstance(leg, dict):
                quotes.append(Quote.from_nse(strike, side, leg))

    if not quotes:
        avail = records.get("expiryDates") or sorted(seen_expiries)
        raise ChainError(
            f"No contracts found for expiry {expiry_nse}. "
            f"Available expiries: {', '.join(avail) if avail else 'unknown'}"
        )

    return Chain(
        expiry=expiry_nse,
        spot=spot,
        timestamp=str(records.get("timestamp") or "unknown"),
        quotes=quotes,
        source=source,
    )


# --------------------------------------------------------------------------- #
# Selection & maths
# --------------------------------------------------------------------------- #
def pick_wing(chain: Chain, opt_type: str, target: float) -> Quote:
    """OTM leg of given type whose LTP is nearest target (ties -> higher OI)."""
    if opt_type == "CE":
        cands = [q for q in chain.quotes if q.opt_type == "CE" and q.strike > chain.spot]
    else:
        cands = [q for q in chain.quotes if q.opt_type == "PE" and q.strike < chain.spot]
    cands = [q for q in cands if q.ltp > 0]
    if not cands:
        raise ChainError(f"No OTM {opt_type} strikes with a traded price (LTP > 0).")
    return min(cands, key=lambda q: (abs(q.ltp - target), -(q.oi or 0)))


@dataclass
class Report:
    call: Quote
    put: Quote
    spot: float
    pair_premium: float
    lot_size: int
    premium_cost: float
    charges: float
    total_cost: float

    @property
    def call_be(self) -> float:
        return self.call.strike + self.pair_premium

    @property
    def put_be(self) -> float:
        return self.put.strike - self.pair_premium


def build_report(chain: Chain, target: float, lot_size: int,
                 charges_flat: float, charges_pct: float) -> Report:
    call = pick_wing(chain, "CE", target)
    put = pick_wing(chain, "PE", target)
    pair = call.ltp + put.ltp
    premium_cost = pair * lot_size
    charges = charges_flat + premium_cost * charges_pct / 100.0
    return Report(call, put, chain.spot, pair, lot_size,
                  premium_cost, charges, premium_cost + charges)


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def _f(v, nd=2, dash="-"):
    return dash if v is None else f"{v:,.{nd}f}"


def pct(a: float, b: float) -> float:
    return (a - b) / b * 100.0


def wing_warnings(rep: "Report", target: float) -> list[str]:
    """Liquidity / staleness flags for the selected wings."""
    warns: list[str] = []
    for q in (rep.call, rep.put):
        if not q.volume:
            warns.append(f"{q.opt_type} {q.strike:,.0f}: zero volume today -- LTP may be stale.")
        if q.bid is not None and q.ask is not None and q.ask > 0 and q.bid > 0:
            spread = (q.ask - q.bid) / q.ask * 100
            if spread > 25:
                warns.append(f"{q.opt_type} {q.strike:,.0f}: wide bid/ask ({spread:.0f}% of ask).")
        if abs(q.ltp - target) > max(2.0, 0.5 * target):
            warns.append(f"{q.opt_type}: nearest LTP {q.ltp:.2f} is far from target {target:g}.")
    return warns


def render(chain: Chain, rep: Report, target: float) -> str:
    out = []
    out.append("=" * 78)
    out.append(f" BANK NIFTY cheap-strangle scan  |  expiry {chain.expiry}  |  target Rs {target:g}/leg")
    out.append(f" Spot {rep.spot:,.2f}   NSE timestamp: {chain.timestamp}   source: {chain.source}")
    try:
        dte = (datetime.strptime(chain.expiry, "%d-%b-%Y").date() - datetime.now().date()).days
        out.append(f" Days to expiry: {dte}")
    except ValueError:
        pass
    out.append("=" * 78)

    hdr = f"{'Leg':<5}{'Strike':>10}{'LTP':>8}{'Bid':>8}{'Ask':>8}{'IV%':>7}{'OI':>11}{'Vol':>10}"
    out.append(hdr)
    out.append("-" * len(hdr))
    for q in (rep.call, rep.put):
        out.append(
            f"{q.opt_type:<5}{q.strike:>10,.0f}{q.ltp:>8.2f}{_f(q.bid):>8}{_f(q.ask):>8}"
            f"{_f(q.iv, 1):>7}{_f(q.oi, 0):>11}{_f(q.volume, 0):>10}"
        )
    out.append("")

    hdr2 = f"{'Leg':<5}{'Dist pts':>10}{'Dist %':>9}{'Breakeven':>12}{'Move to BE':>12}{'Move %':>9}"
    out.append(hdr2)
    out.append("-" * len(hdr2))
    for q, be in ((rep.call, rep.call_be), (rep.put, rep.put_be)):
        dist = q.strike - rep.spot
        move = be - rep.spot
        out.append(
            f"{q.opt_type:<5}{dist:>+10,.0f}{pct(q.strike, rep.spot):>+8.2f}%"
            f"{be:>12,.0f}{move:>+12,.0f}{pct(be, rep.spot):>+8.2f}%"
        )
    out.append("")
    out.append(f" Pair premium      : Rs {rep.pair_premium:.2f}  (CE {rep.call.ltp:.2f} + PE {rep.put.ltp:.2f})")
    out.append(f" x lot size {rep.lot_size:<6} : Rs {rep.premium_cost:,.2f}")
    out.append(f" + charges (est.)  : Rs {rep.charges:,.2f}")
    out.append(f" = Total per pair  : Rs {rep.total_cost:,.2f}")
    out.append("")

    warns = wing_warnings(rep, target)
    if warns:
        out.append(" WARNINGS:")
        out.extend(f"  ! {w}" for w in warns)
        out.append("")
    out.append(" Read-only snapshot. Verify spot, strikes, premiums and lot size live before trading.")
    return "\n".join(out)


def write_csv(chain: Chain, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"banknifty_{chain.expiry}_{stamp}.csv"
    by_strike: dict[float, dict[str, Quote]] = {}
    for q in chain.quotes:
        by_strike.setdefault(q.strike, {})[q.opt_type] = q
    cols = ["ltp", "bid", "ask", "iv", "oi", "volume"]
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["fetched_at", "nse_timestamp", "expiry", "spot", "strike"]
                   + [f"ce_{c}" for c in cols] + [f"pe_{c}" for c in cols])
        fetched = datetime.now().isoformat(timespec="seconds")
        for strike in sorted(by_strike):
            legs = by_strike[strike]
            row = [fetched, chain.timestamp, chain.expiry, chain.spot, strike]
            for side in ("CE", "PE"):
                q = legs.get(side)
                row += [getattr(q, c) if q else "" for c in cols]
            w.writerow(row)
    return path


def save_json(data: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"banknifty_raw_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(data))
    return path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Read-only Bank Nifty cheap-strangle chain scanner.")
    p.add_argument("--expiry", default=DEFAULT_EXPIRY, help="YYYY-MM-DD or DD-Mon-YYYY (default %(default)s)")
    p.add_argument("--target", type=float, default=DEFAULT_TARGET, help="target premium per leg, Rs (default %(default)s)")
    p.add_argument("--lot-size", type=int, default=DEFAULT_LOT_SIZE, help="units per lot (default %(default)s)")
    p.add_argument("--charges-flat", type=float, default=DEFAULT_CHARGES_FLAT, help="flat charges per pair, Rs (default %(default)s)")
    p.add_argument("--charges-pct", type=float, default=DEFAULT_CHARGES_PCT, help="charges as %% of premium (default %(default)s)")
    p.add_argument("--source", choices=["auto", "nse", "nsepython"], default="auto")
    p.add_argument("--from-json", type=Path, help="parse a saved NSE JSON file instead of fetching")
    p.add_argument("--csv", action="store_true", help="dump the full chain to a timestamped CSV")
    p.add_argument("--save-json", action="store_true", help="save the raw NSE response for later replay")
    p.add_argument("--out-dir", type=Path, default=Path(DEFAULT_OUT_DIR), help="output folder (default %(default)s)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        expiry_nse = to_nse_expiry(args.expiry)
        if args.from_json:
            data = json.loads(args.from_json.read_text())
            source = f"file:{args.from_json}"
        else:
            data, source = fetch(args.source, expiry_nse)
        if args.save_json and not args.from_json:
            print(f"Raw JSON saved: {save_json(data, args.out_dir)}")
        chain = parse_chain(data, expiry_nse, source)
        rep = build_report(chain, args.target, args.lot_size, args.charges_flat, args.charges_pct)
        print(render(chain, rep, args.target))
        if args.csv:
            print(f"\nChain CSV written: {write_csv(chain, args.out_dir)}")
        return 0
    except ChainError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("No numbers shown rather than possibly-wrong ones. Check NSE / your network and retry.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
