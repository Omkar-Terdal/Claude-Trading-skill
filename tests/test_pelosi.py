import io
import json
import os
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

ptr = load("trader-pelosi", "ptr")
DIR = os.path.join(FIX, "trader-pelosi")


def text(name):
    with open(os.path.join(DIR, name), encoding="utf-8") as fh:
        return fh.read()


BULK = text("bulk-sample.csv")
BLOCK = text("block-sample.csv")


class Net:
    """Replaces ptr._get: serves canned CSV by URL, records the calls, never touches the network."""

    def __init__(self, bulk=BULK, block=BLOCK):
        self.pages = {ptr.BULK_URL: bulk, ptr.BLOCK_URL: block}
        self.calls = []

    def __call__(self, url, timeout=30):
        self.calls.append(url)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page

    def __enter__(self):
        self.old = ptr._get
        ptr._get = self
        return self

    def __exit__(self, *exc):
        ptr._get = self.old


def run(args, **pages):
    out, err = io.StringIO(), io.StringIO()
    with Net(**pages) as net, redirect_stdout(out), redirect_stderr(err):
        code = ptr.main(args)
    return code, out.getvalue(), err.getvalue(), net


class Parsing(unittest.TestCase):
    def test_bulk_columns_are_read_with_the_real_nse_names(self):
        with Net():
            deals = ptr.fetch_deals(ptr.BULK_URL, "bulk")
        self.assertEqual(len(deals), 3)
        self.assertEqual(deals[0], {
            "kind": "bulk", "symbol": "RELIANCE", "security": "Reliance Industries Limited",
            "client": "ALPHA TEST FUND, LLP", "side": "buy", "qty": "1250000", "price": "1311.90",
            "date": "06-NOV-2026"})
        self.assertEqual((deals[1]["symbol"], deals[1]["side"], deals[1]["qty"], deals[1]["price"]),
                         ("TCS", "sell", "800000", "3820.55"))

    def test_symbols_with_an_ampersand_survive(self):
        with Net():
            deals = ptr.fetch_deals(ptr.BULK_URL, "bulk")
        self.assertEqual(deals[2]["symbol"], "M&M")
        self.assertEqual(deals[2]["security"], "Mahindra & Mahindra Limited")

    def test_quoted_commas_stay_inside_the_client_name_and_quantity_commas_are_dropped(self):
        with Net():
            deals = ptr.fetch_deals(ptr.BULK_URL, "bulk")
        self.assertEqual(deals[0]["client"], "ALPHA TEST FUND, LLP")
        self.assertEqual(deals[0]["qty"], "1250000")
        with Net(block=BLOCK):
            blocks = ptr.fetch_deals(ptr.BLOCK_URL, "block")
        self.assertEqual(blocks[1]["qty"], "2000000")

    def test_block_deals_are_tagged_block(self):
        with Net():
            deals = ptr.fetch_deals(ptr.BLOCK_URL, "block")
        self.assertEqual([d["kind"] for d in deals], ["block", "block"])
        self.assertEqual([d["symbol"] for d in deals], ["RELIANCE", "INFY"])
        self.assertEqual(deals[1]["side"], "buy")

    def test_the_older_column_spellings_still_parse(self):
        old = ("Date,Symbol,Security Name,Client Name,Buy / Sell,Quantity Traded,Wt. Avg. Price\n"
               "06-NOV-2026,ITC,ITC Limited,OMEGA TEST LLP,SELL,10000,412.35\n")
        with Net(bulk=old):
            d = ptr.fetch_deals(ptr.BULK_URL, "bulk")[0]
        self.assertEqual((d["symbol"], d["side"], d["qty"], d["price"]), ("ITC", "sell", "10000", "412.35"))

    def test_lowercase_symbols_are_uppercased_and_blank_lines_ignored(self):
        csv_text = ("Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price\n"
                    "\n"
                    "06-NOV-2026,sbin,State Bank of India,SIGMA TEST,BUY,5000,812.40\n"
                    "\n")
        with Net(bulk=csv_text):
            deals = ptr.fetch_deals(ptr.BULK_URL, "bulk")
        self.assertEqual([d["symbol"] for d in deals], ["SBIN"])

    def test_a_header_only_file_is_no_deals_not_an_error(self):
        head = BULK.splitlines()[0] + "\n"
        with Net(bulk=head):
            self.assertEqual(ptr.fetch_deals(ptr.BULK_URL, "bulk"), [])

    def test_a_short_row_does_not_crash(self):
        short = BULK.splitlines()[0] + "\n06-NOV-2026,RELIANCE\n"
        with Net(bulk=short):
            deals = ptr.fetch_deals(ptr.BULK_URL, "bulk")
        self.assertEqual(len(deals), 1)
        self.assertEqual((deals[0]["symbol"], deals[0]["qty"], deals[0]["price"]), ("RELIANCE", "", ""))

    def test_an_html_error_page_is_an_error_not_phantom_deals(self):
        html = "<html><head><title>Access Denied</title></head>\n<body>\nNo permission\n</body></html>\n"
        with Net(bulk=html):
            with self.assertRaises(ptr.PTRError) as cm:
                ptr.fetch_deals(ptr.BULK_URL, "bulk")
        self.assertIn("did not send a deals CSV", str(cm.exception))

    def test_an_empty_body_is_an_error(self):
        with Net(bulk=""):
            with self.assertRaises(ptr.PTRError):
                ptr.fetch_deals(ptr.BULK_URL, "bulk")


class Transport(unittest.TestCase):
    def test_the_urls_are_the_nse_archive(self):
        self.assertEqual(ptr.BULK_URL, "https://archives.nseindia.com/content/equities/bulk.csv")
        self.assertEqual(ptr.BLOCK_URL, "https://archives.nseindia.com/content/equities/block.csv")

    def test_http_errors_become_one_line_errors(self):
        def boom(req, timeout=30):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
        old = ptr.urllib.request.urlopen
        ptr.urllib.request.urlopen = boom
        try:
            with self.assertRaises(ptr.PTRError) as cm:
                ptr._get(ptr.BULK_URL)
        finally:
            ptr.urllib.request.urlopen = old
        self.assertIn("HTTP 403", str(cm.exception))
        self.assertIn("bulk.csv", str(cm.exception))

    def test_network_failures_become_one_line_errors(self):
        def boom(req, timeout=30):
            raise urllib.error.URLError("name resolution failed")
        old = ptr.urllib.request.urlopen
        ptr.urllib.request.urlopen = boom
        try:
            with self.assertRaises(ptr.PTRError) as cm:
                ptr._get(ptr.BLOCK_URL)
        finally:
            ptr.urllib.request.urlopen = old
        self.assertIn("Could not reach NSE archive", str(cm.exception))
        self.assertIn("name resolution failed", str(cm.exception))

    def test_the_request_carries_a_user_agent(self):
        seen = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"Symbol\n"

        def spy(req, timeout=30):
            seen["ua"] = req.get_header("User-agent")
            return Resp()
        old = ptr.urllib.request.urlopen
        ptr.urllib.request.urlopen = spy
        try:
            self.assertEqual(ptr._get(ptr.BULK_URL), "Symbol\n")
        finally:
            ptr.urllib.request.urlopen = old
        self.assertIn("day-trader-skill", seen["ua"])


class Cli(unittest.TestCase):
    def test_default_shows_bulk_then_block_in_rupees(self):
        code, out, err, net = run([])
        self.assertEqual(code, 0)
        self.assertEqual(net.calls, [ptr.BULK_URL, ptr.BLOCK_URL])
        lines = out.splitlines()
        self.assertEqual(lines[0], "[BULK] 06-NOV-2026 BUY   RELIANCE  qty 1250000 @ ₹1311.90  ALPHA TEST FUND, LLP")
        self.assertIn("[BULK] 06-NOV-2026 SELL  TCS  qty 800000 @ ₹3820.55  BETA SAMPLE TRUST", out)
        self.assertIn("[BLOCK] 06-NOV-2026 SELL  RELIANCE  qty 500000 @ ₹1312.40  DELTA SYNTHETIC CAPITAL", out)
        self.assertIn("5 deal(s). Source: NSE bulk/block deal archive. Data is for context, not advice.", out)
        self.assertNotIn("$", out)
        self.assertEqual(err, "")

    def test_ticker_filter_matches_exact_symbols_case_insensitively(self):
        code, out, _, _ = run(["--tickers", "reliance", "INFY"])
        self.assertEqual(code, 0)
        self.assertEqual(out.count("RELIANCE"), 2)
        self.assertIn("INFY", out)
        self.assertNotIn("TCS", out)
        self.assertNotIn("M&M", out)
        self.assertIn("3 deal(s)", out)

    def test_a_ticker_is_not_matched_by_prefix(self):
        code, out, _, _ = run(["--tickers", "REL"])
        self.assertEqual(code, 1)
        self.assertEqual(out.strip(), "No bulk/block deals found for REL.")

    def test_ampersand_ticker_filter(self):
        code, out, _, _ = run(["--tickers", "m&m"])
        self.assertEqual(code, 0)
        self.assertIn("M&M", out)
        self.assertIn("1 deal(s)", out)

    def test_bulk_only_never_fetches_block(self):
        code, out, _, net = run(["--bulk"])
        self.assertEqual(code, 0)
        self.assertEqual(net.calls, [ptr.BULK_URL])
        self.assertIn("[BULK]", out)
        self.assertNotIn("[BLOCK]", out)
        self.assertIn("3 deal(s)", out)

    def test_block_only_never_fetches_bulk(self):
        code, out, _, net = run(["--block"])
        self.assertEqual(code, 0)
        self.assertEqual(net.calls, [ptr.BLOCK_URL])
        self.assertIn("[BLOCK]", out)
        self.assertNotIn("[BULK]", out)
        self.assertIn("2 deal(s)", out)

    def test_both_flags_together_mean_both(self):
        code, out, _, net = run(["--bulk", "--block"])
        self.assertEqual(net.calls, [ptr.BULK_URL, ptr.BLOCK_URL])
        self.assertIn("5 deal(s)", out)

    def test_json_output(self):
        code, out, _, _ = run(["--json", "--tickers", "TCS"])
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(doc["errors"], [])
        self.assertEqual(len(doc["deals"]), 1)
        d = doc["deals"][0]
        self.assertEqual((d["kind"], d["symbol"], d["side"], d["qty"], d["price"], d["date"]),
                         ("bulk", "TCS", "sell", "800000", "3820.55", "06-NOV-2026"))
        self.assertEqual(set(d), {"kind", "security", "symbol", "client", "side", "qty", "price", "date"})

    def test_json_with_no_match_still_prints_valid_json_and_exits_one(self):
        code, out, _, _ = run(["--json", "--tickers", "NOSUCH"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out), {"deals": [], "errors": []})

    def test_one_source_down_still_shows_the_other_and_says_so(self):
        down = ptr.PTRError("NSE archive answered HTTP 503 for " + ptr.BULK_URL)
        code, out, _, _ = run([], bulk=down)
        self.assertEqual(code, 0)
        self.assertIn("[BLOCK]", out)
        self.assertNotIn("[BULK]", out)
        self.assertIn("note: bulk deals: NSE archive answered HTTP 503", out)

    def test_both_sources_down_exits_one_with_both_notes(self):
        code, out, _, _ = run([], bulk=ptr.PTRError("down one"), block=ptr.PTRError("down two"))
        self.assertEqual(code, 1)
        self.assertIn("No bulk/block deals found.", out)
        self.assertIn("note: bulk deals: down one", out)
        self.assertIn("note: block deals: down two", out)

    def test_json_reports_source_errors(self):
        code, out, _, _ = run(["--json", "--block"], block=ptr.PTRError("down two"))
        doc = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(doc["deals"], [])
        self.assertEqual(doc["errors"], ["block deals: down two"])

    def test_a_blocked_html_page_is_reported_as_a_note_not_as_deals(self):
        html = "<html><body>Access Denied</body></html>"
        code, out, _, _ = run(["--bulk"], bulk=html)
        self.assertEqual(code, 1)
        self.assertIn("No bulk/block deals found.", out)
        self.assertIn("note: bulk deals: NSE did not send a deals CSV", out)


class Render(unittest.TestCase):
    DEAL = {"kind": "bulk", "symbol": "ITC", "security": "ITC Limited", "client": "TEST LLP", "side": "buy",
            "qty": "100", "price": "412.35", "date": "06-NOV-2026"}

    def test_a_missing_price_reads_na(self):
        out = ptr.render([dict(self.DEAL, price="")])
        self.assertIn("qty 100 @ N/A", out)

    def test_an_unknown_side_is_shown_as_is(self):
        out = ptr.render([dict(self.DEAL, side="")])
        self.assertIn("[BULK] 06-NOV-2026", out)
        self.assertIn("ITC", out)

    def test_the_filter_inside_render_and_the_empty_message(self):
        self.assertIn("ITC", ptr.render([self.DEAL], ["itc"]))
        self.assertEqual(ptr.render([self.DEAL], ["TCS"]), "No bulk/block deals found for TCS.")
        self.assertEqual(ptr.render([]), "No bulk/block deals found.")

    def test_it_is_not_the_congress_tracker_any_more(self):
        for name in ("search", "download_index", "parse_ptr", "parse_index", "CLERK"):
            self.assertFalse(hasattr(ptr, name), name)
        self.assertIn("NSE", ptr.__doc__)


if __name__ == "__main__":
    unittest.main()
