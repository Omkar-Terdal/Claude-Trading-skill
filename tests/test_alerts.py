import datetime
import gzip
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

alerts = load("trader-alerts", "alerts")

QDIR = os.path.join(FIX, "trader-alerts")          # synthetic fixtures, see the comment inside each file
PLAN = os.path.join(QDIR, "plan-reliance.json")
DT = datetime.datetime
UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"

# The official NSE equity trading holidays for 2026 (NSE circular, checked against the list the owner pasted)
NSE_2026 = ["2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03",
            "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02",
            "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25"]


def _no_network(req, timeout=None):
    raise AssertionError("a test tried to reach the network: %s" % getattr(req, "full_url", req))


alerts._urlopen = _no_network
alerts._sleep = lambda seconds: None


def ist(text):
    """A wall-clock time in IST (or ...Z) -> the naive UTC the tool works in."""
    return alerts.parse_now(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


def plan_rules():
    with open(PLAN) as fh:
        return alerts.rules_from_plan(json.load(fh))


def read_text(name):
    with open(os.path.join(QDIR, name), encoding="utf-8") as fh:
        return fh.read()


class Calendar(unittest.TestCase):
    def test_holidays_match_the_official_nse_list(self):
        self.assertEqual(sorted(alerts.NSE_HOLIDAYS), NSE_2026)
        self.assertEqual(alerts.CALENDAR_YEARS, (2026,))
        for d in alerts.NSE_HOLIDAYS:
            self.assertLess(datetime.date.fromisoformat(d).weekday(), 5, d)
        self.assertEqual(alerts.NSE_HOLIDAYS["2026-04-03"], "Good Friday")
        self.assertIn("Diwali", alerts.NSE_HOLIDAYS["2026-11-10"])
        self.assertEqual(alerts.NSE_HOLIDAYS["2026-12-25"], "Christmas")
        self.assertNotIn("2026-11-09", alerts.NSE_HOLIDAYS)      # the Monday before Balipratipada trades

    def test_no_us_calendar_is_left(self):
        for name in ("NYSE_HOLIDAYS", "NYSE_EARLY_CLOSE", "eastern_offset", "to_eastern", "eastern_to_utc"):
            self.assertFalse(hasattr(alerts, name), name)

    def test_the_session_is_0915_to_1530_with_no_early_close(self):
        self.assertEqual((alerts.OPEN, alerts.CLOSE), (datetime.time(9, 15), datetime.time(15, 30)))
        for day in ("2026-11-09", "2026-11-27", "2026-12-24", "2026-12-31"):   # 27 Nov and 24 Dec are US early closes
            self.assertEqual(alerts.session(datetime.date.fromisoformat(day)), (alerts.OPEN, alerts.CLOSE), day)
        self.assertIsNone(alerts.session(datetime.date(2026, 11, 10)))        # holiday
        self.assertIsNone(alerts.session(datetime.date(2026, 11, 7)))         # Saturday

    def test_ist_is_a_fixed_offset_with_no_daylight_saving(self):
        for stamp in (DT(2026, 3, 8, 6, 59), DT(2026, 3, 8, 7, 0), DT(2026, 7, 1, 12, 0),
                      DT(2026, 11, 1, 5, 59), DT(2026, 11, 1, 6, 0), DT(2026, 12, 1, 0, 0)):
            self.assertEqual(alerts.to_ist(stamp) - stamp, datetime.timedelta(hours=5, minutes=30), stamp)
            self.assertEqual(alerts.ist_to_utc(alerts.to_ist(stamp)), stamp)
        self.assertEqual(alerts.to_ist(DT(2026, 11, 9, 3, 45)), DT(2026, 11, 9, 9, 15))
        self.assertEqual(alerts.to_ist(DT(2026, 11, 9, 18, 40)), DT(2026, 11, 10, 0, 10))   # IST date rolls first

    def test_parse_now(self):
        self.assertEqual(ist("2026-11-09T09:15"), DT(2026, 11, 9, 3, 45))
        self.assertEqual(ist("2026-11-09 10:15"), DT(2026, 11, 9, 4, 45))
        self.assertEqual(ist("2026-12-01T09:15"), DT(2026, 12, 1, 3, 45))      # same offset in December
        self.assertEqual(ist("2026-07-01T09:15"), DT(2026, 7, 1, 3, 45))       # and in July
        self.assertEqual(ist("2026-11-09T09:30:01Z"), DT(2026, 11, 9, 9, 30, 1))
        self.assertEqual(ist("2026-11-09T15:00+05:30"), DT(2026, 11, 9, 9, 30))
        with self.assertRaises(alerts.AlertError) as ctx:
            ist("tomorrow")
        self.assertIn("IST", str(ctx.exception))

    def test_market_status_through_a_trading_day(self):
        st = alerts.market_status(ist("2026-11-09T10:00"))
        self.assertTrue(st["open"])
        self.assertEqual((st["opens"], st["closes"], st["reason"]), ("09:15", "15:30", "regular session"))
        self.assertEqual(st["now_ist"], "2026-11-09 10:00")
        before = alerts.market_status(ist("2026-11-09T09:14"))
        self.assertFalse(before["open"])
        self.assertEqual((before["reason"], before["next_open"]), ("before the open", "2026-11-09 09:15"))
        self.assertTrue(alerts.market_status(ist("2026-11-09T09:15"))["open"])
        self.assertTrue(alerts.market_status(ist("2026-11-09T15:29"))["open"])
        after = alerts.market_status(ist("2026-11-09T15:30"))
        self.assertFalse(after["open"])
        self.assertEqual(after["reason"], "after the close")

    def test_the_next_open_skips_the_holiday(self):
        # Mon 9 Nov closes, Tue 10 Nov is Diwali-Balipratipada, so the next open is Wed 11 Nov
        self.assertEqual(alerts.market_status(ist("2026-11-09T15:30"))["next_open"], "2026-11-11 09:15")
        diwali = alerts.market_status(ist("2026-11-10T11:00"))
        self.assertFalse(diwali["open"])
        self.assertIn("Diwali", diwali["reason"])
        self.assertTrue(diwali["reason"].startswith("NSE holiday"))
        self.assertNotIn("opens", diwali)
        self.assertEqual(diwali["next_open"], "2026-11-11 09:15")

    def test_good_friday_and_the_long_weekend(self):
        gf = alerts.market_status(ist("2026-04-03T11:00"))
        self.assertFalse(gf["open"])
        self.assertIn("Good Friday", gf["reason"])
        self.assertEqual(gf["next_open"], "2026-04-06 09:15")                 # Sat, Sun, then Monday
        self.assertTrue(alerts.market_status(ist("2026-04-06T11:00"))["open"])

    def test_us_holidays_and_early_closes_do_not_apply(self):
        for text in ("2026-07-03T11:00", "2026-11-26T11:00", "2026-01-19T11:00"):   # US days off, NSE trades
            self.assertTrue(alerts.market_status(ist(text))["open"], text)
        self.assertTrue(alerts.market_status(ist("2026-11-27T13:00"))["open"])      # the US 1 p.m. close
        self.assertTrue(alerts.market_status(ist("2026-11-27T15:29"))["open"])
        self.assertTrue(alerts.market_status(ist("2026-12-24T15:00"))["open"])

    def test_weekend_and_unknown_years(self):
        self.assertEqual(alerts.market_status(ist("2026-11-07T10:00"))["reason"], "weekend")
        sunday = alerts.market_status(ist("2026-11-08T18:30"))                  # Diwali Laxmi Pujan evening
        self.assertFalse(sunday["open"])
        self.assertEqual(sunday["reason"], "weekend")                           # Muhurat trading is not modelled
        self.assertTrue(alerts.market_status(ist("2026-11-09T10:00"))["calendar_known"])
        self.assertFalse(alerts.market_status(ist("2027-03-01T10:00"))["calendar_known"])
        self.assertFalse(alerts.market_status(ist("2025-11-03T10:00"))["calendar_known"])

    def test_the_ist_date_decides_the_day_not_the_utc_date(self):
        # 2026-11-09 19:00 UTC is already 00:30 IST on Tuesday 10 Nov, a holiday
        st = alerts.market_status(DT(2026, 11, 9, 19, 0))
        self.assertEqual(st["date"], "2026-11-10")
        self.assertIn("Diwali", st["reason"])


class Quotes(unittest.TestCase):
    def test_yahoo_fixtures(self):
        q = alerts.parse_yahoo(read_text("yahoo-RELIANCE.json"), "RELIANCE")
        self.assertEqual((q["price"], q["prev_close"], q["open"]), (1320.0, 1313.4, 1316.5))
        self.assertEqual((q["high"], q["low"]), (1322.8, 1309.1))
        self.assertEqual((q["currency"], q["source"]), ("INR", "yahoo"))
        self.assertEqual(q["time"], "2026-11-06T10:00:00Z")                    # 15:30 IST, the Friday close
        q = alerts.parse_yahoo(read_text("yahoo-TCS.json"), "TCS")
        self.assertEqual((q["price"], q["prev_close"]), (2498.5, 2530.0))
        with self.assertRaises(alerts.AlertError) as ctx:
            alerts.parse_yahoo(read_text("yahoo-ZZZZ.json"), "ZZZZ")
        self.assertIn("No data found", str(ctx.exception))

    def test_stooq_fixture(self):
        q = alerts.parse_stooq(read_text("stooq-RELIANCE.csv"), "RELIANCE")
        self.assertEqual((q["price"], q["open"], q["source"]), (1320.0, 1316.5, "stooq"))
        with self.assertRaises(alerts.AlertError):
            alerts.parse_stooq(read_text("stooq-ZZZZ.csv"), "ZZZZ")

    def test_urls_and_user_agent(self):
        self.assertEqual(alerts.UA, UA)
        self.assertEqual(alerts.stooq_url("RELIANCE"),
                         "https://stooq.com/q/l/?s=reliance.in&f=sd2t2ohlcv&h&e=csv")
        self.assertIn("//query1.finance.yahoo.com/", alerts.yahoo_url("TCS", alerts.YAHOO_HOSTS[1]))

    def test_yahoo_symbols_get_ns_and_never_turn_into_a_dash(self):
        base = "https://query2.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=1d"
        self.assertEqual(alerts.yahoo_url("RELIANCE"), base % "RELIANCE.NS")
        self.assertEqual(alerts.yahoo_url("reliance"), base % "RELIANCE.NS")
        self.assertEqual(alerts.yahoo_url("RELIANCE.NS"), base % "RELIANCE.NS")
        self.assertEqual(alerts.yahoo_url("tcs.bo"), base % "TCS.BO")
        self.assertEqual(alerts.yahoo_url("BAJAJ-AUTO"), base % "BAJAJ-AUTO.NS")
        self.assertEqual(alerts.yahoo_url("M&M"), base % "M%26M.NS")
        for sym in ("RELIANCE", "RELIANCE.NS", "HDFCBANK", "M&M", "BAJAJ-AUTO"):
            self.assertNotIn("-NS", alerts.yahoo_url(sym), sym)

    def test_google_urls(self):
        base = "https://www.google.com/finance/quote/%s"
        self.assertEqual(alerts.google_url("RELIANCE"), base % "RELIANCE:NSE")
        self.assertEqual(alerts.google_url("RELIANCE.NS"), base % "RELIANCE:NSE")
        self.assertEqual(alerts.google_url("tcs.bo"), base % "TCS:BOM")
        self.assertEqual(alerts.google_url("M&M"), base % "M%26M:NSE")
        self.assertEqual(alerts.google_url("BAJAJ-AUTO"), base % "BAJAJ-AUTO:NSE")

    def test_google_page_parses_price_prev_close_and_ist_time(self):
        now = DT(2026, 11, 9, 5, 0)                                             # Monday 10:30 IST
        q = alerts.parse_google(read_text("google-RELIANCE.html"), "reliance", now)
        self.assertEqual(q["symbol"], "RELIANCE")
        self.assertEqual((q["price"], q["open"], q["high"], q["low"]), (1320.0, 1316.5, 1322.8, 1309.1))
        self.assertEqual(q["prev_close"], 1313.4)                              # price minus the +6.60 day change
        self.assertEqual((q["currency"], q["source"]), ("INR", "google"))
        self.assertEqual(q["time"], "2026-11-06T10:00:00Z")                    # "Nov 6, 3:30:00 PM GMT+5:30"

    def test_google_down_day_has_a_higher_previous_close(self):
        q = alerts.parse_google(read_text("google-WIPRO.html"), "WIPRO", DT(2026, 11, 9, 5, 0))
        self.assertEqual((q["price"], q["prev_close"]), (198.0, 198.4))        # 198.00 minus (-0.40)

    def test_google_unknown_ticker_is_an_error_not_a_price(self):
        with self.assertRaises(alerts.AlertError) as ctx:
            alerts.parse_google(read_text("google-ZZZZ.html"), "ZZZZ", DT(2026, 11, 9, 5, 0))
        self.assertIn("Google Finance has no quote for ZZZZ", str(ctx.exception))
        with self.assertRaises(alerts.AlertError):
            alerts.parse_google("<html>consent page</html>", "RELIANCE")

    def test_google_refuses_a_non_rupee_price(self):
        original = read_text("google-RELIANCE.html")
        for shown in ("$1,320.00", "1,320.00"):      # the bare number parses fine, so only the currency check can stop it
            page = original.replace("<span>₹1,320.00</span>", "<span>%s</span>" % shown)
            self.assertNotEqual(page, original)
            with self.assertRaises(alerts.AlertError) as ctx:
                alerts.parse_google(page, "RELIANCE", DT(2026, 11, 9, 5, 0))
            self.assertIn("non-rupee or unreadable price", str(ctx.exception), shown)

    def test_google_quote_time_with_plain_space_and_without_a_date(self):
        now = DT(2026, 11, 9, 5, 0)                                             # Monday 10:30 IST
        page = read_text("google-RELIANCE.html")
        plain = page.replace("Nov 6, 3:30:00 PM GMT+5:30", "Nov 6, 3:30:00 PM GMT+5:30")
        self.assertEqual(alerts.parse_google(plain, "RELIANCE", now)["time"], "2026-11-06T10:00:00Z")
        today = page.replace("Nov 6, 3:30:00 PM GMT+5:30", "10:15:00 AM GMT+5:30")
        self.assertEqual(alerts.parse_google(today, "RELIANCE", now)["time"], "2026-11-09T04:45:00Z")
        december = page.replace("Nov 6, 3:30:00 PM GMT+5:30", "Dec 31, 3:30:00 PM GMT+5:30")
        q = alerts.parse_google(december, "RELIANCE", DT(2027, 1, 2, 5, 0))     # a December quote read in January
        self.assertEqual(q["time"], "2026-12-31T10:00:00Z")

    def test_offline_quotes_and_errors(self):
        q, errors = alerts.get_quotes(["RELIANCE", "TCS", "ZZZZ", "NOPE"], "yahoo", QDIR)
        self.assertEqual(sorted(q), ["RELIANCE", "TCS"])
        self.assertEqual(q["TCS"]["price"], 2498.5)
        self.assertEqual(q["TCS"]["source"], "yahoo")
        self.assertEqual(len(errors), 2)
        self.assertIn("No data found", errors[0])
        self.assertIn("Google Finance fallback", errors[0])
        self.assertIn("no offline quote file", errors[1])

    def test_symbols_with_punctuation_read_their_own_files(self):
        q, errors = alerts.get_quotes(["M&M", "BAJAJ-AUTO", "HDFCBANK"], "yahoo", QDIR)
        self.assertEqual(errors, [])
        self.assertEqual((q["M&M"]["price"], q["BAJAJ-AUTO"]["price"], q["HDFCBANK"]["price"]),
                         (3287.2, 10900.0, 788.25))
        self.assertEqual(q["M&M"]["symbol"], "M&M")

    def test_yahoo_without_a_quote_falls_back_to_google_and_says_so(self):
        q, errors = alerts.get_quotes(["WIPRO"], "yahoo", QDIR)               # no yahoo-WIPRO.json here
        self.assertEqual(errors, [])
        self.assertEqual((q["WIPRO"]["source"], q["WIPRO"]["price"], q["WIPRO"]["prev_close"]),
                         ("google", 198.0, 198.4))

    def test_other_sources_do_not_fall_back(self):
        q, errors = alerts.get_quotes(["WIPRO"], "stooq", QDIR)
        self.assertEqual(q, {})
        self.assertEqual(len(errors), 1)
        self.assertNotIn("Google", errors[0])
        q, errors = alerts.get_quotes(["RELIANCE"], "google", QDIR)           # forced google
        self.assertEqual((q["RELIANCE"]["source"], q["RELIANCE"]["price"], errors), ("google", 1320.0, []))

    def test_offline_gz(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(QDIR, "yahoo-RELIANCE.json"), "rb") as src:
                with gzip.open(os.path.join(d, "yahoo-RELIANCE.json.gz"), "wb") as dst:
                    dst.write(src.read())
            q, errors = alerts.get_quotes(["RELIANCE"], "yahoo", d)
            self.assertEqual((q["RELIANCE"]["price"], errors), (1320.0, []))
        finally:
            shutil.rmtree(d)


class FakeNet(object):
    """Stands in for urllib: answers each request with the next status code, by host."""

    def __init__(self, codes, bodies=None):
        self.codes, self.calls, self.urls, self.sleeps = list(codes), [], [], []
        self.bodies = dict(bodies or {})
        self.bodies.setdefault("www.google.com", read_text("google-RELIANCE.html"))

    def urlopen(self, req, timeout=None):
        host = req.full_url.split("/")[2]
        self.calls.append((host, req.get_header("User-agent")))
        self.urls.append(req.full_url)
        code = self.codes.pop(0)
        if code != 200:
            body = b""
            if code == 404 and "yahoo" in host:
                body = (b'{"chart":{"result":null,"error":{"code":"Not Found",'
                        b'"description":"No data found, symbol may be delisted"}}}')
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(body))
        text = self.bodies.get(host) or read_text("yahoo-RELIANCE.json")
        return io.BytesIO(text.encode("utf-8"))


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = (alerts._urlopen, alerts._sleep)

    def tearDown(self):
        alerts._urlopen, alerts._sleep = self.saved

    def net(self, codes, bodies=None):
        f = FakeNet(codes, bodies)
        alerts._urlopen = f.urlopen
        alerts._sleep = f.sleeps.append
        return f

    def hosts(self, f):
        return [h for h, _ in f.calls]

    def test_query2_first_with_the_contract_user_agent_and_the_ns_symbol(self):
        f = self.net([200])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertEqual((q["RELIANCE"]["price"], errors), (1320.0, []))
        self.assertEqual(f.calls, [("query2.finance.yahoo.com", UA)])
        self.assertIn("/v8/finance/chart/RELIANCE.NS?", f.urls[0])
        self.assertNotIn("RELIANCE-NS", f.urls[0])

    def test_one_retry_with_a_pause_on_429(self):
        f = self.net([429, 200])
        self.assertEqual(alerts.get_quotes(["RELIANCE"])[0]["RELIANCE"]["price"], 1320.0)
        self.assertEqual(self.hosts(f), ["query2.finance.yahoo.com"] * 2)
        self.assertEqual(f.sleeps, [alerts.BACKOFF])

    def test_falls_back_to_query1_on_429_and_5xx(self):
        f = self.net([429, 429, 200])
        self.assertIn("RELIANCE", alerts.get_quotes(["RELIANCE"])[0])
        self.assertEqual(self.hosts(f), ["query2.finance.yahoo.com"] * 2 + ["query1.finance.yahoo.com"])
        f = self.net([503, 200])
        self.assertIn("RELIANCE", alerts.get_quotes(["RELIANCE"])[0])
        self.assertEqual(self.hosts(f), ["query2.finance.yahoo.com", "query1.finance.yahoo.com"])

    def test_both_yahoo_hosts_down_falls_back_to_google_for_that_ticker(self):
        f = self.net([500, 502, 200], {"www.google.com": read_text("google-WIPRO.html")})
        q, errors = alerts.get_quotes(["WIPRO"])
        self.assertEqual(errors, [])
        self.assertEqual((q["WIPRO"]["source"], q["WIPRO"]["price"]), ("google", 198.0))
        self.assertEqual(self.hosts(f), ["query2.finance.yahoo.com", "query1.finance.yahoo.com", "www.google.com"])
        self.assertEqual(f.urls[-1], "https://www.google.com/finance/quote/WIPRO:NSE")
        self.assertEqual(f.calls[-1][1], UA)
        self.assertEqual(f.sleeps, [alerts.PAUSE])

    def test_nothing_answers_reports_both_attempts(self):
        f = self.net([500, 502, 503])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertEqual(q, {})
        self.assertIn("query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS answered HTTP 502", errors[0])
        self.assertIn("Google Finance fallback", errors[0])
        self.assertIn("HTTP 503", errors[0])

    def test_a_404_is_yahoos_answer_then_google_confirms_the_ticker_is_unknown(self):
        f = self.net([404, 200], {"www.google.com": read_text("google-ZZZZ.html")})
        q, errors = alerts.get_quotes(["ZZZZ"])
        self.assertEqual(q, {})
        self.assertIn("No data found", errors[0])
        self.assertIn("Google Finance has no quote for ZZZZ", errors[0])
        self.assertEqual(self.hosts(f), ["query2.finance.yahoo.com", "www.google.com"])

    def test_a_403_is_not_retried_on_the_other_yahoo_host(self):
        f = self.net([403, 403])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertEqual(q, {})
        self.assertIn("HTTP 403", errors[0])
        self.assertEqual(self.hosts(f), ["query2.finance.yahoo.com", "www.google.com"])

    def test_forcing_google_makes_one_request_and_no_fallback(self):
        f = self.net([200])
        q, errors = alerts.get_quotes(["RELIANCE"], "google")
        self.assertEqual((q["RELIANCE"]["source"], errors), ("google", []))
        self.assertEqual(self.hosts(f), ["www.google.com"])
        f = self.net([404])
        q, errors = alerts.get_quotes(["RELIANCE"], "google")
        self.assertEqual(q, {})
        self.assertEqual(len(errors), 1)
        self.assertIn("HTTP 404", errors[0])

    def test_a_pause_between_tickers(self):
        f = self.net([200, 200])
        alerts.get_quotes(["RELIANCE", "TCS"])
        self.assertEqual(f.sleeps, [alerts.PAUSE])

    def test_the_guard_really_blocks_unfaked_network_calls(self):
        alerts._urlopen = _no_network
        with self.assertRaises(AssertionError):
            alerts.get_quotes(["RELIANCE"])


class Rules(unittest.TestCase):
    def setUp(self):
        self.q, _ = alerts.get_quotes(["RELIANCE", "TCS", "HDFCBANK", "INFY", "M&M"], "yahoo", QDIR)
        self.now = ist("2026-11-09T10:00")
        self.open = alerts.market_status(self.now)

    def at(self, text, rules, state=None, quotes=None):
        now = ist(text)
        return alerts.evaluate(rules, self.q if quotes is None else quotes, alerts.market_status(now),
                               state or {}, now)

    def test_a_level_rings_once_in_rupees(self):
        rules = [{"id": "reliance-1300", "symbol": "RELIANCE", "type": "above", "price": 1300}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual(len(rings), 1)
        self.assertEqual(rings[0]["message"], "RELIANCE is ₹1320.00: at or above your alert level ₹1300.")
        self.assertNotIn("$", rings[0]["message"])
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])

    def test_after_the_close_it_says_last_not_is(self):
        rules = [{"id": "r", "symbol": "RELIANCE", "type": "above", "price": 1300}]
        closed = alerts.market_status(ist("2026-11-09T16:00"))
        rings, _ = alerts.evaluate(rules, self.q, closed, {}, ist("2026-11-09T16:00"))
        self.assertEqual(rings[0]["message"], "RELIANCE last ₹1320.00: at or above your alert level ₹1300.")

    def test_stop_and_rearm(self):
        rules = [{"id": "hdfc-stop", "symbol": "HDFCBANK", "type": "stop", "price": 790, "rearm": True}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual(rings[0]["message"], "HDFCBANK is ₹788.25: at or below your stop ₹790.")
        self.q["HDFCBANK"]["price"] = 795.0
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])
        self.assertNotIn("hdfc-stop", st["rung"])
        self.q["HDFCBANK"]["price"] = 785.5
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(len(rings), 1)

    def test_pct_move_from_previous_close(self):
        big = [{"id": "tcs-3", "symbol": "TCS", "type": "pct_move", "pct": 3}]
        self.assertEqual(alerts.evaluate(big, self.q, self.open, {}, self.now)[0], [])
        small = [{"id": "tcs-1", "symbol": "TCS", "type": "pct_move", "pct": 1, "direction": "down"}]
        rings, st = alerts.evaluate(small, self.q, self.open, {}, self.now)
        self.assertEqual(rings[0]["message"], "TCS moved -1.25% to ₹2498.50 (from ₹2530.00).")
        self.assertEqual(alerts.evaluate(small, self.q, self.open, st, self.now)[0], [])
        up = [{"id": "tcs-up", "symbol": "TCS", "type": "pct_move", "pct": 1, "direction": "up"}]
        self.assertEqual(alerts.evaluate(up, self.q, self.open, {}, self.now)[0], [])
        mm = [{"id": "mm-5", "symbol": "M&M", "type": "pct_move", "pct": 5}]
        rings, _ = alerts.evaluate(mm, self.q, self.open, {}, self.now)
        self.assertEqual(rings[0]["message"], "M&M moved +5.11% to ₹3287.20 (from ₹3127.50).")

    def test_a_pct_move_rings_once_per_ist_day_and_again_the_next(self):
        rule = [{"id": "hdfc-3", "symbol": "HDFCBANK", "type": "pct_move", "pct": 3}]
        rings, st = self.at("2026-11-09 10:00", rule)
        self.assertIn("+3.72%", rings[0]["message"])
        self.assertEqual(self.at("2026-11-09 11:00", rule, st)[0], [])
        self.assertEqual(len(self.at("2026-11-11 10:00", rule, st)[0]), 1)       # next trading day

    def test_rung_today_uses_the_ist_date(self):
        # 18:40 UTC on 9 Nov is 00:10 IST on 10 Nov
        self.assertTrue(alerts._rang_today("2026-11-09T18:40:00Z", "2026-11-10"))
        self.assertFalse(alerts._rang_today("2026-11-09T18:40:00Z", "2026-11-09"))
        self.assertFalse(alerts._rang_today(None, "2026-11-09"))

    def test_opening_bell_once_per_trading_day_and_not_on_a_holiday(self):
        rules = [{"id": "bell", "type": "opening_bell"}]
        closed = alerts.market_status(ist("2026-11-09T08:00"))
        self.assertEqual(alerts.evaluate(rules, {}, closed, {}, ist("2026-11-09T08:00"))[0], [])
        rings, st = alerts.evaluate(rules, {}, self.open, {}, self.now)
        self.assertEqual(rings[0]["message"], "Opening bell: the NSE is open (2026-11-09 10:00 IST).")
        self.assertEqual(alerts.evaluate(rules, {}, self.open, st, ist("2026-11-09T11:00"))[0], [])
        diwali = alerts.market_status(ist("2026-11-10T09:31"))
        self.assertEqual(alerts.evaluate(rules, {}, diwali, st, ist("2026-11-10T09:31"))[0], [])
        nxt = alerts.market_status(ist("2026-11-11T09:31"))
        self.assertEqual(len(alerts.evaluate(rules, {}, nxt, st, ist("2026-11-11T09:31"))[0]), 1)

    def test_the_plan_gives_a_stop_a_target_and_flat_by_close(self):
        rules = plan_rules()
        self.assertEqual([(r["id"], r["type"]) for r in rules],
                         [("reliance-plan-stop", "stop"), ("reliance-plan-target", "target"),
                          ("reliance-flat-by", "time")])
        self.assertEqual((rules[0]["price"], rules[1]["price"]), (1300.8, 1323.0))
        self.assertEqual(rules[0]["note"], "plan stop: opening-range midpoint")
        self.assertEqual(rules[2], {"id": "reliance-flat-by", "symbol": "RELIANCE", "type": "time", "at": "15:20",
                                    "label": "flat by close", "date": "2026-11-09"})
        self.assertTrue(all(r["date"] == "2026-11-09" for r in rules))
        rings, st = self.at("2026-11-09 10:52", rules)          # 1320.00 sits between the stop and the target
        self.assertEqual(rings, [])
        q = json.loads(json.dumps(self.q))
        q["RELIANCE"]["price"] = 1300.10
        rings, st = self.at("2026-11-09 11:30", rules, st, q)
        self.assertEqual([r["id"] for r in rings], ["reliance-plan-stop"])
        self.assertEqual(rings[0]["message"], "RELIANCE is ₹1300.10: at or below your stop ₹1300.80 "
                                              "(plan stop: opening-range midpoint).")
        q["RELIANCE"]["price"] = 1323.0
        rings = self.at("2026-11-09 12:00", rules, st, q)[0]
        self.assertEqual([r["id"] for r in rings], ["reliance-plan-target"])
        self.assertIn("at or above your target ₹1323 (plan target: 1R (one times the risk))", rings[0]["message"])

    def test_plan_rules_are_for_their_own_day_only(self):
        rules = plan_rules()
        q = json.loads(json.dumps(self.q))
        q["RELIANCE"]["price"] = 1290.0
        self.assertEqual(self.at("2026-11-11 11:00", rules, None, q)[0], [])    # Wednesday: the plan was for Monday
        self.assertEqual(len(self.at("2026-11-09 11:00", rules, None, q)[0]), 1)

    def test_the_flat_by_default_is_1520_not_1555(self):
        plan = {"symbol": "TCS", "stop": 2480.0, "target": 2520.0}
        flat = alerts.rules_from_plan(plan)[2]
        self.assertEqual((flat["at"], flat["id"]), ("15:20", "tcs-flat-by"))
        self.assertEqual(alerts.rules_from_plan(plan, "15:10")[2]["at"], "15:10")
        self.assertEqual(alerts.rules_from_plan(dict(plan, flat_by="15:00"), "15:10")[2]["at"], "15:00")
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan(dict(plan, flat_by="3:20pm"))

    def test_flat_by_close_rings_once_per_trading_day(self):
        rules = plan_rules()[2:]
        self.assertEqual(self.at("2026-11-09 15:19", rules)[0], [])
        rings, st = self.at("2026-11-09 15:20", rules)
        self.assertEqual(len(rings), 1)
        self.assertIn("Flat by close: it is 15:20 IST (set for 15:20), the market closes at 15:30.", rings[0]["message"])
        self.assertIn("trader-broker flatten --symbol RELIANCE", rings[0]["message"])
        self.assertEqual(self.at("2026-11-09 15:29", rules, st)[0], [])
        rings, _ = self.at("2026-11-09 15:40", rules)
        self.assertIn("the market has closed", rings[0]["message"])
        self.assertEqual(self.at("2026-11-10 15:21", rules, st)[0], [])         # plan rules: their day only

    def test_a_time_alert_never_moves_because_nse_has_no_early_close(self):
        rule = [{"id": "flat", "type": "time", "at": "15:20", "label": "flat by close"}]
        for early in ("2026-11-27 12:55", "2026-11-27 13:05", "2026-12-24 12:55"):    # US early-close days
            self.assertEqual(self.at(early, rule)[0], [], early)
        rings, _ = self.at("2026-11-27 15:20", rule)
        self.assertIn("(set for 15:20), the market closes at 15:30", rings[0]["message"])
        stale = [{"id": "flat", "type": "time", "at": "15:20", "label": "x", "before_close": True}]
        self.assertEqual(self.at("2026-11-27 12:55", stale)[0], [])             # the old US flag does nothing
        self.assertEqual(len(self.at("2026-11-27 15:20", stale)[0]), 1)

    def test_a_time_alert_stays_quiet_on_weekends_and_nse_holidays(self):
        rule = [{"id": "flat", "type": "time", "at": "15:20", "label": "flat by close"}]
        self.assertEqual(self.at("2026-11-07 15:21", rule)[0], [])             # Saturday
        self.assertEqual(self.at("2026-11-10 15:21", rule)[0], [])             # Diwali-Balipratipada
        self.assertEqual(self.at("2026-04-03 15:21", rule)[0], [])             # Good Friday
        rings, st = self.at("2026-12-01 15:20", rule)
        self.assertEqual(len(rings), 1)
        self.assertEqual(st["rung"]["flat"], "2026-12-01T09:50:00Z")           # 15:20 IST is 09:50 UTC
        plain = [{"id": "t", "type": "time", "at": "09:20", "label": "check"}]
        self.assertEqual(self.at("2026-11-09 09:19", plain)[0], [])
        self.assertEqual(len(self.at("2026-11-09 09:20", plain)[0]), 1)

    def test_the_example_rules_are_valid_and_shaped_like_a_plan(self):
        with open(os.path.join(ROOT, "trader-alerts", "alerts.example.json")) as fh:
            rules = json.load(fh)["rules"]
        for r in rules:
            alerts.validate(r)
        by_id = {r["id"]: r for r in rules}
        self.assertIn("opening-bell", by_id)
        plan = {r["id"]: r for r in plan_rules()}
        for rid in ("reliance-plan-stop", "reliance-plan-target", "reliance-flat-by"):
            self.assertEqual(set(by_id[rid]), set(plan[rid]), rid)
            self.assertEqual(by_id[rid]["symbol"], "RELIANCE")
        self.assertEqual(by_id["reliance-flat-by"]["at"], "15:20")
        self.assertLess(by_id["reliance-plan-stop"]["price"], by_id["reliance-plan-target"]["price"])
        for r in rules:
            for key in ("date",):
                if key in r:
                    datetime.date.fromisoformat(r[key])

    def test_bad_rules(self):
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "moon"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "RELIANCE"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError) as ctx:
            alerts.evaluate([{"id": "x", "type": "time", "at": "4pm"}], {}, self.open, {}, self.now)
        self.assertIn("IST", str(ctx.exception))
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "time", "at": "25:00"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "price": 1300}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.validate({"id": "x", "type": "above", "symbol": "TCS", "price": 2500, "date": "09-11-2026"})
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"symbol": "RELIANCE", "stop": 1300.8})
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"stop": 1300.8, "target": 1323.0})


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rules = os.path.join(self.d, "alerts.json")
        self.state = os.path.join(self.d, "state", "alerts.json")
        self.prices = os.path.join(self.d, "prices.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def check(self, now):
        return run(["check", "--rules", self.rules, "--state", self.state, "--prices-out", self.prices,
                    "--offline", QDIR, "--now", now])

    def test_the_trade_from_the_plan_across_a_diwali_holiday(self):
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state,
                             "--config", os.path.join(self.d, "none")])
        self.assertEqual(code, 0)
        self.assertIn("Added reliance-flat-by", text)
        self.assertEqual(run(["add", "--rules", self.rules, "--opening-bell"])[0], 0)

        code, text, _ = self.check("2026-11-09 10:15")
        self.assertEqual(code, 0)                                      # the opening bell rings first
        self.assertIn("NSE OPEN (2026-11-09 10:15 IST, regular session).", text)
        code, text, _ = self.check("2026-11-09 10:16")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit. 4 rule(s) watched today, RELIANCE ₹1320.00.", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"RELIANCE": 1320.0})
        self.assertEqual(doc["detail"]["RELIANCE"]["time"], "2026-11-06T10:00:00Z")
        self.assertEqual(doc["detail"]["RELIANCE"]["source"], "yahoo")
        self.assertEqual(doc["detail"]["RELIANCE"]["fetched"], "2026-11-09T04:46:00Z")   # 10:16 IST

        code, text, _ = self.check("2026-11-09 15:20")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Flat by close", text)
        self.assertIn("not an order", text)
        self.assertEqual(self.check("2026-11-09 15:25")[0], 1)

        code, text, _ = self.check("2026-11-10 10:00")                 # Diwali-Balipratipada: the NSE is shut
        self.assertEqual(code, 1)
        self.assertIn("NSE closed (2026-11-10 10:00 IST, NSE holiday: Diwali (Balipratipada)).", text)
        self.assertIn("Nothing hit. 1 rule(s) watched today.", text)
        code, text, _ = self.check("2026-11-11 10:00")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-11-11 10:05")
        self.assertIn("Nothing hit. 1 rule(s) watched today.", text)

        # a new plan replaces the plan rules and re-arms them
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state])
        with open(self.rules) as fh:
            ids = [r["id"] for r in json.load(fh)["rules"]]
        self.assertEqual(sorted(ids), ["opening-bell", "reliance-flat-by", "reliance-plan-stop",
                                       "reliance-plan-target"])
        with open(self.state) as fh:
            self.assertNotIn("reliance-flat-by", json.load(fh)["rung"])

    def test_the_portfolio_evening_and_morning(self):
        for args in (["--opening-bell"], ["--symbol", "RELIANCE", "--above", "1350"],
                     ["--symbol", "HDFCBANK", "--stop", "750"]):
            code, _, _ = run(["add", "--rules", self.rules] + args)
            self.assertEqual(code, 0)
        code, _, err = run(["add", "--rules", self.rules, "--symbol", "RELIANCE", "--above", "1350"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

        code, text, _ = self.check("2026-11-09T20:00")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"HDFCBANK": 788.25, "RELIANCE": 1320.0})

        code, text, _ = self.check("2026-11-11T09:31")                 # 10 Nov was a holiday
        self.assertEqual(code, 0)
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-11-11T09:45")
        self.assertEqual(code, 1)

        code, text, _ = run(["list", "--rules", self.rules, "--state", self.state])
        self.assertIn("rang", text)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 0)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 1)

    def test_a_level_that_is_hit_rings_and_a_failed_quote_is_reported(self):
        run(["add", "--rules", self.rules, "--symbol", "RELIANCE", "--above", "1300"])
        run(["add", "--rules", self.rules, "--symbol", "ZZZZ", "--above", "10"])
        code, text, _ = self.check("2026-11-09 10:30")
        self.assertEqual(code, 0)
        self.assertIn("ALERT RELIANCE is ₹1320.00: at or above your alert level ₹1300.", text)
        self.assertIn("error: ", text)
        self.assertIn("ZZZZ", text)
        self.assertIn("Google Finance fallback", text)

    def test_add_time_and_target(self):
        code, text, _ = run(["add", "--rules", self.rules, "--at", "15:20", "--label", "flat by close",
                             "--symbol", "reliance"])
        self.assertEqual(code, 0)
        code, text, _ = run(["add", "--rules", self.rules, "--symbol", "RELIANCE", "--target", "1323",
                             "--date", "2026-11-09"])
        self.assertEqual(code, 0)
        with open(self.rules) as fh:
            rules = json.load(fh)["rules"]
        self.assertEqual(rules[0], {"id": "time-1520", "type": "time", "at": "15:20", "label": "flat by close",
                                    "symbol": "RELIANCE"})
        self.assertEqual(rules[1], {"id": "reliance-target-1323", "symbol": "RELIANCE", "type": "target",
                                    "price": 1323.0, "date": "2026-11-09"})
        self.assertEqual(run(["add", "--rules", self.rules, "--at", "3pm"])[0], 1)
        code, _, err = run(["add", "--rules", self.rules, "--from-plan", "--plan", os.path.join(self.d, "none")])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)

    def test_the_us_before_close_flag_is_gone(self):
        with self.assertRaises(SystemExit):
            run(["add", "--rules", self.rules, "--at", "15:20", "--before-close"])

    def test_quote_write_merges(self):
        code, text, _ = run(["quote", "RELIANCE", "TCS", "--offline", QDIR, "--write", self.prices,
                             "--now", "2026-11-09T04:46:01Z"])
        self.assertEqual(code, 0)
        self.assertIn("₹1320.00", text)
        self.assertIn("(+0.50% vs prev close ₹1313.40)", text)
        self.assertIn("(-1.25% vs prev close ₹2530.00)", text)
        self.assertIn("[yahoo]", text)
        self.assertIn("delayed", text)
        self.assertNotIn("$", text)
        run(["quote", "HDFCBANK", "--offline", QDIR, "--write", self.prices])
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(doc["prices"]), ["HDFCBANK", "RELIANCE", "TCS"])
        self.assertEqual(doc["detail"]["TCS"]["prev_close"], 2530.0)
        self.assertEqual(doc["detail"]["RELIANCE"]["fetched"], "2026-11-09T04:46:01Z")

    def test_quote_punctuated_symbols(self):
        code, text, _ = run(["quote", "M&M", "BAJAJ-AUTO", "--offline", QDIR, "--json"])
        self.assertEqual(code, 0)
        quotes = json.loads(text)["quotes"]
        self.assertEqual((quotes["M&M"]["price"], quotes["BAJAJ-AUTO"]["price"]), (3287.2, 10900.0))

    def test_quote_falls_back_to_google_and_marks_it(self):
        code, text, _ = run(["quote", "WIPRO", "--offline", QDIR])
        self.assertEqual(code, 0)
        self.assertIn("₹198.00", text)
        self.assertIn("(-0.20% vs prev close ₹198.40)", text)
        self.assertIn("[google]", text)
        code, text, _ = run(["quote", "RELIANCE", "--source", "google", "--offline", QDIR, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["quotes"]["RELIANCE"]["source"], "google")

    def test_quote_unknown_ticker_is_an_error_never_a_price(self):
        code, text, _ = run(["quote", "ZZZZ", "--offline", QDIR])
        self.assertEqual(code, 1)
        self.assertIn("error: ", text)
        self.assertIn("Google Finance has no quote for ZZZZ", text)
        self.assertNotIn("₹", text)

    def test_quote_holdings_from_portfolio_and_plan(self):
        pf = os.path.join(self.d, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"cash": 0, "positions": [{"ticker": "TCS", "shares": 1}]}, fh)
        code, text, _ = run(["quote", "--holdings", "--portfolio", pf, "--plan", PLAN, "--offline", QDIR, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(json.loads(text)["quotes"]), ["RELIANCE", "TCS"])

    def test_clock_text_and_exit_codes(self):
        code, text, _ = run(["clock", "--now", "2026-11-09 10:15"])
        self.assertEqual(code, 0)
        self.assertIn("IST 2026-11-09 10:15 (Monday): NSE OPEN, regular session.", text)
        self.assertIn("Today's session: 09:15 to 15:30.", text)
        code, text, _ = run(["clock", "--now", "2026-12-25T10:00"])
        self.assertEqual(code, 1)
        self.assertIn("Christmas", text)
        self.assertIn("NSE closed", text)
        self.assertIn("Next open: 2026-12-28 09:15 IST.", text)
        code, text, _ = run(["clock", "--now", "2026-11-10T10:00"])
        self.assertEqual(code, 1)
        self.assertIn("Diwali (Balipratipada)", text)
        self.assertIn("Next open: 2026-11-11 09:15 IST.", text)

    def test_clock_says_when_the_holiday_list_is_unknown(self):
        code, text, _ = run(["clock", "--now", "2027-03-01 10:00"])
        self.assertIn("Holiday list covers 2026 only", text)
        code, text, _ = run(["clock", "--now", "2026-11-09 10:00", "--json"])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual((doc["open"], doc["now_ist"], doc["calendar_known"]), (True, "2026-11-09 10:00", True))

    def test_check_without_rules(self):
        code, _, err = run(["check", "--rules", self.rules, "--offline", QDIR])
        self.assertEqual(code, 1)
        self.assertIn("no rules", err)


if __name__ == "__main__":
    unittest.main()
