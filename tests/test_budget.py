import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

size = load("trader-budget", "size")

CFG = os.path.join(ROOT, "trader-budget", "config.example.json")
PLAN = os.path.join(FIX, "trader-budget", "plan-reliance.json")
TARGETS = os.path.join(ROOT, "trader-budget", "targets.example.json")
TOP5 = os.path.join(FIX, "trader-budget", "top5-raw.json")
SRC = os.path.join(ROOT, "trader-budget", "size.py")
NOW = "2026-11-06 09:55"          # Friday, IST: the 9:45 bar has closed, the entry is 9:50
TODAY = "2026-11-06"
# the raw weights (22.04 / 17.14 / 12.62 / 10.86 / 9.20) split Rs 450,000 with a 25% cap
RAW_SPLIT = {"RELIANCE": 125000, "TCS": 111813, "INFY": 82326, "HDFCBANK": 70845, "ITC": 60016}
TARGETS_SPLIT = {"RELIANCE": 125000, "HDFCBANK": 116071, "INFY": 92857, "ITC": 69643, "SBIN": 46429}
PRICES = {"RELIANCE": 1311.90, "TCS": 3180.40, "INFY": 1482.10, "HDFCBANK": 1685.25, "ITC": 428.60}
# the contract's config.example.json, key for key
CONTRACT_CFG = {
    "budget": 500000, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 400000, "min_order_inr": 500,
    "max_loss_per_trade_inr": 5000, "max_daily_loss_inr": 15000, "max_trades_per_day": 3, "max_orders_per_day": 10,
    "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": "09:45", "no_entries_after": "15:30", "flat_by": "15:20",
    "fractional": False, "broker": "nse_broker", "paper": True, "blocked": [],
    "allow_options": False, "allow_margin": False, "allow_short": False,
}
# the weights-mode rules: Rs 5,00,000, 10% cash, 25% cap, fractional so the rupee split shows
V1 = {"budget": 500000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 200000,
      "min_order_inr": 500, "fractional": True, "paper": True, "blocked": []}


def cfg(**over):
    with open(CFG) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def v1(**over):
    c = dict(V1)
    c.update(over)
    return c


def plan(**over):
    with open(PLAN) as fh:
        p = json.load(fh)
    p.update(over)
    return p


def rows(path):
    with open(path) as fh:
        return size.parse_weights(json.load(fh))


def rupees(result):
    return {p["ticker"]: p["dollars"] for p in result["positions"]}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


def prices_doc(price, quote_time="2026-11-06T05:22:13Z"):       # 10:52:13 IST
    return {"as_of": quote_time, "source": "yahoo", "prices": {"RELIANCE": price},
            "detail": {"RELIANCE": {"prev_close": 1313.40, "time": quote_time, "source": "yahoo"}}}


class InTempDir(unittest.TestCase):
    """CLI runs read trader/prices.json and trader/portfolio.json from the working folder: keep it empty."""

    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()


class TheTrade(InTempDir):
    """Rs 5,00,000, 10% buffer, Rs 5,000 max loss, RELIANCE risk 11.10: 285 shares, bound by the 75% cap."""

    def test_config_example_is_the_contract(self):
        with open(CFG) as fh:
            doc = json.load(fh)
        self.assertEqual(doc, CONTRACT_CFG)

    def test_reliance_is_285_shares_bound_by_the_position_cap(self):
        s = size.size_trade(plan(), cfg(include_costs=False), today=TODAY)     # price risk only; costs are tested below
        self.assertEqual(s["qty"], 285)
        self.assertEqual(s["bound_by"], "position cap")
        self.assertEqual(s["risk_per_share"], 11.1)
        self.assertEqual(s["cost"], 373891.5)
        self.assertEqual(s["max_loss"], 3163.5)
        self.assertEqual(s["reward_risk"], 1.0)
        self.assertEqual(s["cash_buffer"], 50000)
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]],
                         [("max loss", 450), ("position cap", 285), ("max order", 304), ("cash", 343)])
        self.assertEqual([l["name"] for l in s["limits"] if l["binds"]], ["position cap"])
        self.assertIsNone(s["refusal"])

    def test_the_bracket_order(self):
        o = size.bracket_order(size.size_trade(plan(), cfg(), today=TODAY))
        why = o.pop("why")
        self.assertEqual(o, {
            "symbol": "RELIANCE", "side": "buy", "qty": "285", "type": "market", "time_in_force": "day",
            "order_class": "bracket", "take_profit_limit_price": "1323.00", "stop_loss_stop_price": "1300.80",
            "plan": {"entry": 1311.9, "stop": 1300.8, "target": 1323.0, "risk_per_share": 11.1,
                     "max_loss": 3163.5, "setup": "orb", "entry_time": "9:50 AM", "flat_by": "15:20"}})
        self.assertIn("bound by your position cap", why)
        self.assertIn("₹3,163.50", why)
        self.assertNotIn("$", why)

    def test_the_order_has_whole_shares_and_no_us_broker_fields(self):
        o = size.bracket_order(size.size_trade(plan(), cfg(), today=TODAY))
        self.assertEqual(o["qty"], str(int(o["qty"])))
        self.assertNotIn("notional", o)
        self.assertNotIn(".NS", o["symbol"])          # the bare NSE symbol; the data tools add .NS themselves

    def test_a_limit_order(self):
        o = size.bracket_order(size.size_trade(plan(), cfg(), limit=1311.90, today=TODAY))
        self.assertEqual((o["type"], o["limit_price"], o["qty"]), ("limit", "1311.90", "285"))
        s = size.size_trade(plan(), cfg(), limit=1311.00, today=TODAY)
        self.assertEqual((s["risk_per_share"], s["qty"], s["max_loss"]), (10.2, 286, 2917.2))
        self.assertEqual(size.bracket_order(s)["limit_price"], "1311.00")

    def test_cli_writes_one_bracket_order(self):
        out = os.path.join("trader", "orders.json")
        code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", "none", "--now", NOW, "--write", out])
        self.assertEqual(code, 0)
        self.assertIn("Size: 285 shares of RELIANCE = ₹373,891.50 at 1311.90", text)
        self.assertIn("285 x 11.10 = ₹3,163.50", text)
        self.assertIn("<- binds", text)
        self.assertIn("Flat by 15:20 IST", text)
        self.assertIn("not financial advice", text)
        self.assertNotIn("$", text)
        with open(out) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["status"], "proposed")
        self.assertIs(doc["paper"], True)
        self.assertEqual(doc["created_at"], "2026-11-06T04:25:00Z")      # 09:55 IST is 04:25 UTC
        self.assertEqual(len(doc["orders"]), 1)
        self.assertEqual(doc["orders"][0], size.bracket_order(size.size_trade(plan(), cfg(), today=TODAY)))

    def test_cli_json_and_limit(self):
        code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--now", NOW,
                             "--limit", "--json"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(out["sizing"]["qty"], 285)
        self.assertEqual(out["orders"][0]["limit_price"], "1311.90")


class PlanRules(InTempDir):
    def test_zero_shares_is_a_plain_refusal(self):
        c = cfg(max_loss_per_trade_inr=10)                  # one share risks Rs 11.10
        s = size.size_trade(plan(), c, today=TODAY)
        self.assertEqual(s["qty"], 0)
        self.assertEqual(s["bound_by"], "max loss")
        self.assertIn("more than your ₹10 max loss per trade", s["refusal"])
        self.assertIn("₹11.10", s["refusal"])
        self.assertIsNone(size.bracket_order(s))
        with open("config.json", "w") as fh:
            json.dump(c, fh)
        code, text, _ = run(["--plan", PLAN, "--config", "config.json", "--portfolio", "none",
                             "--now", NOW, "--write", "orders.json"])
        self.assertEqual(code, 1)
        self.assertIn("No order:", text)
        self.assertFalse(os.path.exists("orders.json"))

    def test_each_limit_can_bind(self):
        s = size.size_trade(plan(), cfg(max_order_inr=100000), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (76, "max order"))
        s = size.size_trade(plan(), cfg(max_position_pct=20), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (76, "position cap"))
        s = size.size_trade(plan(), cfg(), cash=120000, today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["cash_available"]), (53, "cash", 70000))
        s = size.size_trade(plan(), cfg(max_loss_per_trade_inr=3000, include_costs=False), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["max_loss"]), (270, "max loss", 2997))
        s = size.size_trade(plan(), cfg(max_loss_per_trade_inr=10000), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (285, "position cap"))

    def test_a_fresh_higher_price_means_a_bigger_risk(self):
        s = size.size_trade(plan(), cfg(min_reward_risk=0), price=1316.475, today=TODAY)
        self.assertEqual((s["entry"], s["entry_from"], s["risk_per_share"]), (1316.48, "price", 15.68))
        self.assertEqual((s["qty"], s["max_loss"], s["reward_risk"]), (284, 4453.12, 0.42))
        self.assertIsNone(s["refusal"])
        s = size.size_trade(plan(), cfg(), price=1316.475, today=TODAY)
        self.assertIn("reward:risk 0.42 (target 1323.00, entry 1316.48, stop 1300.80) is under your minimum 1",
                      s["refusal"])
        self.assertIsNone(size.bracket_order(s))
        lower = size.size_trade(plan(), cfg(), price=1305.00, today=TODAY)
        self.assertEqual((lower["entry"], lower["qty"]), (1311.9, 285))

    def test_prices_file_is_used_only_when_fresh(self):
        with open("prices.json", "w") as fh:
            json.dump(prices_doc(1316.475), fh)           # quoted at 10:52:13 IST
        base = ["--plan", PLAN, "--config", CFG, "--portfolio", "none", "--prices", "prices.json", "--json"]
        code, text, _ = run(base + ["--now", "2026-11-06 10:55"])
        out = json.loads(text)
        self.assertEqual((code, out["sizing"]["qty"], out["orders"]), (1, 284, []))
        self.assertIn("above the plan's entry", out["notes"][0])
        self.assertIn("10:52 IST", out["notes"][0])
        out = json.loads(run(base + ["--now", "2026-11-06 11:30"])[1])      # 38 minutes later: stale
        self.assertEqual(out["sizing"]["qty"], 285)
        self.assertIn("not used", out["notes"][0])

    def test_the_portfolio_cash_caps_the_size(self):
        with open("portfolio.json", "w") as fh:
            json.dump({"cash": 120000, "positions": []}, fh)
        out = json.loads(run(["--plan", PLAN, "--config", CFG, "--portfolio", "portfolio.json",
                              "--now", NOW, "--json"])[1])
        self.assertEqual((out["sizing"]["qty"], out["sizing"]["bound_by"]), (53, "cash"))

    def test_bad_plans(self):
        for p, word in ((plan(stop=1315.0), "not below the entry"),
                        (plan(target=1310.0), "already happened"),
                        (plan(target=None), "positive target"),
                        (plan(side="sell"), "long (buy) day trades only"),
                        (plan(symbol=""), "no symbol")):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(p, cfg(), today=TODAY)
            self.assertIn(word, str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), price=1330.00, today=TODAY)
        self.assertIn("already happened", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(blocked=["reliance"]), today=TODAY)
        self.assertIn("blocked list", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), today="2026-11-09")          # the next trading day, a Monday
        self.assertIn("make a new plan", str(e.exception))
        self.assertIn("IST", str(e.exception))

    def test_under_the_minimum_order(self):
        cheap = plan(symbol="YESBANK", entry=20.0, stop=19.0, target=21.0)
        s = size.size_trade(cheap, cfg(max_loss_per_trade_inr=20, include_costs=False), today=TODAY)
        self.assertEqual(s["qty"], 20)
        self.assertIn("under your ₹500 minimum order", s["refusal"])
        self.assertIn("₹400", s["refusal"])
        self.assertIsNone(size.bracket_order(s))

    def test_missing_plan_and_weights_only_flags(self):
        code, _, err = run(["--plan", "/nonexistent/plan.json", "--config", CFG, "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", NOW, "--top", "2"])
        self.assertEqual(code, 1)
        self.assertIn("--weights", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", "tomorrow"])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)


class NseSpecifics(InTempDir):
    def test_the_clock_is_ist_with_no_daylight_saving(self):
        self.assertEqual(size.IST_OFFSET, datetime.timedelta(hours=5, minutes=30))
        self.assertEqual(size.parse_now(NOW), datetime.datetime(2026, 11, 6, 4, 25))
        self.assertEqual(size.parse_now("2026-11-06T04:25:00Z"), datetime.datetime(2026, 11, 6, 4, 25))
        self.assertEqual(size.parse_now("2026-11-06T09:55:00+05:30"), datetime.datetime(2026, 11, 6, 4, 25))
        for month in (1, 7):                                     # winter and summer: the same +5:30
            utc = datetime.datetime(2026, month, 15, 3, 45)
            self.assertEqual(size.to_ist(utc), datetime.datetime(2026, month, 15, 9, 15))
            self.assertEqual(size.ist_to_utc(size.to_ist(utc)), utc)

    def test_a_bad_clock_names_ist_not_new_york(self):
        with self.assertRaises(size.SizeError) as e:
            size.parse_now("tomorrow")
        self.assertIn("IST", str(e.exception))
        self.assertNotIn("New York", str(e.exception))

    def test_today_is_the_ist_date_across_midnight_utc(self):
        # 20:00 UTC on 5 Nov is 01:30 IST on 6 Nov: the Friday plan is for "today"
        code, text, err = run(["--plan", PLAN, "--config", CFG, "--portfolio", "none",
                               "--now", "2026-11-05T20:00:00Z", "--json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(text)["sizing"]["qty"], 285)
        # 20:00 IST on 5 Nov is still Thursday: the Friday plan is not for today
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--portfolio", "none", "--now", "2026-11-05 20:00"])
        self.assertEqual(code, 1)
        self.assertIn("make a new plan", err)

    def test_flat_by_defaults_to_1520_and_never_shifts(self):
        self.assertEqual(size.DEFAULTS["flat_by"], "15:20")
        self.assertEqual(size.size_trade(plan(flat_by=None), {"budget": 500000}, today=TODAY)["flat_by"], "15:20")
        self.assertEqual(size.size_trade(plan(flat_by=None), cfg(flat_by="15:10"), today=TODAY)["flat_by"], "15:10")
        self.assertEqual(size.size_trade(plan(), cfg(), today=TODAY)["flat_by"], "15:20")

    def test_the_rules_are_in_rupees_and_us_rules_are_gone(self):
        for k in ("max_order_inr", "min_order_inr", "max_loss_per_trade_inr", "max_daily_loss_inr"):
            self.assertIn(k, size.DEFAULTS)
        for k in size.DEFAULTS:
            self.assertFalse(k.endswith("_usd"), k)
        self.assertNotIn("pdt_guard", size.DEFAULTS)
        self.assertEqual(size.money(1250), "₹1,250")
        self.assertEqual(size.money(373891.5), "₹373,891.50")
        self.assertEqual(size.money(500000), "₹500,000")

    def test_no_us_market_code_is_left(self):
        with open(SRC, encoding="utf-8") as fh:
            src = fh.read().lower()
        for word in ("alpaca", "new york", "nyse", "eastern", "pdt", "place_stock_order", "edgar"):
            self.assertNotIn(word, src, word)

    def test_whole_shares_are_the_default_because_nse_has_no_fractional_shares(self):
        with open("config.json", "w") as fh:
            json.dump({"budget": 500000, "cash_buffer_pct": 10, "max_position_pct": 25}, fh)
        code, _, err = run(["--weights", "RELIANCE=60,TCS=40", "--config", "config.json"])
        self.assertEqual(code, 1)
        self.assertIn("whole shares need a price for: RELIANCE, TCS", err)
        with open("prices.json", "w") as fh:
            json.dump({"prices": PRICES}, fh)
        code, text, err = run(["--weights", "RELIANCE=60,TCS=40", "--config", "config.json",
                               "--prices", "prices.json", "--json"])
        self.assertEqual(code, 0, err)
        out = json.loads(text)
        self.assertTrue(out["result"]["whole_shares"])
        self.assertTrue(all("qty" in o and "notional" not in o for o in out["orders"]))


class WeightsMode(InTempDir):
    """The long-term split, kept behind --weights / --targets: Rs 5,00,000, 10% cash, 25% cap."""

    def check_raw(self, r):
        self.assertEqual(rupees(r), RAW_SPLIT)
        self.assertEqual(r["cash_buffer"], 50000)
        self.assertEqual(r["invested"], 450000)
        self.assertEqual(r["idle"], 0)
        self.assertEqual(r["cap"], 125000)
        self.assertEqual([p["ticker"] for p in r["positions"] if p["capped"]], ["RELIANCE"])

    def test_from_the_raw_weights(self):
        self.check_raw(size.size(rows(TOP5), v1()))

    def test_from_the_example_targets_file(self):
        r = size.size(rows(TARGETS), v1())
        self.assertEqual(rupees(r), TARGETS_SPLIT)
        self.assertEqual(r["invested"], 450000)
        self.assertEqual([p["ticker"] for p in r["positions"] if p["capped"]], ["RELIANCE"])
        self.assertIn("RELIANCE", [p["ticker"] for p in r["positions"]])
        self.assertNotIn("AAPL", rupees(r))

    def test_from_inline_weights(self):
        w = size.parse_weights(size.weights_from_arg("reliance=22.04,TCS=17.14,INFY=12.62,HDFCBANK=10.86,ITC=9.20"))
        self.check_raw(size.size(w, v1()))

    def test_renormalised_weights(self):
        r = size.size(rows(TOP5), v1())
        shown = {p["ticker"]: round(p["weight"], 1) for p in r["positions"]}
        self.assertEqual(shown, {"RELIANCE": 30.7, "TCS": 23.9, "INFY": 17.6, "HDFCBANK": 15.1, "ITC": 12.8})

    def test_one_decimal_weights_stay_close_to_the_raw_split(self):
        w = size.parse_weights({"RELIANCE": 30.7, "TCS": 23.9, "INFY": 17.6, "HDFCBANK": 15.1, "ITC": 12.8})
        r = size.size(w, v1())
        self.assertEqual(r["invested"], 450000)
        self.assertEqual(rupees(r), {"RELIANCE": 125000, "TCS": 111924, "INFY": 82421, "HDFCBANK": 70713,
                                     "ITC": 59942})
        for t, v in rupees(r).items():
            # rounding a weight to 0.1 point moves a slice by at most 0.05% of the Rs 4,50,000
            self.assertLessEqual(abs(v - RAW_SPLIT[t]), 225, t)

    def test_orders_are_whole_rupee_notional_orders_with_no_stop(self):
        r = size.size(rows(TOP5), v1())
        orders = size.orders_from(r, "a long-term split")
        self.assertEqual(orders[0], {"symbol": "RELIANCE", "side": "buy", "notional": "125000", "type": "market",
                                     "time_in_force": "day", "why": orders[0]["why"]})
        self.assertIn("capped at 25%", orders[0]["why"])
        self.assertIn("₹500,000 budget", orders[0]["why"])
        for o in orders:
            self.assertIsInstance(o["notional"], str)
            self.assertNotIn("qty", o)
            self.assertNotIn("order_class", o)
        self.assertEqual([o["notional"] for o in orders], ["125000", "111813", "82326", "70845", "60016"])

    def test_cents_mode_keeps_the_total(self):
        r = size.size(rows(TOP5), v1(), cents=True)
        d = rupees(r)
        self.assertEqual(d["RELIANCE"], 125000)
        self.assertAlmostEqual(sum(d.values()), 450000, places=2)
        self.assertAlmostEqual(d["TCS"], 111812.53, places=2)
        self.assertEqual(size.orders_from(r)[1]["notional"], "111812.53")

    def test_cap_redistributes_until_nobody_is_over(self):
        w = size.parse_weights({"RELIANCE": 60, "HDFCBANK": 30, "ITC": 10})
        r = size.size(w, {"budget": 100000, "cash_buffer_pct": 0, "max_position_pct": 40, "min_order_inr": 500})
        self.assertEqual(rupees(r), {"RELIANCE": 40000, "HDFCBANK": 40000, "ITC": 20000})
        self.assertEqual(sorted(p["ticker"] for p in r["positions"] if p["capped"]), ["HDFCBANK", "RELIANCE"])

    def test_everything_capped_leaves_money_idle(self):
        w = size.parse_weights({"RELIANCE": 50, "ITC": 50})
        r = size.size(w, v1())
        self.assertEqual(rupees(r), {"RELIANCE": 125000, "ITC": 125000})
        self.assertEqual(r["idle"], 200000)
        text = size.report(r, size.orders_from(r))
        self.assertIn("every stock is at its cap", text)
        self.assertIn("Left idle ₹200,000", text)

    def test_slices_under_the_minimum_are_dropped_and_shared_out(self):
        w = size.parse_weights({"RELIANCE": 90, "ITC": 7, "SBIN": 3})
        r = size.size(w, {"budget": 10000, "cash_buffer_pct": 10, "max_position_pct": 100, "min_order_inr": 500,
                          "fractional": True})
        self.assertNotIn("SBIN", rupees(r))                     # its slice is Rs 270, under the Rs 500 minimum
        self.assertEqual(sum(rupees(r).values()), 9000)
        self.assertTrue(all(v >= 500 for v in rupees(r).values()))
        self.assertEqual(r["dropped"][0]["ticker"], "SBIN")
        self.assertIn("₹500 minimum order", r["dropped"][0]["reason"])

    def test_blocked_tickers_are_dropped_first(self):
        r = size.size(rows(TOP5), v1(blocked=["itc"]))
        self.assertEqual(rupees(r), {"RELIANCE": 125000, "TCS": 125000, "INFY": 107496, "HDFCBANK": 92504})
        self.assertEqual(r["invested"], 450000)
        self.assertIn("blocked", r["dropped"][0]["reason"])

    def test_top_n(self):
        r = size.size(rows(TOP5), v1(max_position_pct=100), top=2)
        self.assertEqual(sorted(rupees(r)), ["RELIANCE", "TCS"])

    def test_whole_shares_need_prices(self):
        with self.assertRaises(size.SizeError):
            size.size(rows(TOP5), v1(fractional=False))
        with self.assertRaises(size.SizeError):
            size.size(rows(TOP5), cfg())          # the example config is whole shares

    def test_whole_shares_with_prices(self):
        r = size.size(rows(TOP5), v1(fractional=False), prices=PRICES)
        self.assertTrue(r["whole_shares"])
        for p in r["positions"]:
            self.assertEqual(p["shares"], int(p["shares"]))
            self.assertAlmostEqual(p["dollars"], p["shares"] * PRICES[p["ticker"]], places=2)
            self.assertLessEqual(p["dollars"], 125000)
        self.assertAlmostEqual(r["invested"] + r["idle"], 450000, places=2)
        self.assertLess(r["idle"], min(PRICES.values()) * 2)
        orders = size.orders_from(r)
        self.assertTrue(all("qty" in o and "notional" not in o for o in orders))
        self.assertEqual([o["symbol"] for o in orders], [p["ticker"] for p in r["positions"]])

    def test_no_budget_and_duplicates_are_errors(self):
        with self.assertRaises(size.SizeError):
            size.size(rows(TOP5), {"cash_buffer_pct": 10})
        with self.assertRaises(size.SizeError):
            size.size([{"ticker": "ITC", "name": "", "weight": 1}, {"ticker": "ITC", "name": "", "weight": 2}], v1())

    def test_cli(self):
        with open("config.json", "w") as fh:
            json.dump(v1(), fh)
        out = os.path.join("trader", "orders.json")
        code, text, _ = run(["--targets", TARGETS, "--config", "config.json", "--write", out])
        self.assertEqual(code, 0)
        self.assertIn("require_stop", text)
        self.assertIn("₹450,000", text)
        self.assertNotIn("$", text)
        with open(out) as fh:
            doc = json.load(fh)
        self.assertEqual([o["symbol"] for o in doc["orders"]], ["RELIANCE", "HDFCBANK", "INFY", "ITC", "SBIN"])
        code, text, _ = run(["--targets", TARGETS, "--config", "config.json", "--json"])
        self.assertEqual(json.loads(text)["result"]["idle"], 0)
        code, _, err = run(["--targets", "/nonexistent/targets.json", "--config", "config.json"])
        self.assertEqual(code, 1)
        self.assertIn("no weights file", err)


class Costs(unittest.TestCase):
    """Groww intraday charges, hand-computed from the rates in size.COST_RATES (groww.in/pricing, 2026-11-07)."""

    def test_the_reliance_round_trip_at_the_stop_by_hand(self):
        c = size.trade_costs(285, 1311.90, 1300.80, size.cost_rates({}))
        # buy 373,891.50, sell 370,728.00. Brokerage is 0.1% but capped at 20 a leg.
        self.assertEqual(c["brokerage"], 40.0)
        self.assertEqual(c["stt"], 92.68)               # 0.025% of the sell value only
        self.assertEqual(c["stamp"], 11.22)             # 0.003% of the buy value only
        self.assertEqual(c["sebi"], 0.74)
        self.assertEqual(c["ipft"], 0.74)
        self.assertEqual(c["exchange"], 22.11)          # 0.00297% of each leg, rounded per leg
        self.assertEqual(c["gst"], 11.45)               # 18% of brokerage + exchange + SEBI + IPFT, not STT or stamp
        self.assertEqual(c["total"], 178.94)

    def test_brokerage_has_a_five_rupee_floor_and_a_percent_below_the_cap(self):
        r = size.cost_rates({})
        tiny = size.trade_costs(1, 100.0, 100.0, r)     # 0.1% of 100 is 0.10: the minimum of 5 applies, per leg
        self.assertEqual(tiny["brokerage"], 10.0)
        mid = size.trade_costs(10, 1000.0, 1000.0, r)   # 0.1% of 10,000 is 10, under the 20 cap
        self.assertEqual(mid["brokerage"], 20.0)

    def test_costs_can_be_turned_off_or_overridden(self):
        self.assertIsNone(size.cost_rates({"include_costs": False}))
        self.assertEqual(size.trade_costs(100, 500, 495, None)["total"], 0.0)
        r = size.cost_rates({"cost_rates": {"brokerage_cap_inr": 0, "brokerage_min_inr": 0}})
        self.assertEqual(size.trade_costs(285, 1311.90, 1300.80, r)["brokerage"], 0.0)

    def test_the_stop_loss_includes_the_charges_so_the_limit_holds(self):
        s = size.size_trade(plan(), cfg(max_loss_per_trade_inr=3000), today=TODAY)
        self.assertEqual(s["bound_by"], "max loss")
        self.assertLessEqual(s["net_loss_at_stop"], 3000)                   # price loss plus charges fits the limit
        n = s["qty"] + 1                                                    # one more share would break it
        over = n * 11.10 + size.trade_costs(n, 1311.90, 1300.80, size.cost_rates({}))["total"]
        self.assertGreater(over, 3000)
        self.assertLess(s["qty"], 270)                                      # fewer than the no-cost 270

    def test_the_report_and_net_reward_risk(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertEqual(s["qty"], 285)
        self.assertEqual(s["net_loss_at_stop"], round(3163.5 + s["costs_at_stop"]["total"], 2))
        self.assertEqual(s["net_gain_at_target"], round(3163.5 - s["costs_at_target"]["total"], 2))
        self.assertLess(s["net_reward_risk"], s["reward_risk"])             # charges always shrink the ratio
        self.assertEqual(s["net_reward_risk"], 0.89)
        text = size.plan_report(s, size.bracket_order(s))
        self.assertIn("Charges (brokerage, STT, exchange, GST, stamp)", text)
        self.assertIn("net reward:risk 0.89", text)

    def test_a_small_account_trade_where_charges_halve_the_reward(self):
        small = cfg(budget=8106, max_loss_per_trade_inr=100, max_order_inr=8000, min_order_inr=5)
        s = size.size_trade(plan(), small, today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (4, "position cap"))
        self.assertEqual(s["net_reward_risk"], 0.51)                        # a 1:1 plan pays about half a unit per unit risked
        self.assertIsNone(s["refusal"])

    def test_a_trade_whose_charges_eat_the_whole_gain_is_refused(self):
        tight = plan(entry=1311.90, stop=1311.60, target=1312.20)           # 1:1, but only 0.30 a share either way
        s = size.size_trade(tight, cfg(max_loss_per_trade_inr=500), today=TODAY)
        self.assertIsNotNone(s["refusal"])
        self.assertIn("charges", s["refusal"])
        self.assertIsNone(size.bracket_order(s))

    def test_the_example_config_has_no_cost_keys_so_defaults_apply(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertTrue(s["costs_included"])
        self.assertFalse(size.size_trade(plan(), cfg(include_costs=False), today=TODAY)["costs_included"])


if __name__ == "__main__":
    unittest.main()
