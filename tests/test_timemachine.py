import datetime
import gzip
import hashlib
import io
import json
import os
import random
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal, ROUND_FLOOR

from _load import load

tm = load("trader-timemachine", "timemachine")

# ---------------------------------------------------------------------------------------------
# Synthetic NSE data, built the way Yahoo serves it (v8 chart, range=60d interval=5m): 41 trading
# days of RELIANCE.NS, 75 five-minute bars a day from 09:15 to 15:30 IST, no NSE holiday in it, plus
# a part-built plan day with the live tick Yahoo adds. Timestamps are built with a plain IST
# tzinfo, not with the code under test.
# ---------------------------------------------------------------------------------------------

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
HOLIDAYS = {"2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03", "2026-04-14",
            "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10",
            "2026-11-24", "2026-12-25"}
PLAN_DAY = datetime.date(2026, 11, 6)      # a Friday
NOW = "2026-11-06 10:50"                   # IST
NOW_UTC = tm.parse_now(NOW)


def trading_days(end, n):
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5 and d.isoformat() not in HOLIDAYS:
            days.append(d)
        d -= datetime.timedelta(days=1)
    return sorted(days)


DAYS = trading_days(PLAN_DAY, 41)
PAST = DAYS[:-1]                           # 40 full sessions before the plan day


def epoch(day, hh, mm, ss=0):
    return int(datetime.datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=IST).timestamp())


def slot_ts(day, slot):
    return epoch(day, 9, 15) + 300 * slot


def walk(day):
    """75 deterministic (o, h, l, c, v) bars around Rs 1,300, each bar opening at the last close."""
    rng = random.Random(day.toordinal())
    price = 1300 * (1 + (rng.random() - 0.5) * 0.04)
    bars, prev = [], None
    for i in range(75):
        o = round(price if i == 0 else prev, 2)
        c = round(o * (1 + (rng.random() - 0.5) * 0.0036), 2)
        h = round(max(o, c) * (1 + rng.random() * 0.0012), 2)
        l = round(min(o, c) * (1 - rng.random() * 0.0012), 2)
        bars.append((o, h, l, c, 20000 + int(rng.random() * 80000)))
        prev = c
    return bars


def staircase(step):
    """A session that moves by `step` every bar with no wicks: up (step > 0), down or flat."""
    out, p = [], 1300.0
    for _ in range(75):
        c = p * (1 + step)
        out.append((p, max(p, c), min(p, c), c, 50000))
        p = c
    return out


# the plan day up to the bar that is still forming at 10:50: slots 0..18 (09:15 .. 10:45)
_OR = [(1300.0, 1304.0, 1298.0, 1302.0), (1302.0, 1305.0, 1299.0, 1301.0), (1301.0, 1304.0, 1299.5, 1303.0)]
_CLOSES = [1303.5, 1305.0, 1306.5, 1308.0, 1309.0, 1310.0, 1310.5, 1311.0, 1311.9,   # slots 3..11
           1312.0, 1311.5, 1312.5, 1313.0, 1312.2, 1313.4]                            # slots 12..17
TODAY = list(_OR)
for _c in _CLOSES:
    _o = TODAY[-1][3]
    TODAY.append((_o, max(_o, _c) + 1.0, min(_o, _c) - 1.0, _c))
TODAY.append((1313.4, 1314.5, 1313.0, 1314.0))       # slot 18, 10:45: not finished at 10:49:45
TICK = epoch(PLAN_DAY, 10, 49, 45)                     # Yahoo's live tick, off the 5-minute grid


def build(past=walk):
    stamps, cols = [], {k: [] for k in ("open", "high", "low", "close", "volume")}

    def add(ts, o, h, l, c, v):
        stamps.append(ts)
        for k, x in zip(cols, (o, h, l, c, v)):
            cols[k].append(x)
    for day in PAST:
        for i, b in enumerate(past(day)):
            add(slot_ts(day, i), *b)
    for i, b in enumerate(TODAY):
        add(slot_ts(PLAN_DAY, i), b[0], b[1], b[2], b[3], 100000)
    add(TICK, 1314.0, 1314.0, 1314.0, 1314.0, 0)
    meta = {"symbol": "RELIANCE.NS", "dataGranularity": "5m", "regularMarketTime": TICK}
    return json.dumps({"chart": {"result": [{"meta": meta, "timestamp": stamps,
                                             "indicators": {"quote": [cols]}}], "error": None}})


TEXT = build()

# the plan as trader-exit writes it: entry on the 10:15 close, stop at the opening-range midpoint
# (high 1305, low 1298 -> 1301.50), a 1R target
PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-11-06", "setup": "orb",
        "entry": 1311.90, "entry_time": "10:15 AM", "entry_bar": 11, "interval": "5m",
        "opening_range": {"high": 1305.0, "low": 1298.0, "minutes": 15},
        "stop": 1301.50, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 1322.30, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": 10.40, "reward_risk": 1.0, "flat_by": "15:20"}


def sha(x):
    return hashlib.sha256(json.dumps(x).encode("utf-8")).hexdigest()[:16]


def plan(**kw):
    raw = dict(PLAN)
    raw.update(kw)
    return tm.normalize_plan(raw)


def machine(p=None, text=TEXT, now=NOW_UTC, **kw):
    return tm.time_machine(text, p or plan(), now, **kw)


def edited(fn, text=TEXT):
    """The chart JSON, changed by fn(result), back as text."""
    doc = json.loads(text)
    fn(doc["chart"]["result"][0])
    return json.dumps(doc)


def keep_bars(result, keep):
    q = result["indicators"]["quote"][0]
    idx = [i for i, ts in enumerate(result["timestamp"]) if keep(ts)]
    result["timestamp"] = [result["timestamp"][i] for i in idx]
    for k in q:
        q[k] = [q[k][i] for i in idx]


def null_slots(result, day, slots):
    """Yahoo's way of missing a bar: the timestamp stays, every price is null."""
    q = result["indicators"]["quote"][0]
    want = {slot_ts(day, s) for s in slots}
    for i, ts in enumerate(result["timestamp"]):
        if ts in want:
            for k in q:
                q[k][i] = None


def ist_of(ts):
    return datetime.datetime.fromtimestamp(ts, IST)


def day_of(ts):
    return ist_of(ts).date()


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tm.main(argv)
    return code, out.getvalue(), err.getvalue()


def synthetic(step):
    """Chart text whose every past session is a staircase of `step` per bar."""
    return build(past=lambda day: staircase(step))


_REEL = []


def reel():
    if not _REEL:
        _REEL.append(machine())
    return _REEL[0]


class Session(unittest.TestCase):
    """The NSE session the time machine is built on."""

    def test_constants(self):
        self.assertEqual((tm.SESSION_BARS, tm.OPEN_MIN, tm.CLOSE_MIN), (75, 555, 930))
        self.assertEqual((tm.CLOSE_MIN - tm.OPEN_MIN) // 5, tm.SESSION_BARS)   # 09:15 to 15:30 is 375 minutes
        self.assertEqual(tm.clock24(tm.OPEN_MIN), "09:15")
        self.assertEqual(tm.clock24(tm.CLOSE_MIN), "15:30")
        self.assertEqual(tm.clock12(tm.OPEN_MIN), "9:15 AM")
        self.assertEqual(tm.clock12(tm.CLOSE_MIN), "3:30 PM")

    def test_no_new_york_left(self):
        for gone in ("NYSE_EARLY_CLOSE", "EARLY_MIN", "eastern_offset", "to_eastern", "eastern_to_utc"):
            self.assertFalse(hasattr(tm, gone), gone)

    def test_ist_has_no_daylight_saving(self):
        utc = tm.datetime.datetime(2026, 11, 6, 3, 45)
        self.assertEqual(tm.to_ist(utc), tm.datetime.datetime(2026, 11, 6, 9, 15))
        self.assertEqual(tm.ist_to_utc(tm.to_ist(utc)), utc)
        for day in (tm.datetime.datetime(2026, 1, 5), tm.datetime.datetime(2026, 7, 6), tm.datetime.datetime(2026, 11, 2)):
            self.assertEqual(tm.to_ist(day) - day, tm.datetime.timedelta(hours=5, minutes=30))

    def test_parse_now_reads_ist(self):
        self.assertEqual(tm.parse_now("2026-11-06 10:15"), tm.datetime.datetime(2026, 11, 6, 4, 45))
        self.assertEqual(tm.parse_now("2026-11-06 10:15 IST"), tm.datetime.datetime(2026, 11, 6, 4, 45))
        self.assertEqual(tm.parse_now("2026-11-06T10:15"), tm.datetime.datetime(2026, 11, 6, 4, 45))
        self.assertEqual(tm.parse_now("2026-11-06T04:45Z"), tm.datetime.datetime(2026, 11, 6, 4, 45))
        self.assertEqual(tm.parse_now("2026-11-06 10:15+05:30"), tm.datetime.datetime(2026, 11, 6, 4, 45))
        with self.assertRaises(tm.TMError) as cm:
            tm.parse_now("tomorrow")
        self.assertIn("IST", str(cm.exception))
        self.assertNotIn("New York", str(cm.exception))

    def test_utc_of(self):
        self.assertEqual(tm.utc_of(epoch(PLAN_DAY, 9, 15)), tm.datetime.datetime(2026, 11, 6, 3, 45))

    def test_parse_clock(self):
        for text, minutes in (("10:15 AM", 615), ("10:15am", 615), ("10:15", 615), ("15:20", 920), ("3:20", 920),
                              ("3:20 PM", 920), ("9:15", 555), ("12:00 PM", 720), ("12:00 AM", 0)):
            self.assertEqual(tm.parse_clock(text, "x"), minutes, text)
        for bad in ("noon", "25:00", "10:75", "13:00 PM", ""):
            with self.assertRaises(tm.TMError, msg=bad):
                tm.parse_clock(bad, "--until")
        with self.assertRaises(tm.TMError) as cm:
            tm.parse_clock("noon", "--until")
        self.assertIn("IST time", str(cm.exception))


class Reel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = reel()

    def test_the_reel_odds(self):
        m = self.doc["mc"]
        self.assertEqual((m["replays"], m["seed"], m["window"], m["until"]), (10000, 19, 2, "15:30"))
        self.assertEqual(m["pool_sessions"], 40)
        self.assertEqual(m["bars_left"], 63)             # bars 12..74: 10:20 to 15:30
        self.assertEqual((m["pool_from"], m["pool_to"]), (DAYS[0].isoformat(), "2026-11-05"))
        self.assertEqual((0.4003, 0.3086, 0.2911, 0.5464, 1313.08, 1295.42, 1330.85, 0.091),
                         (m["p_target"], m["p_stop"], m["p_neither"], m["p_green"],
                          m["median_close"], m["p5_close"], m["p95_close"], m["avg_R"]))
        self.assertEqual(m["r_at_target"], 1.0)

    def test_bit_for_bit_regression(self):
        m = self.doc["mc"]
        self.assertEqual(len(m["sample_paths"]), 160)
        self.assertEqual((sha(m["bands"]), sha(m["sample_paths"])),
                         ("c787aac7f57cb672", "ef03d04f6ba2c2eb"))
        self.assertEqual(m["hist"], {"lo": 1288.36, "hi": 1338.56, "bins": [
            106, 98, 167, 207, 301, 379, 430, 576, 638, 728, 696, 744, 765, 699, 653, 571, 535, 449, 368, 257, 192, 146, 82, 56]})

    def test_the_history_replay(self):
        h = self.doc["history"]
        self.assertEqual(h["sessions"], 40)
        self.assertEqual(h["rule"], {"setup": "orb", "or_minutes": 15, "stop_rule": "or_mid", "target_r": 1.0})
        self.assertEqual((h["triggered"], h["target"], h["stop"], h["close"], h["green"], h["avg_R"]),
                         (37, 18, 16, 3, 19, 0.073))      # recomputed from the raw JSON, not through the tool
        self.assertEqual(h["target"] + h["stop"] + h["close"], h["triggered"])
        for t in h["trades"]:
            self.assertTrue(t["stop"] < t["entry"] < t["target"], t)
            self.assertIn(t["outcome"], ("target", "stop", "close"))
            self.assertIn(t["date"], [d.isoformat() for d in PAST])
            self.assertRegex(t["time"], r"^\d{1,2}:\d{2} [AP]M$")
        first = h["trades"][0]
        self.assertEqual((first["date"], first["time"], first["entry"], first["stop"], first["target"], first["outcome"]),
                         ("2026-09-08", "10:00 AM", 1279.72, 1277.55, 1281.89, "target"))
        self.assertEqual(sha([{k: t[k] for k in ("date", "entry", "outcome", "R")} for t in h["trades"]]),
                         "27bf367b8e225a69")

    def test_the_verdict_is_the_one_the_numbers_give(self):
        self.assertEqual(self.doc["verdict"], tm.verdict(self.doc["mc"]))
        self.assertEqual(self.doc["verdict_notes"], tm.verdict_notes(self.doc["mc"]))
        self.assertEqual(self.doc["verdict"], "Leans toward the target: target first 40.0 % vs stop first 30.9 %.")
        self.assertEqual(self.doc["verdict_notes"], [])

    def test_bands_start_at_the_entry(self):
        m = self.doc["mc"]
        self.assertEqual(m["bands"][0], [1311.9] * 5)
        self.assertEqual(len(m["bands"]), 64)
        self.assertEqual((m["band_times"][0], m["band_times"][-1], len(m["band_times"])), ("10:15", "15:30", 64))
        self.assertTrue(all(p[0] == 1311.9 and len(p) == 64 for p in m["sample_paths"]))
        for lo, q25, q50, q75, hi in m["bands"]:
            self.assertTrue(lo <= q25 <= q50 <= q75 <= hi)

    def test_the_plan_is_carried_through(self):
        p = self.doc["plan"]
        self.assertEqual((p["symbol"], p["entry"], p["stop"], p["target"], p["entry_bar"], p["entry_time"]),
                         ("RELIANCE", 1311.9, 1301.5, 1322.3, 11, "10:15 AM"))
        self.assertEqual((p["risk_per_share"], p["target_r"], p["flat_by"]), (10.4, 1.0, "15:20"))
        self.assertEqual(self.doc["warnings"], [])
        self.assertEqual(self.doc["data"]["symbol"], "RELIANCE.NS")
        self.assertEqual(self.doc["data"]["last_bar"], "2026-11-06 10:40 IST")
        self.assertEqual(self.doc["data"]["short_sessions_left_out"], [])
        self.assertEqual(self.doc["created_at"], "2026-11-06T05:20:00Z")      # 10:50 IST is 05:20 UTC

    def test_the_text_report(self):
        text = "\n".join(tm.report(self.doc))
        m, h = self.doc["mc"], self.doc["history"]
        for line in ("RELIANCE buy, Friday 2026-11-06: entry 1311.90 at 10:15 AM (bar 11), stop 1301.50, "
                     "target 1322.30 (1R, risk 10.40 a share)",
                     "10,000 replays", "10:15 AM to 3:30 PM (63 five-minute bars)", "seed 19", "40 full sessions",
                     "target first   %.1f %%" % (100 * m["p_target"]), "stop first     %.1f %%" % (100 * m["p_stop"]),
                     "still open at 3:30 PM", "Price at 3:30 PM: median %.2f" % m["median_close"],
                     "Average R: %+.3f" % m["avg_R"], "fired %d times" % h["triggered"],
                     self.doc["verdict"], "not a prediction", "--seed 19"):
            self.assertIn(line, text)
        for word in ("profit", "P&L", "guarantee", "real time", "tick by tick", "$", "ET ", "New York", "4:00 PM",
                     "early close"):
            self.assertNotIn(word, text)


class Staircases(unittest.TestCase):
    """Data whose answer is known without running the simulation."""

    def test_every_session_rising_hits_the_target_every_time(self):
        doc = machine(text=synthetic(0.001), replays=400)
        m, h = doc["mc"], doc["history"]
        self.assertEqual((m["p_target"], m["p_stop"], m["p_neither"], m["p_green"], m["avg_R"]), (1.0, 0.0, 0.0, 1.0, 1.0))
        self.assertEqual(doc["verdict"], "Strongly toward the target: target first 100.0 % vs stop first 0.0 %.")
        self.assertEqual(doc["verdict_notes"], [])
        self.assertEqual((h["sessions"], h["triggered"], h["target"], h["stop"], h["avg_R"]), (40, 40, 40, 0, 1.0))

    def test_every_session_falling_hits_the_stop_every_time(self):
        doc = machine(text=synthetic(-0.001), replays=400)
        m, h = doc["mc"], doc["history"]
        self.assertEqual((m["p_target"], m["p_stop"], m["p_green"], m["avg_R"]), (0.0, 1.0, 0.0, -1.0))
        self.assertEqual(doc["verdict"], "Strongly toward the stop: target first 0.0 % vs stop first 100.0 %.")

    def test_a_sell_into_falling_sessions_hits_the_target(self):
        p = plan(side="sell", entry=1311.9, stop=1322.3, target=1301.5, stop_rule="pct")
        doc = machine(p, text=synthetic(-0.001), replays=400)
        m, h = doc["mc"], doc["history"]
        self.assertEqual((m["p_target"], m["p_stop"], m["p_green"], m["avg_R"]), (1.0, 0.0, 1.0, 1.0))
        self.assertEqual((h["triggered"], h["target"], h["stop"], h["avg_R"]), (40, 40, 0, 1.0))
        self.assertIn("below the entry", "\n".join(tm.report(doc)))

    def test_flat_sessions_leave_every_replay_open_at_the_close(self):
        doc = machine(text=synthetic(0.0), replays=300)
        m = doc["mc"]
        self.assertEqual((m["p_target"], m["p_stop"], m["p_neither"], m["p_green"]), (0.0, 0.0, 1.0, 0.0))
        self.assertEqual((m["median_close"], m["p5_close"], m["p95_close"], m["avg_R"]), (1311.9, 1311.9, 1311.9, 0.0))
        self.assertEqual(doc["verdict"], "No replay reached the target or the stop by 3:30 PM: "
                                         "the price at 3:30 PM decides every one.")
        self.assertEqual(doc["history"]["triggered"], 0)    # a flat session never closes beyond its range
        self.assertIn("never fired in those 40 sessions", "\n".join(tm.report(doc)))

    def test_until_the_flat_by_time_ends_the_replay_there(self):
        doc = machine(text=synthetic(0.0), until="15:20", replays=100)
        self.assertEqual((doc["mc"]["until"], doc["mc"]["bars_left"], doc["mc"]["band_times"][-1]), ("15:20", 61, "15:20"))
        self.assertIn("No replay reached the target or the stop by 3:20 PM", doc["verdict"])


class Seeds(unittest.TestCase):
    def test_same_seed_same_numbers(self):
        a = machine(replays=2000, seed=5)["mc"]
        b = machine(replays=2000, seed=5)["mc"]
        self.assertEqual(a, b)

    def test_another_seed_gives_other_numbers(self):
        m, base = machine(seed=7)["mc"], reel()["mc"]
        self.assertNotEqual((m["p_target"], m["p_stop"], m["median_close"]),
                            (base["p_target"], base["p_stop"], base["median_close"]))
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertNotEqual(sha(m["bands"]), sha(base["bands"]))

    def test_the_draws_are_stable_across_pythons(self):
        # random.Random(seed), randint and choice are stable since Python 3.2; this pins the first draws so a
        # failure points at the RNG and not at the data
        r = random.Random(19)
        self.assertEqual([r.randint(-2, 2) for _ in range(8)], [-2, 2, -2, 2, -1, 1, 0, 2])
        self.assertEqual(r.choice(range(40)), 18)

    def test_window_and_replays(self):
        a = machine(replays=500, window=0)["mc"]
        self.assertEqual((a["replays"], a["window"]), (500, 0))
        self.assertNotEqual(a["bands"], machine(replays=500)["mc"]["bands"])
        with self.assertRaises(tm.TMError):
            machine(replays=0)
        with self.assertRaises(tm.TMError):
            machine(window=11)


class Bars(unittest.TestCase):
    def setUp(self):
        self.result = tm.chart_result(TEXT, "RELIANCE")

    def test_the_in_progress_tick_is_dropped(self):
        meta, bars, dropped = tm.parse_bars(self.result, NOW_UTC)
        self.assertEqual(dropped, {"no_price": 0, "off_grid": 1, "not_complete": 1, "outside_hours": 0})
        last = bars[-1]
        self.assertEqual((last["date"], last["start"], last["slot"]), ("2026-11-06", "10:40", 17))
        # with the real clock (any time later) the data's own time still marks 10:45 as in progress
        self.assertEqual(tm.parse_bars(self.result, tm.parse_now(None))[1][-1]["start"], "10:40")
        entry = [b for b in bars if b["date"] == "2026-11-06" and b["slot"] == 11][0]
        self.assertEqual((entry["start"], entry["c"]), ("10:10", 1311.9))
        self.assertEqual(len(bars), 40 * 75 + 18)

    def test_the_first_bar_opens_at_9_15_ist(self):
        bars = tm.parse_bars(self.result, NOW_UTC)[1]
        first = [b for b in bars if b["date"] == PAST[0].isoformat()]
        self.assertEqual((first[0]["start"], first[0]["slot"]), ("09:15", 0))
        self.assertEqual((first[-1]["start"], first[-1]["slot"]), ("15:25", 74))
        self.assertEqual(len(first), 75)

    def test_now_pins_the_clock(self):
        bars = tm.parse_bars(self.result, tm.parse_now("2026-11-06 10:12"))[1]
        today = [b for b in bars if b["date"] == "2026-11-06"]
        self.assertEqual(len(today), 11)                     # bars that started 09:15 .. 10:05 are complete
        with self.assertRaises(tm.TMError) as cm:
            machine(now=tm.parse_now("2026-11-06 10:12"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("10:05 bar", str(cm.exception))
        self.assertEqual(machine(now=tm.parse_now("2026-11-06 10:15"), replays=200)["mc"]["pool_sessions"], 40)

    def test_hours_are_09_15_to_15_30_ist_in_every_month(self):
        # IST never shifts: the same wall-clock bar has the same slot in March and in November
        stamps = [epoch(datetime.date(2026, 3, 27), 9, 15),    # first bar of the day
                  epoch(datetime.date(2026, 11, 2), 9, 15),
                  epoch(datetime.date(2026, 11, 2), 9, 10),    # before the open
                  epoch(datetime.date(2026, 11, 2), 15, 25),   # the last bar, closing at 15:30
                  epoch(datetime.date(2026, 11, 2), 15, 30),   # after the close
                  epoch(datetime.date(2026, 11, 2), 16, 0)]
        n = len(stamps)
        res = {"meta": {"symbol": "X"}, "timestamp": stamps,
               "indicators": {"quote": [{"open": [1] * n, "high": [1] * n, "low": [1] * n, "close": [1] * n,
                                         "volume": [0] * n}]}}
        meta, bars, dropped = tm.parse_bars(res, tm.parse_now("2026-11-03 00:00"))
        self.assertEqual([(b["date"], b["start"], b["slot"]) for b in bars],
                         [("2026-03-27", "09:15", 0), ("2026-11-02", "09:15", 0), ("2026-11-02", "15:25", 74)])
        self.assertEqual(dropped["outside_hours"], 3)

    def test_a_short_session_is_left_out(self):
        # NSE has no early close, so a session that stops at 13:00 is a data gap: 45 bars, left out
        day = PAST[10]

        def cut(res):
            keep_bars(res, lambda ts: not (day_of(ts) == day and ist_of(ts).hour >= 13))
        doc = machine(text=edited(cut), replays=300)
        self.assertEqual(doc["mc"]["pool_sessions"], 39)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": day.isoformat(), "bars": 45}])
        self.assertEqual(doc["history"]["sessions"], 39)
        self.assertNotIn(day.isoformat(), [t["date"] for t in doc["history"]["trades"]])
        self.assertIn("Left out 1 short session", "\n".join(tm.report(doc)))

    def test_today_is_not_in_the_pool(self):
        doc = machine(replays=200)
        self.assertEqual(doc["mc"]["pool_to"], "2026-11-05")
        self.assertNotIn("2026-11-06", [t["date"] for t in doc["history"]["trades"]])

    def test_nse_holidays_are_not_in_the_data(self):
        sessions, short = tm.full_sessions(tm.split_sessions(tm.parse_bars(self.result, NOW_UTC)[1]), "2026-11-06")
        dates = [d for d, _ in sessions]
        for holiday in ("2026-09-14", "2026-10-02", "2026-10-20"):   # Ganesh Chaturthi, Gandhi Jayanti, Dussehra
            self.assertIn(holiday, HOLIDAYS)
            self.assertNotIn(holiday, dates)
        self.assertEqual(short, [])
        self.assertEqual(len(sessions), 40)


class Gaps(unittest.TestCase):
    """Yahoo leaves NSE's 15:20 and 15:25 bars empty: up to three missing bars still make a full session."""

    DAY = PAST[5]

    def all_days(self, slots):
        def f(res):
            for d in PAST:
                null_slots(res, d, slots)
        return edited(f)

    def test_the_2_empty_closing_bars_are_filled_in_every_session(self):
        text = self.all_days((73, 74))                       # 15:20 and 15:25
        res = tm.chart_result(text, "RELIANCE")
        meta, bars, dropped = tm.parse_bars(res, NOW_UTC)
        self.assertEqual(dropped["no_price"], 80)
        doc = machine(text=text, replays=200)
        self.assertEqual(doc["mc"]["pool_sessions"], 40)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [])
        self.assertEqual(len(doc["warnings"]), 1)
        self.assertIn("Yahoo left 80 five-minute bar(s) empty across 40 sessions", doc["warnings"][0])
        self.assertIn("15:20 and 15:25", doc["warnings"][0])
        self.assertIn("Note: Yahoo left 80", "\n".join(tm.report(doc)))

    def test_a_filled_bar_is_flat_at_the_previous_close_with_no_volume(self):
        text = self.all_days((73, 74))
        sessions, short = tm.full_sessions(tm.split_sessions(tm.parse_bars(tm.chart_result(text, "RELIANCE"),
                                                                          NOW_UTC)[1]), "2026-11-06")
        self.assertEqual((len(sessions), short), (40, []))
        for d, bars in sessions:
            self.assertEqual(len(bars), 75)
            self.assertEqual([b["slot"] for b in bars], list(range(75)))
            last_real = bars[72]
            self.assertNotIn("filled", last_real)
            for b in bars[73:]:
                self.assertTrue(b["filled"])
                self.assertEqual((b["o"], b["h"], b["l"], b["c"], b["v"]),
                                 (last_real["c"], last_real["c"], last_real["c"], last_real["c"], 0))

    def test_three_missing_bars_anywhere_still_count(self):
        def f(res):
            null_slots(res, self.DAY, (30, 73, 74))
        doc = machine(text=edited(f), replays=200)
        self.assertEqual(doc["mc"]["pool_sessions"], 40)
        self.assertIn("Yahoo left 3 five-minute bar(s) empty across 1 sessions", doc["warnings"][0])
        sessions, _ = tm.full_sessions(tm.split_sessions(tm.parse_bars(tm.chart_result(edited(f), "RELIANCE"),
                                                                      NOW_UTC)[1]), "2026-11-06")
        day = dict(sessions)[self.DAY.isoformat()]
        self.assertTrue(day[30]["filled"])
        self.assertEqual(day[30]["c"], day[29]["c"])

    def test_four_missing_bars_reject_the_session(self):
        def f(res):
            null_slots(res, self.DAY, (30, 72, 73, 74))
        doc = machine(text=edited(f), replays=200)
        self.assertEqual(doc["mc"]["pool_sessions"], 39)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": self.DAY.isoformat(), "bars": 71}])
        self.assertEqual(doc["warnings"], [])
        self.assertIn("Left out 1 short session", "\n".join(tm.report(doc)))

    def test_a_session_without_its_first_bar_is_rejected(self):
        def f(res):
            null_slots(res, self.DAY, (0,))
        doc = machine(text=edited(f), replays=200)
        self.assertEqual(doc["mc"]["pool_sessions"], 39)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": self.DAY.isoformat(), "bars": 74}])

    def test_a_single_missing_price_is_filled_not_lost(self):
        def hole(res):
            res["indicators"]["quote"][0]["close"][5] = None
        doc = machine(text=edited(hole), replays=200)
        self.assertEqual(doc["data"]["dropped"]["no_price"], 1)
        self.assertEqual(doc["mc"]["pool_sessions"], 40)
        self.assertIn("Yahoo left 1 five-minute bar(s) empty across 1 sessions", doc["warnings"][0])

    def test_full_sessions_returns_all_75_bars(self):
        days = tm.split_sessions(tm.parse_bars(tm.chart_result(TEXT, "RELIANCE"), NOW_UTC)[1])
        sessions, short = tm.full_sessions(days, "2026-11-06")
        self.assertEqual((len(sessions), short), (40, []))
        self.assertTrue(all(len(b) == 75 and not any(x.get("filled") for x in b) for _, b in sessions))
        sessions, _ = tm.full_sessions(days, PAST[3].isoformat())      # strictly before the plan date
        self.assertEqual(len(sessions), 3)


class DataChecks(unittest.TestCase):
    def test_empty_and_broken_data(self):
        empty = json.dumps({"chart": {"result": [{"meta": {"symbol": "RELIANCE.NS"}, "timestamp": [],
                                                  "indicators": {"quote": [{}]}}], "error": None}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=empty)
        self.assertIn("no complete 5-minute bars", str(cm.exception))
        gone = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                               "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=gone)
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(tm.TMError):
            machine(text="<html>rate limited</html>")

    def test_fewer_than_20_sessions_is_refused(self):
        def trim(res):
            keep_bars(res, lambda ts: day_of(ts) >= DAYS[-20])      # 19 past sessions and the plan day
        with self.assertRaises(tm.TMError) as cm:
            machine(text=edited(trim))
        self.assertIn("only 19 full session(s)", str(cm.exception))
        self.assertIn("at least 20", str(cm.exception))

        def twenty(res):
            keep_bars(res, lambda ts: day_of(ts) >= DAYS[-21])
        self.assertEqual(machine(text=edited(twenty), replays=200)["mc"]["pool_sessions"], 20)

    def test_the_data_must_be_the_plans_ticker(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(symbol="TCS"))
        self.assertIn("data is for RELIANCE.NS but the plan is for TCS", str(cm.exception))
        self.assertEqual(machine(plan(symbol="RELIANCE.NS"), replays=100)["data"]["symbol"], "RELIANCE.NS")

    def test_the_data_must_be_5_minute_bars(self):
        def hourly(res):
            res["meta"]["dataGranularity"] = "1h"
        with self.assertRaises(tm.TMError) as cm:
            machine(text=edited(hourly))
        self.assertIn("needs 5-minute bars", str(cm.exception))


class Plans(unittest.TestCase):
    def test_an_entry_not_in_the_data_is_refused(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-11-09"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("no bars for that day", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:      # 10:50 has not happened in this data yet
            machine(plan(entry_time="10:55 AM", entry_bar=None))
        self.assertIn("not in the data", str(cm.exception))

    def test_plan_checks(self):
        cases = [
            (dict(entry_time="10:17 AM", entry_bar=None), "5-minute grid"),
            (dict(entry_time="3:30 PM", entry_bar=None), "between 9:20 AM and 3:25 PM IST"),
            (dict(entry_time="9:15 AM", entry_bar=None), "between 9:20 AM and 3:25 PM IST"),
            (dict(entry_time="4:00 PM", entry_bar=None), "between 9:20 AM and 3:25 PM IST"),
            (dict(entry_bar=12), "disagrees with itself"),
            (dict(entry_time=None, entry_bar=None), "entry_time"),
            (dict(interval="1m"), "7 days of 1-min bars"),
            (dict(stop=1315.0), "stop < entry < target"),
            (dict(side="short"), "buy or sell"),
            (dict(symbol=""), "ticker"),
            (dict(entry="abc"), "number for entry"),
            (dict(date="Nov 6"), "date like"),
            (dict(opening_range={"minutes": 7}), "multiple of 5"),
            (dict(opening_range={"minutes": 375}), "multiple of 5"),
            (dict(opening_range={"minutes": 380}), "multiple of 5"),
        ]
        for change, words in cases:
            with self.assertRaises(tm.TMError, msg=change) as cm:
                plan(**change)
            self.assertIn(words, str(cm.exception), change)

    def test_an_opening_range_longer_than_the_session_is_an_error_not_a_crash(self):
        for minutes in (375, 380, 390):
            with self.assertRaises(tm.TMError, msg=minutes) as cm:
                machine(plan(opening_range={"minutes": minutes}))
            self.assertIn("shorter than the 375-minute session", str(cm.exception))
        doc = machine(plan(opening_range={"minutes": 370}), replays=50)      # the longest range that fits: no crash
        self.assertEqual(doc["history"]["triggered"], 1)       # only the 15:25 bar can break a 370-minute range
        self.assertEqual(plan(opening_range={"minutes": 370})["opening_range"]["minutes"], 370)
        self.assertEqual(plan(opening_range={"minutes": 60})["opening_range"]["minutes"], 60)

    def test_nse_tickers_are_accepted(self):
        for sym in ("RELIANCE", "reliance", "M&M", "360ONE", "BAJAJ-AUTO", "RELIANCE.NS", "TCS.BO", "HDFCBANK"):
            self.assertEqual(plan(symbol=sym)["symbol"], sym.upper(), sym)
        for bad in ("", "REL IANCE", "-X", ".NS", "123", "A/B", "&"):
            with self.assertRaises(tm.TMError, msg=bad):
                plan(symbol=bad)

    def test_entry_bar_or_entry_time_alone(self):
        self.assertEqual(plan(entry_time=None)["entry_time"], "10:15 AM")
        self.assertEqual(plan(entry_bar=None)["entry_bar"], 11)
        self.assertEqual(plan(entry_time="10:15", entry_bar=None)["entry_bar"], 11)
        self.assertEqual(plan(entry_time="10:15am", entry_bar=None)["entry_bar"], 11)
        self.assertEqual(plan(entry_time="9:20 AM", entry_bar=None)["entry_bar"], 0)       # earliest entry
        self.assertEqual(plan(entry_time="3:25 PM", entry_bar=None)["entry_bar"], 73)      # latest entry
        self.assertEqual(plan(entry_time="3:25", entry_bar=None)["entry_bar"], 73)
        self.assertEqual(plan(entry_time="15:25", entry_bar=None)["entry_bar"], 73)

    def test_until_cuts_the_replay(self):
        doc = machine(until="15:20", replays=500)
        m = doc["mc"]
        self.assertEqual((m["until"], m["bars_left"], m["band_times"][-1]), ("15:20", 61, "15:20"))
        self.assertIn("still open at 3:20 PM", "\n".join(tm.report(doc)))
        self.assertEqual(machine(until="3:20 PM", replays=500)["mc"], m)
        self.assertEqual(machine(until="15:30", replays=100)["mc"]["until"], "15:30")
        for bad, words in (("10:15 AM", "nothing to replay"), ("15:35", "no later than 3:30 PM"),
                           ("16:00", "no later than 3:30 PM"), ("15:27", "5-minute grid"), ("noon", "IST time")):
            with self.assertRaises(tm.TMError) as cm:
                machine(until=bad, replays=10)
            self.assertIn(words, str(cm.exception), bad)
            self.assertNotIn("early close", str(cm.exception))
            self.assertNotIn("New York", str(cm.exception))

    def test_there_is_no_early_close_on_nse(self):
        # 13:00 is just a time on the grid here (it was the NYSE half-day close); the replay runs to 15:30
        doc = machine(until="13:00", replays=100)
        self.assertEqual((doc["mc"]["until"], doc["mc"]["bars_left"]), ("13:00", 33))     # bars 12..44
        self.assertEqual(machine(replays=100)["mc"]["until"], "15:30")
        self.assertNotIn("1:00 PM", "\n".join(tm.report(machine(replays=100))))

    def test_an_entry_outside_its_bar_is_flagged(self):
        doc = machine(plan(entry=1320.0, stop=1310.0, target=1330.0), replays=200)
        self.assertIn("outside that bar's range", doc["warnings"][0])

    def test_stop_rounds_away_from_the_entry(self):
        self.assertEqual(tm.cents_away(1301.525, True), 1301.52)
        self.assertEqual(tm.cents_away(1301.525, False), 1301.53)
        self.assertEqual(tm.cents_away(1301.5, True), 1301.5)
        self.assertEqual(tm.cents_away(1298.4700012207031, True), 1298.47)
        self.assertEqual(tm.cents_away(1298.46999999999997, True), 1298.47)
        # the same rounding worked out with Decimal
        for x in (1301.525, 1298.4700012207031, 2481.375):
            self.assertEqual(tm.cents_away(x, True), float(Decimal(repr(round(x * 100, 4))).to_integral_value(ROUND_FLOOR)) / 100)

    def test_the_stop_rule_is_worked_out_when_not_given(self):
        rule = machine(plan(stop_rule=None), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "or_mid")
        rule = machine(plan(stop_rule=None, stop=1298.0, target=1325.8), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["target_r"]), ("or_low", 1.0))
        rule = machine(plan(stop_rule="atr", stop=1300.0, target=1335.7), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["stop_pct"], rule["target_r"]),
                         ("pct", round(100 * 11.9 / 1311.9, 4), 2.0))

    def test_a_setup_other_than_orb_skips_the_history(self):
        doc = machine(plan(setup="flag"), replays=200)
        self.assertIsNone(doc["history"]["rule"])
        self.assertIn("orb", doc["history"]["note"])
        self.assertEqual(doc["mc"]["pool_sessions"], 40)
        self.assertIn("History: skipped", "\n".join(tm.report(doc)))

    def test_a_sell_plan_mirrors_the_rules(self):
        doc = machine(plan(side="sell", entry=1311.9, stop=1322.3, target=1301.5, stop_rule="or_high"), replays=2000)
        m = doc["mc"]
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertEqual(m["r_at_target"], 1.0)
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "or_high")
        for t in doc["history"]["trades"]:
            self.assertTrue(t["target"] < t["entry"] < t["stop"], t)
        self.assertIn("below the entry", "\n".join(tm.report(doc)))

    def test_the_verdict_thresholds_at_1r(self):
        # 1R: the target's share of the replays that hit one side against 50 %
        self.assertTrue(tm.verdict({"p_target": 0.30, "p_stop": 0.26}).startswith("Close to a coin flip"))
        self.assertTrue(tm.verdict({"p_target": 0.40, "p_stop": 0.30}).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict({"p_target": 0.20, "p_stop": 0.30}).startswith("Leans toward the stop"))
        self.assertTrue(tm.verdict({"p_target": 0.60, "p_stop": 0.30}).startswith("Strongly toward the target"))
        self.assertEqual(tm.verdict({"p_target": 0.10, "p_stop": 0.60, "r_at_target": 1.0}),
                         "Strongly toward the stop: target first 10.0 % vs stop first 60.0 %.")
        self.assertEqual(tm.verdict({"p_target": 0.0, "p_stop": 0.0, "until": "15:20"}),
                         "No replay reached the target or the stop by 3:20 PM: the price at 3:20 PM decides every one.")
        self.assertEqual(tm.verdict({"p_target": 0.0, "p_stop": 0.0}),
                         "No replay reached the target or the stop by 3:30 PM: the price at 3:30 PM decides every one.")

    def test_the_notes_name_the_nse_close(self):
        mc = {"p_target": 0.29, "p_stop": 0.25, "p_neither": 0.46, "r_at_target": 1.0, "avg_R": 0.03}
        self.assertEqual(tm.verdict_notes(mc), ["46.0 % of the replays hit neither by 3:30 PM; "
                                                "the price at 3:30 PM decides those."])
        self.assertEqual(tm.verdict_notes(dict(mc, until="15:20")),
                         ["46.0 % of the replays hit neither by 3:20 PM; the price at 3:20 PM decides those."])

    def test_a_2r_target_is_judged_against_its_break_even(self):
        # a live 2R run (entry 370.58, stop 367.06, target 377.62) needed 33 % and got 32 %; the verdict maths
        # does not depend on the market, so the numbers carry over unchanged
        mc = {"p_target": 0.241, "p_stop": 0.505, "p_neither": 0.254, "r_at_target": 2.0, "avg_R": 0.095}
        self.assertEqual(tm.verdict(mc), "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 32 % (target first 24.1 % vs stop "
                                         "first 50.5 %).")
        self.assertEqual(tm.verdict_notes(mc), [])
        lean = dict(mc, p_target=0.15, p_stop=0.55)
        self.assertTrue(tm.verdict(lean).startswith("Leans toward the stop: at 2R the target has to come first in "
                                                    "33 % of the replays that hit one side; it did in 21 %"))
        self.assertEqual(tm.verdict_notes(lean), ["Average R is +0.095 all the same: the replays that hit neither "
                                                  "pull it the other way."])
        self.assertTrue(tm.verdict(dict(mc, p_target=0.30, p_stop=0.40)).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict(dict(mc, p_target=0.40, p_stop=0.40)).startswith("Strongly toward the target"))
        self.assertIn("at 1.5R the target has to come first in 40 %", tm.verdict(dict(mc, r_at_target=1.5)))

    def test_2r_on_the_data_is_judged_against_a_third(self):
        doc = machine(plan(stop=1301.5, target=1332.7, stop_rule="or_mid"))
        m = doc["mc"]
        self.assertEqual(m["r_at_target"], 2.0)
        self.assertTrue(doc["verdict"].split(":")[1].startswith(" at 2R the target has to come first in 33 % of the "
                                                                "replays that hit one side"))
        self.assertEqual(doc["verdict"], tm.verdict(m))
        self.assertEqual((m["p_target"], m["p_stop"], m["p_neither"], m["avg_R"]), (0.0761, 0.3133, 0.6106, 0.101))
        self.assertEqual(doc["verdict"], "Leans toward the stop: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 20 % (target first 7.6 % vs stop first "
                                         "31.3 %).")


class Network(unittest.TestCase):
    def setUp(self):
        self.calls, self.urls, self.sleeps = [], [], []
        self._get, self._sleep = tm._get, tm._sleep
        tm._sleep = self.sleeps.append

    def tearDown(self):
        tm._get, tm._sleep = self._get, self._sleep

    def answer(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.urls.append(url)
            self.calls.append(url.split(".finance")[0].split("//")[1])
            code = codes.pop(0)
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return TEXT
        tm._get = fake

    def test_url_and_user_agent(self):
        self.assertEqual(tm.chart_url("reliance"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=60d&interval=5m")
        self.assertIn("/chart/RELIANCE.NS?", tm.chart_url("RELIANCE", "query1"))
        self.assertTrue(tm.chart_url("RELIANCE", "query1").startswith("https://query1.finance.yahoo.com/"))
        self.assertEqual(tm.UA, "Mozilla/5.0 (compatible; day-trader-skill/2.0; "
                                "+https://github.com/Jakeschincariol/day-trader-skill)")

    def test_ns_symbols_are_never_turned_into_dash_ns(self):
        self.assertIn("/chart/RELIANCE.NS?", tm.chart_url("RELIANCE.NS"))
        self.assertIn("/chart/RELIANCE.NS?", tm.chart_url("reliance.ns"))
        self.assertIn("/chart/RELIANCE.BO?", tm.chart_url("RELIANCE.BO"))      # BSE keeps its suffix
        for sym in ("RELIANCE", "RELIANCE.NS", "TCS", "HDFCBANK", "BAJAJ-AUTO"):
            self.assertNotIn("-NS", tm.chart_url(sym), sym)
        self.assertIn("/chart/BAJAJ-AUTO.NS?", tm.chart_url("bajaj-auto"))
        self.assertIn("/chart/M%26M.NS?", tm.chart_url("m&m"))                  # the & is escaped, not lost
        self.assertEqual(tm._nse_yahoo_sym("tcs"), "TCS.NS")

    def test_429_waits_retries_then_falls_back_to_query1(self):
        self.answer([429, 429, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2", "query1"])
        self.assertEqual(self.sleeps, [tm.BACKOFF, tm.PAUSE])
        self.assertTrue(all("/chart/RELIANCE.NS?" in u for u in self.urls))

    def test_one_429_then_ok(self):
        self.answer([429, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2"])

    def test_5xx_falls_back(self):
        self.answer([503, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query1"])

    def test_rate_limited_everywhere_is_one_plain_line(self):
        self.answer([429, 429, 429, 429])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("RELIANCE")
        self.assertIn("rate limiting", str(cm.exception))

    def test_404(self):
        self.answer([404])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("ZZZZ")
        self.assertIn("check the ticker", str(cm.exception))

    def test_run_over_the_network_path(self):
        self.answer([200])
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(PLAN, fh)
            code, text, _ = run(["run", "--plan", p, "--now", NOW, "--replays", "300"])
            self.assertEqual(code, 0)
            self.assertEqual(self.calls, ["query2"])
            self.assertIn("/chart/RELIANCE.NS?", self.urls[0])
        finally:
            shutil.rmtree(d)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.plan = os.path.join(self.d, "trader", "plan.json")
        os.makedirs(os.path.dirname(self.plan))
        with open(self.plan, "w") as fh:
            json.dump(PLAN, fh, indent=2)
        self.out = os.path.join(self.d, "trader", "timemachine.json")
        self.chart = os.path.join(self.d, "RELIANCE-60d5m.json.gz")
        with gzip.open(self.chart, "wt", encoding="utf-8") as fh:
            fh.write(TEXT)

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_run_with_the_plan_end_to_end(self):
        base = reel()
        code, text, err = run(["run", "--plan", self.plan, "--offline", self.chart, "--now", NOW, "--write", self.out])
        self.assertEqual(code, 0, err)
        self.assertIn("target first   %.1f %%" % (100 * base["mc"]["p_target"]), text)
        self.assertIn(base["verdict"], text)
        self.assertIn("Wrote %s." % self.out, text)
        self.assertIn("/trader-budget", text)
        with open(self.out) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(k for k in ("plan", "mc", "history") if k in doc), ["history", "mc", "plan"])
        self.assertEqual(doc["created_by"], "trader-timemachine")
        self.assertEqual(doc["source"], "offline")
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["avg_R"]), (base["mc"]["p_target"], base["mc"]["avg_R"]))
        self.assertEqual(sha(doc["mc"]["sample_paths"]), sha(base["mc"]["sample_paths"]))
        self.assertEqual((doc["history"]["triggered"], doc["history"]["avg_R"]),
                         (base["history"]["triggered"], base["history"]["avg_R"]))
        self.assertEqual(doc["plan"]["from"], self.plan)
        self.assertNotIn("$", text)

    def test_json_output(self):
        code, text, _ = run(["run", "--plan", self.plan, "--offline", self.chart, "--now", NOW, "--json"])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual(doc["mc"]["p_stop"], reel()["mc"]["p_stop"])
        self.assertNotIn("sample_paths", doc["mc"])
        self.assertEqual(doc["verdict"], reel()["verdict"])
        self.assertFalse(os.path.exists(self.out))

    def test_the_plan_as_flags_matches_the_file(self):
        base = reel()
        code, text, err = run(["run", "--symbol", "reliance", "--date", "2026-11-06", "--entry", "1311.90",
                               "--stop", "1301.50", "--target", "1322.30", "--entry-time", "10:15 AM",
                               "--offline", self.chart, "--now", NOW, "--json"])
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["p_green"]), (base["mc"]["p_target"], base["mc"]["p_green"]))
        self.assertEqual(doc["plan"]["stop_rule"], "or_mid")
        self.assertEqual((doc["history"]["target"], doc["history"]["stop"], doc["history"]["avg_R"]),
                         (base["history"]["target"], base["history"]["stop"], base["history"]["avg_R"]))
        code, _, err = run(["run", "--symbol", "RELIANCE", "--entry", "1311.90", "--offline", self.chart])
        self.assertEqual(code, 1)
        self.assertIn("--stop", err)

    def test_the_date_defaults_to_today_in_ist(self):
        # 23:50 UTC on Nov 5 is already Nov 6 in India
        code, text, err = run(["run", "--symbol", "RELIANCE", "--entry", "1311.90", "--stop", "1301.50",
                               "--target", "1322.30", "--entry-time", "10:15 AM", "--offline", self.chart,
                               "--now", "2026-11-05T23:50Z", "--json"])
        self.assertEqual(code, 1)           # 05:20 IST: the plan day exists but has no complete 10:10 bar yet
        self.assertIn("2026-11-06", err)

    def test_offline_folder_and_default_paths(self):
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            code, text, err = run(["run", "--offline", ".", "--now", NOW, "--replays", "500", "--write"])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.exists(os.path.join("trader", "timemachine.json")))
            code, text, err = run(["chart", "--from"])
            self.assertEqual(code, 0, err)
            self.assertIn("* median", text)
            with open(os.path.join("trader", "timemachine.json")) as fh:
                self.assertEqual(json.load(fh)["mc"]["replays"], 500)
        finally:
            os.chdir(cwd)

    def test_errors_are_one_plain_line(self):
        for argv in (["run", "--plan", os.path.join(self.d, "nope.json")],
                     ["run", "--plan", self.plan, "--offline", os.path.join(self.d, "missing.json.gz")],
                     ["run", "--plan", self.plan, "--offline", self.chart, "--until", "9:00"],
                     ["run", "--plan", self.plan, "--offline", self.chart, "--until", "16:00"],
                     ["chart", "--from", os.path.join(self.d, "nope.json")]):
            code, text, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertTrue(err.startswith("timemachine.py: "), err)
            self.assertNotIn("Traceback", err)
        self.assertEqual(run([])[0], 1)

    def test_chart(self):
        run(["run", "--plan", self.plan, "--offline", self.chart, "--now", NOW, "--write", self.out])
        code, text, _ = run(["chart", "--from", self.out])
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertIn("RELIANCE 2026-11-06, 2,000 replay paths from the 10:15 AM entry (seed 19)", lines[0])
        self.assertTrue(any(l.startswith("%9.2f |" % 1322.30) and l.endswith("  target") for l in lines))
        self.assertTrue(any(l.startswith("%9.2f |" % 1301.50) and l.endswith("  stop") for l in lines))
        self.assertTrue(any(l.startswith("%9.2f |*" % 1311.90) and l.endswith("  entry") for l in lines))
        self.assertIn("10:15", text)
        self.assertIn("3:30", text)
        self.assertNotIn("4:00", text)
        self.assertEqual(lines[-len(reel()["verdict_notes"]) - 1:], [reel()["verdict"]] + reel()["verdict_notes"])
        code, again, _ = run(["chart", "--plan", self.plan, "--offline", self.chart, "--now", NOW])
        self.assertEqual(again, text)


if __name__ == "__main__":
    unittest.main()
