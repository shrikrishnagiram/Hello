"""Collector tests: parsers on NSE-shaped samples, scheduling, storage and upload (all offline)."""
import gzip
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import date, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market import config, daemon, jobs, parse, store, sync  # noqa: E402

IDX_JSON = {"name": "NIFTY 200", "timestamp": "12-Nov-2026 11:05:00", "data": [
    {"priority": 1, "symbol": "NIFTY 200", "open": 14100, "dayHigh": 14180, "dayLow": 14050, "lastPrice": 14150.5,
     "previousClose": 14090, "pChange": 0.43, "totalTradedVolume": 512345678, "totalTradedValue": 2.1e11,
     "yearHigh": 14300, "yearLow": 12000},
    {"priority": 0, "symbol": "KALYANKJIL", "open": 675, "dayHigh": 702.3, "dayLow": 671, "lastPrice": 698.4,
     "previousClose": 668.2, "pChange": 4.52, "totalTradedVolume": 18400000, "totalTradedValue": 12.7e9,
     "yearHigh": 702.3, "yearLow": 399.5, "lastUpdateTime": "12-Nov-2026 11:04:59"},
    {"priority": 0, "symbol": "RELIANCE", "open": "1,480.00", "dayHigh": 1490, "dayLow": 1470, "lastPrice": 1484,
     "previousClose": 1478, "pChange": 0.4, "totalTradedVolume": 3200000, "totalTradedValue": 4.7e9,
     "yearHigh": 1600, "yearLow": 1200}]}
INDICES_JSON = {"data": [{"index": "NIFTY 50", "last": 26450.2, "percentChange": 0.31, "open": 26400, "high": 26500,
                          "low": 26380, "previousClose": 26368, "advances": 31, "declines": 19, "unchanged": 0},
                         {"index": "INDIA VIX", "last": 12.4, "percentChange": -2.1},
                         {"index": "NIFTY SOMETHING ELSE", "last": 1}]}
OI_JSON = {"data": [{"symbol": "KALYANKJIL", "latestOI": 53000, "prevOI": 50000, "changeInOI": 3000,
                     "volume": 9100, "underlyingValue": 698.4},
                    {"symbol": "RELIANCE", "latestOI": 120000, "prevOI": 121000, "volume": 30000}]}
CHAIN_JSON = {"records": {"underlyingValue": 698.4, "data": [
    {"strikePrice": 680, "CE": {"openInterest": 900, "changeinOpenInterest": 100, "lastPrice": 31, "impliedVolatility": 38},
     "PE": {"openInterest": 2500, "changeinOpenInterest": 600, "lastPrice": 12, "impliedVolatility": 40}},
    {"strikePrice": 700, "CE": {"openInterest": 3100, "changeinOpenInterest": 800, "lastPrice": 19, "impliedVolatility": 36},
     "PE": {"openInterest": 1800, "changeinOpenInterest": 300, "lastPrice": 20, "impliedVolatility": 37}},
    {"strikePrice": 720, "CE": {"openInterest": 4200, "changeinOpenInterest": 1200, "lastPrice": 10, "impliedVolatility": 35},
     "PE": {"openInterest": 400, "changeinOpenInterest": -50, "lastPrice": 33, "impliedVolatility": 36}}]}}
SEC_BHAV = (" SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE,"
            " AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
            "KALYANKJIL, EQ, 11-Nov-2026, 610.80, 668.20, 689.00, 660.10, 680.00, 681.35, 676.90, 17370000,"
            " 117570.5, 402311, 7120000, 40.99\n"
            "KALYANKJIL, N1, 11-Nov-2026, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1\n"
            "RELIANCE, EQ, 11-Nov-2026, 1470, 1472, 1485, 1466, 1480, 1478, 1477.2, 5100000, 75337.1, 210000, 2900000, 56.86\n")
FO_HEAD = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,"
           "OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,"
           "ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n")
def fo_row(tp, sym, exp, strike, opt, close, oi, choi, vol, und=681.35, lot=1175):
    return f"2026-11-11,2026-11-11,FO,NSE,{tp},1,,{sym},,{exp},{exp},{strike},{opt},x,0,0,0,{close},0,0,{und},0,{oi},{choi},{vol},0,0,F1,{lot},,,,,\n"
FO_TEXT = FO_HEAD + "".join([
    fo_row("STF", "KALYANKJIL", "2026-11-24", "", "", 684.5, 40000000, 5200000, 9000),
    fo_row("STF", "KALYANKJIL", "2026-12-29", "", "", 689.0, 3000000, 900000, 800),
    fo_row("STO", "KALYANKJIL", "2026-11-24", 700, "CE", 18, 3100000, 800000, 50000),
    fo_row("STO", "KALYANKJIL", "2026-11-24", 720, "CE", 9, 4200000, 1200000, 61000),
    fo_row("STO", "KALYANKJIL", "2026-11-24", 660, "PE", 9, 2500000, 600000, 40000),
    fo_row("STO", "KALYANKJIL", "2026-12-29", 800, "CE", 5, 9999999, 0, 10),
])


class TestParsers(unittest.TestCase):
    def test_index_quotes(self):
        rows, idx = parse.index_quotes(IDX_JSON)
        self.assertEqual([r["symbol"] for r in rows], ["KALYANKJIL", "RELIANCE"])
        self.assertEqual(idx["symbol"], "NIFTY 200")
        k = rows[0]
        self.assertEqual((k["last"], k["volume"], k["high"]), (698.4, 18400000, 702.3))
        self.assertAlmostEqual(k["value_cr"], 1270.0)
        self.assertEqual(rows[1]["open"], 1480.0)  # "1,480.00" parsed

    def test_index_quotes_refuses_unknown_format(self):
        with self.assertRaises(parse.ParseError) as cm:
            parse.index_quotes({"data": [{"sym": "X", "price": 1}]})
        self.assertIn("lastPrice", str(cm.exception))

    def test_indices_and_oi(self):
        ind = parse.all_indices(INDICES_JSON)
        self.assertEqual({r["index"] for r in ind}, {"NIFTY 50", "INDIA VIX"})
        oi = {r["symbol"]: r for r in parse.oi_spurts(OI_JSON)}
        self.assertAlmostEqual(oi["KALYANKJIL"]["change_oi_pct"], 6.0)
        self.assertEqual(oi["RELIANCE"]["change_oi"], -1000)   # derived when missing

    def test_chain_summary(self):
        s = parse.chain_summary("KALYANKJIL", CHAIN_JSON, "24-Nov-2026")
        self.assertEqual((s["ce_oi"], s["pe_oi"]), (8200, 4700))
        self.assertEqual((s["max_ce_strike"], s["max_pe_strike"], s["atm_strike"]), (720, 680, 700))
        self.assertAlmostEqual(s["pcr"], 4700 / 8200)
        self.assertEqual(s["atm_ce_iv"], 36)

    def test_sec_bhav(self):
        rows = parse.sec_bhav(SEC_BHAV)
        self.assertEqual(len(rows), 2)        # N1 series skipped
        k = rows[0]
        self.assertEqual((k["symbol"], k["close"], k["volume"], k["deliv_pct"]), ("KALYANKJIL", 681.35, 17370000, 40.99))
        self.assertEqual(len(parse.sec_bhav(SEC_BHAV, {"RELIANCE"})), 1)
        with self.assertRaises(parse.ParseError):
            parse.sec_bhav("A,B\n1,2\n")

    def test_fo_summary(self):
        (k,) = parse.fo_bhav_summary(FO_TEXT)
        self.assertEqual(k["fut_expiry"], "2026-11-24")
        self.assertEqual((k["fut_oi"], k["fut_chg_oi"], k["fut_oi_all"]), (40000000, 5200000, 43000000))
        self.assertEqual(k["opt_expiry"], "2026-11-24")         # December 800 CE ignored (not nearest)
        self.assertEqual((k["ce_oi"], k["pe_oi"], k["max_ce_strike"], k["max_pe_strike"]), (7300000, 2500000, 720, 660))
        self.assertAlmostEqual(k["pcr"], 2500000 / 7300000)
        self.assertEqual(k["lot_size"], 1175)


class FakeClient:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def json(self, path, params=None, retries=3):
        self.calls.append(path)
        if path in self.fail:
            raise jobs.NSEError(f"{path} failed (HTTP 403)")
        return {"equity-stockIndices": IDX_JSON, "allIndices": INDICES_JSON,
                "live-analysis-oi-spurts-underlyings": OI_JSON, "option-chain-v3": CHAIN_JSON,
                "option-chain-contract-info": {"expiryDates": ["13-Nov-2026", "24-Nov-2026"]},
                "holiday-master": {"CM": [{"tradingDate": "13-Nov-2026"}]}}[path]

    def file(self, url, retries=3, timeout=60):
        if "sec_bhavdata" in url:
            return SEC_BHAV.encode()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("fo.csv", FO_TEXT)
        return buf.getvalue()


class TestJobsAndStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(config, "DATA_DIR", Path(self.tmp.name) / "data")
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_snapshot_detail_eod_roundtrip(self):
        when = datetime(2026, 11, 12, 11, 5, tzinfo=config.IST)
        c = FakeClient()
        self.assertIn("2 stocks", jobs.snapshot(c, when))
        rows = store.read_rows(store.snap_path("quotes", when))
        self.assertEqual(rows[0]["symbol"], "KALYANKJIL")
        self.assertEqual(rows[0]["volume"], "18400000.0")
        self.assertEqual(store.read_universe(), ["KALYANKJIL", "RELIANCE"])
        # detail: default list falls back to biggest F&O position changes; skips expiry < 3 days
        msg = jobs.detail(c, when)
        self.assertIn("2/2", msg)
        ch = store.read_rows(store.snap_path("chains", when))
        self.assertEqual(ch[0]["expiry"], "24-Nov-2026")
        ok, msg = jobs.eod(c, date(2026, 11, 11))
        self.assertTrue(ok)
        self.assertEqual(store.read_rows(store.eod_path("fo", date(2026, 11, 11)))[0]["max_ce_strike"], "720.0")
        self.assertEqual(jobs.eod(c, date(2026, 11, 11))[1], "eod 2026-11-11: already saved")

    def test_partial_failure_still_saves_prices(self):
        when = datetime(2026, 11, 12, 11, 5, tzinfo=config.IST)
        msg = jobs.snapshot(FakeClient(fail={"live-analysis-oi-spurts-underlyings"}), when)
        self.assertIn("F&O open interest failed", msg)
        self.assertTrue(store.snap_path("quotes", when).exists())

    def test_holidays_cached(self):
        hol = jobs.holidays(FakeClient())
        self.assertIn(date(2026, 11, 13), hol)
        self.assertEqual(jobs.holidays(None), hol)     # from cache
        self.assertFalse(jobs.is_trading_day(date(2026, 11, 14), hol))  # Saturday

    def test_daemon_runs_due_slots_once_and_respects_holidays(self):
        st = {}
        with mock.patch.object(config, "now_ist", return_value=datetime(2026, 11, 12, 10, 7, tzinfo=config.IST)), \
             mock.patch.object(sync, "push", return_value="pushed") as push:
            c = FakeClient()
            daemon.tick(c, st, set())
            self.assertIn("snap 10:05", st["2026-11-12"]["done"])
            self.assertIn("option-chain-v3", c.calls)      # 10:05 is a detail slot
            n = len(c.calls)
            daemon.tick(c, st, set())
            self.assertEqual(len(c.calls), n)              # not repeated
            push.assert_called_once()
        with mock.patch.object(config, "now_ist", return_value=datetime(2026, 11, 13, 10, 7, tzinfo=config.IST)):
            c = FakeClient()
            daemon.tick(c, {}, {date(2026, 11, 13)})
            self.assertEqual(c.calls, [])                   # holiday: nothing


@unittest.skipUnless(subprocess.run(["git", "--version"], capture_output=True).returncode == 0, "git missing")
class TestSync(unittest.TestCase):
    def test_push_to_remote(self):
        with tempfile.TemporaryDirectory() as t:
            remote, work = Path(t) / "remote.git", Path(t) / "work"
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
            subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(work), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
            (work / "README.md").write_text("x")
            env_cfg = ["-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run(["git", "-C", str(work), *env_cfg, "commit", "-q", "--allow-empty", "-m", "init"], check=True)
            subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(work), *env_cfg, "commit", "-q", "-m", "readme"], check=True)
            subprocess.run(["git", "-C", str(work), "push", "-q", "-u", "origin", "main"], check=True)
            with mock.patch.object(config, "DATA_DIR", work):
                self.assertEqual(sync.push("nothing"), "nothing new")
                store.write_rows(store.snap_path("quotes", datetime(2026, 11, 12, 11, 5)), [{"symbol": "A"}], ["symbol"])
                self.assertEqual(sync.push("snap"), "pushed")
            log = subprocess.run(["git", "-C", str(remote), "log", "--oneline", "main"], capture_output=True, text=True).stdout
            self.assertIn("snap", log)




class TestUniverseSources(unittest.TestCase):
    """NSE retired equity-stockIndices (404) in 2026: list from NSE's file, prices from Yahoo."""
    LIST_CSV = "\ufeffCompany Name,Industry,Symbol,Series,ISIN Code\n" + "".join(
        f"Co {i},Industry {i % 3},SYM{i},EQ,INE{i:09d}\n" for i in range(12)) + "Kalyan Jewellers India Ltd.,Consumer Durables,KALYANKJIL,EQ,INE303R01014\n"
    YJS = {"chart": {"result": [{"meta": {"symbol": "KALYANKJIL.NS", "regularMarketPrice": 698.4,
            "regularMarketDayHigh": 702.3, "regularMarketDayLow": 671.0, "regularMarketVolume": 18400000,
            "chartPreviousClose": 600.0, "fiftyTwoWeekHigh": 702.3, "fiftyTwoWeekLow": 399.5, "regularMarketTime": 1762933500},
            "timestamp": [1, 2], "indicators": {"quote": [{"open": [661, 675], "high": [689, 702.3], "low": [660, 671],
            "close": [668.2, 698.4], "volume": [17370000, 18400000]}]}}], "error": None}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(config, "DATA_DIR", Path(self.tmp.name) / "data")
        self.p.start()
        jobs._PREFERRED["name"] = None

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()
        jobs._PREFERRED["name"] = None

    def test_index_list_and_yahoo_parsers(self):
        rows = parse.index_list(self.LIST_CSV)
        self.assertEqual(rows[-1], {"symbol": "KALYANKJIL", "company": "Kalyan Jewellers India Ltd.", "industry": "Consumer Durables"})
        from market import yahoo
        q = yahoo.parse_chart("KALYANKJIL", self.YJS)
        self.assertEqual((q["last"], q["open"], q["high"], q["volume"], q["prev_close"]), (698.4, 675, 702.3, 18400000, 668.2))
        self.assertAlmostEqual(q["change_pct"], (698.4 - 668.2) / 668.2 * 100)
        with self.assertRaises(yahoo.YahooError):
            yahoo.parse_chart("X", {"chart": {"result": None, "error": {"code": "Not Found"}}})

    def _client(self):
        test = self
        class C(FakeClient):
            def json(self, path, params=None, retries=3):
                self.calls.append(path)
                if path in ("equity-stockIndices", "quote-equity"):
                    raise jobs.NSEError(f"{path} failed (HTTP 404)")
                return super().json(path, params, retries)
            def file(self, url, retries=3, timeout=60):
                return test.LIST_CSV.encode() if "ind_nifty200list" in url else super().file(url)
        return C()

    def test_yahoo_used_when_nse_feed_retired(self):
        from market import yahoo
        class Y:
            name = "Yahoo Finance"
            def quote(self, sym):
                if sym == "SYM3":
                    raise yahoo.YahooError("SYM3: HTTP 404")
                return dict(yahoo.parse_chart(sym, TestUniverseSources.YJS), symbol=sym)
        class G:
            name = "Groww"
            def quote(self, sym):
                return dict(yahoo.parse_chart(sym, TestUniverseSources.YJS), symbol=sym)
        rows, src = jobs.universe_quotes(self._client(), sources=[Y(), G()])
        self.assertEqual(len(rows), 12)                          # 13 listed, one failed; 12/13 > 80% so Groww not needed
        self.assertTrue(src.startswith("Yahoo Finance (12/13)"), src)
        self.assertEqual(store.read_universe()[0], "KALYANKJIL")  # list saved with industries
        self.assertIn("Consumer Durables", (config.DATA_DIR / "meta" / "universe.csv").read_text())

    def test_clear_error_when_everything_fails(self):
        from market import yahoo
        class Y:
            name = "Yahoo Finance"
            def quote(self, sym):
                raise yahoo.YahooError(f"{sym}: HTTP 429")
        class C2(type(self._client())):
            def json(self, path, params=None, retries=3):
                if path == "live-analysis-oi-spurts-underlyings":
                    raise jobs.NSEError("oi failed (HTTP 403)")
                return super().json(path, params, retries)
        c = C2()
        with self.assertRaises(jobs.NSEError) as cm:
            jobs.universe_quotes(c, sources=[Y()])
        self.assertIn("Yahoo Finance: SYM0: HTTP 429", str(cm.exception))
        self.assertLess(sum(1 for x in c.calls if x == "quote-equity"), 1)

    def test_falls_through_to_groww_then_fo_prices(self):
        from market import yahoo
        jobs._PREFERRED["name"] = None
        class Y:
            name = "Yahoo Finance"
            def quote(self, sym):
                raise yahoo.YahooError(f"{sym}: HTTP 429")
        class G:
            name = "Groww"
            calls = 0
            def quote(self, sym):
                G.calls += 1
                return yahoo.parse_groww(sym, {"ltp": 698.4, "open": 675, "high": 702.3, "low": 671,
                                                "close": 668.2, "dayChange": 30.2, "dayChangePerc": 4.52,
                                                "volume": 18400000})
        rows, src = jobs.universe_quotes(self._client(), sources=[Y(), G()])
        self.assertTrue(src.startswith("Groww (13/13)"), src)
        self.assertEqual((rows[0]["prev_close"], rows[0]["volume"]), (668.2, 18400000))
        self.assertEqual(jobs._PREFERRED["name"], "Groww")      # remembered for next time
        # next run tries Groww first: Yahoo never called
        class Ynever(Y):
            def quote(self, sym):
                raise AssertionError("should not be tried first")
        rows, src = jobs.universe_quotes(self._client(), sources=[Ynever(), G()])
        self.assertTrue(src.startswith("Groww"))
        jobs._PREFERRED["name"] = None




class TestSyncLock(unittest.TestCase):
    def test_uploads_take_turns(self):
        import threading, time as _t
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / ".git").mkdir()
            order = []
            with mock.patch.object(config, "DATA_DIR", Path(t)), \
                 mock.patch.object(sync, "_push", side_effect=lambda m: (order.append(f"start {m}"), _t.sleep(0.3), order.append(f"end {m}"), "pushed")[-1]):
                a = threading.Thread(target=sync.push, args=("backfill",)); a.start()
                _t.sleep(0.05)
                b = threading.Thread(target=sync.push, args=("snapshot",)); b.start()
                a.join(); b.join()
            self.assertEqual(order, ["start backfill", "end backfill", "start snapshot", "end snapshot"])


if __name__ == "__main__":
    unittest.main()
