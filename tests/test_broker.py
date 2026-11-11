import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS_PATH = os.path.join(FIX, "trader-broker", "orders-reliance.json")   # synthetic fixture
PLAN_PATH = os.path.join(FIX, "trader-broker", "plan-reliance.json")       # synthetic fixture
NOW = "2026-11-06 10:15"          # a Friday, a normal NSE trading day
TODAY = "2026-11-06"
TCS = {"symbol": "TCS", "side": "buy", "notional": "5000", "type": "market", "time_in_force": "day"}
BATCH = ["TCS", "BAJAJ-AUTO", "HDFCBANK", "M&M", "SBIN"]
RELIANCE_CALL = {"symbol": "RELIANCE", "side": "buy", "qty": "285", "type": "market", "time_in_force": "day",
                 "order_class": "bracket", "take_profit_limit_price": "1323.00",
                 "stop_loss_stop_price": "1300.80"}
RELIANCE_PLAIN = ("BUY 285 RELIANCE, market, good for today / stop-loss 1300.80 / take-profit 1323.00 / max loss "
                  "about ₹3,163.50 if the stop fills at 1300.80 (it can fill worse in a fast market)")


def reliance(**over):
    with open(ORDERS_PATH) as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


class ExplicitYes(unittest.TestCase):
    def yes(self, reply, order=TCS, batch=BATCH):
        return ticket.parse_yes(reply, order, batch)[0]

    def test_plain_yes_counts(self):
        for r in ("yes", "Yes.", "YES", "y", "yes place it", "Yes, buy it", "yes TCS", "yes ₹5,000 of TCS",
                  "yes 5000"):
            self.assertTrue(self.yes(r), r)

    def test_anything_else_does_not(self):
        for r in ("", "sure", "ok", "okay", "go", "do it", "yeah", "yep", "absolutely", "send it",
                  "yes to all", "yes, all of them", "yes to the rest", "yes both",
                  "yes SBIN", "yes 4000", "yes but make it 4000", "yes?", "no", "not yes",
                  "yes, wait", "yes if it is under 4100"):
            self.assertFalse(self.yes(r), r)

    def test_a_yes_naming_another_stock_fails_whatever_its_length(self):
        # NSE symbols run long (HDFCBANK, BAJAJ-AUTO) and carry & and digits (M&M, 360ONE)
        for other in ("HDFCBANK", "BAJAJ-AUTO", "M&M", "SBIN"):
            self.assertFalse(self.yes("yes " + other), other)
        ok, why = ticket.parse_yes("yes 360ONE", TCS, ["TCS", "360ONE"])
        self.assertFalse(ok)
        self.assertIn("360ONE", why)

    def test_amount_must_match_this_order(self):
        sell = {"symbol": "ITC", "side": "sell", "qty": "30", "type": "market", "time_in_force": "day"}
        self.assertTrue(self.yes("yes sell 30 ITC", sell))
        self.assertFalse(self.yes("yes sell 40", sell))

    def test_a_bracket_yes(self):
        o = reliance()
        for r in ("yes", "yes 285 RELIANCE", "yes 1300.80", "yes max loss 3163.50", "yes target 1323"):
            self.assertTrue(self.yes(r, o, ["RELIANCE"]), r)
        for r in ("yes 286", "yes 300 RELIANCE", "yes but stop at 1295", "yes, stop 1300.80", "yes TCS", "sure"):
            self.assertFalse(self.yes(r, o, ["RELIANCE", "TCS"]), r)


class TicketShape(unittest.TestCase):
    def test_tool_input_is_the_broker_call(self):
        o = dict(TCS, why="from a list")
        self.assertEqual(ticket.tool_input(o), TCS)
        self.assertEqual(ticket.tool_input({"symbol": "ITC", "side": "buy", "qty": 30, "type": "limit",
                                            "limit_price": 412.5, "time_in_force": "day"}),
                         {"symbol": "ITC", "side": "buy", "qty": "30", "type": "limit", "limit_price": "412.5",
                          "time_in_force": "day"})

    def test_the_bracket_call_leaves_out_why_and_plan(self):
        self.assertEqual(ticket.tool_input(reliance()), RELIANCE_CALL)
        self.assertEqual(ticket.fingerprint(reliance()), ticket.fingerprint(RELIANCE_CALL))

    def test_plain_english_uses_rupees(self):
        self.assertEqual(ticket.plain(TCS), "BUY ₹5,000 of TCS, market order, good for today")
        self.assertEqual(ticket.plain({"symbol": "ITC", "side": "sell", "qty": "1", "type": "limit",
                                       "limit_price": "412", "time_in_force": "gtc"}),
                         "SELL 1 share of ITC, limit order at ₹412, good until canceled")
        self.assertEqual(ticket.plain(reliance()), RELIANCE_PLAIN)
        self.assertNotIn("$", ticket.plain(reliance()))
        lim = reliance(type="limit", limit_price="1312.00")
        self.assertEqual(ticket.plain(lim).split(" / ")[0], "BUY 285 RELIANCE, limit 1312.00, good for today")
        self.assertEqual(ticket.max_loss(lim), (3192.0, 1300.8))
        no_plan = dict(RELIANCE_CALL)
        self.assertEqual(ticket.max_loss(no_plan), (None, 1300.8))

    def test_time_is_ist_with_a_fixed_offset(self):
        now = ticket.parse_now("2026-11-06 10:15")
        self.assertEqual(now.isoformat(), "2026-11-06T04:45:00")           # IST is UTC+5:30
        self.assertEqual(ticket.ist_iso(now), "2026-11-06T10:15:00+05:30")
        self.assertEqual(ticket.parse_now("2026-11-06T04:45:00Z"), now)
        with self.assertRaises(ticket.TicketError) as cm:
            ticket.parse_now("not a time")
        self.assertIn("IST", str(cm.exception))


class DesktopConfig(unittest.TestCase):
    def test_example_is_the_kite_mcp_bridge_and_holds_no_secrets(self):
        with open(os.path.join(ROOT, "trader-broker", "claude_desktop_config.example.json")) as fh:
            doc = json.load(fh)
        server = doc["mcpServers"]["nse-broker"]
        self.assertEqual((server["command"], server["args"]), ("npx", ["mcp-remote", "https://mcp.kite.trade/mcp"]))
        self.assertNotIn("env", server)


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        shutil.copy(ORDERS_PATH, self.orders)
        self.cfg = os.path.join(self.d, "config.json")
        shutil.copy(CFG_PATH, self.cfg)
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.none = os.path.join(self.d, "none")

    def tearDown(self):
        shutil.rmtree(self.d)

    def args(self, *extra, **kw):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--now", kw.get("now", NOW)]

    def run_risk(self, now=NOW):
        with redirect_stdout(io.StringIO()):
            return check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf,
                               "--prices", self.none, "--state", self.state, "--now", now, "--out", self.risk])

    def state_doc(self):
        with open(self.state) as fh:
            return json.load(fh)

    def test_nothing_is_ready_before_the_risk_check(self):
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", text)
        self.assertNotIn("Reply \"yes\"", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("not ready", err)

    def test_the_full_two_yes_flow(self):
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("ORDER 1 of 1, PAPER account, NSE Broker", text)
        for part in RELIANCE_PLAIN.split(" / "):
            self.assertIn("  " + part + "\n", text)
        self.assertIn("READY: trader-risk PASS", text)
        self.assertIn('"order_class": "bracket"', text)
        self.assertIn('"stop_loss_stop_price": "1300.80"', text)
        self.assertNotIn('"plan"', text)
        self.assertNotIn('"why"', text)
        self.assertIn("Allow once", text)

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "sure"))
        self.assertEqual(code, 1)
        self.assertIn("Not recorded", text)
        self.assertFalse(os.path.exists(self.state))

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 0)
        self.assertIn("place_nse_order", text)
        self.assertEqual(json.loads(text[text.index("{"):text.rindex("}") + 1]), RELIANCE_CALL)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("APPROVED", err)

        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "abc123",
                                      "--filled-qty", "285", "--filled-price", "1312.00", "--portfolio", self.pf,
                                      now="2026-11-06 10:16"))
        self.assertEqual(code, 0)
        st = self.state_doc()
        self.assertEqual([r["status"] for r in st["orders"]], ["approved", "placed", "filled"])
        appr, plc = st["orders"][0], st["orders"][1]
        self.assertEqual((appr["reply"], appr["kind"], appr["max_loss"], appr["date"]),
                         ("yes", "entry", 3163.5, TODAY))
        self.assertEqual(appr["at"], "2026-11-06T10:15:00+05:30")
        self.assertEqual((plc["broker_order_id"], plc["max_loss"], plc["kind"]), ("abc123", 3163.5, "entry"))
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 126080.0)                    # 500000 - 285 x 1312.00
        self.assertEqual(pf["positions"][0]["shares"], 285)

        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: placed", text)
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn("1 placed today", text)
        self.assertIn("max loss ₹3,163.50", text)

        # trader-risk now counts it: a second RELIANCE buy today is a duplicate, and ₹3,163.50 is used
        res = check.check([reliance()], {"budget": 500000}, state=st, now=check.parse_now("2026-11-06 10:30"))
        self.assertIn("already placed today", " ".join(res["results"][0]["reasons"]))
        self.assertEqual((res["today"]["entries_placed"], res["today"]["loss_at_risk"]), (1, 3163.5))

    def test_flatten_by_the_close(self):
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "abc",
                      "--filled-qty", "285", "--filled-price", "1312.00", "--portfolio", self.pf))
        late = "2026-11-06 15:20"
        code, text, _ = run(["flatten", "--symbol", "RELIANCE", "--orders", self.orders, "--portfolio", self.pf,
                             "--plan", PLAN_PATH, "--config", self.cfg, "--now", late])
        self.assertEqual(code, 0)
        self.assertIn("SELL 285 shares of RELIANCE, market order, good for today (flat by 15:20 IST)", text)
        self.assertIn("replaced 1 earlier proposal", text)
        self.assertIn("cancel them first", text)
        with open(self.orders) as fh:
            doc = json.load(fh)
        self.assertEqual(ticket.tool_input(doc["orders"][0]),
                         {"symbol": "RELIANCE", "side": "sell", "qty": "285", "type": "market",
                          "time_in_force": "day"})
        self.assertEqual(self.run_risk(now=late), 0)       # after the 15:15 entry window: an exit still passes
        with open(self.risk) as fh:
            self.assertEqual(json.load(fh)["results"][0]["kind"], "exit")
        code, text, _ = run(self.args("show", now=late))
        self.assertEqual(code, 0)
        self.assertIn("READY", text)
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes sell 285 RELIANCE", now=late))[0], 0)
        code, _, _ = run(self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "285",
                                   "--filled-price", "1318.50", "--portfolio", self.pf, now="2026-11-06 15:21"))
        self.assertEqual(code, 0)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual((pf["cash"], pf["positions"]), (501852.5, []))   # 126080 + 285 x 1318.50
        code, _, err = run(["flatten", "--symbol", "RELIANCE", "--orders", self.orders, "--portfolio", self.pf,
                            "--now", late])
        self.assertEqual(code, 1)
        self.assertIn("nothing to flatten", err)

    def test_flatten_with_the_broker_qty(self):
        code, text, _ = run(["flatten", "--symbol", "reliance", "--qty", "285", "--orders", self.orders,
                             "--portfolio", self.none, "--plan", self.none, "--config", self.cfg, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertNotIn("Check it against your broker position", text)
        self.assertIn("flat by 15:20 IST", text)                  # the default when no plan or config says otherwise
        self.assertEqual(run(["flatten", "--symbol", "RELIANCE", "--qty", "0", "--orders", self.orders])[0], 1)

    def test_an_edited_order_is_not_ready(self):
        self.run_risk()
        with open(self.orders) as fh:
            doc = json.load(fh)
        doc["orders"][0]["qty"] = "290"
        with open(self.orders, "w") as fh:
            json.dump(doc, fh)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("was not in the last risk check", text)

    def test_yesterdays_check_does_not_count(self):
        self.run_risk(now="2026-11-05 10:15")                      # Thursday, a trading day
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("not today", text)

    def test_blocked_orders_are_never_ready(self):
        self.assertEqual(self.run_risk(now="2026-11-06 09:20"), 1)
        code, text, _ = run(self.args("show", "--index", "1", now="2026-11-06 09:20"))
        self.assertIn("BLOCKED by trader-risk: window: no new entries before 09:45", text)
        code, _, _ = run(self.args("approve", "--index", "1", "--reply", "yes", now="2026-11-06 09:20"))
        self.assertEqual(code, 1)

    def test_an_nse_holiday_blocks_every_order(self):
        # 2026-11-10 is Diwali-Balipratipada, an NSE trading holiday
        self.assertEqual(self.run_risk(now="2026-11-10 10:15"), 1)
        code, text, _ = run(self.args("show", "--index", "1", now="2026-11-10 10:15"))
        self.assertIn("BLOCKED", text)
        self.assertIn("holiday", text.lower())

    def test_result_needs_an_approval_and_decline_is_logged(self):
        self.run_risk()
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("no yes recorded", err)
        code, text, _ = run(self.args("decline", "--index", "1", "--reply", "no thanks"))
        self.assertEqual(code, 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: declined", text)

    def test_ibkr_tickets_send_the_final_yes_to_ibkr(self):
        with open(self.cfg) as fh:
            c = json.load(fh)
        c["broker"] = "ibkr"
        with open(self.cfg, "w") as fh:
            json.dump(c, fh)
        self.run_risk()
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("Interactive Brokers", text)
        self.assertIn("after you approve it there", text)
        self.assertNotIn("place_nse_order", text)

    def test_fill_sell_relieves_the_oldest_lot(self):
        pf = {"cash": 0, "positions": [{"ticker": "ITC", "shares": 3, "lots": [
            {"id": "ITC-1", "date": "2025-01-02", "shares": 2, "cost": 800.0},
            {"id": "ITC-2", "date": "2026-01-02", "shares": 1, "cost": 450.0}]}]}
        with open(self.pf, "w") as fh:
            json.dump(pf, fh)
        code, text, _ = run(["fill", "--symbol", "ITC", "--side", "sell", "--qty", "2.5", "--price", "420",
                             "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 0)
        self.assertIn("₹", text)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 1050.0)
        lots = pf["positions"][0]["lots"]
        self.assertEqual([l["id"] for l in lots], ["ITC-2"])
        self.assertEqual(lots[0]["shares"], 0.5)
        self.assertEqual(lots[0]["cost"], 225.0)
        self.assertEqual(self.state_doc()["orders"][-1]["status"], "filled")
        code, _, err = run(["fill", "--symbol", "ITC", "--side", "sell", "--qty", "5", "--price", "420",
                            "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
