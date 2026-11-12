"""Swing stocks app: analysis rules and the app's save actions (practice data, offline)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market import analysis, config, demo  # noqa: E402
import stocks_app  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.p = mock.patch.object(config, "DATA_DIR", self.root)
        self.p.start()
        demo.generate(self.root)

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()


class TestAnalysis(Base):
    def test_breakout_found_and_sized_for_50k(self):
        r = analysis.run(self.root)
        k = r["_by"]["KALYANKJIL"]
        self.assertEqual((k["direction"], k["stage"]), ("up", "breakout"))
        self.assertIn({"kind": "BUY", "symbol": "KALYANKJIL"}, [{"kind": a["kind"], "symbol": a["symbol"]} for a in r["actions"]])
        p = k["plan"]
        self.assertLess(p["stop"], p["entry"])
        self.assertLessEqual(p["risk"], analysis.RISK_PER_TRADE + 1)
        self.assertLessEqual(p["cost"], analysis.MAX_POSITION)
        self.assertGreaterEqual(p["qty"], 1)
        self.assertFalse(p["option"]["affordable"])          # option lot too big for ₹5,000

    def test_every_plan_respects_risk_rules(self):
        r = analysis.run(self.root)
        for s, x in r["_by"].items():
            p = x["plan"]
            if p and p.get("entry"):
                self.assertLess(p["stop"], p["entry"], s)
                self.assertLessEqual(p["risk"], analysis.RISK_PER_TRADE + 1, s)
                self.assertLessEqual(p["cost"], analysis.MAX_POSITION + 1, s)

    def test_unaffordable_stock_not_suggested(self):
        r = analysis.run(self.root)
        d = r["_by"]["DIXON"]["plan"]
        if d and d.get("entry"):
            self.assertFalse(d["fits"])
        self.assertNotIn("DIXON", [a["symbol"] for a in r["actions"] if a["kind"] in ("BUY", "WATCH")])

    def test_breakdowns_flagged(self):
        r = analysis.run(self.root)
        self.assertEqual({x["symbol"] for x in r["radar_down"] if x["stage"] == "breakdown"}, {"INDIGO", "ASIANPAINT"})

    def test_stop_hit_gives_sell(self):
        (self.root / "meta" / "positions.csv").write_text("symbol,qty,price,stop,date\nRELIANCE,5,99999,99000,2026-11-01\n")
        r = analysis.run(self.root)
        self.assertEqual(r["actions"][0]["kind"] if r["actions"][0]["symbol"] == "RELIANCE" else None, "SELL")


class TestAppActions(Base):
    def test_watchlist_and_trades(self):
        app = stocks_app.App(self.root, demo=True)
        app.watch("bel", True)
        self.assertIn("BEL", [w["symbol"] for w in app.overview()["watchlist"]])
        app.watch("BEL", False)
        self.assertNotIn("BEL", [w["symbol"] for w in app.overview()["watchlist"]])
        with self.assertRaises(ValueError):
            app.add_position({"symbol": "TCS", "qty": 1, "price": 100, "stop": 120})   # stop above price
        app.add_position({"symbol": "tcs", "qty": 2, "price": 3500, "stop": 3300})
        self.assertIn("TCS", [p["symbol"] for p in app.overview()["positions"]])
        app.close_position("TCS", 3600)
        t = app.trades()
        self.assertEqual((t[-1]["symbol"], float(t[-1]["pnl"])), ("TCS", 200.0))
        self.assertNotIn("TCS", [p["symbol"] for p in app.overview()["positions"]])


if __name__ == "__main__":
    unittest.main()
