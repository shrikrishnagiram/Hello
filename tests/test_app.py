"""Offline tests for the web app's scan endpoint logic (demo data, no network)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402
import banknifty_strangle as bs  # noqa: E402


class TestApp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = app.State(demo=True, out_dir=Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_shape_and_math(self):
        r = app.scan(self.state, {"target": ["8"], "lot": ["30"], "cflat": ["40"], "cpct": ["2"]})
        self.assertTrue(r["ok"])
        self.assertGreater(r["call"]["strike"], r["spot"])
        self.assertLess(r["put"]["strike"], r["spot"])
        pair = r["call"]["ltp"] + r["put"]["ltp"]
        self.assertAlmostEqual(r["pair_premium"], pair)
        self.assertAlmostEqual(r["total_cost"], pair * 30 * 1.02 + 40)
        self.assertAlmostEqual(r["call"]["be"], r["call"]["strike"] + pair)

    def test_log_and_history(self):
        app.scan(self.state, {"log": ["1"]})
        app.scan(self.state, {"log": ["1"]})
        rows = app.read_log(self.state)
        self.assertEqual(len(rows), 2)
        self.assertIn("pair_premium", rows[0])

    def test_csv_export(self):
        app.scan(self.state, {})
        text = app.chain_csv(self.state.last_chain)
        self.assertTrue(text.startswith("nse_timestamp,expiry,spot,strike"))

    def test_bad_input(self):
        with self.assertRaises(bs.ChainError):
            app.scan(self.state, {"target": ["abc"]})
        with self.assertRaises(bs.ChainError):
            app.scan(self.state, {"lot": ["0"]})


if __name__ == "__main__":
    unittest.main()
