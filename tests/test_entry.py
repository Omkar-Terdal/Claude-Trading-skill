"""trader-entry tests (NSE, IST, rupees). Fixtures in tests/fixtures/trader-entry/:

RELIANCE-2026-11-06-5m.json  SYNTHETIC five-minute bars (not market data) shaped like the
                             RELIANCE example in trader-entry/SKILL.md: Yahoo chart JSON v8,
                             NSE session 09:15-15:30 IST, 19 bars (the 10:45 one still in
                             progress) plus Yahoo's live tick at 10:49:45. Range 1295.60 to
                             1306.00, a wick above it and a close AT it, a near miss, then the
                             09:45 bar closes at 1311.90 on 2.07x the range's volume.
DEMO-1m-synthetic.json       SYNTHETIC one-minute bars (not market data): a wick above the
                             range high, a close AT the high, a null bar, a live tick.
"""

import copy
import datetime
import gzip
import io
import json
import os
import re
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

entry = load("trader-entry", "entry")

EDIR = os.path.join(FIX, "trader-entry")
REL = os.path.join(EDIR, "RELIANCE-2026-11-06-5m.json")
DEMO = os.path.join(EDIR, "DEMO-1m-synthetic.json")
OPEN_TS = 1793936700       # 2026-11-06 09:15 IST = 03:45 UTC
DT = datetime.datetime
DAY = "2026-11-06"         # a Friday


def ist(text):
    return entry.parse_now(text)


def doc(path=REL):
    with open(path) as fh:
        return json.load(fh)


def chart(d=None, sym="RELIANCE"):
    return entry.parse_chart(json.dumps(d if d is not None else doc()), sym)


def at(text, d=None, **kw):
    return entry.evaluate(chart(d), ist(text), **kw)


def shift_to(d, day):
    """The same session moved to another date: 09:15 IST is 03:45 UTC every day of the year."""
    d = copy.deepcopy(d)
    r = d["chart"]["result"][0]
    new_open = int((DT.combine(day, datetime.time(3, 45)) - DT(1970, 1, 1)).total_seconds())
    delta = new_open - OPEN_TS
    r["timestamp"] = [t + delta for t in r["timestamp"]]
    r["meta"]["regularMarketTime"] += delta
    return d


def no_breakout(d=None):
    """Every bar after the range stays under the 1306.00 high."""
    d = copy.deepcopy(d if d is not None else doc())
    q = d["chart"]["result"][0]["indicators"]["quote"][0]
    for i in range(3, len(q["close"])):
        q["close"][i] = min(q["close"][i], 1305.50)
        q["high"][i] = min(q["high"][i], 1305.60)
        q["open"][i] = min(q["open"][i], 1305.50)
    return d


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheExample(unittest.TestCase):
    def test_triggers_at_0950_on_the_0945_bar(self):
        r = at(DAY + " 09:55")
        self.assertEqual(r["status"], "triggered")
        self.assertEqual(r["symbol"], "RELIANCE")
        self.assertEqual(r["opening_range"]["high"], 1306.0)
        self.assertEqual(r["opening_range"]["low"], 1295.6)
        self.assertEqual(r["opening_range"]["bars"], 3)
        t = r["trigger"]
        self.assertEqual(t["time"], "9:50 AM")
        self.assertEqual(t["bar_start"], "09:45")
        self.assertEqual(t["bar_index"], 6)
        self.assertEqual(t["price"], 1311.9)
        self.assertEqual(t["close"], 1311.9)
        self.assertEqual(t["volume_x"], 2.07)
        self.assertEqual(t["pct_over"], 0.45)
        self.assertEqual(r["level"], 1306.0)
        self.assertEqual(r["prev_close"], 1298.3)
        self.assertEqual((r["opens"], r["closes"]), ("09:15", "15:30"))

    def test_at_0948_the_0945_bar_is_still_forming(self):
        r = at(DAY + " 09:48")
        self.assertEqual(r["status"], "waiting")
        self.assertIsNone(r["trigger"])
        self.assertEqual(r["last"]["bar_start"], "09:40")
        self.assertEqual(r["last"]["close"], 1305.7)
        self.assertEqual(r["forming"]["bar_start"], "09:45")
        self.assertIn("₹0.30 under the range high ₹1306.00", r["reason"])
        self.assertEqual(at(DAY + " 09:49:59")["status"], "waiting")

    def test_the_range_is_the_0915_0920_0925_bars(self):
        r = at(DAY + " 09:55")
        self.assertEqual(r["opening_range"]["avg_volume"], 150000)     # (300000 + 90000 + 60000) / 3
        d = copy.deepcopy(doc())
        d["chart"]["result"][0]["indicators"]["quote"][0]["high"][3] = 1400.0   # the 09:30 bar is NOT in the range
        r = at(DAY + " 09:55", d)
        self.assertEqual((r["opening_range"]["high"], r["opening_range"]["bars"]), (1306.0, 3))

    def test_before_the_open_and_while_the_range_forms(self):
        r = at(DAY + " 09:10")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        self.assertIn("opens at 9:15", r["reason"])
        r = at(DAY + " 09:29")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))
        self.assertEqual(r["opening_range"]["bars"], 2)
        self.assertFalse(r["opening_range"]["complete"])
        r = at(DAY + " 09:31")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "open"))
        self.assertEqual(r["opening_range"]["bars"], 3)
        self.assertTrue(r["opening_range"]["complete"])

    def test_the_live_tick_and_the_in_progress_bar_are_dropped(self):
        c = chart()
        self.assertEqual(c["bars"][-1]["t"], OPEN_TS + 5685)             # 10:49:45 is in the file
        r = entry.evaluate(c, entry.utc_of(c["data_time"]))              # read as of the file's own time
        self.assertEqual(r["now"], DAY + " 10:49:45")
        self.assertEqual(r["_bars"][-1]["slot"], 17)                     # the 10:40 bar
        self.assertEqual(r["forming"]["bar_start"], "10:45")
        self.assertEqual(r["bars_closed"], 18)
        # 10:50: closed by the clock, but Yahoo's last trade (10:49:45) is before its close
        r = at(DAY + " 10:50")
        self.assertEqual(r["_bars"][-1]["slot"], 17)
        self.assertIn("Yahoo's last trade is 10:49:45", r["lag_note"])
        # a minute later with no newer trade it counts; the 10:49:45 tick never becomes a bar
        r = at(DAY + " 10:51")
        self.assertEqual(r["_bars"][-1]["slot"], 18)
        self.assertEqual(r["bars_closed"], 19)
        self.assertTrue(all(b["t"] % 300 == 0 for b in r["_bars"]))

    def test_after_the_close_it_stays_triggered_and_says_it_is_late(self):
        r = at(DAY + " 15:35")
        self.assertEqual((r["status"], r["phase"]), ("triggered", "after-close"))
        self.assertEqual(r["trigger"]["time"], "9:50 AM")
        text = "\n".join(entry.describe(r))
        self.assertIn("minutes ago", text)
        self.assertNotIn("This is the moment", text)

    def test_words_are_ist_and_rupees(self):
        text = "\n".join(entry.describe(at(DAY + " 09:55")))
        self.assertIn("Fri 2026-11-06 09:55 IST", text)
        self.assertIn("Opening range 9:15-9:30: high ₹1306.00, low ₹1295.60", text)
        self.assertIn("TRIGGERED 9:50 AM at ₹1311.90", text)
        self.assertNotIn("$", text)
        self.assertNotIn(" ET", text)
        self.assertEqual(chart()["symbol"], "RELIANCE.NS")


class Rules(unittest.TestCase):
    def test_volume_confirmation_moves_the_trigger(self):
        # 09:45 closes above on 2.07x: not enough for 2.5x; the next bar to clear 2.5x is the 10:15 one
        r = at(DAY + " 10:49", min_volume_x=2.5)
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("10:20 AM", "10:15", 12, 1318.2))
        self.assertEqual(t["volume_x"], 2.85)
        r = at(DAY + " 09:51", min_volume_x=2.5)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("2.07x the range's volume, under your 2.50x", r["reason"])
        r = at(DAY + " 09:55", min_volume_x=2.0)                  # 2.07x clears 2.0x
        self.assertEqual(r["trigger"]["bar_index"], 6)

    def test_level_setup(self):
        r = at(DAY + " 10:30", setup="level", level=1316)
        self.assertEqual(r["trigger"]["time"], "10:00 AM")
        self.assertEqual(r["trigger"]["bar_index"], 8)
        self.assertEqual(r["trigger"]["price"], 1316.1)
        # a level under the open: only closes at or after 09:45 (no_entries_before) count,
        # so the first eligible bar is the 09:40 one (it closes at 09:45), not the 09:15 one
        r = at(DAY + " 10:30", setup="level", level=1290)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:45 AM", 5))
        with self.assertRaises(entry.EntryError):
            at(DAY + " 10:30", setup="level")

    def test_no_breakout_waits_then_expires_at_the_cutoff(self):
        d = no_breakout()
        r = at(DAY + " 10:49", d)
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(at(DAY + " 15:29", d)["status"], "waiting")
        r = at(DAY + " 15:30", d)
        self.assertEqual(r["status"], "expired")
        self.assertEqual(r["no_entries_after"], "15:30")
        self.assertIn("by 15:30", r["reason"])
        self.assertEqual(at(DAY + " 15:40", d)["phase"], "after-close")
        # a cutoff before the close is "after-cutoff" until the bell
        cfg = {"no_entries_before": "09:45", "no_entries_after": "15:15"}
        self.assertEqual(at(DAY + " 15:15", d, cfg=cfg)["phase"], "after-cutoff")
        self.assertEqual(at(DAY + " 15:30", d, cfg=cfg)["phase"], "after-close")

    def test_the_bar_closing_at_the_cutoff_still_counts_when_yahoo_lags(self):
        d = no_breakout()
        r0 = d["chart"]["result"][0]
        keep = [i for i, t in enumerate(r0["timestamp"]) if t <= OPEN_TS + 17 * 300]   # live: up to the 10:40 bar
        r0["timestamp"] = [r0["timestamp"][i] for i in keep]
        q = r0["indicators"]["quote"][0]
        for k in q:
            q[k] = [q[k][i] for i in keep]
        r0["meta"]["regularMarketTime"] = OPEN_TS + 89 * 60 + 40                       # last trade 10:44:40
        cfg = {"no_entries_before": "09:45", "no_entries_after": "10:45"}
        r = at(DAY + " 10:45:10", d, cfg=cfg)                               # the 10:40 bar closed by the clock only
        self.assertEqual(r["status"], "waiting")
        self.assertIn("Yahoo's last trade is 10:44:40", r["lag_note"])
        self.assertEqual(at(DAY + " 10:46", d, cfg=cfg)["status"], "expired")

    def test_config_cutoff_and_earliest_entry(self):
        cfg = {"no_entries_before": "09:45", "no_entries_after": "09:45"}
        r = at(DAY + " 09:55", cfg=cfg)                          # the trigger bar closes at 09:50, after the cutoff
        self.assertEqual(r["status"], "expired")
        cfg = {"no_entries_before": "09:55", "no_entries_after": "15:30"}
        r = at(DAY + " 10:30", cfg=cfg)
        self.assertEqual(r["trigger"]["time"], "9:55 AM")        # the 09:50 bar, first to close at or after 09:55
        self.assertEqual(r["trigger"]["bar_index"], 7)

    def test_defaults_are_the_nse_session(self):
        self.assertEqual(entry.DEFAULTS, {"no_entries_before": "09:45", "no_entries_after": "15:30"})
        self.assertEqual((entry.OPEN, entry.CLOSE), (datetime.time(9, 15), datetime.time(15, 30)))
        minutes = (DT.combine(datetime.date(2026, 11, 6), entry.CLOSE)
                   - DT.combine(datetime.date(2026, 11, 6), entry.OPEN)).seconds // 60
        self.assertEqual(minutes // 5, 75)                       # 75 five-minute bars a session

    def test_bad_inputs(self):
        with self.assertRaises(entry.EntryError):
            at(DAY + " 09:55", or_minutes=7)
        with self.assertRaises(entry.EntryError):
            at(DAY + " 09:55", interval="15m")
        with self.assertRaises(entry.EntryError):
            entry.parse_now("tomorrow")
        with self.assertRaises(entry.EntryError):
            entry.parse_hhmm("25:00", "x")
        # 09:55 IST is 04:25 UTC: IST is UTC+5:30, all year
        self.assertEqual(entry.parse_now(DAY + " 09:55"), DT(2026, 11, 6, 4, 25))
        self.assertEqual(entry.parse_now(DAY + "T04:25:00Z"), DT(2026, 11, 6, 4, 25))
        self.assertEqual(entry.parse_now(DAY + " 09:55 IST"), DT(2026, 11, 6, 4, 25))
        self.assertEqual(entry.parse_now(DAY + " 09:55+05:30"), DT(2026, 11, 6, 4, 25))

    def test_or_minutes_30(self):
        r = at(DAY + " 10:49", or_minutes=30)
        self.assertEqual(r["opening_range"]["bars"], 6)
        self.assertEqual(r["opening_range"]["high"], 1307.1)
        self.assertEqual(r["trigger"]["time"], "9:50 AM")


class OneMinute(unittest.TestCase):
    CFG = {"no_entries_before": "09:30", "no_entries_after": "15:30"}

    def c(self):
        return entry.parse_chart(json.dumps(doc(DEMO)), "DEMO")

    def test_wick_and_close_at_the_high_do_not_trigger(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m", cfg=self.CFG)
        self.assertEqual(r["now"], DAY + " 09:51:30")
        self.assertEqual(r["opening_range"], {"high": 100.5, "low": 99.0, "minutes": 15, "bars": 15, "of": 15,
                                              "complete": True, "avg_volume": 10667})
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("9:44 AM", "09:43", 28, 100.62))
        r = entry.evaluate(c, ist(DAY + " 09:43:59"), interval="1m", cfg=self.CFG)
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["forming"]["bar_start"], "09:43")
        self.assertEqual(r["last"]["close"], 100.5)             # the 09:42 bar closed AT the high

    def test_no_entries_before_0945_skips_the_first_close_above(self):
        c = self.c()
        # the 09:43 bar closes above at 09:44, a minute before the default earliest entry: the 09:44 bar is the trigger
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m")
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("9:45 AM", "09:44", 29, 100.7))

    def test_null_bar_tick_and_volume(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m", min_volume_x=2)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:47 AM", 31))
        slots = [b["slot"] for b in r["_bars"]]
        self.assertNotIn(19, slots)                               # 09:34 came as nulls
        self.assertEqual(slots[-1], 35)                           # 09:51 still forming, 09:51:30 tick dropped
        self.assertEqual(r["forming"]["bar_start"], "09:51")

    def test_the_file_and_the_interval_must_agree(self):
        code, _, err = run(["check", "DEMO", "--offline", DEMO])
        self.assertEqual(code, 1)
        self.assertIn("1m bars, not 5m", err)


OFFICIAL_HOLIDAYS = {      # the NSE equity trading holidays for 2026, as NSE publishes them
    "2026-01-15": "Municipal Corporation Election - Maharashtra", "2026-01-26": "Republic Day",
    "2026-03-03": "Holi", "2026-03-26": "Shri Ram Navami", "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday", "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti", "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id", "2026-06-26": "Muharram", "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti", "2026-10-20": "Dussehra", "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev", "2026-12-25": "Christmas"}


class Calendar(unittest.TestCase):
    def test_ist_has_no_daylight_saving(self):
        # the same 09:15 session on days in every season of the year: same wall clock, same bars
        for day in (datetime.date(2026, 12, 7), datetime.date(2026, 6, 15), datetime.date(2026, 3, 9),
                    datetime.date(2026, 10, 30)):
            d = shift_to(doc(), day)
            r = entry.evaluate(chart(d), ist("%s 09:55" % day))
            self.assertEqual(r["status"], "triggered", day)
            self.assertEqual(r["trigger"]["time"], "9:50 AM", day)
            self.assertEqual(r["trigger"]["bar_index"], 6, day)
            self.assertEqual(entry.evaluate(chart(d), ist("%s 09:48" % day))["status"], "waiting", day)
            self.assertEqual(ist("%s 09:55" % day), DT(day.year, day.month, day.day, 4, 25), day)

    def test_no_early_close_on_the_nse(self):
        # 2026-12-24 is a US early close; on the NSE it is an ordinary day with the cutoff at 15:30
        day = datetime.date(2026, 12, 24)
        d = shift_to(no_breakout(), day)
        self.assertEqual(entry.session(day), (entry.OPEN, entry.CLOSE))
        r = entry.evaluate(chart(d), ist("2026-12-24 15:29"))
        self.assertEqual(r["status"], "waiting")
        self.assertEqual((r["closes"], r["no_entries_after"]), ("15:30", "15:30"))
        r = entry.evaluate(chart(d), ist("2026-12-24 15:30"))
        self.assertEqual(r["status"], "expired")
        self.assertNotIn("early close", r["reason"])
        r = entry.evaluate(chart(shift_to(doc(), day)), ist("2026-12-24 09:55"))
        self.assertEqual(r["trigger"]["time"], "9:50 AM")
        self.assertNotIn("Early close", "\n".join(entry.describe(r)))
        # a cutoff the user set stays where they put it
        cfg = {"no_entries_before": "09:45", "no_entries_after": "12:00"}
        self.assertEqual(entry.evaluate(chart(d), ist("2026-12-24 11:00"), cfg=cfg)["no_entries_after"], "12:00")

    def test_us_holidays_are_ordinary_nse_days(self):
        for day in (datetime.date(2026, 11, 26), datetime.date(2026, 7, 3), datetime.date(2026, 9, 7)):
            self.assertEqual(entry.trading_day(day), (True, ""), day)         # Thanksgiving, July 3, Labor Day
        d = shift_to(doc(), datetime.date(2026, 11, 26))
        self.assertEqual(entry.evaluate(chart(d), ist("2026-11-26 09:55"))["status"], "triggered")

    def test_weekend_holiday_and_empty_data(self):
        r = at("2026-11-07 10:30")                                # a Saturday
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertIn("weekend", r["reason"])
        self.assertEqual(r["next_open"], "2026-11-09 09:15")
        r = at("2026-11-10 10:30")                                # Diwali-Balipratipada, a Tuesday
        self.assertIn("NSE holiday: Diwali (Balipratipada)", r["reason"])
        self.assertEqual(r["next_open"], "2026-11-11 09:15")
        empty = copy.deepcopy(doc())
        res = empty["chart"]["result"][0]
        del res["timestamp"]
        res["indicators"] = {"quote": [{}]}
        r = at(DAY + " 09:55", empty)
        self.assertEqual((r["status"], r["phase"]), ("no-data", "open"))
        r = at("2026-11-09 09:55")                                # Yahoo sent Friday's bars only
        self.assertEqual(r["status"], "no-data")
        self.assertTrue(at(DAY + " 10:30")["calendar_known"])
        self.assertFalse(at("2027-03-01 10:30")["calendar_known"])      # only 2026 is built in

    def test_every_official_2026_holiday_is_closed(self):
        self.assertEqual(sorted(entry.NSE_HOLIDAYS), sorted(OFFICIAL_HOLIDAYS))
        for iso in OFFICIAL_HOLIDAYS:
            day = datetime.date.fromisoformat(iso)
            ok, why = entry.trading_day(day)
            self.assertFalse(ok, iso)
            self.assertTrue(why.startswith("NSE holiday: "), iso)
            self.assertIsNone(entry.session(day), iso)
            r = entry.evaluate(chart(shift_to(doc(), day)), ist("%s 09:55" % iso))
            self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"), iso)
        # the day Diwali Laxmi Pujan falls is a Sunday: closed as a weekend
        self.assertEqual(entry.trading_day(datetime.date(2026, 11, 8)), (False, "weekend"))

    def test_yahoo_errors(self):
        bad = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                              "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(entry.EntryError) as cm:
            entry.parse_chart(bad, "ZZZZ")
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(entry.EntryError):
            entry.parse_chart("<html>", "RELIANCE")


class Network(unittest.TestCase):
    """Yahoo is never called: _open_url and _sleep are swapped for fakes, and the clock is pinned."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._open, self._sleep, self._now = entry._open_url, entry._sleep, entry.utc_now
        entry._sleep = lambda s: self.sleeps.append(s)
        entry.utc_now = lambda: DT(2026, 11, 6, 6, 0)            # 11:30 IST that Friday
        with open(REL) as fh:
            self.body = fh.read()

    def tearDown(self):
        entry._open_url, entry._sleep, entry.utc_now = self._open, self._sleep, self._now

    def answers(self, *codes):
        seq = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url)
            code = seq.pop(0)
            if code == 200:
                return self.body
            raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b'{"chart":{"result":null,'
                                                                           b'"error":{"description":"No data found"}}}'))
        entry._open_url = fake

    def test_url_and_429_retry_once_after_a_pause(self):
        self.answers(429, 200)
        c = entry.get_chart("RELIANCE", "5m", datetime.date(2026, 11, 6), today=datetime.date(2026, 11, 6))
        self.assertEqual(len(c["bars"]), 20)                      # 19 bars and the live tick
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m"] * 2)
        self.assertEqual(self.sleeps, [entry.BACKOFF])

    def test_429_twice_falls_back_to_query1(self):
        self.answers(429, 429, 200)
        entry.get_chart("RELIANCE", "5m", datetime.date(2026, 11, 6), today=datetime.date(2026, 11, 6))
        self.assertEqual([u.split("/")[2] for u in self.calls],
                         ["query2.finance.yahoo.com", "query2.finance.yahoo.com", "query1.finance.yahoo.com"])

    def test_5xx_falls_back_without_waiting(self):
        self.answers(503, 200)
        entry.get_chart("RELIANCE", "5m", datetime.date(2026, 11, 6), today=datetime.date(2026, 11, 6))
        self.assertEqual(self.sleeps, [])
        self.assertIn("query1", self.calls[1])

    def test_still_busy_is_one_plain_error(self):
        self.answers(429, 429, 429, 429)
        code, out, err = run(["check", "RELIANCE", "--now", DAY + " 09:55"])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 429", err)
        self.assertNotIn("Traceback", err)
        self.assertTrue(all("/RELIANCE.NS?" in u for u in self.calls))

    def test_unknown_ticker_404(self):
        self.answers(404)
        with self.assertRaises(entry.EntryError) as cm:
            entry.get_chart("ZZZZ", "5m", datetime.date(2026, 11, 6), today=datetime.date(2026, 11, 6))
        self.assertIn("No data found", str(cm.exception))
        self.assertIn("/ZZZZ.NS?", self.calls[0])

    def test_ranges_for_past_sessions(self):
        today = datetime.date(2026, 11, 7)
        self.assertEqual(entry.chart_range(today, today, "5m"), "1d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 11, 6), today, "5m"), "5d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 20), today, "5m"), "1mo")
        self.assertEqual(entry.chart_range(datetime.date(2026, 9, 15), today, "5m"), "60d")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 9, 1), today, "5m")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 10, 20), today, "1m")

    def test_nse_symbols_in_the_url(self):
        self.assertEqual(entry.chart_path("reliance", "1d", "5m"), "/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")
        self.assertEqual(entry.chart_path("TCS.NS", "1d", "5m"), "/v8/finance/chart/TCS.NS?range=1d&interval=5m")
        self.assertEqual(entry.chart_path("RELIANCE.BO", "1d", "5m"), "/v8/finance/chart/RELIANCE.BO?range=1d&interval=5m")
        self.assertEqual(entry.chart_path("bajaj-auto", "1d", "5m"), "/v8/finance/chart/BAJAJ-AUTO.NS?range=1d&interval=5m")
        self.assertEqual(entry.chart_path("m&m", "1d", "5m"), "/v8/finance/chart/M&M.NS?range=1d&interval=5m")


def live_provider(d, lag_until=None, calls=None):
    """Bars the way Yahoo serves them at `now`: closed bars final, the in-progress bar
    showing a spike to 1308.00 (above the 1306.00 high, then it closes under), plus a live tick.
    lag_until: before this UTC time Yahoo's last trade lags 30 seconds behind."""
    base = chart(d)

    def provider(now):
        if calls is not None:
            calls.append(now)
        nts = entry.ts_of(now)
        lagging = lag_until is not None and now < lag_until
        data_ts = nts - 30 if lagging else nts
        bars = []
        for b in base["bars"]:
            if b["t"] % 300 or b["t"] > data_ts:
                continue
            b = dict(b)
            if b["t"] + 300 > data_ts:
                b["c"], b["h"] = 1308.00, max(b["h"], 1308.00)
            bars.append(b)
        if data_ts % 300:
            bars.append({"t": data_ts, "o": 1308.0, "h": 1308.0, "l": 1308.0, "c": 1308.0, "v": 0})
        return {"symbol": "RELIANCE.NS", "meta": {}, "bars": bars, "prev_close": 1298.3, "data_time": data_ts}
    return provider


def clock_of(calls):
    return [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]


class Watch(unittest.TestCase):
    OPTS = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None, "cfg": None}

    def loop(self, provider, start, **kw):
        clock = entry.SimClock(ist(start))
        lines, fired = [], []
        code, res = entry.watch_loop(provider, clock.now, clock.sleep, lines.append, dict(self.OPTS),
                                     on_trigger=lambda r, now: fired.append((r, now)), **kw)
        return code, res, lines, fired, clock

    def test_fires_on_the_first_closed_bar_not_the_spike_in_progress(self):
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), calls=calls), DAY + " 09:42", every=60)
        self.assertEqual(code, 0)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][0]["trigger"]["time"], "9:50 AM")
        self.assertEqual(fired[0][0]["trigger"]["price"], 1311.9)
        # it looked at 09:42 (the 09:40 bar spiking to 1308 in progress) and at 09:45:02 (that bar closed 1305.70)
        self.assertEqual(clock_of(calls), ["09:42:00", "09:45:02", "09:50:02"])
        text = "\n".join(lines)
        self.assertIn("9:40 bar  closed ₹1305.70  ₹0.30 under ₹1306.00", text)
        self.assertIn("9:45 bar  closed ₹1311.90  TRIGGERED", text)
        self.assertEqual(text.count("TRIGGERED"), 1)

    def test_waits_for_yahoo_to_catch_up(self):
        lag = ist(DAY + " 09:50:30")
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), lag_until=lag, calls=calls),
                                                   DAY + " 09:47", every=30)
        self.assertEqual(code, 0)
        self.assertEqual(clock_of(calls), ["09:47:00", "09:50:02", "09:50:32"])   # 09:50:02: Yahoo still at 09:49:32
        self.assertEqual(fired[0][0]["trigger"]["time"], "9:50 AM")

    def test_from_before_the_open_prints_the_range_bar_by_bar(self):
        code, res, lines, fired, clock = self.loop(live_provider(doc()), DAY + " 09:00", every=30)
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("not open yet", lines[0])
        self.assertIn("watching from the first bar's close at 9:20", lines[0])
        self.assertIn(" 9:25 bar  closed ₹1302.60  opening range set: high ₹1306.00, low ₹1295.60", text)
        self.assertEqual(sum(1 for x in lines if " bar  closed " in x), 7)    # 9:15 ... 9:45, one line each

    def test_a_computer_that_dozes_off_catches_up_when_it_wakes(self):
        class Dozing(entry.SimClock):
            naps = 0

            def sleep(self, seconds):
                entry.SimClock.sleep(self, seconds)
                self.naps += 1
                if self.naps == 3:                      # the laptop sleeps for 17 minutes mid-wait
                    self.t += datetime.timedelta(minutes=17)
        clock = Dozing(ist(DAY + " 09:31"))
        calls, lines = [], []
        code, res = entry.watch_loop(live_provider(doc(), calls=calls), clock.now, clock.sleep, lines.append,
                                     dict(self.OPTS), every=30)
        self.assertEqual(code, 0)
        self.assertEqual(clock_of(calls)[:2], ["09:31:00", "09:49:30"])   # straight after waking, not a long sleep later
        text = "\n".join(lines)
        for start in (" 9:30", " 9:35", " 9:40", " 9:45"):
            self.assertIn(start + " bar  closed", text)          # the bars it slept through, one line each
        self.assertEqual(res["trigger"]["time"], "9:50 AM")

    def test_expires_and_until(self):
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), DAY + " 15:00", every=300)
        self.assertEqual(code, 1)
        self.assertEqual(res["status"], "expired")
        self.assertEqual(fired, [])
        self.assertEqual(entry.to_ist(clock.now()).strftime("%H:%M"), "15:30")
        until = DT(2026, 11, 6, 11, 0)
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), DAY + " 10:30", every=300,
                                                   until=until)
        self.assertEqual(code, 1)
        self.assertIn("--until 11:00", lines[-1])

    def test_closed_day_and_repeated_errors(self):
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-11-07 10:00")
        self.assertEqual(code, 1)
        self.assertIn("weekend", lines[0])
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-11-10 10:00")
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday: Diwali (Balipratipada)", lines[0])

        def broken(now):
            raise entry.EntryError("Yahoo did not answer")
        code, _, lines, _, _ = self.loop(broken, DAY + " 10:00", every=30)
        self.assertEqual(code, 1)
        self.assertIn("Stopping after 5 failed reads", lines[-1])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_check_write_is_the_contract_shape(self):
        code, text, _ = run(["check", "RELIANCE", "--offline", REL, "--now", DAY + " 09:55", "--write"])
        self.assertEqual(code, 0)
        self.assertIn("TRIGGERED 9:50 AM at ₹1311.90", text)
        self.assertIn("/trader-exit", text)
        with open(os.path.join("trader", "entry.json"), encoding="utf-8") as fh:
            e = json.load(fh)
        want = {"created_by": "trader-entry", "symbol": "RELIANCE", "setup": "orb", "side": "buy", "date": DAY,
                "status": "triggered", "time": "9:50 AM", "bar_start": "09:45", "bar_index": 6, "price": 1311.9,
                "level": 1306.0, "interval": "5m", "opening_range": {"high": 1306.0, "low": 1295.6, "minutes": 15},
                "source": "yahoo", "checked_at": "2026-11-06T04:25:00Z"}       # 09:55 IST is 04:25 UTC
        for k, v in want.items():
            self.assertEqual(e[k], v, k)

    def test_check_waiting_json_and_exit_codes(self):
        code, text, _ = run(["check", "RELIANCE", "--offline", REL, "--now", DAY + " 09:48", "--json"])
        self.assertEqual(code, 1)
        r = json.loads(text)
        self.assertEqual(r["status"], "waiting")
        self.assertNotIn("_bars", r)
        code, text, _ = run(["check", "RELIANCE", "--offline", REL, "--now", DAY + " 09:48", "--write", "e.json"])
        with open("e.json") as fh:
            self.assertEqual(json.load(fh)["status"], "waiting")
        self.assertIn("still forming", text)

    def test_offline_gz_folder_and_mismatch(self):
        os.mkdir("charts")
        with open(REL, "rb") as src, gzip.open(os.path.join("charts", "RELIANCE.json.gz"), "wb") as dst:
            dst.write(src.read())
        code, text, _ = run(["check", "reliance", "--offline", "charts", "--now", DAY + " 09:55"])
        self.assertEqual(code, 0)
        os.mkdir("ns")                                           # a file named with the Yahoo symbol is found too
        shutil.copy(REL, os.path.join("ns", "RELIANCE.NS.json"))
        code, text, _ = run(["check", "RELIANCE", "--offline", "ns", "--now", DAY + " 09:55"])
        self.assertEqual(code, 0)
        code, _, err = run(["check", "INFY", "--offline", REL])
        self.assertEqual(code, 1)
        self.assertIn("holds RELIANCE.NS, not INFY", err)

    def test_longs_only_and_flag_errors(self):
        code, _, err = run(["check", "RELIANCE", "--offline", REL, "--side", "short"])
        self.assertEqual(code, 1)
        self.assertIn("v2 plans longs only", err)
        code, _, err = run(["check", "RELIANCE", "--offline", REL, "--level", "1300"])
        self.assertIn("--level is for --setup level", err)
        code, _, err = run(["check", "RELIANCE", "--offline", REL, "--setup", "level"])
        self.assertIn("needs --level", err)

    def test_config_is_read(self):
        os.mkdir("trader")
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump({"no_entries_after": "09:45"}, fh)
        code, text, _ = run(["check", "RELIANCE", "--offline", REL, "--now", DAY + " 09:55"])
        self.assertEqual(code, 1)
        self.assertIn("EXPIRED", text)
        with open(os.path.join("trader", "config.json"), "w") as fh:
            fh.write("{nope")
        code, _, err = run(["check", "RELIANCE", "--offline", REL])
        self.assertIn("not valid JSON", err)

    def test_watch_replays_the_file_and_writes_the_entry(self):
        code, text, _ = run(["watch", "RELIANCE", "--offline", REL, "--now", DAY + " 09:30", "--every", "30"])
        self.assertEqual(code, 0)
        self.assertIn("9:45 bar  closed ₹1311.90  TRIGGERED", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            self.assertEqual(json.load(fh)["bar_index"], 6)
        code, text, _ = run(["watch", "RELIANCE", "--offline", REL, "--now", DAY + " 09:30", "--until", "09:35",
                             "--out", "x.json"])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists("x.json"))

    def test_watchlist(self):
        os.mkdir("charts")
        shutil.copy(REL, os.path.join("charts", "RELIANCE.json"))
        shutil.copy(DEMO, os.path.join("charts", "DEMO.json"))
        os.mkdir("trader")
        with open(os.path.join("trader", "watchlist.json"), "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [
                {"symbol": "RELIANCE", "setups": ["orb"], "or_high": 1306.0, "or_low": 1295.6},
                {"symbol": "ZZZZ", "setups": ["orb"]}, {"symbol": "DEMO", "setups": ["orb"]}]}, fh)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", DAY + " 09:55"])
        self.assertEqual(code, 0)
        rows = text.splitlines()
        self.assertTrue(rows[1].startswith("RELIANCE  triggered  9:50 AM"), rows[1])
        self.assertIn("₹1311.90", rows[1])
        self.assertIn("₹1306.00", rows[1])
        self.assertIn("ZZZZ    error: no offline file", rows[2])
        self.assertIn("DEMO    error: DEMO has 1m bars", rows[3])
        self.assertIn("1 of 3 triggered", text)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", DAY + " 09:48", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)[0]["result"]["status"], "waiting")
        code, _, err = run(["watchlist", "--watchlist", "missing.json"])
        self.assertIn("run /trader-scanner first", err)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-entry", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-entry\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("bar by bar", "can lag", "day trading loses most people money", "/trader-exit", "longs only",
                     "in the background", "/loop", "nothing was placed", "9:50 am", "₹1311.90", "bar 6",
                     "9:15", "ist", "nse", ".ns", "no early closes", "15:30"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit",
                      "nyse", "new york", "eastern", "alpaca", "9:30 am", "4:00 pm"):
            self.assertNotIn(never, low)
        self.assertIsNone(re.search(r"\$\d", text))

    def test_skill_md_example_matches_the_tool(self):
        # the SKILL.md sample says the 09:45 bar closes at 9:50 AM at 1311.90, range 1295.60 to 1306.00
        with open(os.path.join(ROOT, "trader-entry", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        r = at(DAY + " 09:55")
        for line in entry.describe(r)[1:]:
            if line.startswith(("Opening range", "TRIGGERED", "This is the moment", "Next:")):
                self.assertIn(line, text)


if __name__ == "__main__":
    unittest.main()
