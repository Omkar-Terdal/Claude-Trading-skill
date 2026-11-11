"""trader-exit tests. The bars for the ATR stop are tests/fixtures/trader-exit/RELIANCE.NS-2026-11-06-5m.json:
synthetic, deterministic five-minute bars for Friday 2026-11-06 (09:15 IST open), plus a live tick off the grid."""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, FIX, ROOT

exitp = load("trader-exit", "exit")
entry = load("trader-entry", "entry")

RELIANCE = os.path.join(FIX, "trader-exit", "RELIANCE.NS-2026-11-06-5m.json")
SRC = os.path.join(ROOT, "trader-exit", "exit.py")

# trader/plan.json for the example trade, exactly as the contract writes it
PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-11-06", "setup": "orb",
        "entry": 1311.90, "entry_time": "9:50 AM", "entry_bar": 6, "interval": "5m",
        "opening_range": {"high": 1306.00, "low": 1295.60, "minutes": 15},
        "stop": 1300.80, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 1323.00, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": 11.10, "reward_risk": 1.0, "flat_by": "15:20"}
ENTRY = {"created_by": "trader-entry", "symbol": "RELIANCE", "setup": "orb", "side": "buy", "date": "2026-11-06",
         "status": "triggered", "time": "9:50 AM", "bar_start": "09:45", "bar_index": 6, "price": 1311.90,
         "level": 1306.00, "interval": "5m", "opening_range": {"high": 1306.00, "low": 1295.60, "minutes": 15},
         "source": "yahoo", "checked_at": "2026-11-06T04:20:00Z"}
NOW = "2026-11-06 09:51"          # IST, one minute after the 9:45 bar closed


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def c(entry_px, stop, target="2R", oh=1306.00, ol=1295.60, atr=None, n=None):
    sr, sp = exitp.parse_stop(stop)
    tr, tp = exitp.parse_target(target)
    return exitp.compute(entry_px, sr, sp, tr, tp, oh, ol, atr, n)


class Math(unittest.TestCase):
    def test_the_example_or_mid_1r(self):
        p = c(1311.90, "or-mid", "1R")
        self.assertEqual(p["stop"], Decimal("1300.80"))             # (1306.00 + 1295.60) / 2, exact
        self.assertEqual(p["risk"], Decimal("11.10"))
        self.assertEqual(p["target"], Decimal("1323.00"))
        self.assertEqual(p["reward_risk"], Decimal("1.00"))
        self.assertEqual(p["target_label"], "1R (one times the risk)")
        self.assertNotIn("rounded down", p["stop_detail"])

    def test_a_midpoint_that_is_not_a_paisa_rounds_down(self):
        p = c(1311.90, "or-mid", "1R", oh=1306.05)                  # 1300.825
        self.assertEqual(p["stop"], Decimal("1300.82"))
        self.assertEqual(p["risk"], Decimal("11.08"))
        self.assertIn("rounded down", p["stop_detail"])

    def test_the_default_2r(self):
        p = c(1311.90, "or-mid")
        self.assertEqual(p["target"], Decimal("1334.10"))           # 1311.90 + 2 x 11.10
        self.assertEqual(p["target_label"], "2R (two times the risk)")

    def test_raw_yahoo_floats_give_the_same_paise(self):
        p = c(1311.9000244140625, "or-mid", "1R", 1306.0, 1295.5999755859375)
        self.assertEqual((p["entry"], p["stop"], p["target"]),
                         (Decimal("1311.90"), Decimal("1300.80"), Decimal("1323.00")))

    def test_other_stops(self):
        p = c(1311.90, "or-low")
        self.assertEqual((p["stop"], p["risk"], p["target"]),
                         (Decimal("1295.60"), Decimal("16.30"), Decimal("1344.50")))
        p = c(1311.90, "price:1300.809")
        self.assertEqual(p["stop"], Decimal("1300.80"))             # rounds away from the entry
        p = c(1311.90, "pct:1")
        self.assertEqual((p["stop"], p["risk"], p["target"]),
                         (Decimal("1298.78"), Decimal("13.12"), Decimal("1338.14")))
        p = c(1311.90, "atr:1.5", atr=5.2571, n=7)
        self.assertEqual(p["stop"], Decimal("1304.01"))             # 1311.90 - 7.88565 = 1304.01435
        self.assertEqual(p["stop_label"], "1.5 x ATR(7) under the entry")
        self.assertIn("fewer than 14 so far", p["stop_detail"])

    def test_rupee_signs_are_accepted_in_flags_and_never_dollar_signs_in_output(self):
        self.assertEqual(exitp.parse_stop("price:₹1300.80"), ("price", Decimal("1300.80")))
        self.assertEqual(exitp.parse_target("price:₹1323"), ("price", Decimal("1323")))
        self.assertEqual(exitp.money(1300.8), "₹1,300.80")
        self.assertEqual(exitp.money("1334.1"), "₹1,334.10")
        self.assertEqual(exitp.money(0.65), "₹0.65")

    def test_target_rounds_half_up_and_price_targets(self):
        p = c(100, "price:99.01", "1.5R", None, None)
        self.assertEqual(p["target"], Decimal("101.49"))            # 100 + 1.485
        self.assertEqual(p["target_label"], "1.5R (1.5 times the risk)")
        p = c(1311.90, "or-mid", "price:1330")
        self.assertEqual(p["reward_risk"], Decimal("1.63"))
        self.assertEqual(p["target_label"], "your price (1.63R)")
        self.assertEqual(p["target_r"], Decimal("1.63"))

    def test_refusals_say_why(self):
        cases = [("price:1311.90", "2R", "at or above the entry"),
                 ("price:1315", "2R", "at or above the entry"),
                 ("or-mid", "price:1311", "at or below the entry"),
                 ("price:1311.25", "2R", "too tight to survive the spread"),
                 ("pct:0.04", "2R", "too tight to survive the spread")]
        for stop, target, why in cases:
            with self.assertRaises(exitp.PlanError) as cm:
                c(1311.90, stop, target)
            self.assertIn(why, str(cm.exception), stop)
        # 0.05% of 1311.90 is 0.65595: a ₹0.66 risk is allowed, ₹0.65 is not
        self.assertEqual(c(1311.90, "price:1311.24")["risk"], Decimal("0.66"))
        with self.assertRaises(exitp.PlanError) as cm:
            c(1311.90, "price:1311.25")
        self.assertIn("₹0.65", str(cm.exception))
        self.assertIn("₹0.66", str(cm.exception))
        self.assertNotIn("$", str(cm.exception))
        with self.assertRaises(exitp.PlanError):
            c(1295.00, "or-mid")                                    # the midpoint is above this entry
        for bad in ("mid-ish", "pct:100", "price:0", "atr:-1"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_stop(bad)
        for bad in ("0R", "R", "lots", "price:x"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_target(bad)
        with self.assertRaises(exitp.PlanError):
            c(1311.90, "or-mid", oh=None, ol=None)
        with self.assertRaises(exitp.PlanError):
            c(1311.90, "or-mid", oh=1295.60, ol=1306.00)

    def test_atr_on_the_session_bars(self):
        ch = exitp.load_chart("RELIANCE", "5m", None, RELIANCE)
        day = datetime.date(2026, 11, 6)
        bars = exitp.session_bars(ch, day, "5m", upto_slot=6)
        self.assertEqual(len(bars), 7)                              # 9:15 ... the 9:45 entry bar
        self.assertEqual(datetime.datetime.combine(day, exitp.OPEN) + datetime.timedelta(minutes=5 * 6),
                         exitp.to_ist(exitp.utc_of(bars[-1]["t"])))
        # by hand: the true range of each bar, the first one without the overnight gap
        trs, prev = [], None
        for b in bars:
            r = b["h"] - b["l"]
            if prev is not None:
                r = max(r, abs(b["h"] - prev), abs(b["l"] - prev))
            trs.append(r)
            prev = b["c"]
        self.assertAlmostEqual(trs[0], 1306.00 - 1295.60, places=4)
        atr, n = exitp.atr_of(bars)
        self.assertEqual(n, 7)
        self.assertAlmostEqual(atr, sum(trs) / 7, places=9)
        self.assertAlmostEqual(atr, 5.2571, places=4)
        # later in the day it uses the last 14 bars; the in-progress 10:35 bar and the 10:38:25 tick never count
        all_bars = exitp.session_bars(ch, day, "5m")
        self.assertEqual(len(all_bars), 16)
        self.assertEqual(exitp.atr_of(all_bars)[1], 14)
        self.assertAlmostEqual(exitp.atr_of(all_bars)[0], 3.4714, places=4)


class NseCalendarAndClock(unittest.TestCase):
    def test_session_is_0915_to_1530_ist(self):
        self.assertEqual(exitp.OPEN, datetime.time(9, 15))
        self.assertEqual(exitp.CLOSE, datetime.time(15, 30))
        self.assertEqual(exitp.session(datetime.date(2026, 11, 6)), (datetime.time(9, 15), datetime.time(15, 30)))

    def test_the_2026_nse_holidays_are_the_official_list(self):
        expect = {"2026-01-15": "Municipal Corporation Election - Maharashtra", "2026-01-26": "Republic Day",
                  "2026-03-03": "Holi", "2026-03-26": "Ram Navami", "2026-03-31": "Mahavir Jayanti",
                  "2026-04-03": "Good Friday", "2026-04-14": "Dr. Ambedkar Jayanti", "2026-05-01": "Maharashtra Day",
                  "2026-05-28": "Bakri Id", "2026-06-26": "Muharram", "2026-09-14": "Ganesh Chaturthi",
                  "2026-10-02": "Gandhi Jayanti", "2026-10-20": "Dussehra", "2026-11-10": "Diwali (Balipratipada)",
                  "2026-11-24": "Guru Nanak Jayanti", "2026-12-25": "Christmas"}
        self.assertEqual(exitp.NSE_HOLIDAYS, expect)
        for d in expect:
            self.assertIsNone(exitp.session(datetime.date.fromisoformat(d)), d)

    def test_dates_that_were_wrong_before_are_not_holidays(self):
        for d in ("2026-04-15", "2026-10-21", "2026-11-05", "2026-03-11", "2026-04-02", "2026-11-26"):
            self.assertNotIn(d, exitp.NSE_HOLIDAYS, d)
        self.assertIsNotNone(exitp.session(datetime.date(2026, 11, 5)))     # a Thursday, a normal day

    def test_weekends_are_closed_even_on_a_festival(self):
        self.assertIsNone(exitp.session(datetime.date(2026, 11, 7)))        # Saturday
        self.assertIsNone(exitp.session(datetime.date(2026, 11, 8)))        # Sunday, Diwali Laxmi Pujan

    def test_us_holidays_do_not_close_the_nse(self):
        for d in ("2026-07-03", "2026-11-26", "2026-09-07"):                # US Independence Day (obs.), Thanksgiving, Labor Day
            self.assertIsNotNone(exitp.session(datetime.date.fromisoformat(d)), d)

    def test_no_early_close_anywhere(self):
        for d in ("2026-11-27", "2026-12-24", "2026-07-02"):                # the US half days
            self.assertEqual(exitp.session(datetime.date.fromisoformat(d)),
                             (datetime.time(9, 15), datetime.time(15, 30)), d)
        self.assertFalse(hasattr(exitp, "NYSE_EARLY_CLOSE"))
        self.assertFalse(hasattr(exitp, "NYSE_HOLIDAYS"))
        self.assertFalse(hasattr(exitp, "EARLY"))

    def test_ist_is_a_fixed_plus_530(self):
        self.assertEqual(exitp.IST_OFFSET, datetime.timedelta(hours=5, minutes=30))
        for month in (1, 7):                                                # winter and summer: no daylight saving
            utc = datetime.datetime(2026, month, 15, 3, 45)
            self.assertEqual(exitp.to_ist(utc), datetime.datetime(2026, month, 15, 9, 15))
            self.assertEqual(exitp.ist_to_utc(exitp.to_ist(utc)), utc)
        self.assertEqual(exitp.to_ist(datetime.datetime(2026, 11, 5, 20, 0)), datetime.datetime(2026, 11, 6, 1, 30))

    def test_parse_now_is_ist(self):
        want = datetime.datetime(2026, 11, 6, 4, 21)
        self.assertEqual(exitp.parse_now("2026-11-06 09:51"), want)
        self.assertEqual(exitp.parse_now("2026-11-06 09:51 IST"), want)
        self.assertEqual(exitp.parse_now("2026-11-06T09:51:00+05:30"), want)
        self.assertEqual(exitp.parse_now("2026-11-06T04:21:00Z"), want)
        with self.assertRaises(exitp.PlanError) as cm:
            exitp.parse_now("tomorrow")
        self.assertIn("IST", str(cm.exception))
        self.assertNotIn("New York", str(cm.exception))

    def test_times_are_displayed_the_indian_way(self):
        self.assertEqual(exitp.ampm(datetime.time(9, 50)), "9:50 AM")
        self.assertEqual(exitp.ampm(datetime.time(15, 20)), "3:20 PM")
        self.assertEqual(exitp.hm(datetime.time(9, 5)), "09:05")

    def test_no_us_market_code_is_left(self):
        with open(SRC, encoding="utf-8") as fh:
            src = fh.read().lower()
        for word in ("alpaca", "new york", "nyse", "eastern", "early_close", "edgar", "sec.gov", "nasdaq"):
            self.assertNotIn(word, src, word)
        self.assertNotRegex(src, r"\$[\d%{]|[\"']\$")             # no dollar amounts or "$" prefixes (regex anchors are fine)
        for us in ("TSLA", "NVDA", "358.32", "364.18"):
            self.assertNotIn(us, src, us)                          # the docstring examples are NSE ones


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def plan(self, *args):
        return run(exitp, ["plan", "--now", NOW] + list(args))

    def read_plan(self):
        with open(os.path.join("trader", "plan.json")) as fh:
            return json.load(fh)

    def test_entry_to_plan_end_to_end(self):
        code, _, err = run(entry, ["check", "RELIANCE", "--offline", RELIANCE, "--now", "2026-11-06 09:50", "--write"])
        self.assertEqual(code, 0, err)
        code, text, err = self.plan("--stop", "or-mid", "--target", "1R", "--write")
        self.assertEqual(code, 0, err)
        p = self.read_plan()
        for k, v in PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["created_at"], "2026-11-06T04:21:00Z")           # 09:51 IST is 04:21 UTC
        self.assertIn("Entry         ₹1,311.90 at 9:50 AM (bar 6)", text)
        self.assertIn("Stop          ₹1,300.80  opening-range midpoint", text)
        self.assertIn("Target        ₹1,323.00  1R (one times the risk)", text)
        self.assertIn("Risk a share  ₹11.10 (0.85% of the entry)", text)
        self.assertIn("Reward:risk   1.0", text)
        self.assertIn("Flat by       15:20 IST, win or lose", text)
        self.assertIn("not a guarantee", text)
        self.assertIn("/trader-timemachine", text)
        self.assertNotIn("$", text)
        self.assertNotIn("ET", text.replace("TARGET", "").replace("Target", ""))
        self.assertNotIn("pro" + "fit", text.lower())   # never on screen
        code, text, _ = self.plan("--write")                       # the defaults: or-mid, 2R
        self.assertEqual(self.read_plan()["target"], 1334.10)
        self.assertEqual(self.read_plan()["target_r"], 2.0)
        code, text, _ = run(exitp, ["show", "--now", "2026-11-06 10:20"])
        self.assertEqual(code, 0)
        self.assertIn("₹1,334.10", text)
        self.assertNotIn("not today", text)
        code, text, _ = run(exitp, ["show", "--now", "2026-11-09 10:20"])
        self.assertIn("not today", text)

    def test_manual_flags_and_json(self):
        code, text, _ = self.plan("--symbol", "reliance", "--entry", "1311.90", "--or-high", "1306.00",
                                  "--or-low", "1295.60", "--time", "09:50", "--entry-bar", "6",
                                  "--date", "2026-11-06", "--target", "1R", "--json")
        self.assertEqual(code, 0)
        p = json.loads(text)
        for k, v in PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["warnings"], [])

    def test_symbols_with_punctuation_and_digits_plan_fine(self):
        for sym in ("M&M", "BAJAJ-AUTO", "360ONE"):
            code, text, err = self.plan("--symbol", sym, "--entry", "1311.90", "--or-high", "1306.00",
                                        "--or-low", "1295.60", "--date", "2026-11-06", "--json")
            self.assertEqual(code, 0, sym + err)
            self.assertEqual(json.loads(text)["symbol"], sym)

    def test_atr_stop_reads_bars(self):
        self.write(os.path.join("trader", "entry.json"), ENTRY)
        code, text, err = self.plan("--stop", "atr:1.5", "--offline", RELIANCE, "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"]), (1304.01, 7.89, 1327.68))
        self.assertEqual((p["stop_rule"], p["atr_bars"]), ("atr", 7))
        self.assertAlmostEqual(p["atr"], 5.2571, places=4)
        code, _, err = self.plan("--stop", "atr:1.5", "--offline", RELIANCE, "--entry-bar", "1")
        self.assertEqual(code, 1)
        self.assertIn("at least 3 closed", err)

    def test_atr_asks_yahoo_for_the_dot_ns_symbol(self):
        asked = []
        old_get, old_now = exitp.yahoo_get, exitp.utc_now
        with open(RELIANCE) as fh:
            body = fh.read()
        exitp.yahoo_get = lambda path: asked.append(path) or body       # no network in tests
        exitp.utc_now = lambda: datetime.datetime(2026, 11, 6, 5, 0)    # 10:30 IST that Friday
        try:
            self.write(os.path.join("trader", "entry.json"), ENTRY)
            code, _, err = self.plan("--stop", "atr:1.5", "--json")
        finally:
            exitp.yahoo_get, exitp.utc_now = old_get, old_now
        self.assertEqual(code, 0, err)
        self.assertEqual(len(asked), 1)
        self.assertIn("/v8/finance/chart/RELIANCE.NS?", asked[0])
        self.assertNotIn("RELIANCE-NS", asked[0])
        self.assertIn("interval=5m", asked[0])

    def test_a_chart_for_another_company_is_refused(self):
        with self.assertRaises(exitp.PlanError) as cm:
            exitp.load_chart("TCS", "5m", None, RELIANCE)
        self.assertIn("holds RELIANCE.NS, not TCS", str(cm.exception))

    def test_offline_folder_finds_the_dot_ns_file(self):
        folder = os.path.join(self.d, "charts")
        os.mkdir(folder)
        shutil.copy(RELIANCE, os.path.join(folder, "RELIANCE.NS.json"))
        ch = exitp.load_chart("RELIANCE", "5m", None, folder)
        self.assertEqual(len(ch["bars"]), 18)
        self.assertEqual(exitp._nse_yahoo_sym("reliance"), "RELIANCE.NS")
        self.assertEqual(exitp._nse_yahoo_sym("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(exitp._nse_yahoo_sym("RELIANCE.BO"), "RELIANCE.BO")

    def test_refusals_from_the_cli(self):
        self.write(os.path.join("trader", "entry.json"), dict(ENTRY, status="waiting", time=None, price=None,
                                                              bar_index=None, bar_start=None))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("RELIANCE is waiting, not triggered", err)
        code, text, _ = self.plan("--entry", "1306.01", "--json")    # plan ahead at the level
        self.assertEqual(code, 0)
        p = json.loads(text)
        self.assertEqual((p["entry"], p["stop"], p["entry_time"]), (1306.01, 1300.80, None))

        self.write(os.path.join("trader", "entry.json"), dict(ENTRY, setup="level", level=1306.0))
        code, _, err = self.plan()
        self.assertIn("level setup has no opening-range stop", err)
        self.assertEqual(self.plan("--stop", "pct:1")[0], 0)

        code, _, err = self.plan("--side", "sell")
        self.assertIn("v2 plans longs only", err)
        self.write(os.path.join("trader", "entry.json"), dict(ENTRY, side="sell"))
        code, _, err = self.plan()
        self.assertIn("longs only", err)
        code, _, err = self.plan("--stop", "price:1311.00")
        self.assertIn("longs only", err)
        os.remove(os.path.join("trader", "entry.json"))
        code, _, err = self.plan()
        self.assertIn("no entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE")
        self.assertIn("needs --entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--from", "x.json", "--entry", "1")
        self.assertIn("not both", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--entry", "1311.90", "--or-high", "1306.00")
        self.assertIn("both --or-high and --or-low", err)
        self.assertNotIn("Traceback", err)

    def test_flat_by_comes_from_the_config_and_min_reward_risk_warns(self):
        self.write(os.path.join("trader", "entry.json"), ENTRY)
        self.write(os.path.join("trader", "config.json"), {"flat_by": "15:10", "min_reward_risk": 1.5})
        code, text, _ = self.plan("--target", "1R", "--json")
        p = json.loads(text)
        self.assertEqual(p["flat_by"], "15:10")
        self.assertIn("under your min_reward_risk 1.5", p["warnings"][0])
        os.remove(os.path.join("trader", "config.json"))
        self.assertEqual(json.loads(self.plan("--json")[1])["flat_by"], "15:20")     # the default

    def test_flat_by_defaults_to_1520_ten_minutes_before_the_close(self):
        self.assertEqual(exitp.DEFAULTS["flat_by"], "15:20")
        day = datetime.date(2026, 11, 6)
        self.assertEqual(exitp.resolve_flat_by(day, None, dict(exitp.DEFAULTS), None), ("15:20", ""))

    def test_nse_has_no_early_close_so_flat_by_never_shifts(self):
        # 27 Nov and 24 Dec are US half days; the NSE trades its normal hours, and the plan says nothing about an early close
        for d in ("2026-11-27", "2026-12-24"):
            self.write(os.path.join("trader", "entry.json"), dict(ENTRY, date=d))
            code, text, err = run(exitp, ["plan", "--now", d + " 09:51"])
            self.assertEqual(code, 0, err)
            self.assertIn("Flat by       15:20 IST", text)
            self.assertNotIn("early", text.lower())
            self.assertNotIn("Flat-by", text)                          # no shift note
            self.assertNotIn("12:55", text)
            code, text, _ = run(exitp, ["plan", "--now", d + " 09:51", "--flat-by", "14:00", "--json"])
            self.assertEqual(code, 0)                                  # 14:00 is fine: it is before the real 15:30 close
            self.assertEqual(json.loads(text)["flat_by"], "14:00")

    def test_flat_by_at_or_after_the_close_is_refused(self):
        self.write(os.path.join("trader", "entry.json"), ENTRY)
        for bad in ("15:30", "15:35", "16:30"):
            code, _, err = self.plan("--flat-by", bad)
            self.assertEqual(code, 1, bad)
            self.assertIn("at or after the 3:30 PM close", err)
        self.assertEqual(self.plan("--flat-by", "15:29")[0], 0)
        self.write(os.path.join("trader", "config.json"), {"flat_by": "15:45"})
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("at or after the NSE close", err)
        code, _, err = self.plan("--flat-by", "soon")
        self.assertIn("IST time like 15:20", err)

    def test_an_entry_at_or_after_flat_by_has_no_time_left(self):
        self.write(os.path.join("trader", "entry.json"), dict(ENTRY, time="3:25 PM"))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("no time left to hold it", err)
        self.write(os.path.join("trader", "entry.json"), dict(ENTRY, time="3:19 PM"))
        self.assertEqual(self.plan()[0], 0)

    def test_a_plan_for_another_day_says_so(self):
        self.write(os.path.join("trader", "entry.json"), ENTRY)
        code, text, _ = run(exitp, ["plan", "--now", "2026-11-09 09:51"])      # Monday
        self.assertEqual(code, 0)
        self.assertIn("This plan is for 2026-11-06, not today (2026-11-09)", text)

    def test_show_without_a_plan(self):
        code, text, _ = run(exitp, ["show"])
        self.assertEqual(code, 1)
        self.assertIn("No plan", text)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-exit", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-exit\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("before the entry", "a stop is a plan, not a guarantee", "day trading loses most people money",
                     "/trader-timemachine", "/trader-budget", "longs only", "₹1,300.80", "₹1,323.00", "₹11.10",
                     "1334.10", "too tight to survive the spread", "15:20", "no early closes", "ist"):
            self.assertIn(must, low)
        shown = low.replace("${claude_skill_dir}", "")             # the one legitimate "$": the skill-folder variable
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit", "new york", "nyse", "alpaca", "$"):
            self.assertNotIn(never, shown)


if __name__ == "__main__":
    unittest.main()
