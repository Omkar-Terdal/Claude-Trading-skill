---
name: trader-budget
description: >-
  Budget Manager. Sizes every position to your budget and your max loss per
  trade, in rupees. Reads the trade plan from trader-exit (entry, stop,
  target), finds the whole shares that fit inside four limits (max loss per
  trade, position cap, max order, cash after your buffer), says which limit
  decided it, and writes ONE bracket order (entry, stop-loss, take-profit) to
  trader/orders.json in the shape the ticket sends to place_nse_order. A stop
  too far away for your max loss is a plain refusal, not a smaller order.
  Nothing is placed. Use when the user says "how many shares", "size this
  trade", "how much should I buy", "I have 5 lakh", "I only want to risk
  5,000 a trade", "set my rules", "keep 10% in cash", or after /trader-exit
  writes a plan.
argument-hint: "[budget in rupees]"
---

# trader-budget

The Budget Manager turns a plan ("buy RELIANCE at 1311.90, stop 1300.80") into
an exact order ("buy 285 shares"), inside rules the user sets once. The stop
decides the size: the further away the stop, the fewer shares. It writes a
proposal only. trader-risk checks it and trader-broker asks the user yes or
no. Day trading loses most people money. Sizing to a max loss keeps each
loss small; it does not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                        # trader/plan.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit --write        # a limit order at the plan's entry
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 1311.00 --json # a limit order at 1311.00
python3 "${CLAUDE_SKILL_DIR}/size.py" --weights RELIANCE=30,INFY=70 --config split.json   # weights mode, below
```

`--now "2026-11-06 09:55"` pins the clock (IST). A plan for another day is
refused: make a new one with trader-exit. All money is in rupees; the display
uses plain thousands separators (₹500,000), not lakh/crore grouping.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader/plan.json` | the trade: entry, stop, target, setup, flat-by time (trader-exit writes it) |
| `trader/prices.json` | optional: a fresh price above the plan's entry means a bigger risk (trader-alerts writes it) |
| `trader/portfolio.json` | optional: its cash caps the size (trader-broker keeps it) |
| `trader/orders.json` | the one proposed bracket order it writes with `--write` |

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: how much money this is for, in rupees. Only money they can afford to lose.
- **cash_buffer_pct**: how much always stays in cash (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills at the stop (example 5,000).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest position (75% of the
  budget), the biggest order (₹400,000 in the example) and the smallest order (₹500).
- The day limits trader-risk enforces: **max_daily_loss_inr** (15,000), **max_trades_per_day** (3),
  **min_reward_risk** (1.0), **require_stop** (true), **no_entries_before** / **no_entries_after**
  ("09:45" / "15:30") and **flat_by** ("15:20"), all IST.
- **blocked**: tickers they never want to trade.
- **broker**: `"nse_broker"` (the default label on tickets).
- Keep **paper** true until they have watched it work. Note it only labels the ticket: a live
  broker account has no paper mode unless the broker offers one.

The example numbers (₹500,000 budget, ₹5,000 max loss) are a template, not advice.
Scale them to money the user can afford to lose.

## Step 2: size the trade

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" --write
```

Whole shares only (a bracket order needs whole shares; NSE equity trades in
lots of one share). The size is the smallest of four limits:

| limit | shares |
| --- | --- |
| max loss | `max_loss_per_trade_inr` / risk a share (entry minus stop) |
| position cap | budget x `max_position_pct` / entry |
| max order | `max_order_inr` / entry |
| cash | (budget minus the cash buffer) / entry, never more than the cash in `trader/portfolio.json` |

The entry it sizes at: the limit price with `--limit`. For a market order, the
plan's entry, or the price in `trader/prices.json` if it is fresh (under 15
minutes old) and higher. A price that ran up since the plan means a bigger
loss at the same stop, so fewer shares. A lower price never shrinks the risk.

It refuses, and writes no order, when: the size is zero shares, the order is
under the minimum, reward:risk is under `min_reward_risk`, the stop is not
below the entry, the target is not above it (the move already happened), the
plan is for another day, the ticker is blocked, or the plan is a short. Say
the reason to the user in one line.

## Step 3: show the user

Show the four limits, which one decided the size, the shares, the rupees
and the max loss at the stop. Say plainly that a stop is an order, not a
promise: in a fast market it can fill below the stop. Then hand over to
`/trader-risk`.

## Example: RELIANCE on Friday 2026-11-06

Real data. The opening-range breakout fired at 9:50 AM: entry 1311.90, stop
1300.80 (the opening-range midpoint), target 1323.00 (1R). Risk 11.10 a share.
The example config: ₹500,000 budget, 10% cash buffer, ₹5,000 max loss. Real
output of `size.py --write`:

```
Limits (whole shares; the smallest one wins):
  max loss      ₹5,000 max loss / 11.10 risk a share                       450 shares
  position cap  75% of ₹500,000 = ₹375,000 / 1311.90                       285 shares  <- binds
  max order     ₹400,000 / 1311.90                                         304 shares
  cash          budget ₹500,000 minus the ₹50,000 cash buffer = ₹450,000 / 1311.90 343 shares

Size: 285 shares of RELIANCE = ₹373,891.50 at 1311.90. Max loss at the stop: 285 x 11.10 = ₹3,163.50.
```

Here the position cap decides, not the max loss: a tight stop on a ₹1,300
stock means the risk limit allows 450 shares but the cap stops it at 285. The
order it writes:

```json
{"symbol": "RELIANCE", "side": "buy", "qty": "285", "type": "market", "time_in_force": "day",
 "order_class": "bracket", "take_profit_limit_price": "1323.00", "stop_loss_stop_price": "1300.80",
 "why": "opening-range breakout on RELIANCE: 285 shares, bound by your position cap (285 x 11.10 = ₹3,163.50 at the stop)",
 "plan": {"entry": 1311.90, "stop": 1300.80, "target": 1323.00, "risk_per_share": 11.1, "max_loss": 3163.5,
          "setup": "orb", "entry_time": "9:50 AM", "flat_by": "15:20"}}
```

`why` and `plan` are notes for trader-risk and trader-broker. They are never
sent to the broker. A stop at 1250.00 instead would risk 61.90 a share and
size to 80 shares at that risk (₹5,000 / 61.90). If the latest quote has run
up to 1318.00, the same plan is sized at 1318.00: risk 17.20 a share and
reward:risk 0.29, so it is refused because the move it was waiting for has
mostly happened.

## Weights mode (kept from v1)

`--weights RELIANCE=30,INFY=70` or `--targets FILE` (`targets.example.json`
here, illustrative weights only) splits a budget across a list by weight: the
cash buffer comes off the top, weights are renormalised to 100, any stock over
`max_position_pct` is pinned at the cap and its excess shared out, slices under
the minimum order are dropped, and amounts round to whole rupees so nothing is
left idle by accident. With `"max_position_pct": 25` and `"fractional": true`,
the example file on a ₹500,000 budget gives RELIANCE ₹125,000 (capped),
HDFCBANK ₹116,071, INFY ₹92,857, ITC ₹69,643 and SBIN ₹46,429. With
`"fractional": false` (the example config) it buys whole shares and needs
prices (`--prices trader/prices.json`): 95 RELIANCE, 151 HDFCBANK, 79 INFY, 250
ITC, 46 SBIN at the 2026-11-06 prices, ₹263.25 left idle. Notional (rupee
amount) orders are a feature of some brokers only; check that yours accepts
them before using `"fractional": true`. These are plain buys with no stop:
trader-risk blocks them while `require_stop` is true and counts each one against
`max_trades_per_day`. Use a separate config (`--config`) for a long-term split.

## Rules

- Proposals only. This skill never calls a broker tool.
- Never size money the user has not said is for this. Ask for the budget and the max loss.
- Every number comes from the config, the plan or the prices. Nothing is guessed.
- No projections: never say what a trade will make. A target is a plan, not a forecast.
- Free Yahoo prices are unofficial and can lag. The broker's price is the one that fills.
- Brokerage, STT, exchange charges, GST and stamp duty are not modelled. They
  take a bigger share of a small win in a day trade than most people expect.

## What it never does

It never places, approves or schedules an order, never raises a limit on its
own, never shrinks a stop to fit more shares, and never sizes options, F&O,
margin or short positions.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, F&O, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker credentials live with the broker's own login or the Claude Desktop config, never in the chat.
