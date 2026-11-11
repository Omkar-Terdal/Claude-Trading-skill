---
name: trader-timemachine
description: >-
  Time Machine. Replays every possible outcome of a trade before you risk a
  rupee: 10,000 simulated sessions plus every past time the setup fired.
  Takes the plan from trader-exit (entry, stop, target), rebuilds the rest of
  the day 10,000 times from the stock's own 5-minute bars over the last 60
  days, and re-runs the same rule on every past session. Prints the odds of
  target first, stop first, or neither by your flat-by time, where the price
  sits then, the average R, the history, and one honest verdict, then hands
  off to /trader-budget. Use when the user says "run the time machine",
  "replay this trade", "what are the odds", "simulate this trade", "monte
  carlo", "backtest this setup", "how often does this hit the target", "is
  this a coin flip", or after /trader-exit writes a plan, before
  /trader-budget.
argument-hint: "[path to plan.json, or TICKER]"
---

# trader-timemachine

The Time Machine sits between the plan and the money. trader-exit has set
the entry, the stop and the target. Before anything is sized or sent, this
skill asks one question: if today plays out like the stock's own last 60
days, how often does this plan hit the target first, the stop first, or
neither? It answers with 10,000 replays and with the real record of the same
rule on every past session. It is a simulation built from the stock's own
recent bars, not a prediction. Day trading loses most people money, and a
good-looking replay does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run                    # trader/plan.json, 10,000 replays, seed 19
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write            # also save trader/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:20      # end each replay at your flat-by time
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol RELIANCE --date 2026-11-06 \
    --entry 1311.90 --stop 1300.80 --target 1323.00 --entry-time "9:50 AM"
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart --from           # ASCII fan chart of the saved run
```

Other flags: `--replays N`, `--seed N` (default 19), `--window N` (default 2
bars), `--offline FILE` (a saved Yahoo chart JSON, `.json` or `.json.gz`),
`--now "2026-11-06 09:55"` (IST) to pin the clock.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the plan from trader-exit: symbol, date, entry, entry_time / entry_bar, stop, stop_rule, target, target_r, opening range |
| Yahoo chart API | one request: 60 days of 5-minute bars (`range=60d&interval=5m`), free and unofficial, can lag |
| `trader/timemachine.json` | with `--write`: `{plan, mc, history}` plus the verdict. `mc` holds the odds, percentiles, the fan-chart bands, 160 sample paths and a 24-bin histogram of the close |

## How it works

1. **The pool.** It keeps only FULL sessions before the plan's date: all 75
   five-minute bars, 9:15 to 15:30 IST. Gaps and today are left out. Every
   bar becomes four ratios to the bar before it: open, high, low and close
   over the previous close. **Yahoo often leaves NSE's 15:20 and 15:25 bars
   empty.** A session missing up to three bars still counts, with each gap
   filled by a flat bar at the previous close and zero volume, and the run
   says how many bars it filled. In the RELIANCE run below that was 108 bars
   across 59 sessions: the last minutes of the day are therefore calmer in
   the replay than they really were, which matters for a flat-by of 15:20.
2. **One replay.** Start at the entry price. For each bar left in the day,
   pick a past bar from the same time of day (plus or minus 2 bars) at
   random, and scale it to the current price. Bar by bar, it checks the stop
   first, then the target. A bar that touches both counts as the stop.
3. **10,000 replays** give four odds. Target first. Stop first. Neither,
   which means the trade was still open at `--until` (default 15:30, the
   close). Green, which means target first or above the entry at `--until`.
   R is +1 at a 1R target, -1 at the stop, and otherwise the distance from
   the entry at `--until` divided by the risk. A gap through the stop still
   counts as -1R here. A real fill can be worse.
4. **The history.** The plan's own rule (the same opening range, stop rule
   and target R) runs again on every one of those past sessions. That is
   what really happened, not a simulation.
5. **The verdict** is one line from the numbers. It looks only at the
   replays that hit one side, and asks how often the target came first. A
   bigger target needs to win less often to break even: 1 in (1 + R). That
   is 50 % at 1R, 33 % at 2R, 25 % at 3R. Less than 5 points from break-even:
   "Close to a coin flip" at 1R, "Close to break-even" at any other R. 5 to
   15 points: "Leans toward the target" or "Leans toward the stop". 15 or
   more: "Strongly toward" one of them.
6. **Below the verdict**, when it applies: a line when more than 40 % of the
   replays hit neither (the price at `--until` decides those), and the
   average R when its sign goes against the lean. Those can only point the
   other way because of the replays that hit neither.

**The seed.** The draws come from Python's `random.Random(seed)`, seed 19 by
default. Same plan, same data, same seed: the same numbers, on any machine.
Say the seed with every result so the user can repeat it. The data is Yahoo's
latest 60 days, so a run on another day uses different sessions.

**5-minute bars only.** Yahoo keeps 60 days of 5-min bars but only about 7
days of 1-min bars, too few sessions for a fair pool. It refuses, in one
plain line, when there are fewer than 20 full sessions, when the entry bar is
not in the data (wrong date, not closed yet, a future day), or when the plan
does not make sense (a stop above a buy's entry, an entry off the 5-minute
grid). A live in-progress bar is never used.

## Steps

1. Make sure `trader/plan.json` is today's plan from `/trader-exit`. If there
   is none, run that first, or pass the plan as flags.
2. Run `timemachine.py run --write --until 15:20`, using the plan's flat-by
   time, so "neither" means still open at the time you would get out.
3. Tell the user, in this order: the four odds, the median and the 5 to 95
   range of the price at the end, the average R, the history line, then the
   verdict line and any line under it exactly as printed. Say the seed.
4. If the verdict is a coin flip or close to break-even, say so plainly. Do
   not dress it up. If the history and the replays disagree, say that too.
5. Say once that this is built from the last 60 days and is not a prediction.
   Results, news, circuit limits and halts can do things those 60 days never
   did. Check `/trader-news` for the stock before going on.
6. If the user still wants the trade, hand off to `/trader-budget` to size it.

## Example: RELIANCE on Friday 2026-11-06

The 15-minute opening range is 1295.60 to 1306.00. The 9:45 bar closes above
it: entry 1311.90 at 9:50 AM (bar 6), stop 1300.80 (the range midpoint),
target 1323.00 (1R, risk 11.10 a share). Real output of
`timemachine.py run --write --until 15:20`:

```
10,000 replays of the rest of the session, 9:50 AM to 3:20 PM (66 five-minute bars), seed 19.
Each bar is drawn whole from RELIANCE.NS's own 5-min bars at the same time of day (+-2 bars) over its last 59 full sessions (2026-08-12 to 2026-11-05).

  target first   31.1 %
  stop first     38.0 %
  neither        30.9 %   still open at 3:20 PM
  green          46.6 %   target first, or above the entry at 3:20 PM

  Price at 3:20 PM: median 1310.82, 90 % of replays between 1290.57 and 1331.23.
  Average R: -0.068 (R = the 11.10 risk: +1R at the target, -1R at the stop).

History: the same rule (15-min opening range, stop at the opening-range midpoint, 1R target) on each of those 59 real sessions fired 31 times: 14 target, 11 stop, 6 neither. Average R +0.054.

Close to a coin flip: target first 31.1 % vs stop first 38.0 %.

Note: Yahoo left 108 five-minute bar(s) empty across 59 sessions (NSE's 15:20 and 15:25 bars are often null); each gap is filled with a flat bar at the previous close.
```

At 1R the target has to come first in half of the replays that hit one
side. It did in 45 % (31.1 of 69.1). The replays leaned slightly toward the
stop, and the real history was slightly better than even. The two disagree,
which is itself the honest answer: this setup is close to a coin flip, and
that is what the user should hear before `/trader-budget` sizes 285 shares.

## Rules

- Every number comes from the run. Never round a coin flip up into an edge.
- The verdict line and the lines under it are printed by the tool. Repeat
  them, do not rewrite them. Never judge a 2R or 3R plan by target first vs
  stop first alone: the verdict already weighs the target's R.
- Simulated odds are not predictions and not promises. Say it once, plainly.
- Free Yahoo data is unofficial and can lag. Bars are 5-minute bars, checked
  bar by bar, never tick by tick and never real time.
- Always say the seed, so any run can be repeated.
- One plan per run. A changed entry, stop, target or date is a new run.

## What it never does

It never places an order, never sizes a position, never changes the plan,
and never calls a result a sure thing. It never prints a P&L or a profit.
It never uses a bar that has not closed yet, and never mixes today's bars
into the pool.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, F&O, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker credentials live with the broker's own login or the Claude Desktop config, never in the chat.
