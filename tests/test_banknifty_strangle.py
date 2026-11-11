"""Offline tests using a synthetic NSE-shaped option chain (no network)."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import banknifty_strangle as bs  # noqa: E402

SPOT = 59500.0


def synth_premium(strike, opt):
    # Crude decaying wing curve: ~Rs 8 at roughly +/-10% from spot.
    dist = (strike - SPOT) if opt == "CE" else (SPOT - strike)
    if dist <= 0:
        return round(-dist + 400, 2)
    return round(max(0.05, 400 * math.exp(-dist / 1520)), 2)


def make_chain(expiry_key="expiryDate", expiry="29-Dec-2026", extra_expiry=True):
    rows = []
    for strike in range(50000, 69100, 500):
        for exp in [expiry] + (["23-Nov-2026"] if extra_expiry else []):
            row = {"strikePrice": strike, expiry_key: exp}
            for opt in ("CE", "PE"):
                row[opt] = {
                    "strikePrice": strike, expiry_key: exp,
                    "lastPrice": synth_premium(strike, opt) if exp == expiry else 99.0,
                    "impliedVolatility": 15.0, "openInterest": 1000,
                    "totalTradedVolume": 500, "bidprice": 1, "askPrice": 1.1,
                    "underlyingValue": SPOT,
                }
            rows.append(row)
    return {"records": {"timestamp": "11-Nov-2026 15:30:00", "underlyingValue": SPOT,
                        "expiryDates": ["23-Nov-2026", expiry], "data": rows}}


class TestStrangle(unittest.TestCase):
    def test_expiry_formats(self):
        self.assertEqual(bs.to_nse_expiry("2026-12-29"), "29-Dec-2026")
        self.assertEqual(bs.to_nse_expiry("29-Dec-2026"), "29-Dec-2026")
        with self.assertRaises(bs.ChainError):
            bs.to_nse_expiry("Dec 29")

    def test_picks_otm_wings_near_target(self):
        chain = bs.parse_chain(make_chain(), "29-Dec-2026", "test")
        rep = bs.build_report(chain, 8, 30, 40, 2)
        self.assertEqual(rep.call.opt_type, "CE")
        self.assertGreater(rep.call.strike, SPOT)
        self.assertLess(rep.put.strike, SPOT)
        self.assertAlmostEqual(rep.call.ltp, 8, delta=3)
        self.assertAlmostEqual(rep.put.ltp, 8, delta=3)
        pair = rep.call.ltp + rep.put.ltp
        self.assertAlmostEqual(rep.premium_cost, pair * 30)
        self.assertAlmostEqual(rep.charges, 40 + pair * 30 * 0.02)
        self.assertAlmostEqual(rep.call_be, rep.call.strike + pair)
        self.assertAlmostEqual(rep.put_be, rep.put.strike - pair)

    def test_filters_other_expiry(self):
        chain = bs.parse_chain(make_chain(), "29-Dec-2026", "test")
        self.assertTrue(all(q.ltp != 99.0 for q in chain.quotes))

    def test_v3_shape(self):
        chain = bs.parse_chain(make_chain(expiry_key="expiryDates", extra_expiry=False),
                               "29-Dec-2026", "test")
        self.assertTrue(chain.quotes)

    def test_missing_expiry_lists_available(self):
        with self.assertRaises(bs.ChainError) as cm:
            bs.parse_chain(make_chain(), "26-Jan-2027", "test")
        self.assertIn("23-Nov-2026", str(cm.exception))

    def test_empty_and_no_spot(self):
        with self.assertRaises(bs.ChainError):
            bs.parse_chain({}, "29-Dec-2026", "test")
        d = make_chain()
        d["records"]["underlyingValue"] = 0
        for r in d["records"]["data"]:
            for s in ("CE", "PE"):
                r[s]["underlyingValue"] = 0
        with self.assertRaises(bs.ChainError):
            bs.parse_chain(d, "29-Dec-2026", "test")

    def test_cli_from_json_and_csv(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "chain.json"
            p.write_text(json.dumps(make_chain()))
            rc = bs.main(["--from-json", str(p), "--csv", "--out-dir", td])
            self.assertEqual(rc, 0)
            self.assertTrue(list(Path(td).glob("banknifty_29-Dec-2026_*.csv")))


if __name__ == "__main__":
    unittest.main()


def chain_from_rows(spot, rows):
    """rows: list of (strike, opt, ltp, ask, volume)."""
    by = {}
    for strike, opt, ltp, ask, vol in rows:
        by.setdefault(strike, {"strikePrice": strike, "expiryDate": "29-Dec-2026"})[opt] = {
            "lastPrice": ltp, "askPrice": ask, "bidprice": ltp, "totalTradedVolume": vol,
            "openInterest": 100, "impliedVolatility": 20, "underlyingValue": spot}
    return {"records": {"underlyingValue": spot, "timestamp": "t", "data": list(by.values())}}


class TestNoMatch(unittest.TestCase):
    """Reproduces the first live scan: nothing near Rs 8 trades for Dec."""

    def setUp(self):
        self.data = chain_from_rows(60858, [
            (64000, "CE", 300, 302, 9000), (67000, "CE", 139, 141.9, 4900),
            (68000, "CE", 0, 0, 0), (70000, "CE", 0, 0, 0),
            (55000, "PE", 180, 182, 3000), (49500, "PE", 24.15, 0, 13),
        ])

    def test_reports_no_match_instead_of_a_pick(self):
        chain = bs.parse_chain(self.data, "29-Dec-2026", "t")
        rep = bs.build_report(chain, 8, 30, 40, 2)
        self.assertFalse(rep.call_wing.matched)
        self.assertFalse(rep.put_wing.matched)
        self.assertFalse(rep.matched)
        self.assertEqual(rep.call_wing.farthest_traded, 67000)
        self.assertEqual(rep.call_wing.farthest_listed, 70000)
        warns = " ".join(bs.wing_warnings(rep, 8))
        self.assertIn("No call near Rs 8", warns)
        self.assertIn("no seller quoting", warns)
        text = bs.render(chain, rep, 8)
        self.assertIn("NO Rs 8 STRANGLE AVAILABLE", text)

    def test_quote_only_strike_counts_at_ask(self):
        self.data["records"]["data"].append({"strikePrice": 72000, "expiryDate": "29-Dec-2026",
            "CE": {"lastPrice": 0, "askPrice": 8.5, "bidprice": 6, "totalTradedVolume": 0, "underlyingValue": 60858}})
        chain = bs.parse_chain(self.data, "29-Dec-2026", "t")
        w = bs.pick_wing(chain, "CE", 8)
        self.assertTrue(w.matched)
        self.assertEqual(w.quote.strike, 72000)
        self.assertEqual(w.price, 8.5)
        self.assertTrue(w.basis.startswith("ask"))
