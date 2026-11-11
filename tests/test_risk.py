import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

check = load("trader-risk", "check")
ticket = load("trader-broker", "ticket")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
REL_PATH = os.path.join(FIX, "trader-risk", "orders-reliance.json")
BASKET_PATH = os.path.join(FIX, "trader-risk", "orders-basket.json")
NOW = "2026-10-19 10:15"            # a Monday, 10:15 IST
TODAY = "2026-10-19"
BASKET_TODAY = "2026-10-13"         # a Tuesday
PRICES = {"RELIANCE": 1311.90, "HDFCBANK": 1119.00, "TCS": 3820.55, "ITC": 412.35, "SBIN": 812.40}
QUOTE_TIME = datetime.datetime(2026, 10, 19, 5, 22, 13)   # a quote stamped 10:52:13 IST, as naive UTC
# long-term checks only: every day-trade check switched off
BASKET = {"budget": 500000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 200000,
          "min_order_inr": 500, "max_orders_per_day": 10, "require_stop": False, "no_entries_before": None,
          "no_entries_after": None, "max_trades_per_day": 0, "max_daily_loss_inr": 0,
          "max_loss_per_trade_inr": 0, "min_reward_risk": 0}
OFFICIAL_2026 = ["2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03",
                 "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02",
                 "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25"]


def at(text):
    return check.parse_now(text)


def cfg(**over):
    with open(CFG_PATH) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def basket_cfg(**over):
    c = dict(BASKET)
    c.update(over)
    return c


def rel(**over):
    with open(REL_PATH) as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def bracket(sym, qty, entry, stop, target):
    return {"symbol": sym, "side": "buy", "qty": str(qty), "type": "market", "time_in_force": "day",
            "order_class": "bracket", "take_profit_limit_price": "%.2f" % target,
            "stop_loss_stop_price": "%.2f" % stop,
            "plan": {"entry": entry, "stop": stop, "target": target}}


def basket_orders():
    with open(BASKET_PATH) as fh:
        return json.load(fh)["orders"]


def buy(sym, inr):
    return {"symbol": sym, "side": "buy", "notional": str(inr), "type": "market", "time_in_force": "day"}


def placed(sym, side="buy", day=TODAY, max_loss=3163.5, kind=None, status="placed"):
    r = {"symbol": sym, "side": side, "status": status, "date": day, "at": day + "T10:20:00+05:30"}
    if max_loss is not None:
        r["max_loss"] = max_loss
    if kind:
        r["kind"] = kind
    return r


def verdicts(res):
    return [r["verdict"] for r in res["results"]]


def reasons(res, i=0):
    return " | ".join(res["results"][i]["reasons"])


def warnings(res, i=0):
    return " | ".join(res["results"][i]["warnings"])


class TheReliance(unittest.TestCase):
    def test_the_reliance_bracket_passes_every_check(self):
        res = check.check([rel()], cfg(), now=at(NOW))
        r = res["results"][0]
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertEqual(r["reasons"], [])
        self.assertEqual(r["warnings"], [])
        self.assertEqual((r["kind"], r["entry_ref"], r["stop"], r["target"]), ("entry", 1311.9, 1300.8, 1323.0))
        self.assertEqual((r["risk_per_share"], r["max_loss"], r["reward_risk"]), (11.1, 3163.5, 1.0))
        self.assertEqual(r["value_inr"], 373891.5)
        self.assertEqual(res["cash_after"], 126108.5)
        self.assertEqual(res["date"], TODAY)
        self.assertIs(res["paper"], True)

    def test_the_result_is_stamped_in_ist(self):
        res = check.check([rel()], cfg(), now=at(NOW))
        self.assertEqual(res["now_ist"], "2026-10-19 10:15")
        self.assertNotIn("now_new_york", res)

    def test_too_many_shares_is_blocked_by_the_max_loss_only(self):
        c = cfg(budget=1000000, max_order_inr=1000000, max_position_pct=100, cash_buffer_pct=0)
        res = check.check([rel(qty="451")], c, now=at(NOW))
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertEqual(res["results"][0]["reasons"],
                         ["loss: 451 shares x 11.10 risk a share = ₹5,006.10 at the stop, "
                          "over your ₹5,000 max loss per trade"])

    def test_a_big_order_trips_size_cash_and_position(self):
        res = check.check([rel(qty="400")], cfg(), now=at(NOW))
        why = reasons(res)
        self.assertIn("size: ₹524,760 is over your ₹400,000 max order", why)
        self.assertIn("cash: needs ₹524,760 but only ₹500,000 cash", why)
        self.assertIn("over your 75% cap", why)

    def test_fingerprint_is_shared_with_the_broker(self):
        o = rel()
        self.assertEqual(check.fingerprint(o), ticket.fingerprint(o))
        self.assertEqual(check.fingerprint(dict(o, why="anything", plan={})), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, qty="284")), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, stop_loss_stop_price="1300.00")), check.fingerprint(o))
        self.assertEqual(len(check.fingerprint(o)), 16)

    def test_broker_fields_are_shared_and_the_alpaca_name_is_gone(self):
        self.assertEqual(check.BROKER_FIELDS, ticket.BROKER_FIELDS)
        self.assertIn("stop_loss_stop_price", check.BROKER_FIELDS)
        self.assertFalse(hasattr(check, "ALPACA_FIELDS"))
        self.assertFalse(hasattr(ticket, "ALPACA_FIELDS"))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "state", "risk.json")
            none = os.path.join(d, "none")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", REL_PATH, "--config", CFG_PATH, "--portfolio", none,
                                   "--prices", none, "--state", none, "--now", NOW, "--out", out])
            text = buf.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("PASS  #1 buy RELIANCE 285 shares, entry", text)
            self.assertIn("entry 1311.90 (the plan's entry), stop 1300.80, target 1323.00: risk 11.10 a share",
                          text)
            self.assertIn("max loss ₹3,163.50 (limit ₹5,000), reward:risk 1.00 (min 1)", text)
            self.assertIn("1 passed, 0 blocked at 2026-10-19 10:15 IST", text)
            self.assertIn("not financial advice", text)
            self.assertNotIn("$", text)
            self.assertNotIn("New York", text)
            with open(out) as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], TODAY)
            self.assertEqual(rec["results"][0]["kind"], "entry")
            self.assertEqual(rec["results"][0]["fingerprint"], check.fingerprint(rel()))


class TheNseCalendar(unittest.TestCase):
    def test_the_holiday_list_is_the_official_2026_list(self):
        self.assertEqual(sorted(check.NSE_HOLIDAYS), OFFICIAL_2026)
        self.assertEqual(check.CALENDAR_YEARS, (2026,))
        self.assertEqual(check.NSE_HOLIDAYS["2026-11-10"], "Diwali (Balipratipada)")
        self.assertEqual(check.NSE_HOLIDAYS["2026-04-03"], "Good Friday")

    def test_every_listed_holiday_is_a_closed_weekday(self):
        for day in OFFICIAL_2026:
            d = datetime.date.fromisoformat(day)
            self.assertLess(d.weekday(), 5, day)
            ok, why = check.trading_day(d)
            self.assertFalse(ok, day)
            self.assertTrue(why.startswith("NSE holiday: "), why)
            self.assertTrue(check.trading_day(d + datetime.timedelta(days=7))[0] or
                            (d + datetime.timedelta(days=7)).isoformat() in check.NSE_HOLIDAYS, day)

    def test_weekends_and_ordinary_days(self):
        self.assertEqual(check.trading_day(datetime.date(2026, 10, 17)), (False, "weekend"))
        self.assertEqual(check.trading_day(datetime.date(2026, 10, 18)), (False, "weekend"))
        self.assertEqual(check.trading_day(datetime.date(2026, 10, 19)), (True, ""))

    def test_us_market_days_are_ordinary_nse_days(self):
        for day in ("2026-11-26", "2026-07-03", "2026-09-07"):     # Thanksgiving, US Independence Day (observed), Labor Day
            self.assertTrue(check.trading_day(datetime.date.fromisoformat(day))[0], day)

    def test_the_session_is_nine_fifteen_to_three_thirty(self):
        self.assertEqual(check.OPEN, datetime.time(9, 15))
        self.assertEqual(check.CLOSE, datetime.time(15, 30))

    def test_no_early_close_and_no_new_york_left(self):
        for name in ("NYSE_HOLIDAYS", "NYSE_EARLY_CLOSE", "EARLY", "to_eastern", "eastern_to_utc", "eastern_offset"):
            self.assertFalse(hasattr(check, name), name)

    def test_business_days_skip_weekends_and_nse_holidays(self):
        # Thu 12th back: 11th, then the 10th is Diwali (Balipratipada), then Mon 9th, a weekend, Fri 6th, Thu 5th
        got = check.business_days(datetime.date(2026, 11, 12), 5)
        self.assertEqual(got, ["2026-11-12", "2026-11-11", "2026-11-09", "2026-11-06", "2026-11-05"])


class Ist(unittest.TestCase):
    def test_ist_is_utc_plus_five_thirty_all_year(self):
        self.assertEqual(check.IST_OFFSET, datetime.timedelta(hours=5, minutes=30))
        self.assertEqual(at("2026-10-19 10:15"), datetime.datetime(2026, 10, 19, 4, 45))
        self.assertEqual(at("2026-12-01 10:15"), datetime.datetime(2026, 12, 1, 4, 45))
        self.assertEqual(at("2026-06-01 10:15"), datetime.datetime(2026, 6, 1, 4, 45))

    def test_to_ist_and_back(self):
        utc = datetime.datetime(2026, 10, 19, 4, 45)
        self.assertEqual(check.to_ist(utc), datetime.datetime(2026, 10, 19, 10, 15))
        self.assertEqual(check.ist_to_utc(check.to_ist(utc)), utc)

    def test_offsets_are_honoured_and_naive_means_ist(self):
        self.assertEqual(at("2026-10-19T04:45:00Z"), datetime.datetime(2026, 10, 19, 4, 45))
        self.assertEqual(at("2026-10-19 10:15+05:30"), datetime.datetime(2026, 10, 19, 4, 45))
        self.assertEqual(at("2026-10-19 10:15"), at("2026-10-19T04:45:00Z"))

    def test_a_bad_clock_names_ist(self):
        with self.assertRaises(check.RiskError) as cm:
            at("noon")
        self.assertIn("IST", str(cm.exception))


class DayTradeChecks(unittest.TestCase):
    def one(self, order, now=NOW, **over):
        return check.check([order], cfg(**over), now=at(now))

    def test_a_stop_is_required(self):
        o = rel()
        for k in ("order_class", "take_profit_limit_price", "stop_loss_stop_price", "plan"):
            o.pop(k)
        o["plan"] = {"entry": 1311.90}
        res = self.one(o)
        self.assertIn("stop: no stop", reasons(res))
        res = self.one(o, require_stop=False, min_reward_risk=0)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("no stop", warnings(res))

    def test_a_plan_stop_counts_but_warns(self):
        o = rel()
        for k in ("order_class", "take_profit_limit_price", "stop_loss_stop_price"):
            o.pop(k)
        res = self.one(o)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("in the plan only", warnings(res))
        self.assertEqual(res["results"][0]["max_loss"], 3163.5)

    def test_stop_and_target_on_the_right_side(self):
        self.assertIn("must be below the entry 1311.90", reasons(self.one(rel(stop_loss_stop_price="1312.00"))))
        res = self.one(rel(take_profit_limit_price="1311.00"))
        self.assertIn("target: the target 1311.00 must be above the entry 1311.90", reasons(res))

    def test_reward_risk(self):
        res = self.one(rel(take_profit_limit_price="1318.00"))
        self.assertIn("reward:risk: 0.55", reasons(res))
        self.assertEqual(verdicts(self.one(rel(take_profit_limit_price="1318.00"), min_reward_risk=0.5)), ["PASS"])

    def test_a_fresh_higher_price_is_the_entry(self):
        later = at("2026-10-19 10:55")
        res = check.check([rel()], cfg(max_loss_per_trade_inr=4000), prices={"RELIANCE": 1315.00}, now=later,
                          price_times={"RELIANCE": QUOTE_TIME})
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["entry_ref_from"], r["risk_per_share"]), (1315.0, "price", 14.2))
        self.assertIn("loss: 285 shares x 14.20 risk a share = ₹4,047 at the stop, over your ₹4,000",
                      reasons(res))

    def test_a_stale_or_future_price_is_not_used(self):
        for now in ("2026-10-19 11:30", "2026-10-19 10:15"):
            res = check.check([rel()], cfg(), prices={"RELIANCE": 1315.00}, now=at(now),
                              price_times={"RELIANCE": QUOTE_TIME})
            self.assertEqual(verdicts(res), ["PASS"], now)
            self.assertEqual(res["results"][0]["entry_ref"], 1311.9)
            self.assertIn("older than 15 minutes", warnings(res))

    def test_a_lower_price_does_not_shrink_the_risk(self):
        res = check.check([rel()], cfg(), prices={"RELIANCE": 1300.00}, now=at(NOW))
        self.assertEqual(res["results"][0]["entry_ref"], 1311.9)

    def test_a_limit_order_uses_its_limit_price(self):
        o = rel(type="limit", limit_price="1311.00")
        res = check.check([o], cfg(), prices={"RELIANCE": 1315.00}, now=at(NOW))
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["max_loss"], r["verdict"]), (1311.0, 2907.0, "PASS"))

    def test_daily_loss_and_trades_per_day(self):
        two = {"orders": [placed("HDFCBANK"), placed("TCS")]}
        res = check.check([rel()], cfg(), state=two, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("today before this: 2 of 3 trades, ₹6,327 of ₹15,000 daily loss",
                      res["results"][0]["info"][-1])
        three = {"orders": [placed(s, max_loss=5000) for s in ("HDFCBANK", "TCS", "INFY")]}
        res = check.check([rel()], cfg(), state=three, now=at(NOW))
        why = reasons(res)
        self.assertIn("trades: 3 entries placed today", why)
        self.assertIn("daily loss: ₹15,000 already at risk today plus ₹3,163.50 for this one = "
                      "₹18,163.50, over your ₹15,000", why)
        yesterday = {"orders": [placed(s, day="2026-10-16", max_loss=5000) for s in ("HDFCBANK", "TCS", "INFY")]}
        self.assertEqual(verdicts(check.check([rel()], cfg(), state=yesterday, now=at(NOW))), ["PASS"])

    def test_exits_and_declines_do_not_count_as_trades(self):
        st = {"orders": [placed("HDFCBANK"), placed("HDFCBANK", side="sell", kind="exit", max_loss=None),
                         placed("TCS", status="declined"), placed("INFY", status="failed")]}
        res = check.check([rel()], cfg(), state=st, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertEqual(res["today"]["entries_placed"], 1)

    def test_the_batch_counts_toward_the_daily_limits(self):
        orders = [bracket(s, 1, 500.0, 100.0, 900.0) for s in ("AAA", "BBB", "CCC", "DDD")]
        res = check.check(orders, cfg(max_daily_loss_inr=1500), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "PASS", "BLOCK"])
        why = reasons(res, 3)
        self.assertIn("trades: 0 entries placed today plus 3 passed earlier", why)
        self.assertIn("daily loss: ₹1,200 already at risk today plus ₹400 for this one = ₹1,600", why)

    def test_entry_window_ist(self):
        cases = [("2026-10-19 09:44", "no new entries before 09:45 IST (it is 09:44)"),
                 ("2026-10-19 09:14", "the market is closed (Mon 2026-10-19 09:14 IST; the session is 09:15 to 15:30)"),
                 ("2026-10-19 08:00", "the market is closed (Mon 2026-10-19 08:00"),
                 ("2026-10-19 15:30", "the market is closed (Mon 2026-10-19 15:30 IST"),
                 ("2026-10-19 16:05", "the market is closed"),
                 ("2026-10-17 10:15", "the market is closed today (weekend"),
                 ("2026-11-10 10:15", "the market is closed today (NSE holiday: Diwali (Balipratipada)"),
                 ("2026-04-03 10:15", "the market is closed today (NSE holiday: Good Friday"),
                 ("2026-01-15 10:15", "the market is closed today (NSE holiday: Municipal Corporation Election"),
                 ("2026-11-02T04:10:00Z", "no new entries before 09:45 IST (it is 09:40)"),
                 ("2026-10-19T10:00:00Z", "the market is closed (Mon 2026-10-19 15:30 IST")]
        for now, want in cases:
            res = self.one(rel(), now=now)
            self.assertEqual(verdicts(res), ["BLOCK"], now)
            self.assertIn("window: " + want, reasons(res), now)
        for now in ("2026-10-19 09:45", "2026-10-19 15:29", "2026-11-02T04:15:00Z", "2026-10-30T05:00:00Z",
                    "2026-10-19T09:59:00Z"):
            self.assertEqual(verdicts(self.one(rel(), now=now)), ["PASS"], now)
        res = self.one(rel(), now="2026-10-17 10:15", no_entries_before=None, no_entries_after=None)
        self.assertEqual(verdicts(res), ["PASS"])
        with self.assertRaises(check.RiskError) as cm:
            self.one(rel(), no_entries_before="9.45am")
        self.assertIn("IST", str(cm.exception))

    def test_a_late_cutoff_blocks_new_entries_after_it(self):
        res = self.one(rel(), now="2026-10-19 15:01", no_entries_after="15:00")
        self.assertIn("window: no new entries after 15:00 IST (it is 15:01)", reasons(res))
        self.assertEqual(verdicts(self.one(rel(), now="2026-10-19 15:00", no_entries_after="15:00")), ["PASS"])

    def test_nse_has_no_early_close(self):
        # Fri 27 Nov and Thu 24 Dec are shortened days on US exchanges, ordinary days on NSE
        for now in ("2026-11-27 13:05", "2026-12-24 15:00", "2026-11-27 12:31"):
            res = self.one(rel(), now=now)
            self.assertEqual(verdicts(res), ["PASS"], now)
            self.assertNotIn("early close", " ".join(res["warnings"] + res["results"][0]["warnings"]), now)

    def test_a_date_outside_the_holiday_list_warns(self):
        res = self.one(rel(), now="2027-01-04 10:15")
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertTrue(any("the holiday list covers 2026 only: check nseindia.com for 2027-01-04" in w
                            for w in res["warnings"]), res["warnings"])
        self.assertFalse(any("covers" in w for w in self.one(rel())["warnings"]))

    def test_the_window_uses_ist_not_the_machine(self):
        res = self.one(rel(), now="2026-10-19T04:45:00Z")     # 10:15 IST, 00:45 in New York
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertEqual(res["now_ist"], "2026-10-19 10:15")
        res = self.one(rel(), now="2026-10-19T14:45:00Z")     # 20:15 IST, still 10:45 in New York
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertIn("the market is closed", reasons(res))

    def test_there_is_no_pdt_rule(self):
        def trade(day):
            return [placed("X" + day[-2:], day=day), placed("X" + day[-2:], side="sell", day=day, kind="exit",
                                                            status="filled", max_loss=None)]
        st = {"orders": trade("2026-10-13") + trade("2026-10-14") + trade("2026-10-15") + trade("2026-10-16")}
        for guard in (False, True):
            res = check.check([rel()], cfg(pdt_guard=guard), state=st, now=at(NOW))
            self.assertEqual(verdicts(res), ["PASS"], guard)
            self.assertNotIn("pdt", (reasons(res) + warnings(res)).lower())
        pending = {"orders": trade("2026-10-14") + trade("2026-10-15") + [placed("AMD")]}
        res = check.check([rel()], cfg(pdt_guard=True), state=pending, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertNotIn("pdt_guard", check.DEFAULTS)

    def test_a_short_entry_when_allowed(self):
        short = {"symbol": "RELIANCE", "side": "sell", "qty": "285", "type": "market", "time_in_force": "day",
                 "order_class": "bracket", "take_profit_limit_price": "1300.80", "stop_loss_stop_price": "1323.00",
                 "plan": {"entry": 1311.90}}
        res = self.one(short)
        self.assertIn("short: selling more RELIANCE than you hold", reasons(res))
        res = self.one(short, allow_short=True)
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"], r["max_loss"], r["reward_risk"]), ("PASS", "entry", 3163.5, 1.0))
        res = self.one(dict(short, stop_loss_stop_price="1305.00"), allow_short=True)
        self.assertIn("must be above the entry 1311.90 for a sell", reasons(res))

    def test_bracket_format(self):
        self.assertIn("whole-share qty", reasons(self.one(dict(rel(qty=None), notional="373000"))))
        self.assertIn("whole shares only", reasons(self.one(rel(qty="285.5"))))
        self.assertIn("time_in_force day or gtc", reasons(self.one(rel(time_in_force="ioc"))))
        self.assertEqual(verdicts(self.one(rel(time_in_force="gtc"))), ["PASS"])
        o = rel()
        o.pop("take_profit_limit_price")
        self.assertIn("needs both take_profit_limit_price and stop_loss_stop_price", reasons(self.one(o)))
        self.assertIn("needs a stop_loss_stop_price", reasons(self.one(dict(rel(), stop_loss_stop_price=None,
                                                                            stop_loss_limit_price="1299.00"))))
        w = warnings(self.one(rel(stop_loss_stop_price=1300.8)))
        self.assertIn("as a string", w)
        self.assertIn("place_nse_order", w)

    def test_extended_hours_flag_still_needs_a_limit_order(self):
        # a leftover broker field: NSE has no extended-hours session, but the format rule is still enforced
        lim = {"symbol": "ITC", "side": "buy", "qty": "10", "type": "limit", "limit_price": "410",
               "time_in_force": "gtc", "extended_hours": True}
        res = check.check([lim], basket_cfg(), prices=PRICES, today=BASKET_TODAY)
        self.assertEqual(verdicts(res), ["PASS"])
        res = check.check([dict(lim, type="market", limit_price=None)], basket_cfg(), prices=PRICES,
                          today=BASKET_TODAY)
        self.assertIn("extended hours needs a limit order", reasons(res))


class Exits(unittest.TestCase):
    PF = {"cash": 132668.0, "positions": [{"ticker": "RELIANCE", "shares": 280,
                                            "lots": [{"date": TODAY, "shares": 280, "cost": 367332.0}]}]}
    SELL = {"symbol": "RELIANCE", "side": "sell", "qty": "280", "type": "market", "time_in_force": "day"}

    def test_getting_out_is_never_blocked_by_the_day_limits(self):
        full = {"orders": [placed("RELIANCE"), placed("HDFCBANK"), placed("TCS")] +
                          [placed("T%d" % i, max_loss=1) for i in range(6)] +
                          [placed("RELIANCE", side="sell", kind="exit", max_loss=None)]}
        c = cfg(blocked=["RELIANCE"], max_order_inr=1000, max_daily_loss_inr=500)
        res = check.check([self.SELL], c, portfolio=self.PF, state=full, now=at("2026-10-19 15:45"))
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"]), ("PASS", "exit"), r["reasons"])
        why = warnings(res)
        for word in ("blocked:", "duplicate:", "daily limit:"):
            self.assertIn("exit, not blocked: " + word, why)
        self.assertNotIn("price", why)
        res = check.check([self.SELL], cfg(), portfolio=self.PF, now=at("2026-10-17 12:00"))
        self.assertEqual(verdicts(res), ["PASS"])

    def test_a_partial_exit_and_the_rest(self):
        half = dict(self.SELL, qty="140")
        res = check.check([half, half], cfg(), portfolio=self.PF, prices={"RELIANCE": 1315.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual([r["kind"] for r in res["results"]], ["exit", "exit"])
        self.assertIn("exit, not blocked: duplicate", warnings(res, 1))
        res = check.check([half, half, half], cfg(), portfolio=self.PF, prices={"RELIANCE": 1315.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "BLOCK"])
        self.assertIn("short:", reasons(res, 2))

    def test_format_asset_and_short_still_apply_to_a_sell(self):
        res = check.check([dict(self.SELL, type="limit")], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("needs a limit_price", reasons(res))
        res = check.check([dict(self.SELL, qty="281")], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("short: selling more RELIANCE than you hold (280 shares)", reasons(res))
        res = check.check([self.SELL], cfg(), now=at(NOW))
        self.assertIn("record the fill first", reasons(res))
        res = check.check([dict(self.SELL, symbol="NIFTY26NOVFUT")], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("asset:", reasons(res))

    def test_buy_then_sell_in_one_batch(self):
        res = check.check([rel(), dict(self.SELL, qty="285")], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual(res["results"][1]["kind"], "exit")


class Tickers(unittest.TestCase):
    def one(self, sym, **over):
        o = dict(buy(sym, 10000))
        return check.check([o], basket_cfg(**over), today=BASKET_TODAY)["results"][0]

    def test_real_nse_symbols_pass_the_asset_check(self):
        for sym in ("RELIANCE", "HDFCBANK", "M&M", "BAJAJ-AUTO", "360ONE", "RELIANCE.NS", "TATAMOTORS.BO",
                    "reliance", "ITC"):
            r = self.one(sym)
            self.assertEqual(r["verdict"], "PASS", (sym, r["reasons"]))
            self.assertEqual(r["symbol"], sym.upper())

    def test_futures_options_crypto_and_junk_are_blocked(self):
        for sym in ("NIFTY26NOVFUT", "NIFTY26NOV24000CE", "BANKNIFTY26NOV52000PE", "BTC/USD", "RELIANCE INDUSTRIES",
                    "-ABC", "123456", "AAAAAAAAAAA", "HDFC$", "RELIANCE.NSE"):
            r = self.one(sym)
            self.assertEqual(r["verdict"], "BLOCK", sym)
            why = " ".join(r["reasons"])
            self.assertIn("asset: ", why, sym)
            self.assertIn("NSE/BSE stocks and ETFs only", why, sym)

    def test_futures_and_options_stay_blocked_even_when_options_are_allowed(self):
        for sym in ("NIFTY26NOV24000CE", "NIFTY26NOVFUT"):
            self.assertEqual(self.one(sym, allow_options=True)["verdict"], "BLOCK", sym)

    def test_the_old_us_option_symbol_is_still_an_option(self):
        opt = {"symbol": "BE270617C00100000", "side": "buy", "qty": "100", "type": "limit",
               "limit_price": "12.50", "time_in_force": "day"}
        r = check.check([opt], basket_cfg(), prices=PRICES, today=BASKET_TODAY)["results"][0]
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("option", " ".join(r["reasons"]))
        r = check.check([opt], basket_cfg(allow_options=True), prices=PRICES, today=BASKET_TODAY)["results"][0]
        self.assertEqual(r["verdict"], "PASS")
        self.assertTrue(r["warnings"])


class BasketChecks(unittest.TestCase):
    """The long-term checks still hold with the day-trade checks off."""

    def one(self, order, **over):
        return check.check([order], basket_cfg(**over), prices=PRICES, today=BASKET_TODAY)["results"][0]

    def test_the_five_buys_pass_on_a_fresh_5_lakh_paper_account(self):
        res = check.check(basket_orders(), basket_cfg(), today=BASKET_TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5)
        self.assertEqual(res["cash_after"], 50000)
        self.assertEqual(res["results"][0]["value_inr"], 125000)
        self.assertEqual(res["results"][0]["amount"], "₹125,000")

    def test_a_sixth_buy_is_blocked_three_ways(self):
        res = check.check(basket_orders() + [buy("RELIANCE", 10000)], basket_cfg(), today=BASKET_TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5 + ["BLOCK"])
        why = reasons(res, 5)
        self.assertIn("cash:", why)
        self.assertIn("position:", why)
        self.assertIn("duplicate:", why)

    def test_the_day_trading_config_blocks_buys_without_a_stop(self):
        res = check.check(basket_orders(), cfg(), now=at(NOW))
        self.assertEqual(verdicts(res)[0], "BLOCK")
        self.assertIn("stop: no stop", reasons(res))

    def test_max_and_min_order(self):
        self.assertIn("over your ₹200,000 max order",
                      " ".join(self.one(buy("ITC", 250000), max_position_pct=100)["reasons"]))
        self.assertIn("under your ₹500 minimum", " ".join(self.one(buy("ITC", 200))["reasons"]))

    def test_blocked_list(self):
        r = self.one(buy("ITC", 10000), blocked=["ITC"])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("blocked list", r["reasons"][0])

    def test_format_rules(self):
        both = dict(buy("ITC", 10000), qty="1")
        self.assertIn("exactly one of qty or notional", " ".join(self.one(both)["reasons"]))
        lim = dict(buy("ITC", 10000), type="limit", limit_price="410")
        self.assertIn("market order with time_in_force day", " ".join(self.one(lim)["reasons"]))
        nolimit = {"symbol": "ITC", "side": "buy", "qty": "1", "type": "limit", "time_in_force": "day"}
        self.assertIn("needs a limit_price", " ".join(self.one(nolimit)["reasons"]))
        bad = {"symbol": "ITC", "side": "hold", "notional": "10000"}
        self.assertIn("side must be buy or sell", " ".join(self.one(bad)["reasons"]))

    def test_numbers_should_be_strings(self):
        r = self.one({"symbol": "ITC", "side": "buy", "notional": 10000, "type": "market", "time_in_force": "day"})
        self.assertEqual(r["verdict"], "PASS")
        self.assertIn("as a string", r["warnings"][0])

    def test_share_orders_need_a_price(self):
        q = {"symbol": "ZZZZ", "side": "buy", "qty": "2", "type": "market", "time_in_force": "day"}
        self.assertIn("no price for ZZZZ", " ".join(self.one(q)["reasons"]))
        q["symbol"] = "ITC"
        r = self.one(q)
        self.assertEqual(r["verdict"], "PASS")
        self.assertAlmostEqual(r["value_inr"], 824.70)

    def test_margin(self):
        r = check.check([buy("ITC", 150000)], basket_cfg(cash_buffer_pct=0, max_position_pct=100),
                        portfolio={"cash": 100000, "positions": []}, today=BASKET_TODAY)["results"][0]
        self.assertIn("margin", " ".join(r["reasons"]))

    PF = {"cash": 50000.0, "positions": [{"ticker": "ITC", "shares": 170,
                                           "lots": [{"date": "2026-10-13", "shares": 170, "cost": 70000.0}]}]}

    def test_short_sales_are_blocked(self):
        sell = {"symbol": "ITC", "side": "sell", "qty": "200", "type": "market", "time_in_force": "day"}
        r = check.check([sell], basket_cfg(), portfolio=self.PF, prices=PRICES, today=BASKET_TODAY)["results"][0]
        self.assertIn("short:", " ".join(r["reasons"]))
        sell["qty"] = "60"
        r = check.check([sell], basket_cfg(), portfolio=self.PF, prices=PRICES, today=BASKET_TODAY)["results"][0]
        self.assertEqual(r["verdict"], "PASS")

    def test_a_sell_frees_cash_for_a_later_buy(self):
        orders = [{"symbol": "ITC", "side": "sell", "qty": "100", "type": "market", "time_in_force": "day"},
                  buy("SBIN", 70000)]
        res = check.check(orders, basket_cfg(max_position_pct=100), portfolio=self.PF, prices=PRICES,
                          today=BASKET_TODAY)
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        res = check.check([buy("SBIN", 70000)], basket_cfg(max_position_pct=100), portfolio=self.PF,
                          prices=PRICES, today=BASKET_TODAY)
        self.assertEqual(verdicts(res), ["BLOCK"])

    def test_daily_limit_and_already_placed_today(self):
        st = {"orders": [{"symbol": "T%d" % i, "side": "buy", "status": "placed",
                          "at": BASKET_TODAY + "T10:00:00"} for i in range(10)]}
        res = check.check([buy("ITC", 10000)], basket_cfg(), state=st, today=BASKET_TODAY)
        self.assertIn("daily limit", reasons(res))
        res = check.check([buy("ITC", 10000)], basket_cfg(), state=st, today="2026-10-14")
        self.assertEqual(verdicts(res), ["PASS"])
        itc = {"orders": [{"symbol": "ITC", "side": "buy", "status": "placed", "at": BASKET_TODAY + "T10:00:00"}]}
        res = check.check([buy("ITC", 10000)], basket_cfg(), state=itc, today=BASKET_TODAY)
        self.assertIn("already placed today", reasons(res))
        declined = {"orders": [{"symbol": "ITC", "side": "buy", "status": "declined",
                                "at": BASKET_TODAY + "T10:00:00"}]}
        self.assertEqual(verdicts(check.check([buy("ITC", 10000)], basket_cfg(), state=declined,
                                              today=BASKET_TODAY)), ["PASS"])

    def test_live_account_warns(self):
        res = check.check([buy("ITC", 10000)], basket_cfg(paper=False), today=BASKET_TODAY)
        self.assertIn("LIVE", res["warnings"][0])


class Cli(unittest.TestCase):
    def test_prices_file_freshness(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump({"as_of": "2026-10-19T05:25:00Z", "prices": {"RELIANCE": 1315.00},
                           "detail": {"RELIANCE": {"time": "2026-10-19T05:22:13Z",
                                                   "fetched": "2026-10-19T05:25:00Z"}}}, fh)
            cp = os.path.join(d, "config.json")
            with open(cp, "w") as fh:
                json.dump(cfg(max_loss_per_trade_inr=4000), fh)
            none = os.path.join(d, "none")
            base = ["--orders", REL_PATH, "--config", cp, "--portfolio", none, "--prices", pp,
                    "--state", none, "--no-write"]
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(check.main(base + ["--now", "2026-10-19 10:55"]), 1)
            self.assertIn("285 shares x 14.20 risk a share = ₹4,047 at the stop", buf.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(check.main(base + ["--now", "2026-10-19 11:30"]), 0)

    def test_basket_orders_with_a_long_term_config_file(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "config.json")
            with open(c, "w") as fh:
                json.dump(basket_cfg(), fh)
            none = os.path.join(d, "none")
            out = os.path.join(d, "state", "risk.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", BASKET_PATH, "--config", c, "--portfolio", none, "--prices", none,
                                   "--state", none, "--today", BASKET_TODAY, "--out", out])
            self.assertEqual(code, 0)
            self.assertIn("5 passed, 0 blocked", buf.getvalue())
            self.assertIn("₹", buf.getvalue())
            with open(out) as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], BASKET_TODAY)
            self.assertTrue(all(len(r["fingerprint"]) == 16 for r in rec["results"]))
            bad = os.path.join(d, "orders.json")
            with open(bad, "w") as fh:
                json.dump({"orders": basket_orders() + [buy("RELIANCE", 10000)]}, fh)
            with redirect_stdout(io.StringIO()):
                code = check.main(["--orders", bad, "--config", c, "--portfolio", none, "--prices", none,
                                   "--state", none, "--today", BASKET_TODAY, "--no-write"])
            self.assertEqual(code, 1)

    def test_errors_are_one_line(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", "/nonexistent.json", "--no-write"]), 1)
        self.assertIn("no orders file", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", REL_PATH, "--now", "noon", "--no-write"]), 1)
        self.assertIn("--now wants", err.getvalue())
        self.assertIn("IST", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", REL_PATH, "--today", "monday", "--no-write"]), 1)
        self.assertIn("--today wants", err.getvalue())


if __name__ == "__main__":
    unittest.main()
