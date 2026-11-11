# The Day Trader skill

11 Claude skills for day trading on the Indian market (NSE): scan thousands of charts, track breaking news, time every entry and exit, and replay every outcome before you risk a rupee.

This fork converts the original US pack (NYSE, Alpaca, SEC EDGAR, Congress filings) to NSE, IST and rupees. Prices and times are Indian, but the strategy logic is unchanged and not tuned for Indian markets.

Free, MIT, no signup, no API key. The tools are plain Python that read free
public data: Yahoo Finance charts and fundamentals, NSE's equity list and
bulk/block deal files, and Google News headlines.

One scans thousands of charts for setups. Another tracks breaking news on
every stock you watch. One watches a setup bar by bar and tells you the moment
to get in, and another sets your stop and your target before you do. And the
craziest one is the Time Machine: before you risk a rupee, it replays the
rest of the session 10,000 times from the stock's own recent bars, plus every
past day the same setup fired, and tells you the honest odds.

And no trade goes through until you say yes. Nothing in this pack can place
an order on its own. Paper trading is the default.

> **Looking for the original Trader skill?** Copy an investor's 13F, the
> stock screener, tax lots and rebalancing live in
> [github.com/Jakeschincariol/trader-skill](https://github.com/Jakeschincariol/trader-skill).
> This repo is its day-trading sequel: the Trader skill v2.

## Install

Paste this into Claude:

```
https://github.com/Jakeschincariol/day-trader-skill

install skill
```

Or as a plugin, in Claude Code:

```
/plugin marketplace add Jakeschincariol/day-trader-skill
/plugin install day-trader-skill@day-trader-skill
```

Claude Code namespaces plugin skills, so installed as a plugin they show up as
`/day-trader-skill:trader-scanner` and so on. Copy the folders instead if you want
plain `/trader-scanner`:

```bash
git clone https://github.com/Jakeschincariol/day-trader-skill.git
cp -r day-trader-skill/trader-* ~/.claude/skills/
```

Project-local instead of global: copy the same folders into your repo's
`.claude/skills/`. No Claude Code at all? Paste any single `SKILL.md` at the
top of a chat and it runs as a mode. You lose the Python tools, but the method
works.

The tools need Python 3.8 or newer. Nothing to pip install, and no API keys.

## The eleven

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Chart Scanner | Scans thousands of charts for setups: opening-range breakouts, flags, oversold bounces. |
| `/trader-news` | News Monitor | Tracks breaking news on every stock you watch and flags what moves it. |
| `/trader-entry` | Entry Timer | Watches a setup bar by bar and tells you the moment to get in. |
| `/trader-exit` | Exit Planner | Sets your stop and your target before you enter, so you know when to get out. |
| `/trader-timemachine` | Time Machine | Replays every possible outcome of a trade before you risk a rupee: 10,000 simulated sessions plus every past time the setup fired. |
| `/trader-risk` | Risk Manager | Checks every trade against your limits and stops anything over them. |
| `/trader-budget` | Budget Manager | Sizes every position to your budget and your max loss per trade. |
| `/trader-broker` | Order Executor | Sends approved orders to your broker, paper trading by default, never without your yes. |
| `/trader-alerts` | Price Alerts | Rings when a price, a stop or the opening bell hits. |
| `/trader-analyst` | Earnings Analyst | Sums up a company's yearly results from Yahoo Finance data and flags numbers that look wrong. |
| `/trader-pelosi` | Bulk/Block Deal Tracker | Lists NSE's daily bulk and block deals. The folder keeps its old name; there is no Congress tracking here. |

## How to use it

Everything lands in a `trader/` folder in your project, and each skill reads
what the last one wrote:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: stop, target, flat by the close                                       (while it is open)
news / analyst / pelosi                                                        (on a schedule)
```

An example, a real trade on real data: RELIANCE, Friday November 6 2026. The numbers
below are the tools' actual output.

1. **`/trader-scanner`** at 11:00 AM IST over 20 large caps: 12 of 19 charts had closed
   above their 15-minute opening range. RELIANCE is one: range 1295.60 to 1306.00, the 9:45
   candle closed at 1311.90, 0.45 % over the top.
2. **`/trader-news`**: headlines come from Google News because Yahoo's feed is empty for
   Indian tickers. Many hits are price pages, so check the story before you rely on it.
3. **`/trader-entry`** watched RELIANCE bar by bar. At 9:48 it said waiting (the 9:45 bar
   was still forming). At 9:55 it said triggered: the first 5-minute close above the
   range, 1311.90 at 9:50 AM.
4. **`/trader-exit`** set the plan before the entry: stop at the range's midpoint, 1300.80,
   so 11.10 a share at risk; target one times that risk, 1323.00; flat by 15:20 either way.
5. **`/trader-timemachine`** replayed the rest of the day 10,000 times from RELIANCE's own
   candles over the previous 59 sessions (seed 19): target first 31.1 %, stop first
   38.0 %, still open at 3:20 PM 30.9 %. The same rule on those sessions fired 31 times:
   14 target, 11 stop, 6 neither. Verdict: close to a coin flip. Yahoo leaves NSE's last
   5-minute bars empty, so the run fills those gaps and says how many it filled.
6. **`/trader-budget`** with a ₹500,000 account and a ₹5,000 max loss per trade: the max
   loss alone would allow 450 shares, but the 75 % position cap holds it to 285 shares
   (₹373,891.50). Loss at the stop: ₹3,163.50. It writes one bracket order.
7. **`/trader-risk`** checks it against your limits: PASS or BLOCK, with the reason.
8. **`/trader-broker`** shows you the ticket in plain words. You type yes for that exact
   order. Claude calls your broker's MCP connector (Zerodha Kite is the documented example;
   you must map its real order tool, see the skill), and Claude's own "Allow once / Deny"
   card asks you one more time.
9. **`/trader-alerts`** rings on the stop, the target and at 3:20 PM to flatten whatever
   is still open.

## The tools

All standard-library Python. The data tools read public endpoints and are
polite about it.

```bash
python3 trader-scanner/scan.py universe                                # ~2,400 NSE EQ-series stocks
python3 trader-scanner/scan.py daily                                   # pre-market setups, whole universe
python3 trader-scanner/scan.py orb --at 10:15                          # opening-range breakouts so far
python3 trader-news/news.py --tickers RELIANCE --hours 12              # headlines, today's move
python3 trader-entry/entry.py check RELIANCE --setup orb               # waiting / triggered / expired
python3 trader-entry/entry.py watch RELIANCE --every 30                # bar by bar until it triggers
python3 trader-exit/exit.py plan --stop or-mid --target 1R --write     # -> trader/plan.json
python3 trader-timemachine/timemachine.py run --write                  # 10,000 replays + the history
python3 trader-budget/size.py --write                                  # -> trader/orders.json
python3 trader-risk/check.py                                           # PASS / BLOCK per order
python3 trader-broker/ticket.py show                                   # the tickets, READY or not
python3 trader-alerts/alerts.py add --from-plan                        # stop, target, flat by close
python3 trader-alerts/alerts.py check                                  # rings what hit, once
python3 trader-analyst/facts.py RELIANCE --changes                       # yearly results, growth
python3 trader-pelosi/ptr.py --tickers RELIANCE                       # NSE bulk/block deals
```

```bash
python3 -m unittest discover -s tests -v
```

**The test suite has not been ported.** It still encodes the original US pack: TSLA
fixtures, New York times, NYSE holidays, Alpaca. At the last run 182 of its 308 tests
failed against the NSE code, and those failures say nothing about whether the NSE
behaviour is right. The worked example above was checked by running the tools on live
Yahoo and NSE data, not by these tests.

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation and it cannot see the future.
Most people who day trade lose money. You can lose money. Decide for
yourself, and talk to a licensed professional if you need advice.

**No trade goes through until you say yes.** Only `/trader-broker` can lead to
an order, and only after `/trader-risk` passes that exact order and you type
yes for it in the chat. Set your brokerage tool to "Needs approval" in Claude
(Customize, Connectors, tool permissions), never "Always allow", so Claude's
own "Allow once / Deny" card is a second check. No options, margin, short
selling or leverage unless you turn them on.

**Paper first.** `"paper": true` is the default in `config.example.json`, but it only
labels the ticket. Many brokers have no paper account, so check yours: an order that
reaches a live account is a real order with real money. Logins and keys stay with the
broker, never in the chat.

**The Time Machine is a simulation, not a prediction.** It is built from the
stock's own last 60 days of 5-minute bars. The future can do things those 60
days never did: an earnings report, a halt, a headline. Treat its odds as a
reality check, never as a promise.

**A stop is a plan, not a guarantee.** In a fast market or across a gap, a
stop order can fill well past its price. The max loss shown is the loss at
the stop price.

**The data is free and unofficial.** Yahoo Finance's chart endpoint is not an
official API: it can lag, change or rate-limit you. The scanner and the entry
timer read 1-minute or 5-minute bars, not every tick. Check anything that
matters against your broker before you act on it.

**Indian market rules.** There is no pattern-day-trader rule here, so the PDT check was
removed. Brokerage, STT, exchange charges, GST and stamp duty are not modelled and take
a real share of a small intraday win. Price bands (circuit limits), tick sizes, MIS
square-off times and your broker's margin rules are not modelled either. The holiday
list is the 2026 calendar: check nseindia.com for dates.

**Bulk and block deals are public, not inside information.** They are disclosures of
trades that already happened. A big buyer does not make a stock rise.

**Taxes.** Intraday and delivery trades are generally taxed differently in India, and
the rules change. Nothing here is tax advice: ask a chartered accountant.

## Files

```
trader-scanner/     SKILL.md, scan.py
trader-news/        SKILL.md, news.py
trader-entry/       SKILL.md, entry.py
trader-exit/        SKILL.md, exit.py
trader-timemachine/ SKILL.md, timemachine.py
trader-risk/        SKILL.md, check.py
trader-budget/      SKILL.md, size.py, config.example.json, targets.example.json
trader-broker/      SKILL.md, ticket.py, claude_desktop_config.example.json
trader-alerts/      SKILL.md, alerts.py, alerts.example.json
trader-analyst/     SKILL.md, facts.py, metrics.example.json
trader-pelosi/      SKILL.md, ptr.py
tests/              unittest suite + offline fixtures
```

MIT. Made by [Jake Schincariol](https://opusjake.ai).
