"""Nifty / Sensex support: expiry selection, BSE format detection, per-index plans."""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402
import banknifty_strangle as bs  # noqa: E402
import bse  # noqa: E402
import strategy as st  # noqa: E402
from indices import choose_expiries  # noqa: E402

TODAY = date(2026, 11, 11)


class TestChooseExpiries(unittest.TestCase):
    def test_weeklies_reduced_to_nearest_plus_monthlies(self):
        weekly = ["17-Nov-2026", "24-Nov-2026", "01-Dec-2026", "08-Dec-2026", "15-Dec-2026",
                  "22-Dec-2026", "29-Dec-2026", "26-Jan-2027", "23-Feb-2027", "30-Mar-2027"]
        self.assertEqual(choose_expiries(weekly, TODAY),
                         ["17-Nov-2026", "24-Nov-2026", "29-Dec-2026", "26-Jan-2027", "23-Feb-2027"])

    def test_past_expiries_dropped(self):
        self.assertEqual(choose_expiries(["27-Oct-2026", "24-Nov-2026"], TODAY), ["24-Nov-2026"])


# A plausible BSE-style payload (field names are a best guess at BSE's format).
BSE_SAMPLE = {"Table": [
    {"Strike_Price1": "86,000.00", "UlaValue": "86,850.25",
     "C_Last_Trd_Price": "1,120.50", "C_BuyPrice": "1,118.00", "C_SellPrice": "1,123.00",
     "C_Open_Interest": "5,400", "C_Vol_Traded": "12,000", "C_IV": "13.1", "C_Ltp_Chng": "-15.2", "C_BuyQty": "40",
     "P_Last_Trd_Price": "260.10", "P_BuyPrice": "259.00", "P_SellPrice": "261.00",
     "P_Open_Interest": "6,100", "P_Vol_Traded": "15,500", "P_IV": "13.6", "P_Ltp_Chng": "4.0", "P_BuyQty": "60"},
    {"Strike_Price1": "92,000.00", "UlaValue": "86,850.25",
     "C_Last_Trd_Price": "60.00", "C_BuyPrice": "59.00", "C_SellPrice": "61.00",
     "C_Open_Interest": "900", "C_Vol_Traded": "800", "C_IV": "14.0", "C_Ltp_Chng": "-2", "C_BuyQty": "20",
     "P_Last_Trd_Price": "6,000", "P_BuyPrice": "-", "P_SellPrice": "-",
     "P_Open_Interest": "0", "P_Vol_Traded": "0", "P_IV": "-", "P_Ltp_Chng": "-", "P_BuyQty": "0"},
]}


class TestBse(unittest.TestCase):
    def test_column_detection(self):
        m = bse.map_columns(BSE_SAMPLE["Table"][0].keys())
        self.assertEqual(m["strike"], "Strike_Price1")
        self.assertEqual(m["spot"], "UlaValue")
        self.assertEqual(m["CE"], {"ltp": "C_Last_Trd_Price", "bid": "C_BuyPrice", "ask": "C_SellPrice",
                                   "oi": "C_Open_Interest", "volume": "C_Vol_Traded", "iv": "C_IV"})
        self.assertEqual(m["PE"]["ltp"], "P_Last_Trd_Price")

    def test_converts_to_nse_shape_and_parses(self):
        data = bse.to_nse_shape(BSE_SAMPLE, "31-Dec-2026")
        ch = bs.parse_chain(data, "31-Dec-2026", "bse")
        self.assertAlmostEqual(ch.spot, 86850.25)
        q = next(q for q in ch.quotes if q.strike == 92000 and q.opt_type == "CE")
        self.assertEqual((q.ltp, q.bid, q.ask, q.oi), (60.0, 59.0, 61.0, 900.0))

    def test_unrecognised_format_is_refused_with_columns_shown(self):
        with self.assertRaises(bs.ChainError) as cm:
            bse.to_nse_shape({"Table": [{"Strike": 1, "Foo": 2}]}, "31-Dec-2026")
        self.assertIn("Foo", str(cm.exception))
        with self.assertRaises(bs.ChainError):
            bse.to_nse_shape({"Message": "Access denied"}, "31-Dec-2026")

    def test_expiry_detection(self):
        found = bse.find_expiries({"Table1": [{"ExpiryDate": "26 Nov 2026"}, {"ExpiryDate": "31 Dec 2026"}]})
        self.assertEqual(found, {"26-Nov-2026": "26 Nov 2026", "31-Dec-2026": "31 Dec 2026"})


class TestPerIndex(unittest.TestCase):
    def test_bank_earnings_only_for_bank_nifty(self):
        exps = ["29-Dec-2026", "26-Jan-2027", "23-Feb-2027"]
        names = lambda ix: [p["event"] for p in st.build_plan(exps, TODAY, index=ix)]
        self.assertIn("Q3 bank earnings (approx. start)", names("BANKNIFTY"))
        self.assertNotIn("Q3 bank earnings (approx. start)", names("NIFTY"))

    def test_app_uses_index_lot_and_compare_covers_all(self):
        state = app.State(demo=True, out_dir=Path(tempfile.mkdtemp()))
        r = app.propose(state, {"index": ["NIFTY"]})
        self.assertEqual(r["rules"]["lot_size"], 65)
        self.assertEqual(r["index"]["name"], "Nifty 50")
        r = app.propose(state, {"index": ["SENSEX"], "lot": ["10"]})
        self.assertEqual(r["rules"]["lot_size"], 10)
        c = app.compare(state, {})
        self.assertEqual([x["index"] for x in c["rows"]], ["BANKNIFTY", "NIFTY", "SENSEX"])
        self.assertTrue(all(x.get("best") for x in c["rows"]))
        with self.assertRaises(bs.ChainError):
            app.propose(state, {"index": ["FINNIFTY"]})


if __name__ == "__main__":
    unittest.main()
