import datetime
import gzip
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

scan = load("trader-scanner", "scan")

# Everything here is synthetic and deterministic: charts are built in code, and the one fixture file is a
# hand-written EQUITY_L.csv (quoted comma, non-EQ series, a truncated row, a blank line).
NSEDIR = os.path.join(FIX, "trader-scanner", "nse")
DT = datetime.datetime
D = datetime.date
HOLIDAYS_2026 = ["2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03",
                 "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02",
                 "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25"]   # the official NSE list


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


def ts(ist_text):
    """IST wall-clock text -> unix seconds, by plain UTC+5:30 arithmetic (no DST)."""
    local = DT.strptime(ist_text, "%Y-%m-%d %H:%M:%S" if ist_text.count(":") == 2 else "%Y-%m-%d %H:%M")
    return int(((local - datetime.timedelta(hours=5, minutes=30)) - DT(1970, 1, 1)).total_seconds())


def bars_series(day, closes, highs=None, lows=None, start="09:15", extra=None, vol=1000):
    """A v8-like series of 5-minute bars from `start` on `day` (YYYY-MM-DD); None closes are missing bars."""
    t0 = DT.strptime(day + " " + start, "%Y-%m-%d %H:%M")
    n = len(closes)
    t = [ts((t0 + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M")) for i in range(n)]
    s = {"t": t, "c": list(closes),
         "h": list(highs or [c + 0.1 if c is not None else None for c in closes]),
         "l": list(lows or [c - 0.1 if c is not None else None for c in closes]),
         "o": None, "v": list(vol) if isinstance(vol, list) else [vol] * n, "meta": {}}
    for when, c in (extra or []):
        s["t"].append(ts(when))
        for k in ("c", "h", "l"):
            s[k].append(c)
        s["v"].append(10)
    return s


def chart_doc(series, ysym):
    q = {"high": series["h"], "low": series["l"], "close": series["c"], "volume": series["v"]}
    return {"chart": {"result": [{"meta": dict(series["meta"], symbol=ysym), "timestamp": series["t"],
                                  "indicators": {"quote": [q]}}], "error": None}}


DEAD = {"chart": {"result": None, "error": {"code": "Not Found",
                                            "description": "No data found, symbol may be delisted"}}}


def spark_doc(entries):
    """{yahoo symbol: series, or None for an item with an empty response} -> a spark answer (closes only)."""
    res = []
    for y, s in entries.items():
        if s is None:
            res.append({"symbol": y, "response": []})
            continue
        res.append({"symbol": y, "response": [{"meta": dict(s["meta"], symbol=y), "timestamp": list(s["t"]),
                                               "indicators": {"quote": [{"close": list(s["c"])}]}}]})
    return {"spark": {"result": res, "error": None}}


def write(path, doc, gz=False):
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            json.dump(doc, fh)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)


# ---- daily charts: 40 sessions ending Fri 2026-10-16, no bar on the NSE holidays of Sep 14 and Oct 2

SKIP = {"2026-09-14", "2026-10-02"}


def weekdays_ending(last, n):
    out, d = [], D.fromisoformat(last)
    while len(out) < n:
        if d.weekday() < 5 and d.isoformat() not in SKIP:
            out.append(d)
        d -= datetime.timedelta(days=1)
    return out[::-1]


def daily_series(closes, volume, last="2026-10-16", extra=()):
    days = weekdays_ending(last, len(closes))
    t = [ts("%s 09:15" % d.isoformat()) for d in days]
    c = list(closes)
    for day, close in extra:
        t.append(ts(day + " 09:15"))
        c.append(close)
    return {"t": t, "c": c, "meta": {"regularMarketPrice": closes[-1], "regularMarketVolume": volume}}


def alt(n):
    return [100.5 if p % 2 == 0 else 99.5 for p in range(n)]


def pat_breakout():     # alternating 100.5 / 99.5, then a close above all of them
    return alt(39) + [103.0]


def pat_flag():         # flat, a 15 % run in ten sessions, then five closes inside a 1 % range
    return [100.0] * 25 + [102, 104, 106, 108, 110, 112, 113, 114, 115] + [115, 114.5, 115.5, 115.0, 114.8, 115.2]


def pat_reclaim():      # alternating, one close under the 20-day average, then back over it
    c = alt(40)
    c[38], c[39] = 99.0, 100.2
    return c


def pat_oversold():     # a straight decline: RSI 0
    return [130.0 - p for p in range(40)]


def pat_bounce():       # the same decline, closing up on the last day
    return [130.0 - p for p in range(39)] + [93.0]


def pat_quiet():        # nothing happens
    return alt(40)


def scaled(pat, k):
    return [round(x * k, 2) for x in pat]


# name -> (pattern, rupee scale, day volume): what each should be tagged
DAILY = {
    "RELIANCE": (pat_breakout(), 13, 5000000),
    "TCS": (pat_flag(), 30, 2000000),
    "INFY": (pat_reclaim(), 15, 3000000),
    "SBIN": (pat_oversold(), 6, 8000000),
    "ITC": (pat_bounce(), 4, 200000),            # fewer than 1M shares: illiquid at the default floor
    "HDFCBANK": (pat_quiet(), 17, 4000000),
    "PENNYCO": (pat_quiet(), 0.04, 9000000),     # under Rs 5
}
EXPECT = {"RELIANCE": ["breakout_20d"], "TCS": ["bull_flag"], "INFY": ["reclaim_ma20"],
          "SBIN": ["oversold"], "ITC": ["oversold", "oversold_bounce"]}


def daily_data():
    return {sym: daily_series(scaled(p, k), v) for sym, (p, k, v) in DAILY.items()}


# ---- 5-minute charts for the opening-range tests

def orb_series(day, b, kind, k=4, u=1.0, vol=1000, n=14):
    """Range bars (closes b, b+2, b+1 at 9:15 / 9:20 / 9:25) -> range high b+3.5u, low b-1.5u, top close b+2u.
    'break': from bar k on, closes b+5u (over the range high). 'near': one close of b+3u (a stage-1 candidate
    that never clears the high). 'quiet': stays at b+u."""
    c = [b, b + 2 * u, b + u] + [b + u] * (n - 3)
    if kind == "break":
        for i in range(k, n):
            c[i] = b + 5 * u
    elif kind == "near":
        c[5] = b + 3 * u
    return bars_series(day, c, highs=[x + 1.5 * u for x in c], lows=[x - 1.5 * u for x in c], vol=vol)


DAY = "2026-10-19"      # a Monday
ORB = {
    "RELIANCE": dict(b=1300.0, kind="break", k=4),
    "BAJAJ-AUTO": dict(b=9000.0, kind="break", k=4, u=3.0, vol=100),
    "PENNYCO": dict(b=4.0, kind="break", k=5, u=0.1, vol=100000),
    "INFY": dict(b=1500.0, kind="break", k=8),
    "HDFCBANK": dict(b=1700.0, kind="break", k=11),
    "SBIN": dict(b=800.0, kind="break", k=12),      # breaks on the 10:15 bar, which closes at 10:20
    "TCS": dict(b=3500.0, kind="near"),
    "M&M": dict(b=3000.0, kind="quiet"),
}


def orb_data():
    data = {sym: orb_series(DAY, **spec) for sym, spec in ORB.items()}
    gappy = [500.0, 502.0, 501.0, 501.0, 505.0, 505.0, None, 505.0, 505.0, 505.0, 505.0, 505.0, 505.0, 505.0]
    data["GAPPY"] = bars_series(DAY, gappy)
    data["RELIANCE"] = bars_series(DAY, [1300, 1302, 1301, 1301, 1305] + [1305] * 9,
                                   highs=[x + 1.5 for x in [1300, 1302, 1301, 1301, 1305] + [1305] * 9],
                                   lows=[x - 1.5 for x in [1300, 1302, 1301, 1301, 1305] + [1305] * 9],
                                   extra=[(DAY + " 10:18:07", 1306.0)])   # a live tick off the 5-minute grid
    return data


def orb_bundle():
    """A bundle of v8 answers keyed by Yahoo symbol, DEADCO being Yahoo's 'no data' answer."""
    b = {sym + ".NS": chart_doc(s, sym + ".NS") for sym, s in orb_data().items()}
    b["DEADCO.NS"] = DEAD
    return b


def stage_two_data():
    """Thirty charts that all break out, each a little more traded than the one before."""
    return {"S%02d" % i: orb_series(DAY, b=100.0 + i, kind="break", k=4, vol=(i + 1) * 1000) for i in range(30)}


class Universe(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(NSEDIR, "EQUITY_L.csv"), encoding="utf-8") as fh:
            self.text = fh.read()

    def test_only_the_eq_series_is_kept(self):
        kept, counts, created = scan.parse_nse_equitylist(self.text)
        self.assertEqual([s for s, _ in kept], ["BAJAJ-AUTO", "FOOBAR", "M&M", "RELIANCE", "TCS"])
        self.assertEqual(counts, {"rows": 8, "equity": 5, "other": 3})   # BE, SM and N1 series are dropped
        self.assertIsNone(created)
        names = dict(kept)
        self.assertEqual(names["RELIANCE"], "Reliance Industries Limited")
        self.assertEqual(names["M&M"], "Mahindra & Mahindra Limited")

    def test_a_quoted_name_with_a_comma(self):
        kept, _, _ = scan.parse_nse_equitylist(self.text)
        self.assertEqual(dict(kept)["FOOBAR"], "Foo, Bar & Co Limited")

    def test_build_universe(self):
        kept, counts, _ = scan.build_universe({"EQUITY_L.csv": self.text})
        self.assertEqual(len(kept), 5)
        self.assertEqual(counts["rows"], 8)
        self.assertEqual(scan.NSE_FILES, ("EQUITY_L.csv",))
        with self.assertRaises(scan.ScanError):
            scan.build_universe({})

    def test_a_byte_order_mark_and_crlf(self):
        text = "﻿" + self.text.replace("\n", "\r\n")
        kept, counts, _ = scan.parse_nse_equitylist(text)
        self.assertEqual(len(kept), 5)
        self.assertEqual(counts["rows"], 8)

    def test_not_an_equity_list(self):
        for bad in ("<html>Access Denied</html>", "", "SYMBOL,NAME\nRELIANCE,Reliance\n"):
            with self.assertRaises(scan.ScanError, msg=bad):
                scan.parse_nse_equitylist(bad)

    def test_no_us_universe_left(self):
        for name in ("NASDAQ_FILES", "NASDAQ_URL", "parse_symdir", "classify", "eastern_offset",
                     "eastern_to_utc", "NYSE_HOLIDAYS", "NYSE_EARLY_CLOSE", "EARLY"):
            self.assertFalse(hasattr(scan, name), name)


class Time(unittest.TestCase):
    def test_ist_is_a_fixed_offset(self):
        self.assertEqual(ts("2026-10-19 09:15"), 1792381500)                      # 03:45 UTC
        self.assertEqual(scan.ts_to_ist(1792381500), DT(2026, 10, 19, 9, 15))
        self.assertEqual(scan.ts_to_ist(ts("2026-12-01 09:15")), DT(2026, 12, 1, 9, 15))   # no DST shift
        self.assertEqual(scan.parse_now("2026-10-19 10:15"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(scan.parse_now("2026-12-01 10:15"), DT(2026, 12, 1, 4, 45))
        self.assertEqual(scan.parse_now("2026-10-19T04:45:00Z"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(scan.parse_now("2026-10-19T10:15:00+05:30"), DT(2026, 10, 19, 4, 45))
        self.assertEqual(scan.to_ist(DT(2026, 10, 19, 4, 45)), DT(2026, 10, 19, 10, 15))
        self.assertEqual(scan.ist_to_utc(DT(2026, 10, 19, 10, 15)), DT(2026, 10, 19, 4, 45))
        self.assertEqual(scan.clock_label(DT(2026, 10, 19, 10, 15)), "10:15 AM")
        self.assertEqual(scan.clock_label(datetime.time(12, 5)), "12:05 PM")
        self.assertEqual(scan.clock_label(datetime.time(15, 30)), "3:30 PM")
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_now("tomorrow")
        self.assertIn("IST", str(cm.exception))
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_hhmm("25:00")
        self.assertIn("IST", str(cm.exception))

    def test_the_session_is_9_15_to_15_30_with_75_bars(self):
        self.assertEqual((scan.OPEN, scan.CLOSE), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertEqual(scan.session(D(2026, 10, 19)), (scan.OPEN, scan.CLOSE))
        self.assertEqual(scan.slot(D(2026, 10, 19), 0), DT(2026, 10, 19, 9, 15))
        self.assertEqual(scan.slot(D(2026, 10, 19), 74), DT(2026, 10, 19, 15, 25))   # closes at 15:30
        self.assertEqual(scan.BAR_MIN, 5)

    def test_the_2026_holiday_list_is_the_official_one(self):
        self.assertEqual(sorted(scan.NSE_HOLIDAYS), HOLIDAYS_2026)
        self.assertEqual(scan.CALENDAR_YEARS, (2026,))
        for day in HOLIDAYS_2026:
            self.assertEqual(scan.session(D.fromisoformat(day)), None, day)
            ok, why = scan.trading_day(D.fromisoformat(day))
            self.assertFalse(ok)
        # the fixed-date ones are weekdays or the list would have no reason to name them
        self.assertEqual(scan.trading_day(D(2026, 11, 10)), (False, "NSE holiday: Diwali (Balipratipada)"))
        self.assertEqual(scan.trading_day(D(2026, 10, 20)), (False, "NSE holiday: Dussehra"))
        self.assertEqual(scan.trading_day(D(2026, 1, 15))[1],
                         "NSE holiday: Municipal Corporation Election - Maharashtra")
        self.assertEqual(scan.trading_day(D(2026, 10, 17)), (False, "weekend"))
        # Sunday Nov 8 is Diwali Laxmi Pujan: closed as a weekend (the Muhurat hour is not modelled)
        self.assertEqual(scan.trading_day(D(2026, 11, 8)), (False, "weekend"))

    def test_us_holidays_are_trading_days_and_there_is_no_early_close(self):
        for day in (D(2026, 11, 26), D(2026, 11, 27), D(2026, 12, 24), D(2026, 7, 3), D(2026, 2, 16)):
            self.assertEqual(scan.trading_day(day), (True, ""), day)
            self.assertEqual(scan.session(day), (datetime.time(9, 15), datetime.time(15, 30)), day)


class Spark(unittest.TestCase):
    def test_a_spark_answer_with_nse_symbols(self):
        ser = lambda: daily_series(scaled(pat_quiet(), 10), 1000000)
        doc = spark_doc({"RELIANCE.NS": ser(), "BAJAJ-AUTO.NS": ser(), "M&M.NS": ser(), "SBIN.NS": ser(),
                         "NOSUCH.NS": None})
        wanted = {scan.to_yahoo(s): s for s in ["RELIANCE", "BAJAJ-AUTO", "M&M", "SBIN", "NOSUCH"]}
        data = scan.parse_spark(json.dumps(doc), wanted)
        self.assertEqual(sorted(data), ["BAJAJ-AUTO", "M&M", "RELIANCE", "SBIN"])   # the empty response is dropped
        self.assertIsNone(data["RELIANCE"]["h"])                                   # spark is closes only
        self.assertEqual(len(data["RELIANCE"]["t"]), 40)
        self.assertEqual(data["M&M"]["meta"]["regularMarketVolume"], 1000000)
        # without the wanted map the .NS suffix is dropped and the hyphen is kept
        self.assertEqual(sorted(scan.parse_spark(doc)), ["BAJAJ-AUTO", "M&M", "RELIANCE", "SBIN"])

    def test_daily_bars_follow_the_ist_calendar(self):
        s = daily_series(scaled(pat_quiet(), 10), 1000000, extra=[("2026-10-19", 999.0)])
        # Tuesday Oct 20 is Dussehra: nothing is in progress, so Monday's bar counts
        closes = scan.daily_closes(s, scan.parse_now("2026-10-20 09:14"))
        self.assertEqual(closes[-1], (D(2026, 10, 19), 999.0))
        # the same answer read during Monday's session: Monday's bar is still in progress
        closes = scan.daily_closes(s, scan.parse_now("2026-10-19 12:00"))
        self.assertEqual(closes[-1][0], D(2026, 10, 16))
        self.assertEqual(len(closes), 40)

    def test_spark_errors(self):
        too_many = '{"spark":{"result":null,"error":{"code":"Bad Request",' \
                   '"description":"Number of symbols needs to be less than or equal to 20"}}}'
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_spark(too_many)
        self.assertIn("less than or equal to 20", str(cm.exception))
        self.assertEqual(scan.parse_spark('{"spark":{"result":[],"error":null}}'), {})
        with self.assertRaises(scan.ScanError):
            scan.parse_spark('{"chart":{}}')

    def test_urls_carry_the_ns_suffix_and_survive_special_characters(self):
        self.assertEqual(scan.to_yahoo("RELIANCE"), "RELIANCE.NS")
        self.assertEqual(scan.to_yahoo("reliance"), "RELIANCE.NS")
        self.assertEqual(scan.to_yahoo("RELIANCE.NS"), "RELIANCE.NS")          # not doubled
        self.assertEqual(scan.to_yahoo("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")         # the hyphen is part of the symbol
        self.assertEqual(scan.to_yahoo("M&M"), "M&M.NS")
        self.assertEqual(scan.spark_path(["BAJAJ-AUTO", "m&m", "reliance"], "3mo", "1d"),
                         "/v7/finance/spark?symbols=BAJAJ-AUTO.NS,M%26M.NS,RELIANCE.NS&range=3mo&interval=1d")
        self.assertEqual(scan.chart_path("M&M", "1d", "5m"), "/v8/finance/chart/M%26M.NS?range=1d&interval=5m")
        self.assertEqual(scan.chart_path("RELIANCE", "5d", "5m"), "/v8/finance/chart/RELIANCE.NS?range=5d&interval=5m")
        # an unescaped & would cut the symbols list in two
        query = urllib.parse.urlsplit(scan.spark_path(["M&M", "TCS"], "3mo", "1d")).query
        self.assertEqual(urllib.parse.parse_qs(query)["symbols"], ["M&M.NS,TCS.NS"])


class Network(unittest.TestCase):
    """429s and 5xx without the network."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = scan.http_get, scan.SLEEP
        scan.SLEEP = self.sleeps.append
        self.names = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN", "ITC", "BAJAJ-AUTO", "M&M", "LT", "WIPRO",
                      "AXISBANK", "KOTAKBANK", "MARUTI", "TITAN", "NTPC", "ONGC", "COALINDIA", "SUNPHARMA",
                      "HCLTECH", "NOSUCH"]
        ser = daily_series(scaled(pat_quiet(), 10), 1000000)
        self.body = json.dumps(spark_doc({n + ".NS": (ser if n != "NOSUCH" else None) for n in self.names}))

    def tearDown(self):
        scan.http_get, scan.SLEEP = self._get, self._sleep

    def answers(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url.split("/v")[0])
            code = codes.pop(0) if codes else 200
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return self.body
        scan.http_get = fake

    def test_429_falls_back_then_waits_once(self):
        self.answers([429, 429, 200])
        text = scan.yahoo_get("/v7/finance/spark?symbols=RELIANCE.NS&range=3mo&interval=1d")
        self.assertEqual(text, self.body)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com",
                                      "https://query2.finance.yahoo.com"])
        self.assertEqual(self.sleeps, [scan.BACKOFF])

    def test_5xx_goes_to_query1_without_waiting(self):
        self.answers([503, 200])
        scan.yahoo_get("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.sleeps, [])

    def test_429_for_good_is_reported_as_partial(self):
        self.answers([200] + [429] * 10)
        syms = self.names + ["X%02d" % i for i in range(20)]
        data, failed = scan.fetch_spark(syms, "3mo", "1d", 0.3)
        self.assertEqual(len(data), 19)
        self.assertIn("M&M", data)
        self.assertIn("BAJAJ-AUTO", data)
        self.assertEqual(failed["NOSUCH"], "no data from Yahoo")
        self.assertEqual(len(failed), 21)
        self.assertIn("HTTP 429", failed["X00"])
        self.assertEqual(self.sleeps, [0.3, scan.BACKOFF])  # a pause between batches, one backoff
        hits, counts, _ = scan.scan_daily(data, syms, scan.parse_now("2026-10-19 09:00"), 5, 1000000)
        self.assertEqual(counts["requested"], 40)
        self.assertEqual(counts["fetched"], 19)

    def test_a_404_is_final(self):
        self.answers([404])
        with self.assertRaises(scan.ScanError):
            scan.yahoo_get("/v8/finance/chart/NOPE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 1)


class Daily(unittest.TestCase):
    def setUp(self):
        self.data = daily_data()
        self.syms = sorted(self.data)

    def test_setups_before_the_open(self):
        hits, counts, last = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 09:00"), 0, 0)
        self.assertEqual({h["symbol"]: h["setups"] for h in hits}, EXPECT)
        self.assertEqual(last, D(2026, 10, 16))
        self.assertEqual(counts, {"requested": 7, "fetched": 7, "illiquid": 0, "short": 0, "scanned": 7, "hits": 5})
        rel = [h for h in hits if h["symbol"] == "RELIANCE"][0]
        self.assertEqual((rel["price"], rel["close_date"], rel["volume"]), (1339.0, "2026-10-16", 5000000))
        self.assertGreater(rel["rsi"], 50)
        sbin = [h for h in hits if h["symbol"] == "SBIN"][0]
        self.assertEqual(sbin["rsi"], 0.0)
        # most traded first: price x volume
        self.assertEqual([h["symbol"] for h in hits],
                         sorted([h["symbol"] for h in hits],
                                key=lambda s: -next(x["price"] * x["volume"] for x in hits if x["symbol"] == s)))

    def test_the_in_progress_bar_is_dropped_until_the_nse_close(self):
        data = {"X": daily_series(scaled(pat_breakout(), 13), 5000000, extra=[("2026-10-19", 1400.0)])}
        before = scan.scan_daily(data, ["X"], scan.parse_now("2026-10-19 09:00"), 0, 0)
        during = scan.scan_daily(data, ["X"], scan.parse_now("2026-10-19 10:50"), 0, 0)
        self.assertEqual(before, during)
        bars = scan.daily_closes(data["X"], scan.parse_now("2026-10-19 15:29"))
        self.assertEqual(bars[-1], (D(2026, 10, 16), 1339.0))
        bars = scan.daily_closes(data["X"], scan.parse_now("2026-10-19 15:30"))
        self.assertEqual(bars[-1], (D(2026, 10, 19), 1400.0))

    def test_no_half_day_a_bar_completes_at_the_normal_close(self):
        # Fri Nov 27 2026 would be a 1:00 PM early close in New York; the NSE runs it to 15:30
        s = {"t": [ts("2026-11-25 09:15"), ts("2026-11-27 09:15")], "c": [10.0, 11.0], "meta": {}}
        self.assertEqual(len(scan.daily_closes(s, scan.parse_now("2026-11-27 13:00"))), 1)
        self.assertEqual(len(scan.daily_closes(s, scan.parse_now("2026-11-27 15:29"))), 1)
        self.assertEqual(len(scan.daily_closes(s, scan.parse_now("2026-11-27 15:30"))), 2)

    def test_liquidity_filter(self):
        hits, counts, _ = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-19 09:00"), 5, 1000000)
        self.assertEqual(sorted(h["symbol"] for h in hits), ["INFY", "RELIANCE", "SBIN", "TCS"])
        self.assertEqual(counts["illiquid"], 2)     # PENNYCO under Rs 5; ITC under 1M shares
        self.assertEqual(counts["scanned"], 5)

    def test_short_and_empty_charts(self):
        s = {"t": [ts("2026-10-%02d 09:15" % d) for d in range(1, 11)], "c": [10.0] * 10,
             "meta": {"regularMarketPrice": 10, "regularMarketVolume": 5000000}}
        hits, counts, last = scan.scan_daily({"X": s, "E": {"t": [], "c": [], "meta": {}}}, ["X", "E", "GONE"],
                                             scan.parse_now("2026-10-20 09:00"), 5, 1000000)
        self.assertEqual(hits, [])
        self.assertEqual((counts["fetched"], counts["short"], counts["illiquid"], counts["scanned"]), (2, 1, 1, 0))
        self.assertIsNone(last)

    def test_rsi_matches_wilders_math(self):
        self.assertIsNone(scan.rsi([1.0] * 10))
        self.assertEqual(scan.rsi([float(x) for x in range(1, 40)]), 100.0)
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
                  46.28, 46.28]
        self.assertAlmostEqual(scan.rsi(closes), 70.46, places=1)  # Wilder's textbook example


class Orb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.path = os.path.join(cls.d, "bundle.json.gz")
        write(cls.path, orb_bundle(), gz=True)
        cls.data, cls.errors = scan.load_offline(cls.path)
        cls.syms = sorted(set(cls.data) | set(cls.errors))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d)

    def scan_at(self, now, min_price=0, syms=None, or_minutes=15, min_volume=0):
        day, cutoff = scan.orb_window(scan.parse_now(now), None, or_minutes, datetime.time(15, 30))
        return scan.scan_orb(self.data, syms or self.syms, day, cutoff, or_minutes, min_price, min_volume)

    def test_the_bundle_loads_with_nse_symbols(self):
        self.assertEqual(sorted(self.data), ["BAJAJ-AUTO", "GAPPY", "HDFCBANK", "INFY", "M&M", "PENNYCO",
                                             "RELIANCE", "SBIN", "TCS"])
        self.assertEqual(self.errors, {"DEADCO": "no data for DEADCO: No data found, symbol may be delisted"})

    def test_the_scan_at_1015(self):
        hits, counts, failed = self.scan_at("2026-10-19 10:15")
        self.assertEqual(len(self.syms), 10)
        self.assertEqual((counts["fetched"], counts["unusable"], counts["scanned"]), (9, 1, 8))   # GAPPY is missing a bar
        self.assertEqual((counts["candidates"], counts["confirmed"], counts["hits"]), (6, 6, 5))
        self.assertEqual([(h["symbol"], h["at"]) for h in hits],
                         [("BAJAJ-AUTO", "9:40 AM"), ("RELIANCE", "9:40 AM"), ("PENNYCO", "9:45 AM"),
                          ("INFY", "10:00 AM"), ("HDFCBANK", "10:15 AM")])   # by time, then symbol
        rel = [h for h in hits if h["symbol"] == "RELIANCE"][0]
        self.assertEqual(rel, {"symbol": "RELIANCE", "setups": ["orb"], "price": 1305.0, "at": "9:40 AM",
                               "bar_start": "09:35", "bar_index": 4, "or_high": 1303.5, "or_low": 1298.5,
                               "pct_over": 0.12})
        bajaj = [h for h in hits if h["symbol"] == "BAJAJ-AUTO"][0]
        self.assertEqual((bajaj["price"], bajaj["or_high"], bajaj["or_low"], bajaj["pct_over"]),
                         (9015.0, 9010.5, 8995.5, 0.05))
        self.assertNotIn("SBIN", [h["symbol"] for h in hits])    # its breakout bar closes at 10:20
        self.assertNotIn("TCS", [h["symbol"] for h in hits])     # a candidate that never clears the range high
        hits, counts, _ = self.scan_at("2026-10-19 10:20")
        self.assertIn(("SBIN", "10:20 AM"), [(h["symbol"], h["at"]) for h in hits])

    def test_price_and_volume_floors(self):
        hits, counts, _ = self.scan_at("2026-10-19 10:15", min_price=5)
        self.assertEqual(len(hits), 4)                    # PENNYCO at Rs 4.50 drops out
        self.assertEqual(counts["illiquid"], 1)
        self.assertNotIn("PENNYCO", [h["symbol"] for h in hits])
        hits, counts, _ = self.scan_at("2026-10-19 10:15", min_volume=1000000)
        self.assertEqual([h["symbol"] for h in hits], ["PENNYCO"])
        self.assertEqual((counts["illiquid"], counts["scanned"]), (7, 1))

    def test_in_progress_bar_and_no_breakout_yet(self):
        hits, _, _ = self.scan_at("2026-10-19 10:14", syms=["HDFCBANK"])
        self.assertEqual(hits, [])                         # the 10:10 bar is not done until 10:15
        hits, _, _ = self.scan_at("2026-10-19 10:15", syms=["HDFCBANK"])
        self.assertEqual(hits[0]["at"], "10:15 AM")

    def test_the_live_tick_is_dropped(self):
        s = self.data["RELIANCE"]
        self.assertEqual(scan.ts_to_ist(s["t"][-1]), DT(2026, 10, 19, 10, 18, 7))   # off the grid
        cutoff = DT(2026, 10, 19, 10, 19)
        bars = scan.session_bars(s, D(2026, 10, 19), cutoff)
        self.assertEqual(len(bars), 12)                    # 9:15 to 10:10; the 10:15 bar is in progress, the tick dropped
        self.assertEqual(bars[-1]["start"], DT(2026, 10, 19, 10, 10))
        self.assertTrue(scan.usable(bars, D(2026, 10, 19), cutoff, 3))
        hits, _, _ = self.scan_at("2026-10-19 10:19", syms=["RELIANCE"])
        self.assertEqual(hits[0]["price"], 1305.0)         # still the first close above the range

    def test_the_session_is_75_bars_and_nothing_outside_it_counts(self):
        day = D(2026, 10, 19)
        s = bars_series(DAY, [100.0] * 76, extra=[(DAY + " 09:10", 50.0)])    # 9:15 ... 15:30, plus a pre-open bar
        cutoff = DT(2026, 10, 19, 15, 30)
        bars = scan.session_bars(s, day, cutoff)
        self.assertEqual(len(bars), 75)                    # the 15:30 bar and the 09:10 bar are outside the session
        self.assertEqual((bars[0]["start"], bars[-1]["start"]), (DT(2026, 10, 19, 9, 15), DT(2026, 10, 19, 15, 25)))
        self.assertTrue(scan.usable(bars, day, cutoff, 3))
        # a chart that starts at 9:30 (the NYSE open) is missing its first three slots
        late = bars_series(DAY, [100.0] * 20, start="09:30")
        bars = scan.session_bars(late, day, DT(2026, 10, 19, 11, 0))
        self.assertFalse(scan.usable(bars, day, DT(2026, 10, 19, 11, 0), 3))

    def test_stage_one_is_a_necessary_condition(self):
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 10, 15)
        for sym, s in self.data.items():
            bars = scan.session_bars(s, day, cut)
            if scan.usable(bars, day, cut, 3) and scan.orb_hit(bars, 3):
                self.assertTrue(scan.orb_candidate(bars, 3), sym)

    def test_confirm_uses_the_v8_highs(self):
        spark_like = {k: dict(v, h=None, l=None) for k, v in self.data.items()}  # closes only, like spark
        asked = []

        def confirm(sym):
            asked.append(sym)
            return self.data[sym]
        hits, counts, failed = scan.scan_orb(spark_like, self.syms, D(2026, 10, 19), DT(2026, 10, 19, 10, 15), 15,
                                             0, 0, confirm)
        self.assertEqual(len(hits), 5)
        self.assertEqual(counts["candidates"], len(asked))
        self.assertEqual(sorted(asked), ["BAJAJ-AUTO", "HDFCBANK", "INFY", "PENNYCO", "RELIANCE", "TCS"])
        self.assertLess(len(asked), counts["scanned"])    # stage 1 skipped the quiet charts

        def broken(sym):
            raise scan.ScanError("Yahoo answered HTTP 429 for /v8/finance/chart/%s.NS" % sym)
        hits, counts, failed = scan.scan_orb(spark_like, ["RELIANCE", "M&M"], D(2026, 10, 19),
                                             DT(2026, 10, 19, 10, 15), 15, 0, 0, broken)
        self.assertEqual(hits, [])
        self.assertIn("429", failed["RELIANCE"])
        gappy = lambda sym: self.data["GAPPY"]
        hits, counts, failed = scan.scan_orb(spark_like, ["RELIANCE"], D(2026, 10, 19), DT(2026, 10, 19, 10, 15), 15,
                                             0, 0, gappy)
        self.assertEqual(failed, {"RELIANCE": "the v8 chart has a missing bar"})

    def test_a_missing_bar_makes_a_chart_unusable(self):
        s = bars_series("2026-10-21", [10, 10.2, 10.1, None, 10.5, 10.6, 10.7, 10.8, 10.9])
        bars = scan.session_bars(s, D(2026, 10, 21), DT(2026, 10, 21, 10, 0))
        self.assertFalse(scan.usable(bars, D(2026, 10, 21), DT(2026, 10, 21, 10, 0), 3))

    def test_a_30_minute_range_and_the_late_session(self):
        # Mon Oct 26: range 9:15 to 9:45 (six bars, high 101.4); the 9:45 bar closes at 101.5
        closes = [100, 101, 100.5, 100.8, 101.2, 100.9, 101.5] + [101.0] * 40
        s = bars_series("2026-10-26", closes, highs=[c + 0.2 for c in closes])
        hits, counts, _ = scan.scan_orb({"X": s}, ["X"], D(2026, 10, 26), DT(2026, 10, 26, 12, 0), 30, 0, 0)
        self.assertEqual(counts["scanned"], 1)
        self.assertEqual(hits[0]["at"], "9:50 AM")
        self.assertEqual(hits[0]["or_high"], 101.4)
        self.assertEqual(hits[0]["bar_index"], 6)
        self.assertEqual(scan.ts_to_ist(s["t"][0]), DT(2026, 10, 26, 9, 15))
        bars = scan.session_bars(s, D(2026, 10, 26), DT(2026, 10, 26, 12, 59))
        self.assertEqual(len(bars), 44)                    # 9:15 to 12:50 are done at 12:59; 12:55 closes at 1:00
        self.assertEqual(bars[-1]["start"], DT(2026, 10, 26, 12, 50))

    def test_window_messages(self):
        until = datetime.time(15, 30)

        def msg(now, at=None, until=until):
            with self.assertRaises(scan.ScanError) as cm:
                scan.orb_window(scan.parse_now(now), at, 15, until)
            return str(cm.exception)
        self.assertIn("weekend", msg("2026-10-17 10:15"))
        m = msg("2026-11-10 10:15")
        self.assertIn("NSE holiday: Diwali (Balipratipada)", m)
        self.assertIn("the NSE is closed", m)
        self.assertIn("NSE holiday: Dussehra", msg("2026-10-20 10:15"))
        m = msg("2026-10-19 09:30")
        self.assertIn("Run orb again after 9:35 AM", m)
        self.assertIn("opening range runs 9:15 to 9:30", m)
        self.assertIn("IST", m)
        self.assertIn("Run orb again after 9:35 AM", msg("2026-10-19 09:34"))
        self.assertIn("Run orb again after 9:35 AM", msg("2026-10-19 09:00"))
        self.assertIn("expired", msg("2026-10-19 15:10", until=datetime.time(15, 0)))
        self.assertIn("expired", msg("2026-10-19 11:30", until=datetime.time(11, 0)))
        self.assertIn("closed at 3:30 PM IST", msg("2026-10-19 15:30"))
        self.assertIn("closed at 3:30 PM IST", msg("2026-10-19 16:30"))
        self.assertIn("later than now", msg("2026-10-19 10:00", at=datetime.time(10, 15)))
        self.assertIn("after the 3:30 PM close", msg("2026-10-19 17:00", at=datetime.time(15, 45)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-19 16:30"), datetime.time(10, 15), 15, until),
                         (D(2026, 10, 19), DT(2026, 10, 19, 10, 15)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-19 09:35"), None, 15, until)[1],
                         DT(2026, 10, 19, 9, 35))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-19 15:29"), None, 15, until)[1],
                         DT(2026, 10, 19, 15, 29))                  # up to the close: no early shut
        with self.assertRaises(scan.ScanError):
            scan.orb_window(scan.parse_now("2026-10-19 10:15"), None, 7, until)

    def test_thanksgiving_is_just_another_nse_day(self):
        day, cutoff = scan.orb_window(scan.parse_now("2026-11-26 10:15"), None, 15, datetime.time(15, 30))
        self.assertEqual((day, cutoff), (D(2026, 11, 26), DT(2026, 11, 26, 10, 15)))
        day, cutoff = scan.orb_window(scan.parse_now("2026-11-27 13:10"), None, 15, datetime.time(15, 30))
        self.assertEqual(cutoff, DT(2026, 11, 27, 13, 10))


class LiveFiveMinute(unittest.TestCase):
    """A spark 5-minute answer and a v8 chart read at 10:01:47 AM IST on Wed Oct 21 2026 (Tuesday was Dussehra)."""

    DAY21 = "2026-10-21"

    def setUp(self):
        closes = [1300, 1302, 1301, 1301, 1301, 1301, 1305, 1306, 1306]       # 9:15 ... 9:55, all complete
        spark_closes = closes + [1306.5]                                       # the 10:00 bar, in progress
        t = bars_series(self.DAY21, spark_closes, extra=[(self.DAY21 + " 10:01:47", 1307.0)])   # + the live tick
        t["meta"] = {"regularMarketPrice": 1307.0, "regularMarketVolume": 900000}
        pen = bars_series(self.DAY21, [4.0] * 10, extra=[(self.DAY21 + " 10:01:47", 4.0)])
        pen["meta"] = {"regularMarketPrice": 4.0, "regularMarketVolume": 100000}
        self.spark = scan.parse_spark(spark_doc({"RELIANCE.NS": t, "PENNYCO.NS": pen}))
        v8 = bars_series(self.DAY21, closes, highs=[c + 1.5 for c in closes], lows=[c - 1.5 for c in closes])
        self.v8 = scan.parse_chart(json.dumps(chart_doc(v8, "RELIANCE.NS")), "RELIANCE")
        self.now = scan.parse_now("2026-10-21T04:31:48Z")                     # 10:01:48 IST

    def test_in_progress_bar_and_live_tick_are_dropped(self):
        t = self.spark["RELIANCE"]
        self.assertEqual(len(t["t"]), 11)                  # 9:15 ... 10:00 (in progress) + the 10:01:47 tick
        self.assertEqual(scan.ts_to_ist(t["t"][-1]), DT(2026, 10, 21, 10, 1, 47))
        day, cut = scan.orb_window(self.now, None, 15, datetime.time(15, 30))
        self.assertEqual((day, cut), (D(2026, 10, 21), DT(2026, 10, 21, 10, 1)))
        bars = scan.session_bars(t, day, cut)
        self.assertEqual([b["start"].strftime("%H:%M") for b in bars],
                         ["09:15", "09:20", "09:25", "09:30", "09:35", "09:40", "09:45", "09:50", "09:55"])
        self.assertEqual(len(scan.session_bars(self.v8, day, cut)), 9)

    def test_reliance_broke_its_range_at_9_50(self):
        day, cut = scan.orb_window(self.now, None, 15, datetime.time(15, 30))
        hits, counts, failed = scan.scan_orb(self.spark, ["RELIANCE", "PENNYCO"], day, cut, 15, 5, 0,
                                             lambda s: self.v8)
        # range 9:15 to 9:30: high 1303.5 (the 9:20 bar), low 1298.5; the 9:45 bar closed 1305
        self.assertEqual(hits, [{"symbol": "RELIANCE", "setups": ["orb"], "price": 1305.0, "at": "9:50 AM",
                                 "bar_start": "09:45", "bar_index": 6, "or_high": 1303.5, "or_low": 1298.5,
                                 "pct_over": 0.12}])
        self.assertEqual(counts["illiquid"], 1)            # PENNYCO under the Rs 5 floor

    def test_stage_two_cap_takes_the_most_traded(self):
        data = stage_two_data()
        spark_like = {k: dict(v, h=None, l=None) for k, v in data.items()}
        asked = []

        def confirm(sym):
            asked.append(sym)
            return data[sym]
        day, cut = D(2026, 10, 19), DT(2026, 10, 19, 10, 15)
        hits, counts, _ = scan.scan_orb(spark_like, sorted(data), day, cut, 15, 0, 0, confirm, max_confirm=10)
        self.assertEqual((counts["candidates"], counts["confirmed"], counts["unconfirmed"]), (30, 10, 20))
        self.assertEqual(sorted(asked), ["S%02d" % i for i in range(20, 30)])   # the most rupees traded
        self.assertEqual(len(hits), 10)
        hits_all, _, _ = scan.scan_orb(spark_like, sorted(data), day, cut, 15, 0, 0, confirm, max_confirm=0)
        self.assertEqual(len(hits_all), 30)
        for h in hits:
            self.assertIn(h, hits_all)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.t = os.path.join(self.d, "trader")
        self.wl = os.path.join(self.t, "watchlist.json")
        self.cfg = os.path.join(self.d, "no-config.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def spark_file(self, names):
        path = os.path.join(self.d, "daily-spark.json")
        data = daily_data()
        write(path, spark_doc({n + ".NS": data[n] for n in names}))
        return path

    def bundle(self):
        path = os.path.join(self.d, "wall5m.json.gz")
        write(path, orb_bundle(), gz=True)
        return path

    def test_universe_daily_orb_end_to_end(self):
        uni = os.path.join(self.t, "universe.txt")
        code, text, _ = run(["universe", "--offline", NSEDIR, "--out", uni])
        self.assertEqual(code, 0)
        self.assertIn("NSE equity list (EQUITY_L.csv): 8 rows.", text)
        self.assertIn("Kept 5 NSE-listed equity (EQ series) stocks.", text)
        with open(uni, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(lines, ["BAJAJ-AUTO|Bajaj Auto Limited", "FOOBAR|Foo, Bar & Co Limited",
                                 "M&M|Mahindra & Mahindra Limited", "RELIANCE|Reliance Industries Limited",
                                 "TCS|Tata Consultancy Services Limited"])
        code, text, _ = run(["universe", "--offline", NSEDIR, "--no-write", "--json"])
        self.assertEqual(json.loads(text)["count"], 5)
        self.assertEqual((json.loads(text)["rows"], json.loads(text)["other"]), (8, 3))

        # daily over that universe: only RELIANCE and TCS have charts, so it is partial
        code, text, _ = run(["daily", "--universe", uni, "--offline", self.spark_file(["RELIANCE", "TCS"]),
                             "--now", "2026-10-19 09:00", "--out", self.wl, "--min-volume", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Fetched 2 of 5 charts (3 failed or had no data)", text)
        self.assertIn("₹1,339.00", text)
        self.assertIn("closes through 2026-10-16", text)
        self.assertIn("IST", text)
        doc = self.read(self.wl)
        self.assertEqual((doc["mode"], doc["universe"], doc["scanned"], doc["failed"]), ("daily", 5, 2, 3))
        self.assertEqual({h["symbol"]: (h["setups"], h["price"], h["volume"]) for h in doc["hits"]},
                         {"RELIANCE": (["breakout_20d"], 1339.0, 5000000), "TCS": (["bull_flag"], 3456.0, 2000000)})
        self.assertEqual(doc["as_of"], "2026-10-16 close")

        # the 10:15 scan
        code, text, _ = run(["orb", "--offline", self.bundle(), "--now", "2026-10-19 10:15", "--min-price", "0",
                             "--out", self.wl, "--config", self.cfg])
        self.assertEqual(code, 0)
        self.assertIn("5 of 8 charts closed above their opening-range high by 10:15 AM", text)
        self.assertIn("9:15 to 9:30", text)
        self.assertIn("Mon 2026-10-19 10:15 AM IST", text)
        self.assertIn("RELIANCE %10s  range 1,298.50 to 1,303.50  +0.12%% over" % "₹1,305.00", text)
        self.assertIn("not a list of stocks to buy", text)
        doc = self.read(self.wl)
        self.assertEqual({k: doc[k] for k in ("created_by", "created_at", "mode", "as_of", "universe", "scanned",
                                              "failed", "source")},
                         {"created_by": "trader-scanner", "created_at": "2026-10-19T04:45:00Z", "mode": "orb",
                          "as_of": "2026-10-19 10:15 IST", "universe": 10, "scanned": 8, "failed": 1,
                          "source": "yahoo"})
        self.assertEqual([h["symbol"] for h in doc["hits"]], ["BAJAJ-AUTO", "RELIANCE", "PENNYCO", "INFY", "HDFCBANK"])

        # the hits read back as a watchlist, with --at instead of --now
        code, text, _ = run(["orb", "--offline", self.bundle(), "--at", "10:15", "--watchlist", self.wl,
                             "--min-price", "0", "--json", "--no-write", "--config", self.cfg])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], len(out["hits"])), (5, 5, 5))

    def test_the_until_default_is_the_nse_close(self):
        # no --until and no config: entries are allowed right up to 15:30, so 15:20 is fine and the scan is not "expired"
        code, text, err = run(["orb", "--offline", self.bundle(), "--now", "2026-10-19 15:20", "--no-write",
                               "--min-price", "0", "--config", self.cfg])
        self.assertNotIn("expired", err)
        self.assertIn("3:20 PM", text)
        code, _, err = run(["orb", "--offline", self.bundle(), "--now", "2026-10-19 15:20", "--until", "15:00",
                            "--config", self.cfg])
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        self.assertIn("3:20 PM IST", err)
        code, _, err = run(["orb", "--offline", self.bundle(), "--now", "2026-10-19 15:30", "--config", self.cfg])
        self.assertEqual(code, 1)
        self.assertIn("closed at 3:30 PM IST", err)

    def test_partial_stage_two_is_said(self):
        """The networked path with a fake Yahoo answering from a bundle of 32 charts."""
        data = stage_two_data()
        data["BAJAJ-AUTO"] = orb_series(DAY, b=9000.0, kind="break", k=4, u=3.0, vol=100)
        data["M&M"] = orb_series(DAY, b=3000.0, kind="quiet")
        bundle = {s + ".NS": chart_doc(v, s + ".NS") for s, v in data.items()}
        calls = []

        def get(path):
            calls.append(path)
            if path.startswith("/v7/finance/spark"):
                res = []
                for y in urllib.parse.unquote(path.split("symbols=")[1].split("&")[0]).split(","):
                    r = bundle[y]["chart"]["result"][0]
                    res.append({"symbol": y, "response": [dict(r, indicators={"quote": [
                        {"close": r["indicators"]["quote"][0]["close"]}]})]})
                return json.dumps({"spark": {"result": res, "error": None}})
            return json.dumps(bundle[urllib.parse.unquote(path.split("/chart/")[1].split("?")[0])])
        old_get, old_sleep, real_now = scan.yahoo_get, scan.SLEEP, scan.parse_now
        scan.yahoo_get, scan.SLEEP = get, (lambda s: None)
        scan.parse_now = lambda text: real_now(text or "2026-10-19 10:20")  # the machine clock, pinned
        uni = os.path.join(self.d, "u.txt")
        with open(uni, "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(data)) + "\n")
        try:
            code, text, _ = run(["orb", "--universe", uni, "--now", "2026-10-19 10:15", "--min-price", "0",
                                 "--max-confirm", "10", "--no-write", "--config", self.cfg])
        finally:
            scan.yahoo_get, scan.SLEEP, scan.parse_now = old_get, old_sleep, real_now
        self.assertEqual(code, 0)
        self.assertIn("32 usable; 31 candidates, 10 confirmed", text)
        self.assertIn("PARTIAL: 21 more candidates were not checked", text)
        self.assertIn("10 of the 10 checked charts closed above their opening-range high by 10:15 AM", text)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v8/")), 10)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v7/")), 2)   # 32 symbols, 20 a request
        self.assertTrue(all("range=1d&interval=5m" in c for c in calls))
        self.assertTrue(all(".NS" in c for c in calls))
        self.assertTrue(any("M%26M.NS" in c for c in calls if c.startswith("/v7/")))

    def test_plain_messages_and_exit_codes(self):
        wall = self.bundle()
        code, _, err = run(["orb", "--offline", wall, "--now", "2026-10-17 10:15", "--out", self.wl,
                            "--config", self.cfg])
        self.assertEqual(code, 1)
        self.assertIn("No session on Sat 2026-10-17 (weekend)", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.wl))
        code, _, err = run(["orb", "--offline", wall, "--now", "2026-11-10 10:15", "--config", self.cfg])
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday: Diwali (Balipratipada)", err)
        code, _, err = run(["orb", "--offline", wall, "--now", "2026-10-19 09:34", "--out", self.wl,
                            "--config", self.cfg])
        self.assertIn("after 9:35 AM", err)
        cfg = os.path.join(self.t, "config.json")
        os.makedirs(self.t)
        with open(cfg, "w") as fh:
            json.dump({"no_entries_after": "10:00"}, fh)
        code, _, err = run(["orb", "--offline", wall, "--now", "2026-10-19 10:15", "--config", cfg])
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        code, _, err = run(["orb", "--offline", wall, "--config", self.cfg])
        self.assertIn("--at HH:MM", err)
        code, text, _ = run(["orb", "--offline", wall, "--now", "2026-10-19 10:10", "--symbols", "SBIN",
                             "--out", self.wl, "--config", self.cfg])
        self.assertEqual(code, 1)                          # no breakout yet, but the empty list is written
        self.assertIn("No chart closed above its opening-range high by 10:10 AM", text)
        self.assertEqual(self.read(self.wl)["hits"], [])
        code, _, err = run(["daily", "--universe", os.path.join(self.d, "nope.txt")])
        self.assertEqual(code, 1)
        self.assertIn("scan.py universe", err)
        code, _, err = run(["daily", "--offline", os.path.join(self.d, "nothing-here")])
        self.assertIn("no offline file", err)
        empty = os.path.join(self.d, "empty.json")
        with open(empty, "w") as fh:
            fh.write("{}")
        code, _, err = run(["daily", "--offline", empty])
        self.assertEqual(code, 1)
        self.assertIn("no symbols to scan", err)

    def test_universe_errors(self):
        bad = os.path.join(self.d, "bad")
        os.makedirs(bad)
        with open(os.path.join(bad, "EQUITY_L.csv"), "w") as fh:
            fh.write("<html>Access Denied</html>")
        code, _, err = run(["universe", "--offline", bad, "--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("EQUITY_L.csv", err)
        self.assertNotIn("Traceback", err)
        code, _, err = run(["universe", "--offline", os.path.join(self.d, "missing"), "--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("no EQUITY_L.csv in", err)

    def test_offline_folder_of_v8_files(self):
        folder = os.path.join(self.d, "charts")
        os.makedirs(folder)
        bundle = orb_bundle()
        for file, key in (("RELIANCE.json", "RELIANCE.NS"), ("BAJAJ-AUTO.json", "BAJAJ-AUTO.NS"),
                          ("M&M.json", "M&M.NS"), ("INFY.NS.json", "INFY.NS"), ("DEADCO.json", "DEADCO.NS")):
            write(os.path.join(folder, file), bundle[key])
        code, text, _ = run(["orb", "--offline", folder, "--now", "2026-10-19 10:15", "--no-write", "--json",
                             "--config", self.cfg])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], out["failed"]), (5, 4, 1))
        self.assertEqual([h["symbol"] for h in out["hits"]], ["BAJAJ-AUTO", "RELIANCE", "INFY"])
        self.assertEqual(code, 0)

    def test_symbol_lists(self):
        txt = os.path.join(self.d, "list.txt")
        with open(txt, "w", encoding="utf-8") as fh:
            fh.write("# my list\nRELIANCE|Reliance Industries Limited\ntcs\n\nRELIANCE\n")
        self.assertEqual(scan.symbols_from_file(txt), ["RELIANCE", "TCS", "RELIANCE"])
        js = os.path.join(self.d, "wl.json")
        with open(js, "w") as fh:
            json.dump({"hits": [{"symbol": "tcs"}, {"symbol": "M&M"}]}, fh)
        self.assertEqual(scan.symbols_from_file(js), ["TCS", "M&M"])
        spark = self.spark_file(sorted(DAILY))
        code, text, _ = run(["daily", "--offline", spark, "--watchlist", txt, "--now", "2026-10-19 09:00",
                             "--json", "--no-write", "--min-volume", "0"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"]), (2, 2))
        code, text, _ = run(["daily", "--offline", spark, "--limit", "3", "--now", "2026-10-19 09:00", "--json",
                             "--no-write", "--min-volume", "0"])
        self.assertEqual(json.loads(text)["universe"], 3)

    def test_daily_json(self):
        code, text, _ = run(["daily", "--offline", self.spark_file(sorted(DAILY)), "--now", "2026-10-19 09:00",
                             "--json", "--no-write", "--min-volume", "0", "--min-price", "0"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(sorted(h["symbol"] for h in out["hits"]), sorted(EXPECT))
        self.assertEqual(out["as_of"], "2026-10-16 close")
        self.assertEqual(out["note"], "A scan is a list of charts that match a rule, not a list of stocks to buy.")


if __name__ == "__main__":
    unittest.main()
