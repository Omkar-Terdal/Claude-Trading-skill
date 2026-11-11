---
name: trader-alerts
description: >-
  Price Alerts. Rings when a price, a stop or the opening bell hits. Turns
  the trade plan into three alerts in one go (the stop, the target and "flat
  by close" at your flat-by time), plus above / below / stop / target levels,
  percent moves from the previous close, any time of day, and the NSE
  opening bell, each ringing once so you are not spammed. Knows NSE hours
  (9:15 AM to 3:30 PM IST) with the 2026 holiday list, works out Indian
  Standard Time itself, fetches free delayed quotes from Yahoo Finance
  (RELIANCE becomes RELIANCE.NS), with Google Finance as the fallback, one ticker at a time, and writes
  trader/prices.json for sizing and the risk check. Use when the user says
  "alert me when", "tell me if RELIANCE hits", "watch my stop", "watch my
  trade", "remind me to close by 3:20", "is the market open", "when does the
  market open", "get me a price", or right after /trader-exit writes a plan.
argument-hint: "[ticker] [price]"
---

# trader-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill. With a bracket order the stop-loss and the
take-profit sit at the broker and do the work; the alerts tell the user what
happened and when it is time to be flat.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat by close from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol INFY --stop 1485.02
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol INFY --target 1530.98
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:20 --label "flat by close" --symbol INFY
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol ITC --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote INFY --write             # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan tickers
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset infy-plan-stop
```

Tickers are NSE symbols without a suffix (INFY, RELIANCE). `.NS` is added for
Yahoo; give `.BO` yourself for a BSE-only stock. Use the symbol exactly as
Yahoo lists it. `--now "2026-11-09 15:20"` pins the
clock (IST); `--offline DIR` reads saved quote files instead of the network.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes, merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the tickers held, for `quote --holdings` |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the IST clock passed `at` ("15:20") on a trading day | once per trading day |
| `opening_bell` | the NSE is open | once per trading day |

A rule with `"date"` is watched on that IST date only; `--from-plan` rules
carry the plan's date, so yesterday's stop never rings today. The NSE has no
early-close days, so a `time` rule always rings at its `at` time.

## Quotes

- `yahoo` (default): `query2.finance.yahoo.com/v8/finance/chart/<TICKER>.NS`, an
  unofficial free JSON endpoint, one request per ticker, half a second apart.
  On a 429 it waits and tries once more; on a 429 or a 5xx after that it
  tries `query1`. It identifies itself as `day-trader-skill/2.0`.
- **Google Finance fallback.** When Yahoo has no quote for a ticker, the tool
  tries Google Finance for that ticker and marks the quote `"source": "google"`
  (in `detail` in `trader/prices.json`, and in brackets on the `quote` line).
  Google has no data feed, so this reads the HTML of
  `google.com/finance/quote/RELIANCE:NSE` (`.BO` tickers use `:BOM`). It gets the
  current price, open, high, low, the previous close and the quote time, but no
  bars, so the scanner, entry, exit and time machine still need Yahoo. An
  unknown ticker returns a normal page with no price, and the tool treats that
  as an error, never a guess. Because it scrapes a page layout, Google can
  break it without notice: the failure shows as "Google Finance has no quote".
  Force it with `quote RELIANCE --source google`.
- `stooq` is still selectable with `--source stooq`, but it has not been
  tested against NSE symbols here.
- Free quotes are unofficial, delayed and can lag. They are fine for alerts
  and for checking a size, not for timing an order. The broker's price is the
  one that fills. Each price in `trader/prices.json` keeps its quote time, and
  trader-risk only uses one under 15 minutes old.
- If both are down, read the price from the broker connector's quote tool and
  write it into `trader/prices.json` as `{"as_of": "...Z", "prices": {"INFY": 1508.00}}`.

## The NSE clock

Regular session 9:15 AM to 3:30 PM IST (UTC+5:30, no daylight saving ever),
closed on weekends and on the NSE trading holidays hard-coded in `alerts.py`
(`NSE_HOLIDAYS`, the 2026 equity calendar: Republic Day, Holi, Good Friday,
Ganesh Chaturthi, Gandhi Jayanti, Dussehra, Diwali Balipratipada, Guru Nanak
Jayanti, Christmas and the others in that list). Check it against nseindia.com
before relying on a date: exchange holidays are sometimes moved. For any
year the list does not cover, the clock says the holiday list is unknown.
Muhurat trading on Diwali evening is not modelled.

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm
   the three alerts back to the user: the stop, the target and the flat-by time.
2. Run `check` while the trade is open: every few minutes from a Claude
   scheduled task or `/loop`. Each run fetches only the tickers in today's
   rules and rings each alert once. A check reads the latest delayed quote;
   it is not a live feed, and a price can pass a level between two checks.
3. Tell the user what rang in one line each. A stop or target alert means the
   bracket order has probably filled: check the position at the broker and
   record the fill with trader-broker. "Flat by close" means close anything
   still open: trader-broker `flatten`, then trader-risk, then the user's yes.
   Never act for them.

## Example: INFY on Monday 2026-11-09

Entry 1508.00 at 10:00 AM, stop 1485.02, target 1530.98, flat by 15:20.
`add --from-plan` printed (real output):

```
Added infy-plan-stop: {"id": "infy-plan-stop", "symbol": "INFY", "type": "stop", "price": 1485.02, ...}
Added infy-plan-target: {"id": "infy-plan-target", "symbol": "INFY", "type": "target", "price": 1530.98, ...}
Added infy-flat-by: {"id": "infy-flat-by", "symbol": "INFY", "type": "time", "at": "15:20", "label": "flat by close", ...}
```

`clock` on a trading morning, on the Diwali holiday and on a Sunday (real output):

```
IST 2026-11-09 10:20 (Monday): NSE OPEN, regular session.
Today's session: 09:15 to 15:30.
IST 2026-11-10 10:20 (Tuesday): NSE closed, NSE holiday: Diwali (Balipratipada).
Next open: 2026-11-11 09:15 IST.
IST 2026-11-08 12:00 (Sunday): NSE closed, weekend.
Next open: 2026-11-09 09:15 IST.
```

A `check` at 3:20 PM:

```
NSE OPEN (2026-11-09 15:20 IST, regular session).
ALERT Flat by close: it is 15:20 IST (set for 15:20), the market closes at 15:30. If INFY is still open, close it: trader-broker flatten --symbol INFY builds the sell ticket, then trader-risk and your yes.
```

It rings once. The next morning the plan's three alerts are no longer
watched; with `add --opening-bell`, the bell rings at 9:15.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Free quotes are delayed and unofficial.
- Confirm every new rule back to the user: ticker, type, level, day.

## What it never does

It never places, cancels or changes an order, never claims a quote is
live, and never predicts where a price is going.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, F&O, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
