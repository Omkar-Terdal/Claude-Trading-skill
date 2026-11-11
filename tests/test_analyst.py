import io
import json
import os
import tempfile
import types
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

facts = load("trader-analyst", "facts")
DIR = os.path.join(FIX, "trader-analyst")      # synthetic quoteSummary fixtures, see the comment inside each file


def fixture(yahoo_sym):
    with open(os.path.join(DIR, "quoteSummary-%s.json" % yahoo_sym), encoding="utf-8") as fh:
        return fh.read()


def http_error(url, code):
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))


class FakeYahoo(object):
    """Stands in for the cookie-carrying opener. Like the real Yahoo it refuses quoteSummary without a valid crumb."""

    def __init__(self, crumb="Cr1mbX9", codes=None, crumb_down=False):
        self.crumb, self.codes, self.crumb_down = crumb, list(codes or []), crumb_down
        self.log, self.addheaders = [], []

    def open(self, target, timeout=None):
        url = target if isinstance(target, str) else target.full_url
        self.log.append(url)
        if "fc.yahoo.com" in url:
            raise http_error(url, 404)                       # the real one answers 404 but still sets the cookie
        if "getcrumb" in url:
            if self.crumb_down:
                raise urllib.error.URLError("offline")
            return io.BytesIO(self.crumb.encode("utf-8"))
        host = url.split("/")[2].split(".")[0]
        code = self.codes.pop(0) if self.codes else 200
        if code != 200:
            raise http_error(url, code)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if not self.crumb or query.get("crumb") != [self.crumb]:
            raise http_error(url, 401)
        sym = urllib.parse.unquote(url.split("/quoteSummary/")[1].split("?")[0])
        path = os.path.join(DIR, "quoteSummary-%s.json" % sym)
        if not os.path.exists(path):
            raise http_error(url, 404)
        self.log.append("served " + host)
        return io.BytesIO(fixture(sym).encode("utf-8"))


class Offline(unittest.TestCase):
    """Every test runs against FakeYahoo: the real urllib opener is never built."""

    def setUp(self):
        self.saved = (facts.urllib.request.build_opener, facts.time, dict(facts._SESSION))
        facts._SESSION.clear()
        self.yahoo = FakeYahoo()
        self.built = 0
        outer = self

        def build(*handlers):
            outer.built += 1
            return outer.yahoo
        facts.urllib.request.build_opener = build
        facts.time = types.SimpleNamespace(sleep=lambda s: None)

    def tearDown(self):
        facts.urllib.request.build_opener, facts.time = self.saved[0], self.saved[1]
        facts._SESSION.clear()
        facts._SESSION.update(self.saved[2])

    def served(self):
        return [u for u in self.yahoo.log if u.startswith("served")]

    def quote_summary_urls(self):
        return [u for u in self.yahoo.log if "/quoteSummary/" in u and not u.startswith("served")]


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = facts.main(args)
    return code, out.getvalue(), err.getvalue()


class Symbols(unittest.TestCase):
    def test_ns_is_added_once_and_never_becomes_a_dash(self):
        pick = facts._nse_yahoo_sym
        self.assertEqual(pick("RELIANCE"), "RELIANCE.NS")
        self.assertEqual(pick(" reliance "), "RELIANCE.NS")
        self.assertEqual(pick("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(pick("tcs.ns"), "TCS.NS")
        self.assertEqual(pick("TCS.BO"), "TCS.BO")
        self.assertEqual(pick("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(pick("M&M"), "M&M.NS")
        for sym in ("RELIANCE", "RELIANCE.NS", "HDFCBANK", "M&M", "BAJAJ-AUTO"):
            self.assertNotIn("-NS", pick(sym), sym)

    def test_rupee_amounts_use_crore_and_lakh(self):
        self.assertEqual(facts._inr(1e7), "₹1.00 Cr")
        self.assertEqual(facts._inr(438510000000), "₹43,851.00 Cr")
        self.assertEqual(facts._inr(150000), "₹1.50 L")
        self.assertEqual(facts._inr(999.5), "₹999.50")
        self.assertEqual(facts._inr(-2.5e7), "₹-2.50 Cr")
        self.assertEqual(facts._inr(None), "N/A")
        self.assertEqual(facts._inr_m(438510000000), "₹43851Cr")
        self.assertEqual(facts._inr_m(250000), "₹2L")
        self.assertEqual(facts._inr_m(None), "-")
        self.assertNotIn("$", facts._inr(5e9) + facts._inr_m(5e9))

    def test_a_foreign_currency_total_is_labelled_not_converted(self):
        self.assertEqual(facts._ttm_money(20366999552, "USD"), "USD 20.37B")
        self.assertEqual(facts._ttm_money(3.5e6, "USD"), "USD 3.50M")
        self.assertEqual(facts._ttm_money(12.5, "USD"), "USD 12.50")
        self.assertEqual(facts._ttm_money(1.2e11, "INR"), "₹12,000.00 Cr")
        self.assertEqual(facts._ttm_money(None, "USD"), "N/A")

    def test_growth_helpers(self):
        self.assertEqual(facts.pct(110, 100), 10.0)
        self.assertEqual(facts.pct(90, -100), 190.0)
        self.assertIsNone(facts.pct(5, 0))
        self.assertIsNone(facts.pct(None, 5))
        self.assertEqual(facts.margin(25, 200), 12.5)
        self.assertIsNone(facts.margin(1, 0))
        self.assertIsNone(facts.margin(None, 100))


class YahooSession(Offline):
    def test_the_cookie_and_crumb_come_first_then_quote_summary_carries_the_crumb(self):
        facts.facts_for("RELIANCE")
        self.assertEqual(self.built, 1)
        self.assertTrue(self.yahoo.log[0].startswith("https://fc.yahoo.com"))
        self.assertEqual(self.yahoo.log[1], "https://query1.finance.yahoo.com/v1/test/getcrumb")
        urls = self.quote_summary_urls()
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].startswith("https://query2.finance.yahoo.com/v10/finance/quoteSummary/RELIANCE.NS?"))
        self.assertIn("crumb=Cr1mbX9", urls[0])
        self.assertIn("modules=financialData,incomeStatementHistory,defaultKeyStatistics", urls[0])

    def test_one_session_serves_every_ticker(self):
        facts.facts_for("RELIANCE")
        facts.facts_for("HDFCBANK")
        self.assertEqual(self.built, 1)
        self.assertEqual(sum("getcrumb" in u for u in self.yahoo.log), 1)
        self.assertEqual(len(self.served()), 2)

    def test_a_crumb_with_special_characters_is_url_encoded(self):
        self.yahoo = FakeYahoo(crumb="a/b+c=")
        facts.facts_for("RELIANCE")
        # "+" and "=" must be escaped (a bare "+" would read as a space); "/" is legal inside a query string
        self.assertTrue(self.quote_summary_urls()[0].endswith("&crumb=a/b%2Bc%3D"), self.quote_summary_urls()[0])
        self.assertEqual(len(self.served()), 1)

    def test_without_a_crumb_yahoo_says_401(self):
        self.yahoo = FakeYahoo(crumb="")
        with self.assertRaises(facts.FactsError) as ctx:
            facts.facts_for("RELIANCE")
        self.assertIn("HTTP 401 for RELIANCE.NS", str(ctx.exception))
        self.assertEqual(len(self.quote_summary_urls()), 2)          # query2 then query1, both refused

    def test_no_crumb_endpoint_is_a_clear_error(self):
        self.yahoo = FakeYahoo(crumb_down=True)
        with self.assertRaises(facts.FactsError) as ctx:
            facts.facts_for("RELIANCE")
        self.assertIn("could not get a Yahoo Finance crumb", str(ctx.exception))
        self.assertEqual(self.quote_summary_urls(), [])

    def test_a_5xx_on_query2_moves_to_query1(self):
        self.yahoo = FakeYahoo(codes=[503, 200])
        got = facts.facts_for("RELIANCE")
        self.assertEqual(got["name"], "Reliance Industries Limited")
        urls = self.quote_summary_urls()
        self.assertEqual([u.split("/")[2].split(".")[0] for u in urls], ["query2", "query1"])

    def test_both_hosts_failing_reports_the_last_status(self):
        self.yahoo = FakeYahoo(codes=[500, 502])
        with self.assertRaises(facts.FactsError) as ctx:
            facts.facts_for("RELIANCE")
        self.assertIn("HTTP 502 for RELIANCE.NS", str(ctx.exception))

    def test_a_404_means_check_the_ticker_and_is_not_retried(self):
        with self.assertRaises(facts.FactsError) as ctx:
            facts.facts_for("ZZZZ")
        self.assertIn("no data for ZZZZ.NS (check the ticker)", str(ctx.exception))
        self.assertEqual(len(self.quote_summary_urls()), 1)

    def test_yahoos_own_error_json_and_an_empty_result_are_errors(self):
        for body, want in (({"quoteSummary": {"result": None, "error": {"code": "Bad", "description": "nope"}}}, "error"),
                           ({"quoteSummary": {"result": [], "error": None}}, "empty result")):
            self.yahoo.open = lambda target, timeout=None, b=body: io.BytesIO(json.dumps(b).encode("utf-8")) \
                if "/quoteSummary/" in getattr(target, "full_url", target) else io.BytesIO(b"Cr1mbX9")
            facts._SESSION.clear()
            with self.assertRaises(facts.FactsError) as ctx:
                facts.facts_for("RELIANCE")
            self.assertIn(want, str(ctx.exception))

    def test_no_sec_left_in_the_module(self):
        for name in ("user_agent", "Fetcher", "analyse", "latest_filings", "exhibit_name", "SEC_USER_AGENT"):
            self.assertFalse(hasattr(facts, name), name)
        with open(os.path.join(os.path.dirname(os.path.abspath(facts.__file__)), "facts.py"), encoding="utf-8") as fh:
            src = fh.read()
        for word in ("sec.gov", "us-gaap", "xbrl", "edgar"):
            self.assertNotIn(word, src.lower(), word)


class Reliance(Offline):
    def setUp(self):
        Offline.setUp(self)
        self.f = facts.facts_for("RELIANCE")

    def test_identity_and_currency(self):
        self.assertEqual((self.f["sym"], self.f["yahoo_sym"]), ("RELIANCE", "RELIANCE.NS"))
        self.assertEqual((self.f["name"], self.f["currency"]), ("Reliance Industries Limited", "INR"))

    def test_trailing_twelve_months(self):
        t = self.f["ttm"]
        self.assertEqual(t["revenue"], 11990330245120)
        self.assertEqual(t["gross_profit"], 3836410068992)
        self.assertEqual(t["ebitda"], 1868889980928)
        self.assertEqual((t["eps"], t["eps_forward"]), (60.68, 73.7244))
        self.assertEqual((t["revenue_growth_yoy"], t["earnings_growth_yoy"]), (29.8, 40.7))
        self.assertEqual(t["return_on_equity"], 8.8)
        self.assertEqual(t["target_price"], 1666.0)
        self.assertEqual(t["recommendation"], "strong_buy")

    def test_ttm_net_income_comes_from_the_key_statistics_when_financial_data_lacks_it(self):
        self.assertEqual(self.f["ttm"]["net_income"], 821100019712)

    def test_annual_rows_and_growth(self):
        rows = self.f["annual"]
        self.assertEqual([r["end"] for r in rows], ["2026-03-31", "2025-03-31", "2024-03-31", "2023-03-31"])
        top = rows[0]
        self.assertEqual((top["revenue"], top["net_income"]), (10572190000000, 438510000000))
        self.assertEqual(top["revenue_yoy"], 104.4)            # 10.57T vs 5.17T
        self.assertEqual(top["net_income_yoy"], 24.4)
        self.assertEqual(top["net_margin"], 4.1)
        self.assertIsNone(rows[-1]["revenue_yoy"])             # nothing earlier to compare with

    def test_a_zero_gross_profit_means_not_reported(self):
        top = self.f["annual"][0]
        self.assertIsNone(top["gross_profit"])
        self.assertIsNone(top["gross_margin"])
        self.assertIsNone(top["operating_income"])
        self.assertIsNone(top["operating_margin"])

    def test_render_is_in_rupees(self):
        text = facts.render(self.f)
        self.assertIn("Reliance Industries Limited (RELIANCE)  |  Yahoo: RELIANCE.NS  |  Currency: INR", text)
        self.assertIn("Revenue        : ₹1,199,033.02 Cr", text)
        self.assertIn("Net Income     : ₹82,110.00 Cr", text)
        self.assertIn("EPS (TTM)      : ₹60.68", text)
        self.assertIn("EPS (Forward)  : ₹73.72", text)
        self.assertIn("Gross    : 32.0%", text)
        self.assertIn("Revenue  : +29.8%", text)
        self.assertIn("Analyst mean target (Yahoo's survey, not our view): ₹1666.00", text)
        self.assertIn("2026-03-31", text)
        self.assertNotIn("$", text)
        self.assertNotIn("NOTE: Yahoo reports", text)
        self.assertNotIn(chr(0x2014), text)

    def test_what_changed_in_words_and_the_sanity_flag(self):
        lines = facts.changes(self.f)
        self.assertEqual(lines[0], "Year ending 2026-03-31: revenue ₹1,057,219.00 Cr (+104.4% vs a year earlier), "
                                   "net income ₹43,851.00 Cr (+24.4%).")
        self.assertIn("Net margin 4.1% (-2.7 points vs prior year).", lines)     # 4.1% against 6.8% a year earlier
        check = [l for l in lines if l.startswith("CHECK: revenue moved +104% in a year.")]
        self.assertEqual(len(check), 1)
        self.assertIn("nseindia.com", check[0])
        self.assertIn("TTM revenue growth (YoY): +29.8%.", lines)
        self.assertIn("TTM earnings growth (YoY): +40.7%.", lines)
        self.assertFalse(any(l.startswith("Gross margin") for l in lines))     # not reported, so not shown
        self.assertFalse(any("$" in l for l in lines))

    def test_a_normal_year_gets_no_sanity_flag(self):
        quiet = {"ttm": self.f["ttm"], "annual": [dict(self.f["annual"][0], revenue_yoy=9.6)]}
        self.assertFalse(any(l.startswith("CHECK") for l in facts.changes(quiet)))
        self.assertEqual(facts.changes({"ttm": self.f["ttm"], "annual": []}), [])


class Infosys(Offline):
    """Yahoo reports Infosys's totals in USD but its share price and EPS in rupees."""

    def setUp(self):
        Offline.setUp(self)
        self.f = facts.facts_for("INFY")

    def test_totals_are_labelled_usd_and_per_share_numbers_are_rupees(self):
        self.assertEqual(self.f["currency"], "USD")
        text = facts.render(self.f)
        self.assertIn("NOTE: Yahoo reports this company's TTM figures in USD; the annual table below is in ₹.", text)
        self.assertIn("Revenue        : USD 20.37B", text)
        self.assertIn("EBITDA         : USD 4.90B", text)
        self.assertIn("Net Income     : USD 3.30B", text)
        self.assertNotIn("₹20", text.split("Revenue        :")[1].split("\n")[0])
        # trailingEps 75.1 against a Rs 1181.40 share price is a P/E near 16: it is rupees, not dollars
        self.assertIn("EPS (TTM)      : ₹75.10", text)
        self.assertIn("EPS (Forward)  : ₹79.90", text)
        self.assertNotIn("USD 75.10", text)

    def test_the_annual_table_is_in_rupees(self):
        text = facts.render(self.f)
        table = text.split("=== Annual History ===")[1]
        self.assertIn("2026-03-31      ₹178650Cr", table)
        self.assertNotIn("USD", table)

    def test_a_flat_growth_never_prints_a_minus_zero(self):
        self.assertEqual(self.f["ttm"]["earnings_growth_yoy"], 0.0)
        lines = facts.changes(self.f)
        self.assertIn("TTM earnings growth (YoY): +0.0%.", lines)
        self.assertNotIn("-0.0", " ".join(lines) + facts.render(self.f))


class Hdfcbank(Offline):
    def setUp(self):
        Offline.setUp(self)
        self.f = facts.facts_for("hdfcbank")

    def test_a_bank_has_no_ebitda_or_gross_profit_and_says_na(self):
        self.assertEqual(self.f["yahoo_sym"], "HDFCBANK.NS")
        text = facts.render(self.f)
        self.assertIn("EBITDA         : N/A", text)
        self.assertIn("Free Cash Flow : N/A", text)
        self.assertIn("Debt/Equity      : N/A", text)
        self.assertIn("Current Ratio    : N/A", text)
        self.assertNotIn("Analyst mean target", text)
        self.assertEqual(self.f["ttm"]["net_income"], 801021296640)
        self.assertEqual(self.f["ttm"]["revenue_growth_yoy"], 80.8)

    def test_the_big_annual_revenue_jump_is_flagged(self):
        top = self.f["annual"][0]
        self.assertEqual(top["revenue_yoy"], -33.4)
        self.assertEqual(self.f["annual"][1]["revenue_yoy"], 81.9)        # a +82% step further back: not the latest row
        lines = facts.changes(self.f)
        self.assertFalse(any(l.startswith("CHECK") for l in lines))        # -33% is under the 50% flag
        jumpy = {"ttm": self.f["ttm"], "annual": [dict(top, revenue_yoy=-66.0)]}
        self.assertTrue(any(l.startswith("CHECK: revenue moved -66%") for l in facts.changes(jumpy)))


class Ampersand(Offline):
    def test_m_and_m_survives_the_url(self):
        f = facts.facts_for("M&M")
        self.assertEqual((f["sym"], f["yahoo_sym"], f["name"]), ("M&M", "M&M.NS", "Mahindra & Mahindra Limited"))
        url = self.quote_summary_urls()[0]
        self.assertIn("/quoteSummary/M%26M.NS?", url)
        self.assertNotIn("M-NS", url)
        self.assertEqual(f["ttm"]["eps"], 118.4)
        self.assertEqual(f["annual"][0]["revenue_yoy"], 13.6)


class Cli(Offline):
    def test_metrics_file_for_the_screener(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "trader", "metrics.json")
            code, out, err = run(["RELIANCE", "HDFCBANK", "--metrics", path])
            self.assertEqual(code, 0, err)
            with open(path) as fh:
                data = json.load(fh)
            rows = {r["ticker"]: r for r in data["companies"]}
            self.assertEqual(sorted(rows), ["HDFCBANK", "RELIANCE"])
            self.assertEqual(rows["RELIANCE"]["eps_ttm"], 60.68)
            self.assertEqual(rows["RELIANCE"]["net_income_ttm"], 821100019712)
            self.assertEqual(rows["RELIANCE"]["revenue_growth_yoy"], 29.8)
            self.assertEqual(rows["RELIANCE"]["period_end"], "2026-03-31")
            self.assertEqual(rows["RELIANCE"]["yahoo_sym"], "RELIANCE.NS")
            self.assertEqual(rows["RELIANCE"]["currency"], "INR")
            self.assertEqual(rows["RELIANCE"]["gross_margin"], 32.0)
            self.assertIn("wrote", out)
            self.assertNotIn(chr(0x2014), out)
            code, out, err = run(["M&M", "--metrics", path])             # merges, keeps the others, stays sorted
            self.assertEqual(code, 0, err)
            with open(path) as fh:
                merged = json.load(fh)
            self.assertEqual([r["ticker"] for r in merged["companies"]], ["HDFCBANK", "M&M", "RELIANCE"])
            self.assertIn("wrote %s (3 companies)" % path, out)

    def test_unknown_ticker_exits_2_with_a_message(self):
        code, out, err = run(["ZZZZ"])
        self.assertEqual(code, 2)
        self.assertIn("facts: ZZZZ: Yahoo Finance: no data for ZZZZ.NS (check the ticker)", err)
        self.assertEqual(out, "")

    def test_one_good_and_one_bad_ticker_exits_1_and_still_prints_the_good_one(self):
        code, out, err = run(["RELIANCE", "ZZZZ"])
        self.assertEqual(code, 1)
        self.assertIn("Reliance Industries Limited (RELIANCE)", out)
        self.assertIn("ZZZZ", err)

    def test_no_tickers_prints_help_and_exits_2(self):
        code, out, err = run([])
        self.assertEqual(code, 2)
        self.assertIn("NSE tickers", out)

    def test_changes_flag_prints_the_plain_sentences(self):
        code, out, err = run(["RELIANCE", "--changes"])
        self.assertEqual(code, 0, err)
        self.assertIn("What changed:", out)
        self.assertIn("  - Year ending 2026-03-31: revenue", out)
        self.assertIn("  - CHECK: revenue moved +104%", out)

    def test_json_output_and_quarters_trim(self):
        code, out, err = run(["RELIANCE", "INFY", "--json", "--quarters", "2"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual([d["sym"] for d in data], ["RELIANCE", "INFY"])
        self.assertEqual(len(data[0]["annual"]), 2)
        self.assertEqual(data[1]["currency"], "USD")
        self.assertEqual(data[0]["ttm"]["eps"], 60.68)

    def test_default_shows_four_annual_periods(self):
        code, out, err = run(["RELIANCE", "--json"])
        self.assertEqual(len(json.loads(out)[0]["annual"]), 4)


if __name__ == "__main__":
    unittest.main()
