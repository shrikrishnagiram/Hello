# Index strangle planner: Bank Nifty, Nifty 50, Sensex

A **read-only** script that pulls the Bank Nifty option chain for one expiry. It finds the
far-OTM call and put whose LTP is nearest a target premium (default ₹8), then reports
how far each wing is from spot, the breakevens, and the cost of one lot of the pair.

> It does not place orders. Every figure is a live snapshot, so check it in your broker
> before you trade.

## On an Android phone
See **[PHONE_SETUP.md](PHONE_SETUP.md)**: runs on the phone through Termux and installs as a home-screen app.

## Indices
| Index | Exchange | Data source | Lot (verify) | Expiries |
|---|---|---|---|---|
| Bank Nifty | NSE | NSE option-chain API | 30 | monthly |
| Nifty 50 | NSE | NSE option-chain API | 65 | weekly + monthly |
| Sensex | BSE | BSE API (`bse.py`), with column detection | 20 | weekly + monthly |

For weekly indices the planner analyses the nearest 2 expiries plus each of the next 4
monthly expiries. Q3 bank earnings appear only in the Bank Nifty plan. "Compare all three"
lines up the best Near/Mid/Far pair for each index's next catalyst.

The Sensex reader has not been tested against live BSE data yet. If BSE's format isn't
recognised, the app shows the columns it received instead of guessing.

## How the planner decides (strategy.py)
- **Every listed expiry** is scanned (nearest 4). Expiries with < 7 days left are skipped.
- **Reachable strikes only:** each strike's distance is measured in the market's own
  expected-move units, σ = at-the-money IV × √(days/365). Only strikes **0.75σ–2σ** from
  spot are allowed. Closer is an expensive near-the-money strangle; farther is an
  unrealistic lottery ticket (e.g. a −18.7% put with 48 days left ≈ 3σ → rejected).
- **Tradeable only:** two-sided quote, spread ≤ 15%, open interest ≥ 500, traded today.
  Prices use the **ask** (what you'd pay).
- **Combinations:** every allowed call × put in the same expiry, roughly balanced in σ.
  They're grouped **Near / Mid / Far** and ranked by what the pair is worth after a ±5%
  move within a week (in the weaker direction). Each proposal also shows ±3% and ±8%,
  breakevens, the market-implied chance of touching a breakeven, and daily decay.
- **Plan:** for each catalyst, enter in the week before it, use an expiry at least 2
  weeks after it, exit 2 trading days after the event if nothing moved, never hold the
  last week, and take profit at 3× / 5×. Expiries that don't cover a catalyst are marked
  "no catalyst".
- Probabilities are **market-implied** (what the chain prices), not forecasts.

## Nifty 50 historical data (`nifty_data.py`)
Downloads Nifty 50 index history, the live option chain, and the daily price history of
Nifty options at **every strike** into CSV files in `data/`. Run it on your own computer or
in Termux: NSE blocks cloud servers.

```bash
python nifty_data.py index --from 2015-01-01                      # daily OHLC of Nifty 50
python nifty_data.py chain                                        # live chain, nearest 6 expiries
python nifty_data.py chain --all-expiries
python nifty_data.py options --from 2026-10-01 --to 2026-11-10    # all strikes, all expiries
python nifty_data.py options --from 2026-10-01 --expiry 2026-11-24 --near 10 --pivot
python nifty_data.py options --from 2026-10-01 --strikes 23500,24000,24500 --type PE
python nifty_data.py contract --strike 24000 --type CE --expiry 2026-11-24 --from 2026-10-01
```

| Command | Source | Output |
|---|---|---|
| `index` | Yahoo `^NSEI` (fallback: NSE `ind_close_all` daily archive) | `nifty50_daily_*.csv`: date, OHLC, volume |
| `chain` | NSE option-chain API | `nifty_chain_*.csv`: per strike CE/PE LTP, bid, ask, IV, OI, volume |
| `options` | NSE F&O bhavcopy, one zip per trading day (UDiFF from 8-Jul-2024, old format before) | `nifty_options_*.csv`: date, expiry, strike, type, OHLC, close, settle, underlying, OI, OI change, volume |
| `contract` | NSE `foCPV` API (fallback: bhavcopies) | one strike's daily history |

- `--near N` keeps N strikes on each side of that day's spot; `--min-strike`/`--max-strike`
  and `--strikes` narrow further. `--pivot` also writes a date × strike matrix
  (`--pivot-value close|settle|oi|volume`).
- Bhavcopies are cached in `data/bhav_cache/`, so a re-run or a different filter
  over the same dates doesn't download again. Holidays are skipped.
- End-of-day data only: bhavcopies have no bid/ask or IV. Intraday option history
  isn't published free by NSE; you'd need a broker API (Kite, Upstox, etc.).
- The old-format bhavcopy (before 8-Jul-2024) has no underlying price or traded quantity
  column; those fields are blank. Join with `index` output for spot.

## The app (recommended)
A local web app that shows the same scan visually: wing cards, the cost of a pair, a chart
of premium by strike, the catalyst calendar with countdowns, the full option chain, and a
scan history.

```bash
pip install -r requirements.txt
python app.py            # live NSE data → opens http://127.0.0.1:8050
python app.py --demo     # synthetic data: try the UI any time (clearly badged DEMO)
```

- **Run it on your own computer.** NSE blocks most cloud servers.
- **Auto-refresh:** off, 1, 5 or 15 min. NSE is called at most once every 20 s per expiry.
- **"Log scan to history"** appends each scan to `data/scan_log.csv`, so you can watch
  pair premium and IV going into a catalyst like 4 Dec RBI.
- **"Download chain CSV"** saves the full chain from the latest scan.
- It listens only on `127.0.0.1`. Use `--host 0.0.0.0` only if you want to open it from
  your phone on the same Wi-Fi.
- On an error (NSE down, missing expiry, no spot price) it shows a red banner and no
  numbers.

## Command-line script

## Setup
```bash
pip install -r requirements.txt      # optional: pip install nsepython
```

## Usage
```bash
python banknifty_strangle.py                                 # 29-Dec-2026 expiry, ₹8 target
python banknifty_strangle.py --expiry 2026-12-29 --target 8 --csv
python banknifty_strangle.py --save-json                     # keep the raw NSE response
python banknifty_strangle.py --from-json data/banknifty_raw_XXXX.json   # offline replay
```

| Option | Default | Meaning |
|---|---|---|
| `--expiry` | `2026-12-29` | `YYYY-MM-DD` or `DD-Mon-YYYY` |
| `--target` | `8` | target premium per leg (₹) |
| `--lot-size` | `30` | units per lot (check this in your broker) |
| `--charges-flat` / `--charges-pct` | `40` / `2` | rough charge estimate per pair |
| `--source` | `auto` | `nse` (raw NSE API: v3 first, then the legacy endpoint), `nsepython`, or `auto` (raw NSE, then nsepython) |
| `--csv` | off | write the full chain to `data/banknifty_<expiry>_<timestamp>.csv` |
| `--out-dir` | `data` | output folder (gitignored) |

## How it works
- **Wing selection:** among OTM strikes (calls above spot, puts below spot), it finds the
  one whose buy price is closest to the target. The buy price is the LTP, or the ask if
  the strike hasn't traded today. A wing counts as a **match** only if its price is within
  ±max(₹2, 50% of target) of the target (±₹4 for ₹8). With no match, the app says
  **"No ₹8 strangle available"**, explains how far out strikes are trading, and shows the
  closest strikes greyed out for reference only.
- **Breakevens:** call BE = call strike + pair premium; put BE = put strike − pair
  premium. "Move %" is the distance from spot to the breakeven.
- **Warnings** appear for zero-volume legs (the LTP may be stale), bid/ask spreads wider
  than 25% of the ask, and legs with no LTP near the target.
- **Errors:** if NSE fails, returns no rows, has no spot price, or doesn't have the
  expiry, the script exits with code 2 and a clear message (listing the available
  expiries when relevant). It never prints partial or guessed numbers.

## Tests
```bash
python -m unittest discover -s tests -v
```
The tests run offline against a synthetic chain built in the same shape as NSE's JSON.
