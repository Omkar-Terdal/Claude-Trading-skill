import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from _load import load

groww = load("trader-broker", "groww")
ticket = load("trader-broker", "ticket")

RELIANCE = {"symbol": "RELIANCE", "side": "buy", "qty": "285", "type": "market", "time_in_force": "day",
            "order_class": "bracket", "take_profit_limit_price": "1330.00", "stop_loss_stop_price": "1300.80"}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = groww.main(argv)
    return code, out.getvalue(), err.getvalue()


class Symbols(unittest.TestCase):
    def test_suffixes_are_stripped_and_exchange_chosen(self):
        self.assertEqual(groww.clean_symbol("reliance.ns"), "RELIANCE")
        self.assertEqual(groww.clean_symbol("M&M"), "M&M")
        self.assertEqual(groww.clean_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO")
        self.assertEqual(groww.exchange_of("TCS.BO"), "BSE")
        self.assertEqual(groww.exchange_of("TCS"), "NSE")

    def test_junk_is_refused(self):
        for bad in ("", "RELI ANCE", "TCS;DROP", "../x", "A" * 25):
            with self.assertRaises(groww.GrowwError, msg=bad):
                groww.clean_symbol(bad)


class Token(unittest.TestCase):
    def test_checksum_is_sha256_of_secret_then_timestamp(self):
        import hashlib
        self.assertEqual(groww.checksum("sek", "1700000000"),
                         hashlib.sha256(b"sek1700000000").hexdigest())

    def test_make_token_sends_approval_body(self):
        with mock.patch.object(groww, "call", return_value={"token": "T"}) as c:
            groww.make_token("KEY", "sek", now=1700000000)
        method, path, key = c.call_args[0][:3]
        body = c.call_args[1]["body"]
        self.assertEqual((method, path, key), ("POST", "/token/api/access", "KEY"))
        self.assertEqual(body["key_type"], "approval")
        self.assertEqual(body["timestamp"], "1700000000")
        self.assertEqual(body["checksum"], groww.checksum("sek", "1700000000"))
        self.assertFalse(c.call_args[1]["envelope"])

    def test_token_endpoint_answers_without_the_status_wrapper(self):
        # live check 2026-11-07: HTTP 200 with token, expiry, sessionName, tokenRefId, active at the top level
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return json.dumps({"token": "T", "expiry": "2026-11-08T06:00:00", "active": True,
                                            "sessionName": "x", "tokenRefId": "r"}).encode()
        with mock.patch("urllib.request.urlopen", return_value=R()):
            self.assertEqual(groww.make_token("KEY", "sek", now=1700000000)["token"], "T")

    def test_a_blank_refusal_still_says_something(self):
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return json.dumps({"status": "FAILURE"}).encode()
        with mock.patch("urllib.request.urlopen", return_value=R()):
            with self.assertRaises(groww.GrowwError) as cm:
                groww.call("GET", "/x", "T")
        self.assertIn("no reason given", str(cm.exception))

    def test_env_token_wins_and_missing_token_explains(self):
        self.assertEqual(groww.get_token({"GROWW_ACCESS_TOKEN": "abc"}, "/nonexistent"), "abc")
        with self.assertRaises(groww.GrowwError) as cm:
            groww.get_token({}, "/nonexistent")
        self.assertIn("GROWW_ACCESS_TOKEN", str(cm.exception))

    def test_cached_token_expiry(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "t.json")
            future = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)).isoformat()
            past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).isoformat()
            groww.save_token({"token": "OK", "expiry": future}, p)
            self.assertEqual(groww.get_token({}, p), "OK")
            self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o600")
            groww.save_token({"token": "OLD", "expiry": past}, p)
            with self.assertRaises(groww.GrowwError):
                groww.get_token({}, p)
        finally:
            shutil.rmtree(d)


class Calls(unittest.TestCase):
    def test_ltp_builds_exchange_symbols(self):
        with mock.patch.object(groww, "call", return_value={"NSE_RELIANCE": 1320.0}) as c:
            groww.ltp("T", ["RELIANCE.NS", "TCS.BO"])
        self.assertEqual(c.call_args[0][3]["exchange_symbols"], "NSE_RELIANCE,BSE_TCS")
        with self.assertRaises(groww.GrowwError):
            groww.ltp("T", [])
        with self.assertRaises(groww.GrowwError):
            groww.ltp("T", ["A%d" % i for i in range(51)])

    def test_candles_parse_both_timestamp_styles_and_sort(self):
        payload = {"candles": [["2026-11-06T09:20:00", 1311.9, 1313, 1310, 1312, 1000, None],
                               [1793936700, 1310, 1312, 1309, 1311.9, 2500, None]]}
        s = datetime.datetime(2026, 11, 6, 3, 0)
        with mock.patch.object(groww, "call", return_value=payload) as c:
            bars = groww.candles("T", "RELIANCE", s, s + datetime.timedelta(days=1))
        params = c.call_args[0][3]
        self.assertEqual(params["groww_symbol"], "NSE-RELIANCE")
        self.assertEqual(params["candle_interval"], "5minute")
        self.assertEqual(params["start_time"], "2026-11-06 08:30:00")
        self.assertEqual([b["t"] for b in bars], sorted(b["t"] for b in bars))
        self.assertEqual([b["t"] for b in bars], [datetime.datetime(2026, 11, 6, 9, 15),
                                                  datetime.datetime(2026, 11, 6, 9, 20)])
        self.assertEqual(bars[0]["v"], 2500)

    def test_candle_range_limits(self):
        s = datetime.datetime(2026, 1, 1)
        with self.assertRaises(groww.GrowwError):
            groww.candles("T", "RELIANCE", s, s + datetime.timedelta(days=31), "5minute")
        with self.assertRaises(groww.GrowwError):
            groww.candles("T", "RELIANCE", s, s + datetime.timedelta(days=2), "7minute")

    def test_failure_envelope_raises_with_groww_error(self):
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return json.dumps({"status": "FAILURE",
                                            "error": {"code": "GA007", "message": "dup"}}).encode()
        with mock.patch("urllib.request.urlopen", return_value=R()):
            with self.assertRaises(groww.GrowwError) as cm:
                groww.call("POST", "/order/create", "T", body={})
        self.assertIn("GA007", str(cm.exception))

    def test_headers_carry_token_and_version(self):
        h = groww._headers("TOK", post=True)
        self.assertEqual(h["Authorization"], "Bearer TOK")
        self.assertEqual(h["X-API-VERSION"], "1.0")
        self.assertEqual(h["Content-Type"], "application/json")


class OrderBodies(unittest.TestCase):
    def test_market_order(self):
        fp = ticket.fingerprint(RELIANCE)
        b = groww.build_order(RELIANCE, fp)
        self.assertEqual((b["trading_symbol"], b["quantity"], b["order_type"], b["transaction_type"]),
                         ("RELIANCE", 285, "MARKET", "BUY"))
        self.assertEqual((b["exchange"], b["segment"], b["product"], b["validity"]),
                         ("NSE", "CASH", "MIS", "DAY"))
        self.assertNotIn("price", b)
        self.assertRegex(b["order_reference_id"], r"^[A-Za-z0-9\-]{8,20}$")
        self.assertLessEqual(b["order_reference_id"].count("-"), 2)

    def test_same_order_same_reference_so_groww_can_refuse_a_double(self):
        fp = ticket.fingerprint(RELIANCE)
        self.assertEqual(groww.build_order(RELIANCE, fp)["order_reference_id"],
                         groww.build_order(RELIANCE, fp)["order_reference_id"])
        other = dict(RELIANCE, qty="286")
        self.assertNotEqual(groww.build_order(other, ticket.fingerprint(other))["order_reference_id"],
                            groww.build_order(RELIANCE, fp)["order_reference_id"])

    def test_limit_needs_price_and_unsupported_types_refused(self):
        lim = dict(RELIANCE, type="limit", limit_price="1311.90")
        self.assertEqual(groww.build_order(lim, "abcd1234abcd1234")["price"], 1311.9)
        with self.assertRaises(groww.GrowwError):
            groww.build_order(dict(RELIANCE, type="limit"), "abcd1234abcd1234")
        for t in ("stop", "stop_limit", "trailing_stop"):
            with self.assertRaises(groww.GrowwError, msg=t):
                groww.build_order(dict(RELIANCE, type=t), "abcd1234abcd1234")

    def test_fractional_and_rupee_amount_refused(self):
        with self.assertRaises(groww.GrowwError):
            groww.build_order(dict(RELIANCE, qty="2.5"), "abcd1234abcd1234")
        with self.assertRaises(groww.GrowwError):
            groww.build_order({"symbol": "TCS", "side": "buy", "notional": "5000", "type": "market"},
                              "abcd1234abcd1234")
        with self.assertRaises(groww.GrowwError):
            groww.build_order(dict(RELIANCE, qty="0"), "abcd1234abcd1234")

    def test_oco_closing_a_long(self):
        b = groww.build_oco("RELIANCE", 285, "sell", 1330, 1300.8)
        self.assertEqual(b["transaction_type"], "SELL")
        self.assertEqual(b["net_position_quantity"], 285)
        self.assertEqual(b["target"], {"trigger_price": "1330.00", "order_type": "LIMIT", "price": "1330.00"})
        self.assertEqual(b["stop_loss"], {"trigger_price": "1300.80", "order_type": "SL_M", "price": None})
        self.assertEqual((b["product_type"], b["segment"], b["duration"]), ("MIS", "CASH", "DAY"))
        self.assertRegex(b["reference_id"], r"^[A-Za-z0-9\-]{8,20}$")

    def test_oco_refuses_a_target_on_the_wrong_side_of_the_stop(self):
        with self.assertRaises(groww.GrowwError):
            groww.build_oco("RELIANCE", 285, "sell", 1290, 1300)
        with self.assertRaises(groww.GrowwError):
            groww.build_oco("RELIANCE", 285, "buy", 1330, 1300)
        self.assertEqual(groww.build_oco("RELIANCE", 10, "buy", 1290, 1300)["net_position_quantity"], -10)


class SendGate(unittest.TestCase):
    def test_paper_config_never_sends(self):
        self.assertFalse(groww.may_send({"paper": True}, True)[0])
        self.assertFalse(groww.may_send({}, True)[0])
        self.assertFalse(groww.may_send({"paper": False}, False)[0])
        self.assertTrue(groww.may_send({"paper": False}, True)[0])


class PlaceFlow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        j = lambda n: os.path.join(self.d, n)
        self.orders, self.cfg = j("orders.json"), j("config.json")
        self.risk, self.state = j("risk.json"), j("broker.json")
        self.fp = ticket.fingerprint(RELIANCE)
        self.write(self.orders, {"orders": [RELIANCE]})
        self.write(self.cfg, {"paper": False})
        self.write(self.risk, {"date": "2026-11-06", "results": [{"fingerprint": self.fp, "verdict": "PASS"}]})
        self.argv = ["place", "--index", "1", "--orders", self.orders, "--config", self.cfg,
                     "--risk", self.risk, "--state", self.state, "--now", "2026-11-06 10:15"]

    def tearDown(self):
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def approve(self):
        self.write(self.state, {"orders": [{"fingerprint": self.fp, "date": "2026-11-06",
                                            "at": "2026-11-06T10:15:00+05:30", "status": "approved",
                                            "symbol": "RELIANCE", "side": "buy",
                                            "plain": "BUY 285 RELIANCE"}]})

    def test_no_recorded_yes_means_nothing_is_sent(self):
        with mock.patch.object(groww, "call") as c:
            code, out, err = run(self.argv + ["--send"])
        self.assertEqual(code, 1)
        self.assertIn("no recorded yes", err)
        c.assert_not_called()

    def test_risk_not_passed_means_nothing_is_sent(self):
        self.approve()
        self.write(self.risk, {"date": "2026-11-06", "results": [{"fingerprint": self.fp, "verdict": "BLOCK"}]})
        with mock.patch.object(groww, "call") as c:
            code, out, err = run(self.argv + ["--send"])
        self.assertEqual(code, 1)
        c.assert_not_called()

    def test_stale_risk_check_is_refused(self):
        self.approve()
        self.write(self.risk, {"date": "2026-11-05", "results": [{"fingerprint": self.fp, "verdict": "PASS"}]})
        with mock.patch.object(groww, "call") as c:
            self.assertEqual(run(self.argv + ["--send"])[0], 1)
        c.assert_not_called()

    def test_dry_run_prints_body_and_sends_nothing(self):
        self.approve()
        with mock.patch.object(groww, "call") as c:
            code, out, err = run(self.argv)
        self.assertEqual(code, 0)
        self.assertIn("NOT SENT", out)
        self.assertIn('"trading_symbol": "RELIANCE"', out)
        c.assert_not_called()

    def test_paper_config_blocks_even_with_send(self):
        self.approve()
        self.write(self.cfg, {"paper": True})
        with mock.patch.object(groww, "call") as c:
            code, out, err = run(self.argv + ["--send"])
        self.assertIn("NOT SENT", out)
        c.assert_not_called()

    def test_live_send_posts_once_and_records_the_broker_id(self):
        self.approve()
        reply = {"groww_order_id": "GMK1", "order_status": "OPEN"}
        with mock.patch.object(groww, "call", return_value=reply) as c, \
                mock.patch.object(groww, "get_token", return_value="T"):
            code, out, err = run(self.argv + ["--send"])
        self.assertEqual(code, 0, err)
        self.assertEqual(c.call_count, 1)
        self.assertEqual(c.call_args[0][:2], ("POST", "/order/create"))
        with open(self.state) as fh:
            recs = json.load(fh)["orders"]
        self.assertEqual(recs[-1]["status"], "placed")
        self.assertEqual(recs[-1]["broker_order_id"], "GMK1")


class CredentialsFile(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "groww.env")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_setup_creates_a_private_template_once_and_never_overwrites(self):
        self.assertTrue(groww.setup_env_file(self.path))
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")
        with open(self.path, "w") as fh:
            fh.write("GROWW_ACCESS_TOKEN=keepme\n")
        self.assertFalse(groww.setup_env_file(self.path))
        with open(self.path) as fh:
            self.assertIn("keepme", fh.read())

    def test_the_template_has_no_values(self):
        self.assertEqual(groww.parse_env_text(groww.ENV_TEMPLATE), {})

    def test_parse_ignores_comments_unknown_keys_and_quotes(self):
        text = "# c\nGROWW_API_KEY=\"abc\"\nOTHER=1\nGROWW_API_SECRET='s e'\nGROWW_ACCESS_TOKEN=\n"
        self.assertEqual(groww.parse_env_text(text), {"GROWW_API_KEY": "abc", "GROWW_API_SECRET": "s e"})

    def test_a_world_readable_file_is_refused(self):
        with open(self.path, "w") as fh:
            fh.write("GROWW_ACCESS_TOKEN=abc\n")
        os.chmod(self.path, 0o644)
        with self.assertRaises(groww.GrowwError) as cm:
            groww.load_env_file(self.path, {})
        self.assertIn("chmod 600", str(cm.exception))
        self.assertNotIn("abc", str(cm.exception))

    def test_file_fills_gaps_but_the_environment_wins(self):
        with open(self.path, "w") as fh:
            fh.write("GROWW_ACCESS_TOKEN=fromfile\nGROWW_API_KEY=k\n")
        os.chmod(self.path, 0o600)
        env = {"GROWW_ACCESS_TOKEN": "fromenv"}
        self.assertEqual(groww.load_env_file(self.path, env), ["GROWW_API_KEY"])
        self.assertEqual(env, {"GROWW_ACCESS_TOKEN": "fromenv", "GROWW_API_KEY": "k"})

    def test_missing_file_is_fine(self):
        self.assertEqual(groww.load_env_file(os.path.join(self.d, "none"), {}), [])

    def test_check_prints_no_secret(self):
        env = {"GROWW_ACCESS_TOKEN": "SECRETTOKEN123"}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(groww, "ENV_FILE", os.path.join(self.d, "none")), \
                mock.patch.object(groww, "ltp", return_value={"NSE_RELIANCE": 1320.0}), \
                mock.patch.object(groww, "candles", return_value=[]), \
                mock.patch.object(groww, "positions", return_value={"positions": []}), \
                mock.patch.object(groww, "margin", return_value={"clear_cash": 5000,
                                                                "equity_margin_details": {"mis_balance_available": 20000}}):
            code, out, err = run(["check"])
        self.assertEqual(code, 0, err)
        self.assertIn("OK      live prices", out)
        self.assertIn("cash ₹5000", out)
        self.assertNotIn("SECRETTOKEN123", out + err)


    def test_check_reports_each_part_when_data_calls_are_refused(self):
        env = {"GROWW_ACCESS_TOKEN": "SECRETTOKEN123"}
        deny = groww.GrowwError("Groww answered HTTP 403 403: Access forbidden for this request.")
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(groww, "ENV_FILE", os.path.join(self.d, "none")), \
                mock.patch.object(groww, "ltp", side_effect=deny), \
                mock.patch.object(groww, "candles", side_effect=deny), \
                mock.patch.object(groww, "positions", return_value={"positions": []}), \
                mock.patch.object(groww, "margin", return_value={"clear_cash": 8106.47}):
            code, out, err = run(["check"])
        self.assertEqual(code, 1)
        self.assertIn("OK      account margin: cash ₹8106.47", out)
        self.assertIn("OK      positions", out)
        self.assertIn("FAILED  live prices", out)
        self.assertIn("FAILED  5-minute candles", out)
        self.assertNotIn("SECRETTOKEN123", out + err)


if __name__ == "__main__":
    unittest.main()
