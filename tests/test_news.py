import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, ROOT, load

news = load("trader-news", "news")
DIR = os.path.join(FIX, "trader-news")
SRC = os.path.join(ROOT, "trader-news", "news.py")
# Fri 2026-11-06, 10:15 IST (04:45 UTC). The fixtures are synthetic, see the comment inside each file.
NOW = "2026-11-06T10:15:00+05:30"
WATCH = {"RELIANCE": "Reliance Industries", "INFY": "Infosys", "TCS": "Tata Consultancy Services", "HDFCBANK": None}


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(args)
    return code, out.getvalue(), err.getvalue()


class Recorder(object):
    """A fetcher that never touches the network: serves named files and remembers the URLs it was asked for."""

    offline = None

    def __init__(self, files):
        self.files = files
        self.urls = []

    def get(self, url, name):
        self.urls.append(url)
        if name in self.files:
            return self.files[name]
        raise news.NewsError("offline: no file %s" % name)


EMPTY_RSS = b'<?xml version="1.0"?><rss version="2.0"><channel><title>none</title></channel></rss>'


class Feeds(unittest.TestCase):
    def test_google_news_rss(self):
        items = news.parse_rss(raw("gnews-RELIANCE.xml"), "RELIANCE", source="Google News")
        self.assertEqual(len(items), 6)
        first = items[0]
        self.assertEqual(first["title"], "Reliance Industries Q2 results: net profit rises, beats estimates - Moneycontrol")
        self.assertEqual(first["time"], datetime.datetime(2026, 11, 5, 14, 30, tzinfo=datetime.timezone.utc))
        self.assertEqual(first["source"], "Google News")
        self.assertTrue(first["link"].startswith("https://news.google.com/"))
        self.assertTrue(first["id"].startswith("rss:"))

    def test_yahoo_rss(self):
        items = news.parse_rss(raw("yahoo-INFY.xml"), "INFY")
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["source"], "Yahoo Finance")
        self.assertEqual(items[0]["time"], datetime.datetime(2026, 11, 6, 3, 30, tzinfo=datetime.timezone.utc))
        self.assertTrue(items[0]["link"].startswith("https://finance.yahoo.com/"))
        self.assertEqual(news.parse_rss(raw("yahoo-RELIANCE.xml"), "RELIANCE"), [])    # Yahoo's feed is empty for .NS

    def test_bad_xml_is_a_news_error(self):
        with self.assertRaises(news.NewsError):
            news.parse_rss(b"<rss><oops>", "RELIANCE")

    def test_times(self):
        self.assertEqual(news.parse_time("Fri, 06 Nov 2026 03:10:00 GMT").hour, 3)
        self.assertEqual(news.parse_time("2026-11-06T09:40:35+05:30").hour, 4)       # IST to UTC
        self.assertEqual(news.parse_time("2026-11-06T04:10:35Z").hour, 4)
        self.assertIsNone(news.parse_time("soon"))

    def test_yahoo_symbols_get_the_exchange_suffix(self):
        self.assertEqual(news.yahoo_symbol("RELIANCE"), "RELIANCE.NS")
        self.assertEqual(news.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(news.yahoo_symbol("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(news.yahoo_symbol("RELIANCE.BO"), "RELIANCE.BO")
        self.assertEqual(news.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(news.yahoo_symbol("M&M"), "M&M.NS")
        for t in ("RELIANCE", "INFY", "BAJAJ-AUTO"):
            self.assertNotIn("-NS", news.yahoo_symbol(t).replace("BAJAJ-AUTO", ""))

    def test_chart_move_in_rupees_and_ist(self):
        m = news.parse_chart(raw("chart-RELIANCE.json"), "RELIANCE")
        self.assertEqual((m["price"], m["prev_close"], m["pct"]), (1354.6, 1313.4, 3.14))
        self.assertEqual(news.to_ist(m["time"]).strftime("%H:%M"), "10:52")
        self.assertEqual(m["time"].isoformat(), "2026-11-06T05:22:00+00:00")
        with self.assertRaises(news.NewsError):
            news.parse_chart(b'{"chart": {"result": null}}', "ZZZZ")


class FetchingNseSymbols(unittest.TestCase):
    def test_yahoo_headlines_ask_for_the_dot_ns_symbol_in_india(self):
        rec = Recorder({"yahoo-INFY.xml": raw("yahoo-INFY.xml")})
        items = news.collect(rec, {"INFY": "Infosys"}, {})
        self.assertEqual(len(items), 3)
        self.assertEqual(rec.urls, ["https://feeds.finance.yahoo.com/rss/2.0/headline?s=INFY.NS&region=IN&lang=en-IN"])
        self.assertNotIn("region=US", rec.urls[0])
        self.assertNotIn("INFY-NS", rec.urls[0])

    def test_google_news_is_asked_only_when_yahoo_is_empty(self):
        rec = Recorder({"yahoo-RELIANCE.xml": raw("yahoo-RELIANCE.xml"), "gnews-RELIANCE.xml": raw("gnews-RELIANCE.xml")})
        errors = []
        items = news.collect(rec, {"RELIANCE": "Reliance Industries"}, {}, errors)
        self.assertEqual(errors, [])
        self.assertEqual(len(items), 6)
        self.assertEqual({i["source"] for i in items}, {"Google News"})
        self.assertEqual(len(rec.urls), 2)
        self.assertTrue(rec.urls[0].startswith("https://feeds.finance.yahoo.com/"))
        self.assertEqual(rec.urls[1], "https://news.google.com/rss/search?q=%22Reliance%20Industries%22%20NSE%20share"
                                      "%20when%3A7d&hl=en-IN&gl=IN&ceid=IN:en")

    def test_google_query_falls_back_to_the_ticker_when_there_is_no_name(self):
        rec = Recorder({"yahoo-HDFCBANK.xml": EMPTY_RSS, "gnews-HDFCBANK.xml": raw("gnews-RELIANCE.xml")})
        news.collect(rec, {"HDFCBANK": None}, {})
        self.assertIn("q=%22HDFCBANK%22%20NSE%20share%20when%3A7d", rec.urls[1])

    def test_yahoo_with_items_means_no_google_request(self):
        rec = Recorder({"yahoo-INFY.xml": raw("yahoo-INFY.xml"), "gnews-INFY.xml": raw("gnews-RELIANCE.xml")})
        items = news.collect(rec, {"INFY": "Infosys"}, {})
        self.assertEqual({i["source"] for i in items}, {"Yahoo Finance"})
        self.assertEqual(len(rec.urls), 1)

    def test_a_yahoo_error_still_falls_back_and_is_noted(self):
        rec = Recorder({"gnews-RELIANCE.xml": raw("gnews-RELIANCE.xml")})       # no yahoo file: a fetch error
        errors = []
        items = news.collect(rec, {"RELIANCE": "Reliance Industries"}, {}, errors)
        self.assertEqual(len(items), 6)
        self.assertEqual(len(errors), 1)
        self.assertIn("headlines for RELIANCE", errors[0])
        rec = Recorder({})
        errors = []
        self.assertEqual(news.collect(rec, {"RELIANCE": None}, {}, errors), [])
        self.assertEqual(len(errors), 2)
        self.assertIn("Google News", errors[1])

    def test_symbols_with_an_ampersand_are_url_encoded(self):
        # M&M is a Nifty 50 stock: a bare & would end the s= parameter of the RSS query at "M"
        rec = Recorder({"yahoo-M&M.xml": raw("yahoo-INFY.xml")})
        news.collect(rec, {"M&M": "Mahindra & Mahindra"}, {})
        self.assertEqual(rec.urls[0], "https://feeds.finance.yahoo.com/rss/2.0/headline?s=M%26M.NS&region=IN&lang=en-IN")
        rec = Recorder({"chart-M&M.json": raw("chart-RELIANCE.json")})
        news.collect_moves(rec, {"M&M": None})
        self.assertEqual(rec.urls, ["https://query2.finance.yahoo.com/v8/finance/chart/M%26M.NS?range=1d&interval=5m"])

    def test_chart_asks_for_dot_ns_and_retries_on_query1_after_a_429(self):
        class Flaky(Recorder):
            def get(self, url, name):
                self.urls.append(url)
                if "query2" in url:
                    raise news.NewsError("HTTP 429 for %s" % url)
                return raw("chart-RELIANCE.json")

        rec = Flaky({})
        errors = []
        moves = news.collect_moves(rec, {"RELIANCE": None}, errors)
        self.assertEqual(errors, [])
        self.assertEqual(len(moves), 1)
        self.assertEqual(rec.urls, ["https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m",
                                    "https://query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m"])
        for u in rec.urls:
            self.assertNotIn("RELIANCE-NS", u)

    def test_a_404_is_an_error_note_not_a_retry(self):
        class Gone(Recorder):
            def get(self, url, name):
                self.urls.append(url)
                raise news.NewsError("HTTP 404 for %s" % url)

        rec = Gone({})
        errors = []
        self.assertEqual(news.collect_moves(rec, {"NOSUCH": None}, errors), [])
        self.assertEqual(len(rec.urls), 1)
        self.assertIn("price for NOSUCH", errors[0])

    def test_the_fetcher_has_no_sec_mode(self):
        with self.assertRaises(TypeError):
            news.Fetcher(None).get("https://example.org/x", "x", sec=True)
        with self.assertRaises(news.NewsError):
            news.Fetcher("/nonexistent-dir").get("https://example.org/x", "x")


class Tagging(unittest.TestCase):
    def tagged(self, name, ticker, source="Yahoo Finance"):
        return news.tag(news.parse_rss(raw(name), ticker, source=source), WATCH)

    def test_touches_and_tags(self):
        items = self.tagged("yahoo-INFY.xml", "INFY")
        by = {i["title"][:12]: i for i in items}
        cloud = by["Infosys (INF"]
        self.assertEqual(cloud["touches"], ["INFY"])
        self.assertIn("deal", cloud["tags"])
        both = by["Tata Consult"]
        self.assertEqual(both["touches"], ["INFY", "TCS"])         # one story, two of your tickers
        self.assertEqual(both["tags"], ["analyst"])
        ceo = by["Infosys CEO "]
        self.assertEqual(ceo["tags"], ["earnings", "people"])      # "guidance" and "CEO"

    def test_sebi_rbi_and_indian_corporate_actions_are_tagged(self):
        got = self.tagged("gnews-RELIANCE.xml", "RELIANCE", "Google News")

        def items(word):
            return [i for i in got if word in i["title"]][0]

        self.assertEqual(items("Q2 results")["tags"], ["earnings"])           # "results"
        sebi = items("SEBI approves")
        self.assertEqual(sebi["tags"], ["regulatory"])
        rbi = items("RBI repo rate")
        self.assertEqual(rbi["tags"], ["regulatory", "macro"])
        bonus = items("bonus shares")
        self.assertEqual(bonus["tags"], ["capital"])                                    # "bonus shares"
        self.assertEqual(items("retail arm")["tags"], [])
        self.assertEqual(items("Share Price Today")["tags"], [])                  # a quote page: no news tag

    def test_indian_regulators_and_events(self):
        def tags(title):
            it = [{"id": "x", "source": "Google News", "kind": "headline", "ticker": "RELIANCE", "title": title,
                   "summary": "", "link": "", "time": None}]
            return news.tag(it, {"RELIANCE": None})[0]["tags"]

        self.assertEqual(tags("SEBI charges promoter in insider trading probe"), ["legal", "regulatory"])
        self.assertIn("regulatory", tags("NCLT admits insolvency plea against Reliance unit"))
        self.assertIn("regulatory", tags("CCI clears Reliance Disney merger"))
        self.assertEqual(tags("RBI keeps repo rate unchanged"), ["regulatory", "macro"])
        self.assertIn("macro", tags("Repo Rate cut hopes lift banks"))
        self.assertIn("capital", tags("Board approves bonus shares and stock split"))
        self.assertIn("deal", tags("Reliance to acquire stake in new energy firm"))

    def test_day_trading_tags(self):
        items = [{"id": "x", "source": "Google News", "kind": "headline", "ticker": "RELIANCE",
                  "title": "Trading halted in XYZ after SEBI approval; futures rise before CPI",
                  "summary": "", "link": "", "time": None}]
        tags = news.tag(items, {"RELIANCE": None})[0]["tags"]
        for t in ("halt", "regulatory", "macro"):
            self.assertIn(t, tags)

    def test_mentions_use_nse_and_bse_not_us_exchanges(self):
        def touches(title, ticker="INFY"):
            it = [{"id": "x", "source": "s", "kind": "headline", "ticker": "OTHER", "title": title,
                   "summary": "", "link": "", "time": None}]
            return news.tag(it, {ticker: None})[0]["touches"]

        self.assertEqual(touches("Brokerages tip (INFY) ahead of results"), ["INFY"])
        self.assertEqual(touches("Stock to buy today: NSE: INFY"), ["INFY"])
        self.assertEqual(touches("Stock to buy today: BSE : INFY"), ["INFY"])
        self.assertEqual(touches("Stock to buy today: NYSE: INFY"), [])
        self.assertEqual(touches("Stock to buy today: NASDAQ: INFY"), [])
        self.assertEqual(touches("Infosys wins deal", "INFY"), [])               # no name given, only the ticker counts

    def test_dedupe_merges_the_same_story(self):
        a = news.parse_rss(raw("gnews-RELIANCE.xml"), "RELIANCE", source="Google News")
        b = news.parse_rss(raw("gnews-RELIANCE.xml"), "INFY", source="Google News")
        items = news.dedupe(news.tag(a + b, {"RELIANCE": "Reliance Industries", "INFY": None}))
        self.assertEqual(len(items), 6)
        self.assertEqual(items[0]["touches"], ["INFY", "RELIANCE"])


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "news.json")
        self.base = ["--offline", DIR, "--state", self.state, "--now", NOW,
                     "--portfolio", os.path.join(self.tmp.name, "none.json"),
                     "--watchlist", os.path.join(self.tmp.name, "none.json"),
                     "--plan", os.path.join(self.tmp.name, "none.json")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_items_once_newest_first(self):
        code, out, err = run(["--tickers", "RELIANCE", "INFY", "--hours", "24", "--json"] + self.base)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        times = [i["time"] for i in data["items"]]
        self.assertEqual(times, sorted(times, reverse=True))
        # 4 Reliance (the Oct 31 story is too old, the 11:00 IST one is after the pinned clock) + 3 Infosys
        self.assertEqual(len(data["items"]), 7)
        self.assertEqual(data["checked"], "2026-11-06T04:45:00+00:00")
        code, out, _ = run(["--tickers", "RELIANCE", "INFY", "--hours", "24"] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)

    def test_the_wider_window_shows_older_stories_in_ist(self):
        code, out, err = run(["--tickers", "RELIANCE", "--hours", "2400"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertIn("2026-10-31 15:30 IST", out)             # 10:00 UTC is 15:30 IST
        self.assertIn("2026-11-05 20:00 IST", out)             # 14:30 UTC is 20:00 IST
        self.assertIn("Reliance retail arm opens 200 new stores", out)
        self.assertIn("Not financial advice", out)
        self.assertNotIn(chr(0x2014), out)
        self.assertNotIn("$", out)

    def test_the_morning_check_at_1015_ist(self):
        # Fri 2026-11-06, checked at 10:15 IST: the 11:00 IST bonus story is after the clock and must not show
        code, out, err = run(["--tickers", "RELIANCE", "--hours", "6", "--no-moves"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertIn("2026-11-06 08:40 IST  RELIANCE", out)       # 03:10 UTC
        self.assertIn("2026-11-06 07:30 IST", out)                 # 02:00 UTC
        self.assertIn("2026-11-06 09:30 IST", out)                 # 04:00 UTC
        self.assertNotIn("11:00 IST", out)
        self.assertNotIn("bonus shares", out)
        self.assertNotIn("Moneycontrol", out)                      # the Nov 5 results story is older than 6 hours
        self.assertIn("SEBI approves Reliance Jio IPO draft papers", out)
        lines = out.splitlines()
        sebi = lines[[i for i, l in enumerate(lines) if "SEBI approves" in l][0] + 1]
        self.assertIn("regulatory", sebi)
        rbi = lines[[i for i, l in enumerate(lines) if "RBI repo rate" in l][0] + 1]
        self.assertIn("regulatory", rbi)
        self.assertIn("macro", rbi)
        self.assertIn("Checked 2026-11-06 10:15 IST", out)
        self.assertEqual(len([l for l in lines if l.startswith("  2026-11-06")]), 3)
        self.assertIn("Google News", out)
        for us in (" ET", "New York", "EDT", "EST"):
            self.assertNotIn(us, out.replace("Moneycontrol", ""), us)

    def test_the_price_line_is_in_rupees_with_the_ist_time(self):
        code, out, err = run(["--tickers", "RELIANCE", "INFY", "--hours", "6", "--dry-run",
                              "--now", "2026-11-06T10:52:00+05:30"] + self.base[:4] + self.base[6:])
        self.assertRegex(out, r"RELIANCE ₹1,354\.60 \+3\.14% vs yesterday's close ₹1,313\.40 at 10:52 IST  MOVING")
        self.assertRegex(out, r"INFY\s+₹1,482\.10 \+0\.26% vs yesterday's close ₹1,478\.30 at 10:50 IST\s*\n")
        self.assertNotIn("$", out)

    def test_moves(self):
        m = news.parse_chart(raw("chart-RELIANCE.json"), "RELIANCE")
        quiet = dict(m, pct=1.2)
        self.assertNotIn("MOVING", news.render_moves([quiet])[0])
        self.assertIn("MOVING", news.render_moves([quiet], move_pct=1.0)[0])
        self.assertIn("MOVING", news.render_moves([dict(m, pct=-3.5)])[0])             # a drop is a move too
        code, out, _ = run(["--tickers", "TCS", "--hours", "24", "--dry-run"] + self.base)   # no chart file for TCS
        self.assertNotIn("vs yesterday's close", out)
        code, out, _ = run(["--tickers", "RELIANCE", "--hours", "6", "--dry-run", "--json"] + self.base)
        data = json.loads(out)
        self.assertEqual(data["moves"][0]["ticker"], "RELIANCE")
        self.assertIs(data["moves"][0]["moving"], True)
        self.assertEqual(data["moves"][0]["time"], "2026-11-06T05:22:00+00:00")

    def test_tickers_from_watchlist_and_plan(self):
        wl = os.path.join(self.tmp.name, "watchlist.json")
        with open(wl, "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [{"symbol": "RELIANCE", "setups": ["orb"]}]}, fh)
        plan = os.path.join(self.tmp.name, "plan.json")
        with open(plan, "w") as fh:
            json.dump({"created_by": "trader-exit", "symbol": "INFY"}, fh)
        self.assertEqual(sorted(news.watch_from_files("/nonexistent.json", wl, plan)), ["INFY", "RELIANCE"])
        args = list(self.base)
        args[args.index("--watchlist") + 1] = wl
        code, out, err = run(["--json", "--dry-run", "--hours", "24"] + args)
        self.assertEqual(code, 0, err)
        tickers = {t for i in json.loads(out)["items"] for t in i["touches"]}
        self.assertEqual(tickers, {"RELIANCE"})
        self.assertFalse(os.path.exists(self.state))

    def test_portfolio_positions_are_watched(self):
        pf = os.path.join(self.tmp.name, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"cash": 100000, "positions": [{"ticker": "INFY", "name": "Infosys"}]}, fh)
        self.assertEqual(news.watch_from_files(pf, "/none.json", "/none.json"), {"INFY": "Infosys"})

    def test_no_tickers_is_an_error(self):
        code, _, err = run(self.base)
        self.assertEqual(code, 2)
        self.assertIn("no tickers", err)

    def test_sec_options_are_gone(self):
        for flag in ("--no-sec", "--forms", "--user-agent"):
            err = io.StringIO()
            with redirect_stderr(err), self.assertRaises(SystemExit):
                news.main(["--tickers", "RELIANCE", flag, "x"] + self.base)
            self.assertIn("unrecognized arguments", err.getvalue(), flag)

    def test_state_forgets_after_30_days(self):
        state = {"seen": {"old": "2026-08-01T00:00:00+00:00"}}
        now = news.parse_time(NOW)
        news.remember([], state, now)
        self.assertNotIn("old", state["seen"])
        self.assertEqual(state["last_check"], now.isoformat())


class IndiaNotAmerica(unittest.TestCase):
    def test_ist_is_a_fixed_plus_530_with_no_daylight_saving(self):
        self.assertEqual(news.IST_OFFSET, datetime.timedelta(hours=5, minutes=30))
        for month in (1, 7):
            utc = datetime.datetime(2026, month, 15, 3, 45, tzinfo=datetime.timezone.utc)
            self.assertEqual(news.to_ist(utc), datetime.datetime(2026, month, 15, 9, 15))
        self.assertEqual(news.to_ist(datetime.datetime(2026, 11, 5, 20, 0)), datetime.datetime(2026, 11, 6, 1, 30))
        aware_ist = datetime.datetime(2026, 11, 6, 9, 15, tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
        self.assertEqual(news.to_ist(aware_ist), datetime.datetime(2026, 11, 6, 9, 15))

    def test_the_sec_and_new_york_code_is_gone(self):
        for name in ("parse_sec_atom", "cik_map", "sec_user_agent", "ITEMS_8K", "IMPORTANT_ITEMS", "SEC_FEED",
                     "TICKERS_URL", "PLACEHOLDER", "to_eastern", "eastern_offset"):
            self.assertFalse(hasattr(news, name), name)
        with open(SRC, encoding="utf-8") as fh:
            src = fh.read().lower()
        for word in ("sec.gov", "edgar", "8-k", "new york", "eastern", "nyse", "nasdaq", "region=us", "company_tickers"):
            self.assertNotIn(word, src, word)
        self.assertIn("region=in", src)

    def test_no_us_fixtures_are_left(self):
        names = os.listdir(DIR)
        for bad in ("company_tickers.json", "chart-TSLA.json", "yahoo-AAPL.xml", "yahoo-KO.xml", "yahoo-TSLA.xml"):
            self.assertNotIn(bad, names)
        self.assertFalse([n for n in names if n.startswith("sec-")])
        for n in names:
            with open(os.path.join(DIR, n), encoding="utf-8") as fh:
                self.assertIn("ynthetic", fh.read(400), n)         # every fixture says it is synthetic


if __name__ == "__main__":
    unittest.main()
