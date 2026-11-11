---
name: trader-scanner
description: >-
  Chart Scanner. Scans thousands of NSE charts for setups: opening-range
  breakouts, flags, oversold bounces. Builds the list of NSE-listed equity
  (EQ series) stocks from NSE's EQUITY_L.csv, runs a pre-market scan of
  daily closes (20-day closing-high breakout, bull flag, reclaimed the 20-day
  average, oversold, oversold bounce) and an intraday opening-range breakout
  scan on 5-minute bars from the 9:15 AM IST open, then writes
  trader/watchlist.json for trader-news and trader-entry. Free Yahoo data,
  bar by bar. Use when the user says "scan the market", "find me setups",
  "what is breaking out", "opening range breakouts", "scan for bull flags",
  "oversold stocks", "pre-market scan", "build my watchlist", "what should I
  look at today", or at the start of a trading day before /trader-news.
argument-hint: "[universe | daily | orb] [--at HH:MM]"
---

# trader-scanner

The Chart Scanner is the first step of a day trade. It reads thousands of
free charts and lists the ones that match a rule. That is all it does. A scan
is a list of charts that match a rule, not a list of stocks to buy, and a
setup on a chart is not a prediction. Most people who day trade lose money;
the scanner does not change that. It only saves you from flipping through
2,400 charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                       # -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                          # before the open -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,INFY,TCS   # or --watchlist FILE, --limit 200
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                            # after 9:35 AM IST -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15                 # cut the clock at 10:15 AM IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
```

Flags: `--min-price 5` and `--min-volume` (daily 1,000,000, orb 0; the price
floor is in rupees), `--or-minutes 15`, `--max-confirm 200` (orb stage 2),
`--now "2026-11-06 10:15"` (IST) to pin the clock, `--offline PATH` to read
saved Yahoo files, `--no-write`, `--show N`, `--json`. Exit code 0 when the
scan found something, 1 when it found nothing or could not run (the reason is
one plain line). Symbols are stored without a suffix; `.NS` is added for Yahoo.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line, read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol` |
| `trader/config.json` | read by `orb`: `no_entries_after` (default 15:30) |

## The universe

`universe` downloads `EQUITY_L.csv` from `archives.nseindia.com/content/equities/`
and keeps every row in the `EQ` series (normal rolling-settlement equity). It
leaves out the other series (BE, SM, ST and so on, which include trade-for-trade
and SME stocks), so illiquid and surveillance-listed names are not scanned.
On 2026-11-07 the file had 2,630 rows and 2,390 were kept. Scan a different
set with `--symbols` or `--watchlist`; ETFs are not in this list. BSE-only
stocks are not covered.

## The daily scan (before the open)

Yahoo's spark endpoint returns closes for 20 symbols a request, so 2,390
symbols take 120 requests, with a 0.3 s pause between them. Liquidity comes
from the spark meta: last price at least `--min-price` and last session's
volume at least `--min-volume`. Run it before the open or after the close:
during the session the volume is the day's so far, and today's daily bar is
left out until the close.

The setups are computed from daily closes only (spark has no highs or lows):

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

A closing-high breakout fires more often than an intraday-high one. Hits are
listed most traded first (price times volume). Indian stocks have daily price
bands (circuit limits): a close at the band is a different event from a free
move, and the scan does not detect it.

## The opening-range breakout scan (during the session)

The opening range is the first `--or-minutes` (15) of the session: the 9:15,
9:20 and 9:25 five-minute bars. A hit is the first completed 5-minute bar to
CLOSE above the range high, at that bar's close time. The first scan is
possible at 9:35 AM.

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a
   request. A chart goes on only if a completed bar after the range closed at
   or above the highest close of the range. That is a necessary condition
   (the range high is at least every range close), so no breakout is lost.
2. Stage 2, each candidate: the v8 chart with real highs and lows, one
   request each. Range high = the highest high of the range bars, range low =
   the lowest low. On a strong morning most charts are candidates, so stage 2
   checks at most `--max-confirm` (200) of them, the most traded first (price
   times volume so far), and prints PARTIAL with the count it skipped.
   `--max-confirm 0` checks them all.

Timing, measured: a full-universe `orb` at 10:15 AM on Fri 2026-11-06 took
273 seconds (120 spark requests, then 200 v8 charts): 2,312 of 2,390 charts
came back, 1,429 were usable, 857 were candidates, the 200 most traded were
checked and 130 of those had broken out. 657 candidates were not checked, and
the scan said so (PARTIAL). For a fast answer, scan the daily watchlist or a
list of names you trade (`--watchlist`, `--symbols`): 20 large caps took a few
seconds.

A chart is usable only when every 5-minute bar from 9:15 to the cutoff is
there; a missing bar means a halt or a thin stock. Null closes are dropped, a
bar off the 5-minute grid (the live tick, like 10:58:07) is dropped, and a bar
counts only once it is complete (start + 5 minutes). Before 9:35 AM, on a
weekend or an NSE holiday, after the 15:30 close, or after `no_entries_after`
(an expired setup) it prints one plain line and writes nothing. The NSE has no
early closes.

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and can lag. Bars are
  5-minute bars, bar by bar, never tick by tick. query2 first, query1 on a 429
  or 5xx, one retry after a 5-second wait on a 429.
- 78 of 2,390 charts (3 %) failed or had no data in the full run. Say so:
  "Fetched 2,312 of 2,390 charts (78 failed or had no data)". Never round a
  partial scan up to "the whole market".
- The NSE holiday list is the 2026 calendar hard-coded in the tools. Check
  nseindia.com for the current year's list.

## Steps

1. Once a week or so: `scan.py universe`.
2. Before the open: `scan.py daily`. Tell the user the counts (fetched,
   liquid, scanned, hits) and the top few by setup.
3. After 9:35 AM IST (10:00 to 10:15 is a common time to look): `scan.py orb`.
   Tell the user how many usable charts broke their range and when, and that a
   later hit is closer to the price now.
4. The user picks one chart. Hand it on: `/trader-news TICKER` (what is moving
   it), then `/trader-entry TICKER` (the entry bar by bar). Never pick for them.

## Example: Friday 2026-11-06

Real data. A scan of 20 large caps at 11:00 AM:

```
Opening-range breakout scan, Fri 2026-11-06 11:00 AM IST: 15-minute range (9:15 to 9:30 AM), 5-minute bars, Yahoo.
Fetched 19 of 20 charts (1 failed or had no data); 19 usable; 17 candidates, 17 confirmed with real highs and lows.
12 of 19 charts closed above their opening-range high by 11:00 AM:
   9:35 AM  HDFCBANK    ₹767.65  range 754.65 to 767.40  +0.03% over
   9:50 AM  RELIANCE  ₹1,311.90  range 1,295.60 to 1,306.00  +0.45% over
   ...
```

RELIANCE's range is the 9:15 to 9:25 bars, high 1306.00, low 1295.60; the 9:45
bar closed at 1311.90, 0.45 % over the high, at 9:50 AM. The user picks
RELIANCE and Claude hands it to `/trader-news RELIANCE`, then `/trader-entry
RELIANCE`, which reported the same trigger.

The pre-market `daily` scan over the first 400 symbols of the universe (closes
through Thu 2026-11-05) fetched 380 charts, 63 passed the liquidity filter and
33 matched a setup: 21 20-day closing-high breakouts, 2 bull flags, 8
reclaims of the 20-day average, 2 oversold and 1 oversold bounce. The first
400 are alphabetical, so this is a sample, not the market.

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, and never promises that a setup works.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, F&O, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker credentials live with the broker's own login or the Claude Desktop config, never in the chat.
