# Bank Nifty cheap-strangle chain reader

A **read-only** script that pulls the Bank Nifty option chain for one expiry. It finds the
far-OTM call and put whose LTP is nearest a target premium (default ₹8), then reports
how far each wing is from spot, the breakevens, and the cost of one lot of the pair.

> It does not place orders. Every figure is a live snapshot, so check it in your broker
> before you trade.

## On an Android phone
See **[PHONE_SETUP.md](PHONE_SETUP.md)**: runs on the phone through Termux and installs as a home-screen app.

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
- **Wing selection:** among OTM strikes with LTP > 0 (calls above spot, puts below
  spot), it picks the strike with the LTP closest to the target. On a tie it picks the
  strike with the higher OI.
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
