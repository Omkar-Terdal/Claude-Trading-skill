---
name: trader-broker
description: >-
  Order Executor. Sends approved orders to your Indian broker, paper trading
  by default, never without your yes. Shows each order as a plain ticket (a
  bracket shows its stop-loss, take-profit and the max loss at the stop)
  plus the exact JSON for the place_nse_order tool, records your yes word
  for word, then Claude's own Allow once / Deny card asks again. Builds the
  sell ticket that closes a day trade by your flat-by time, records fills,
  and logs each trade's max loss so trader-risk can hold the daily limit.
  Covers connecting a broker's MCP server in Claude Desktop (Zerodha Kite
  as the worked example, keys never in chat) and setting order tools to
  "Needs approval". Use when the user says "place the order", "buy it",
  "execute", "send it to my broker", "close my position", "flatten", "get
  me out", "connect Zerodha", "set up paper trading", or after /trader-risk
  passes.
argument-hint: "[order number]"
---

# trader-broker

The Order Executor is the only skill in this pack that can lead to a real
order, so it is the strictest. An order reaches the market only after three
separate gates, every time, for every order:

1. **trader-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat.
3. **The user clicked Allow once** on Claude's own permission card, which
   appears because the order tool is set to "Needs approval".

Two yeses from the user, one PASS from the rules. No yes, no order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id ID --filled-qty 217 --filled-price 1508.00
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol INFY --side sell --qty 217 --price 1530.98
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol INFY --qty 217
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` never talks to a broker. It renders tickets, checks that a reply
is a real yes for that order, builds the flatten ticket, and keeps the log in
`trader/state/broker.json`. `--now "2026-11-09 10:00"` pins the clock (IST).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `flatten` replaces it) |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every yes, no, placed, failed and filled, with each entry's max loss |
| `trader/portfolio.json` | cash and shares, updated by `fill` and by `result --filled-qty` |
| `trader/plan.json` | the flat-by time for `flatten` |

## Connecting a broker

This pack was written around one order tool, `place_nse_order`, whose
arguments are the fields in the ticket JSON (`symbol`, `side`, `qty`, `type`,
`time_in_force`, and for a bracket `take_profit_limit_price` and
`stop_loss_stop_price`). **No Indian broker's MCP server is guaranteed to
expose a tool with exactly that name or shape.** Before the first order, list
the connector's real tools in Claude, find the one that places an equity
order, and map the ticket's fields onto its parameters by hand. If it takes
different fields (product type, exchange, trigger price, validity), say what
you are changing and show the user the exact call before asking for the yes.
Treat that mapped tool as "the order tool" everywhere below.

Intraday day trades on NSE are normally placed as product type MIS
(margin intraday square-off) or as CNC for delivery. This pack never uses
margin by default (`allow_margin` is false), so confirm with the user which
product type their broker applies, and that the account has enough cash.
Whether a single bracket order (entry plus stop-loss plus target) is
supported depends on the broker and the connector: if it is not, the entry is
placed first and the stop-loss and target are placed as separate orders after
the fill. Say so plainly when it applies,
and do not claim a stop sits at the broker until you have seen it there.

### Example: Zerodha Kite MCP in Claude Desktop

Zerodha publishes a hosted MCP server for Kite at `https://mcp.kite.trade/mcp`
(check Zerodha's own documentation for its current URL, tool list and whether
order placement is enabled for your account). It runs through the `mcp-remote`
bridge, needs Node.js (for `npx`), and you log in to Kite in the browser window
it opens, not in the chat.

1. The user has a Zerodha (or other broker) account with API/MCP access.
2. In Claude Desktop: Settings > Developer > Edit Config. That opens
   `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`,
   Windows: `%APPDATA%\Claude\`). Add this block (also in
   `claude_desktop_config.example.json` here):

```json
{
  "mcpServers": {
    "nse-broker": {
      "command": "npx",
      "args": ["mcp-remote", "https://mcp.kite.trade/mcp"]
    }
  }
}
```

3. Restart Claude Desktop and complete the broker's login in the browser.
   **Passwords, PINs, TOTP codes and API keys never go in the chat.** Never ask
   for them, and if the user pastes one anyway, tell them to delete the message
   and rotate it at the broker.
4. **Set the tool permissions.** Customize > Connectors, open the broker, and
   for each tool pick Always allow, Needs approval or Blocked:
   - the equity order tool (the one you mapped to `place_nse_order`): **Needs
     approval**. Never Always allow.
   - every tool that modifies, cancels or converts anything (modify order,
     cancel order, convert position, GTT or alert changes): Needs approval.
   - anything for F&O, currency, commodities, mutual funds or bulk actions
     such as "cancel all" / "exit all": **Blocked** unless the user has a
     reason and says so.
   - read-only tools (profile, margins, holdings, positions, orders, quotes):
     Always allow is fine.
5. Check it: ask Claude for the account's margins or holdings (read only) and
   confirm with the user it is the account they meant. If the broker has no
   paper mode, `"paper": true` in `trader/config.json` only labels the ticket:
   **an order sent to a live broker account is real money.** Tell the user.

In Claude Code instead of Claude Desktop, keep the order tool on ask in
`.claude/settings.json`: `"permissions": {"ask": ["mcp__nse-broker__<order tool>"]}`,
and never put an order tool in `allow`.

For another broker (Upstox, Angel One, Dhan and others), use whatever
MCP connector that broker supports and follow the same steps: map the order
tool, set it to Needs approval, block the bulk and derivative tools.

### Groww: direct API through `groww.py`

Groww has no MCP connector here, so `groww.py` calls the Groww Trade API
(`https://api.groww.in/v1`) itself, standard library only. You need an active
Groww Trading API subscription. **Groww has no paper trading: a sent order is a
live order.**

Credentials stay in your shell environment, never in the chat or a file in
the repo: `GROWW_ACCESS_TOKEN` (the dashboard token, which lapses daily at
6:00 AM), or `GROWW_API_KEY` plus `GROWW_API_SECRET`, then
`python3 "${CLAUDE_SKILL_DIR}/groww.py" token` (approve the key on the Groww
Cloud API Keys page first; it is cached in `trader/state/groww_token.json`, mode 600).
`trader/` (your plans, orders and this token) is in this repo's `.gitignore`.

```bash
python3 "${CLAUDE_SKILL_DIR}/groww.py" ltp RELIANCE TCS        # live last price
python3 "${CLAUDE_SKILL_DIR}/groww.py" candles RELIANCE --days 5 --interval 5minute
python3 "${CLAUDE_SKILL_DIR}/groww.py" margin                   # cash and MIS balance
python3 "${CLAUDE_SKILL_DIR}/groww.py" place --index 1          # dry run: prints the request
python3 "${CLAUDE_SKILL_DIR}/groww.py" place --index 1 --send   # real order
python3 "${CLAUDE_SKILL_DIR}/groww.py" oco --symbol RELIANCE --qty 285 --side sell --target 1323 --stop 1300.80
python3 "${CLAUDE_SKILL_DIR}/groww.py" cancel GMK... [--oco] --send
```

What protects you:
- **`place` refuses** unless trader-risk passed this exact order today and
  `ticket.py approve` recorded your yes for it. It also refuses limit-less
  stop orders, fractional shares and rupee-amount orders (Groww needs whole shares).
- **Nothing is sent without both** `--send` and `"paper": false` in
  `trader/config.json`. Otherwise it prints the exact request and stops.
- **Doubles are refused by Groww:** the order reference id comes from the
  order's fingerprint, so sending the same order twice returns GA007.
- **Keep `groww.py ... --send` on "ask"** in `.claude/settings.json`
  (`"permissions": {"ask": ["Bash(python3 *groww.py* --send*)"]}`, never in `allow`).
  That prompt is the third gate, the same as the Allow once card.

**Static IP for orders.** Groww's blog (dated 2 September 2026) says SEBI
compliance requires every API order to come from a static IP you register
(a primary and an optional secondary) at `groww.in/trade-api/api-keys`, then
"Add static IP". Dynamic IPs are not accepted. A typical home connection has a
changing IP, so `place --send` and `oco --send` from a laptop at home will
likely be refused. The options the page lists are an ISP static-IP add-on or a
cloud server (a small VPS) you run the orders from. The page covers order
placement only: read calls such as margin, positions and orders worked from a
home connection in our test, so reading is fine for paper trading. Test with a
one-share order before relying on it.

Product type is MIS by default (set `"groww_product": "CNC"` in the config for
delivery). Groww's bracket order does not exist, so the flow is two steps:
`place` the entry, and once it fills, `oco` places the stop-loss and target as
one pair where one leg cancels the other (a stop-loss market, a limit target).
Check the OCO sits in your Groww app before you rely on it; the docs do not say
how MIS auto square-off interacts with an OCO, so still flatten by your
flat-by time. Groww's rate limits are 10 orders a second and 250 a minute,
10 live-data calls a second and 300 a minute. Quotes may be delayed or limited
by your plan, so compare the first few against your Groww screen.

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/trader-risk`.
2. Show ONE ticket and ask: "Place this order? Reply yes or no." One order
   per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", the reply was not an explicit yes for this
   order (for example "sure", "yes to all", or a yes that names a different
   stock or amount). Ask again. Never turn a reply into a yes yourself.
4. Only after "Recorded your yes", call the order tool with the arguments the
   ticket printed (as mapped above). Claude's card appears: Allow once or Deny.
5. Record what happened: `ticket.py result --index N --status placed
   --broker-id <id> --filled-qty Q --filled-price P` (the fill from the
   response or the broker's positions), or `--status denied`, or `--status failed`
   with `--note`. A failed order is not retried without a new check and a new yes.
   The log keeps the entry's max loss for trader-risk's daily limit.

## Steps: while it is open, and out by the close

- trader-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`). Where the broker holds the stop-loss and
  target, those orders do the stopping.
- When the stop-loss or the take-profit fills, record it:
  `ticket.py fill --symbol INFY --side sell --qty 217 --price 1485.02`.
- By `flat_by` (15:20 IST, ten minutes before the 15:30 close), anything still
  open gets closed:
  1. Ask the broker how many shares are held (read only).
  2. `ticket.py flatten --symbol INFY --qty 217` writes a market sell for those
     shares to `trader/orders.json`.
  3. `/trader-risk`. A sell that only closes what the user holds is never
     blocked by the daily limits or the entry window.
  4. `ticket.py show`, then the user's yes for that one order, as above.
  5. If the broker still holds open stop-loss or target orders on those shares
     it will usually reject a new sell until they are cancelled: list the open
     orders (read only), then cancel each one (Needs approval). Then place the
     ticket's sell, then `result`.
- MIS positions are auto-squared-off by many brokers shortly before the close,
  often with a fee. Being flat by 15:20 yourself avoids that surprise.

## Example: INFY on Monday 2026-11-09

This is real output from `ticket.py show` (ticket for the 217-share bracket
that trader-budget sized and trader-risk passed):

```
ORDER 1 of 1, PAPER account, NSE Broker
  BUY 217 INFY, market, good for today
  stop-loss 1485.02
  take-profit 1530.98
  max loss about ₹4,986.66 if the stop fills at 1485.02 (it can fill worse in a fast market)
  why: opening-range breakout on INFY: 217 shares, bound by your ₹5,000 max loss per trade (217 x 22.98 = ₹4,986.66 at the stop)
  READY: trader-risk PASS, waiting for your yes
  Claude will call place_nse_order with exactly:
    {"symbol": "INFY", "side": "buy", "qty": "217", "type": "market", "time_in_force": "day",
     "order_class": "bracket", "take_profit_limit_price": "1530.98", "stop_loss_stop_price": "1485.02"}
```

The user replies "yes". Claude records it, calls the order tool, and the card
shows the call with two buttons: Allow once, Deny. The `why` and the plan stay
in the file; they are never sent.

## What it never does

- Never places an order without all three gates, and never two orders on one yes.
- Never places orders from a scheduled or background task: nobody is there to say yes.
- Never asks for, reads, stores or repeats API keys, passwords, PINs or TOTP codes.
- Never switches `"paper"` to live. The user does that themselves.
- Never calls bulk cancel / exit-all tools, F&O, currency or commodity order tools on its own.
- Never promises a fill price: a market order and a stop fill at whatever the market gives.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, F&O, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker credentials live with the broker's own login or the Claude Desktop config, never in the chat.
