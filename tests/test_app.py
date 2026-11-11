"""Offline tests for the proposal engine and web app (demo/synthetic data, no network)."""
import math
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402
import banknifty_strangle as bs  # noqa: E402
import strategy as st  # noqa: E402
from demo_chain import demo_chain  # noqa: E402

TODAY = date(2026, 11, 11)


def nse_rows(spot, legs, expiry="29-Dec-2026"):
    """legs: (strike, opt, bid, ask, ltp, iv%, oi, vol)"""
    by = {}
    for k, o, bid, ask, ltp, iv, oi, vol in legs:
        by.setdefault(k, {"strikePrice": k, "expiryDate": expiry})[o] = {
            "bidprice": bid, "askPrice": ask, "lastPrice": ltp, "impliedVolatility": iv,
            "openInterest": oi, "totalTradedVolume": vol, "underlyingValue": spot}
    return {"records": {"underlyingValue": spot, "timestamp": "t", "data": list(by.values())}}


class TestMath(unittest.TestCase):
    def test_put_call_parity_and_iv_roundtrip(self):
        S, K, T, r, v = 60000, 62000, 0.13, 0.06, 0.18
        c, p = st.bs_price(S, K, T, v, r, "CE"), st.bs_price(S, K, T, v, r, "PE")
        self.assertAlmostEqual(c - p, S - K * math.exp(-r * T), places=6)
        self.assertAlmostEqual(st.implied_vol(c, S, K, T, r, "CE"), v, places=4)


class TestEngine(unittest.TestCase):
    def test_rejects_the_49500_put_from_the_first_live_scan(self):
        # Shape of the user's first real scan: spot 60,858, Dec expiry, ATM IV ~17%.
        spot = 60858
        legs = [(61000, "CE", 900, 905, 902, 17.1, 9000, 90000), (60500, "PE", 880, 885, 882, 17.5, 9000, 90000),
                (67000, "CE", 137.9, 141.9, 139, 17.1, 1747, 4900),
                (55000, "PE", 150, 154, 152, 22.0, 3000, 8000),
                (49500, "PE", 24.15, 0, 24.15, 26.6, 139, 13)]
        ch = bs.parse_chain(nse_rows(spot, legs), "29-Dec-2026", "t")
        ea = st.analyze_expiry(ch, TODAY, st.Rules())
        self.assertIn("too far (unrealistic)", ea.excluded)
        picked = [(c.call.strike, c.put.strike) for b in ea.bands.values() for c in b]
        self.assertTrue(picked, "expected at least one proposal")
        self.assertNotIn(49500, [p for _, p in picked])
        self.assertIn((67000, 55000), picked)

    def test_bands_are_distinct_and_within_zone(self):
        ch = bs.parse_chain(demo_chain("29-Dec-2026", spot=60850, seed=1), "29-Dec-2026", "demo")
        rules = st.Rules()
        ea = st.analyze_expiry(ch, TODAY, rules)
        self.assertTrue(all(ea.bands[b] for b in ("Near", "Mid", "Far")))
        for band, combos in ea.bands.items():
            for c in combos:
                for leg in (c.call, c.put):
                    self.assertGreaterEqual(abs(leg.sigma), rules.min_sigma - 1e-9)
                    self.assertLessEqual(abs(leg.sigma), rules.max_sigma + 1e-9)
                    self.assertGreater(leg.ask, 0)
                self.assertAlmostEqual(c.premium, c.call.ask + c.put.ask)
        far, near = ea.bands["Far"][0], ea.bands["Near"][0]
        self.assertLess(far.cost, near.cost)          # far is cheaper
        self.assertLess(far.p_touch, near.p_touch)    # ...and less likely to pay

    def test_budget_caps_cost(self):
        ch = bs.parse_chain(demo_chain("29-Dec-2026", spot=60850, seed=2), "29-Dec-2026", "demo")
        ea = st.analyze_expiry(ch, TODAY, st.Rules(budget=7000))
        for combos in ea.bands.values():
            for c in combos:
                self.assertLessEqual(c.cost, 7000)

    def test_short_expiry_skipped(self):
        ch = bs.parse_chain(demo_chain("17-Nov-2026", spot=60850, seed=3), "17-Nov-2026", "demo")
        self.assertIsNotNone(st.analyze_expiry(ch, TODAY, st.Rules()).skipped_reason)


class TestPlan(unittest.TestCase):
    def test_rbi_uses_dec_expiry_with_two_week_buffer(self):
        plan = st.build_plan(["24-Nov-2026", "29-Dec-2026", "26-Jan-2027"], TODAY)
        rbi = plan[0]
        self.assertEqual(rbi["event"], "RBI MPC decision")
        self.assertEqual(rbi["expiry"], "29-Dec-2026")
        self.assertEqual(rbi["entry_from"], "2026-11-27")
        self.assertEqual(rbi["entry_to"], "2026-12-03")
        self.assertEqual(rbi["exit_if_no_move"], "2026-12-08")
        budget = next(p for p in plan if p["event"] == "Union Budget")
        self.assertIsNone(budget["expiry"])          # Feb not listed in this list
        self.assertTrue(budget["expiry_note"])


class TestApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = app.State(demo=True, out_dir=Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_proposals_payload_and_log(self):
        r = app.propose(self.state, {"log": ["1"]})
        self.assertTrue(r["ok"])
        self.assertTrue(r["plan"])
        usable = [e for e in r["expiries"] if not e["skipped_reason"]]
        self.assertTrue(usable)
        self.assertTrue(any(e["bands"]["Mid"] for e in usable))
        rows = app.read_log(self.state)
        self.assertEqual(len(rows), len(usable))
        self.assertIn("atm_iv", rows[0])

    def test_bad_input(self):
        with self.assertRaises(bs.ChainError):
            app.propose(self.state, {"lot": ["abc"]})
        with self.assertRaises(bs.ChainError):
            app.propose(self.state, {"min_sigma": ["2"], "max_sigma": ["1"]})


if __name__ == "__main__":
    unittest.main()
