import json
import os
import re
import unittest

from _load import ROOT

# v2, the day-trading pack (NSE edition): the eleven folders the repo card shows
SKILLS = ["trader-scanner", "trader-news", "trader-entry", "trader-exit", "trader-timemachine",
          "trader-risk", "trader-budget", "trader-broker", "trader-alerts", "trader-analyst",
          "trader-pelosi"]
TAIL = ("scan thousands of charts, track breaking news, time every entry and exit, and replay every "
        "outcome before you risk a rupee.")
ABOUT = "11 Claude skills for day trading on NSE: " + TAIL                          # .claude-plugin/plugin.json
ABOUT_README = "11 Claude skills for day trading on the Indian market (NSE): " + TAIL   # README.md
# skills on the way to an order carry the safety block
ORDER_PATH = ["trader-scanner", "trader-entry", "trader-exit", "trader-timemachine", "trader-budget",
              "trader-pelosi", "trader-risk", "trader-broker", "trader-alerts"]
V1_ONLY = ["trader-copycat", "trader-screener", "trader-tax", "trader-rebalance"]
# the five tools that carry their own copy of the NSE holiday list (2026, as NSE publishes it)
HOLIDAY_TOOLS = [("trader-alerts", "alerts"), ("trader-entry", "entry"), ("trader-exit", "exit"),
                 ("trader-risk", "check"), ("trader-scanner", "scan")]
OFFICIAL_2026 = ["2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03",
                 "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02",
                 "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25"]
# files that explain what was removed from the US pack, so they may name it
MAY_NAME_US = {"README.md", os.path.join("trader-pelosi", "SKILL.md"), os.path.join("trader-news", "SKILL.md"),
               os.path.join("trader-risk", "SKILL.md"), os.path.join("trader-pelosi", "ptr.py")}
US_TERMS = ("NYSE", "New York", "Alpaca", "Eastern Time", "America/New_York", "nasdaqtrader", "EDGAR",
            "Thanksgiving", "1:00 p.m.", "PDT")
# the NYSE's 9:30 open: a range or a clock that starts there ("9:15 to 9:30 AM" is the NSE range ending)
NYSE_OPEN = re.compile(r"9:30\s*(?:AM\s*)?(?:to|-|until)\s*9:45|opens? at 9:30|9:30\s*(?:AM\s*)?open|4:00\s*PM|16:00", re.I)


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) if m else ""), text


def every_text_file():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".venv", "venv")]
        for f in files:
            if f.endswith((".md", ".py", ".json", ".csv", ".txt", ".xml")) or f == "LICENSE":
                yield os.path.join(base, f)


class Repo(unittest.TestCase):
    def test_eleven_skill_folders_at_the_root(self):
        found = sorted(d for d in os.listdir(ROOT)
                       if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))

    def test_root_matches_the_reel(self):
        # .idea, .venv and trader/ (your own plans, orders and token) are local, not part of the pack
        root = sorted(d for d in os.listdir(ROOT) if not d.startswith((".git", ".idea", ".venv")) and d != "trader")
        self.assertEqual(root, sorted([".claude-plugin", "LICENSE", "README.md", "tests"] + SKILLS))

    def test_frontmatter_name_and_description(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("name: %s\n" % s, fm + "\n", s)
            self.assertRegex(fm, r"description: ", s)
            self.assertGreater(len(fm), 200, "%s description is too thin" % s)
            self.assertGreater(len(text.splitlines()), 60, s)

    def test_order_path_skills_carry_the_safety_rule(self):
        for s in ORDER_PATH:
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            low = " ".join(text.lower().split())
            self.assertIn("not financial advice", low, s)
            self.assertIn("yes", low, s)

    def test_plugin_manifest_points_at_the_root_skills(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as fh:
            plugin = json.load(fh)
        self.assertEqual(plugin["name"], "day-trader-skill")
        self.assertEqual(plugin["skills"], "./")
        self.assertEqual(plugin["description"], ABOUT)
        self.assertIn("NSE", plugin["description"])
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")) as fh:
            market = json.load(fh)
        self.assertEqual(market["name"], "day-trader-skill")
        self.assertEqual(market["plugins"][0]["name"], "day-trader-skill")

    def test_readme_carries_the_reel_copy(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = " ".join(fh.read().split())
        self.assertTrue(readme.startswith("# The Day Trader skill"))
        for line in (ABOUT_README,
                     "https://github.com/Jakeschincariol/day-trader-skill install skill",
                     "/plugin marketplace add Jakeschincariol/day-trader-skill",
                     "/plugin install day-trader-skill@day-trader-skill",
                     "## Fine print"):
            self.assertIn(line, readme)
        for s in SKILLS:
            self.assertIn("/" + s, readme, s)
        for word in ("NSE", "IST", "rupee"):
            self.assertIn(word, readme, word)

    def test_no_em_dashes_and_no_personal_email(self):
        for path in every_text_file():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            rel = os.path.relpath(path, ROOT)
            if not rel.startswith(os.path.join("tests", "fixtures")):  # fixtures are real data, kept as published
                self.assertNotIn("\u2014", text, rel)
            self.assertNotIn("jakeschincariol" + "@" + "gmail.com", text, rel)  # built, so this file passes too

    def test_no_v1_only_skill_is_left_behind(self):
        for path in every_text_file():
            rel = os.path.relpath(path, ROOT)
            if rel.startswith("tests" + os.sep) or rel == "README.md":
                continue  # the README points v1 users to the v1 commit
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for old in V1_ONLY:
                self.assertNotIn(old, text, rel)

    def test_every_tool_runs_on_python_3_8(self):
        # no 3.9+ only modules or syntax in the tools (the README promises 3.8)
        for s in SKILLS:
            folder = os.path.join(ROOT, s)
            for f in os.listdir(folder):
                if not f.endswith(".py"):
                    continue
                with open(os.path.join(folder, f), encoding="utf-8") as fh:
                    src = fh.read()
                for bad in ("import zoneinfo", "from zoneinfo", ".removeprefix(", ".removesuffix(",
                            "match ", "list[", "dict[", "tuple["):
                    if bad == "match ":
                        self.assertNotRegex(src, r"(?m)^\s+match \w+.*:$", "%s/%s" % (s, f))
                    elif bad.endswith("["):
                        self.assertNotRegex(src, r"(?<![\w.])%s" % re.escape(bad), "%s/%s" % (s, f))
                    else:
                        self.assertNotIn(bad, src, "%s/%s" % (s, f))

    def test_day_trading_limits_ship_in_the_example_config(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        for k in ("max_loss_per_trade_inr", "max_daily_loss_inr", "max_trades_per_day",
                  "min_reward_risk", "require_stop", "no_entries_after", "flat_by"):
            self.assertIn(k, cfg, k)
        self.assertIs(cfg["require_stop"], True)
        self.assertIs(cfg.get("allow_short", False), False)
        self.assertIs(cfg.get("allow_margin", False), False)

    def test_example_config_is_rupees_and_the_nse_session(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        for k in ("max_loss_per_trade_inr", "max_daily_loss_inr", "max_order_inr", "min_order_inr"):
            self.assertIn(k, cfg, k)
        for k in cfg:
            self.assertNotIn("usd", k.lower(), k)
        self.assertEqual((cfg["no_entries_before"], cfg["no_entries_after"], cfg["flat_by"]),
                         ("09:45", "15:30", "15:20"))     # inside 09:15-15:30 IST, flat before the closing auction
        self.assertLess(cfg["max_loss_per_trade_inr"], cfg["max_daily_loss_inr"])
        self.assertLessEqual(cfg["max_order_inr"], cfg["budget"])
        self.assertEqual(cfg["broker"], "nse_broker")

    def test_the_five_holiday_lists_are_the_official_nse_2026_list(self):
        from _load import load
        for folder, name in HOLIDAY_TOOLS:
            mod = load(folder, name)
            self.assertEqual(sorted(mod.NSE_HOLIDAYS), OFFICIAL_2026, folder)
            if hasattr(mod, "CALENDAR_YEARS"):          # exit.py has no "list covers only" warning (reported)
                self.assertEqual(mod.CALENDAR_YEARS, (2026,), folder)
            self.assertEqual((mod.OPEN.hour, mod.OPEN.minute, mod.CLOSE.hour, mod.CLOSE.minute), (9, 15, 15, 30), folder)
            self.assertFalse(hasattr(mod, "NYSE_HOLIDAYS"), folder)
        first = load(*HOLIDAY_TOOLS[0]).NSE_HOLIDAYS
        for folder, name in HOLIDAY_TOOLS[1:]:
            self.assertEqual(load(folder, name).NSE_HOLIDAYS, first, folder)       # the five copies agree on names too

    def test_no_us_market_text_is_left_in_the_docs_configs_and_tools(self):
        for path in every_text_file():
            rel = os.path.relpath(path, ROOT)
            if rel.startswith(("tests" + os.sep, ".idea")) or rel in MAY_NAME_US:
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for term in US_TERMS:
                self.assertNotIn(term.lower(), text.lower(), "%s mentions %s" % (rel, term))
            if rel.endswith((".md", ".json")):
                self.assertIsNone(re.search(r"\$\d", text), "%s has a dollar amount" % rel)

    def test_docs_and_configs_say_rupees_not_dollars(self):
        for path in every_text_file():
            rel = os.path.relpath(path, ROOT)
            if rel.startswith(("tests" + os.sep, ".idea")) or not rel.endswith((".md", ".json")):
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            self.assertNotRegex(text, r"(?i)\bdollars?\b", rel)
            if rel != os.path.join("trader-analyst", "SKILL.md") and "metrics.example" not in rel:
                self.assertNotIn("USD", text, rel)       # the analyst may name USD: Yahoo reports some TTM figures in it

    def test_skill_docs_speak_nse_and_ist(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertRegex(text, r"NSE|IST|rupee|₹", s)
        for s in ("trader-scanner", "trader-entry", "trader-alerts", "trader-risk"):
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("9:15", text, s)                  # the NSE open
            self.assertIsNone(NYSE_OPEN.search(text), "%s still describes the NYSE session" % s)

    def test_paper_trading_is_the_default(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json")) as fh:
            cfg = json.load(fh)
        self.assertIs(cfg["paper"], True)
        self.assertIs(cfg.get("allow_options", False), False)


if __name__ == "__main__":
    unittest.main()
