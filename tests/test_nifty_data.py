"""Offline tests for nifty_data.py: bhavcopy parsing (both formats), filters, pivots."""
import io
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nifty_data as nd  # noqa: E402

UDIFF_HEAD = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
              "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
              "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
              "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4")


def udiff_csv(day="2026-11-10", spot=24050.0):
    lines = [UDIFF_HEAD]
    for k in range(23500, 24650, 50):
        for t in ("CE", "PE"):
            px = max(1.0, (spot - k if t == "CE" else k - spot)) + 20
            lines.append(f"{day},{day},FO,NSE,IDO,1,,NIFTY,,2026-11-24,2026-11-24,{k},{t},NIFTY24NOV{k}{t},"
                         f"{px},{px+5},{px-5},{px},{px},{px+1},{spot},{px},1000,50,65000,1e6,300,F1,65,,,,,")
    lines.append(f"{day},{day},FO,NSE,IDF,2,,NIFTY,,2026-11-24,2026-11-24,,,NIFTYFUT,1,1,1,24100,1,1,{spot},1,1,1,1,1,1,F1,65,,,,,")
    lines.append(f"{day},{day},FO,NSE,IDO,3,,BANKNIFTY,,2026-11-24,2026-11-24,52000,CE,X,1,1,1,1,1,1,1,1,1,1,1,1,1,F1,30,,,,,")
    return "\n".join(lines) + "\n"


OLD_CSV = (
    "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,"
    "VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,\n"
    "OPTIDX,NIFTY,27-Jun-2024,23000,CE,150,180,140,170.5,170.5,1200,500,45000,-300,05-JUN-2024,\n"
    "OPTIDX,BANKNIFTY,26-Jun-2024,49000,PE,1,1,1,1,1,1,1,1,1,05-JUN-2024,\n"
    "FUTIDX,NIFTY,27-Jun-2024,0,XX,1,1,1,1,1,1,1,1,1,05-JUN-2024,\n")


def zipped(text, name="x.csv"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


class TestBhav(unittest.TestCase):
    def test_udiff_keeps_only_nifty_index_options(self):
        rows = nd.parse_bhav_csv(udiff_csv(), date(2026, 11, 10))
        self.assertEqual(len(rows), 23 * 2)
        r = next(r for r in rows if r["strike"] == 24000 and r["type"] == "CE")
        self.assertEqual((r["date"], r["expiry"], r["underlying"], r["oi"]), ("2026-11-10", "2026-11-24", 24050.0, 1000.0))
        self.assertEqual(r["close"], 70.0)

    def test_old_format(self):
        rows = nd.parse_bhav_csv(OLD_CSV, date(2024, 6, 5))
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["date"], r["expiry"], r["strike"], r["type"], r["close"]),
                         ("2024-06-05", "2024-06-27", 23000.0, "CE", 170.5))
        self.assertEqual(r["turnover"], 500 * 1e5)

    def test_unknown_columns_raise(self):
        with self.assertRaises(nd.DataError):
            nd.parse_bhav_csv("a,b\n1,2\n", date(2026, 1, 1))

    def test_urls_switch_format_on_8_jul_2024(self):
        self.assertIn("BhavCopy_NSE_FO_0_0_0_20240708_F_0000", nd.bhav_urls(date(2024, 7, 8))[0])
        self.assertIn("2024/JUL/fo05JUL2024bhav.csv.zip", nd.bhav_urls(date(2024, 7, 5))[0])

    def test_filters_and_near(self):
        rows = nd.parse_bhav_csv(udiff_csv(), date(2026, 11, 10))
        ce = nd.filter_rows(rows, opt_type="CE", near=2)
        self.assertEqual(sorted(r["strike"] for r in ce), [23950, 24000, 24050, 24100, 24150])
        self.assertEqual(len(nd.filter_rows(rows, strikes={24000.0})), 2)
        self.assertEqual(nd.filter_rows(rows, expiry=date(2026, 12, 29)), [])

    def test_collect_uses_cache_and_pivot(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            for d in ("20261109", "20261110"):
                (cache / f"fo_{d}.csv.zip").write_bytes(zipped(udiff_csv(f"{d[:4]}-{d[4:6]}-{d[6:]}")))
            with mock.patch.object(nd, "_download", side_effect=AssertionError("no network")):
                rows = nd.collect_options(date(2026, 11, 9), date(2026, 11, 10), cache, verbose=False,
                                          opt_type="PE", strikes={24000.0, 24100.0})
        self.assertEqual(len(rows), 4)
        cols, table = nd.pivot(rows)
        self.assertEqual(cols, ["date", "expiry", "type", "underlying", "24000", "24100"])
        self.assertEqual([t["date"] for t in table], ["2026-11-09", "2026-11-10"])

    def test_holiday_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(nd, "_download", return_value=None):
                rows = nd.collect_options(date(2026, 11, 9), date(2026, 11, 9), Path(tmp), verbose=False)
        self.assertEqual(rows, [])


class TestIndexAndContract(unittest.TestCase):
    def test_yahoo(self):
        data = {"chart": {"result": [{"timestamp": [1762746300, 1762832700],
                "indicators": {"quote": [{"open": [1, 2], "high": [3, 4], "low": [0, 1],
                                          "close": [2, None], "volume": [0, 0]}]}}]}}
        rows = nd.parse_yahoo(data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2025-11-10")

    def test_ind_close(self):
        text = ("Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,"
                "Closing Index Value,Points Change,Change(%),Volume\n"
                "Nifty 50,10-11-2026,24000.1,24100,23950,24050.55,10,0.1,123\n"
                "Nifty Bank,10-11-2026,1,1,1,1,1,1,1\n")
        r = nd.parse_ind_close(text, date(2026, 11, 10))
        self.assertEqual((r["open"], r["close"]), (24000.1, 24050.55))

    def test_focpv(self):
        data = {"data": [{"FH_TIMESTAMP": "10-Nov-2026", "FH_EXPIRY_DT": "24-Nov-2026",
                          "FH_STRIKE_PRICE": "24000.00", "FH_OPTION_TYPE": "CE",
                          "FH_CLOSING_PRICE": "120.5", "FH_SETTLE_PRICE": "120.5",
                          "FH_OPEN_INT": "1,000", "FH_UNDERLYING_VALUE": "24050"}]}
        r = nd.parse_focpv(data)[0]
        self.assertEqual((r["date"], r["expiry"], r["strike"], r["close"], r["oi"]),
                         ("2026-11-10", "2026-11-24", 24000.0, 120.5, 1000.0))


if __name__ == "__main__":
    unittest.main()
