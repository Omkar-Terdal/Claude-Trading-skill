---
name: trader-analyst
description: >-
  The Earnings Analyst. Reads an NSE company's reported numbers from Yahoo
  Finance (free quoteSummary data, standard library, no key) and sums up what
  changed: revenue, net income, margins, EPS and growth, year by year with
  the trailing twelve months, formatted in rupees, lakh and crore. Flags
  numbers that look wrong and says when Yahoo reports in another currency.
  Reads an earnings call transcript when you paste one. Use when the user
  says "how did Infosys's results look", "analyze the earnings", "what
  changed this year", "is revenue growing", "compare margins", "sum up the
  call", "when does it report", or before trading a stock around its results.
argument-hint: "[tickers]"
---

# trader-analyst

Results move stocks more than anything else on the calendar, and the press
release is written to put the best number first. This skill pulls the numbers
Yahoo Finance holds for the company, lines them up year by year, and says
plainly what changed. For a day trader it answers two questions before a
trade: what did the last report say, and is the next one inside this trade?

**What it is not.** This is a summary of a data vendor's figures, not the
company's filings. India's primary sources are the quarterly results and
annual reports on nseindia.com and bseindia.com. Yahoo's numbers can be
consolidated or standalone, can lag, and can be wrong. The tool shows annual
periods (default 4) and a trailing-twelve-month block, not quarters.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE TCS INFY --changes
python3 "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE --metrics trader/metrics.json
python3 "${CLAUDE_SKILL_DIR}/facts.py" HDFCBANK --json
python3 "${CLAUDE_SKILL_DIR}/facts.py" --help
```

Tickers are NSE symbols; `.NS` is added, or give `.BO`. `--quarters N` is the
number of ANNUAL periods to show (the name is a leftover; Yahoo's free data
has no quarterly income statement here).

## What it reads and writes

- Reads Yahoo Finance's `quoteSummary` for `<TICKER>.NS` with the modules
  `financialData`, `incomeStatementHistory` and `defaultKeyStatistics`. Yahoo
  now answers 401 unless the request carries a cookie and a "crumb": the tool
  gets both itself (a request to fc.yahoo.com, then `getcrumb`) and reuses
  them for the run. If that handshake changes, the tool fails with an HTTP
  error rather than guessing.
- Writes `trader/metrics.json` with `--metrics`: one row per company (TTM
  revenue, net income and EPS, margins, growth, return on equity, debt to
  equity). `metrics.example.json` in this folder is a real run for RELIANCE,
  TCS, INFY, HDFCBANK and ITC on 2026-11-07.
- Earnings calls: the tool does not fetch transcripts. If the user pastes one
  (or the company's investor page has it), read it and sum up what management
  said changed: guidance, margins, demand, anything they declined to answer.
- The next results date is not in this data. Look for it in `/trader-news`
  headlines or on the company's investor relations page or the NSE board
  meetings list, and say where it came from.

## The rules

- **Yahoo's numbers, said as Yahoo's.** Name the source whenever you quote a
  figure, and check anything that drives a trade against the exchange filing.
- **Compare like with like.** Growth is against the same period a year
  earlier. Indian companies report on a April to March fiscal year, so
  "2026-03-31" is FY26.
- **Mind the currency.** Some Indian IT exporters (INFY in the 2026-11-07 run)
  have TTM figures reported in USD by Yahoo while the annual table is in
  rupees. The tool prints a NOTE and shows TTM money as `USD 20.37B` when so.
  Never mix the two in one sentence.
- **Distrust a jump.** In the 2026-11-07 run Yahoo gave Reliance's annual
  revenue as ₹1,057,219 Cr for FY26 against ₹517,349 Cr for FY25 (+104 %), while
  its own trailing growth said +29.8 %. The tool now prints a CHECK line for
  any annual move over 50 %. Do not report such a number as a result: open the
  filing.
- **Missing is not zero.** Gross profit and operating income that Yahoo
  leaves out are shown blank. Banks and NBFCs have no gross margin, and
  Yahoo's net income and EPS history for several Indian names are blank too
  (`Net Income: N/A`, `EPS dil: -`).
- **Flag one-offs.** When net income looks out of line with operating
  profit, say it could be a gain or tax item and send the user to the filing.
- **Recommendations are Yahoo's survey.** The "Analyst mean target" line is a
  third-party survey. Quote it as that, never as this tool's view.

## Steps

1. Run it for the companies in question (or the tickers on
   `trader/watchlist.json`):

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE
   python3 "${CLAUDE_SKILL_DIR}/facts.py" RELIANCE TCS INFY --changes --metrics trader/metrics.json
   ```

2. Read any NOTE or CHECK line first. If there is one, the figures it covers
   need the exchange filing before they go in a summary.
3. Write the summary: one line on the year (revenue, profit, growth), one on
   margins, one on anything unusual (a CHECK, a currency note, a blank), and
   where the numbers came from. Plain words, real numbers, no adjectives the
   numbers do not support.
4. If the next results date falls inside the planned trade (an overnight hold,
   or a report before the open), say it before `/trader-exit` sets the stop: a
   stop does not protect against a gap, the stock can open past it.

## Example: 2026-11-07

`facts.py TCS --changes`, real output (shortened): FY26 (year ending
2026-03-31) revenue ₹267,021 Cr, +4.6 % on a year earlier, net income ₹49,210
Cr (+1.4 %); the trailing block says revenue growth +11.2 % and earnings
growth +15.0 %, operating margin 24.0 %, net margin 18.2 %, return on equity
and debt to equity from Yahoo. The summary: "Per Yahoo, TCS grew FY26 revenue
4.6 % and profit 1.4 %, with a net margin of 18.4 %, 0.6 points lower than the
year before. Yahoo's trailing figures are faster (+11.2 %), so the two windows
disagree: check the latest quarterly result on nseindia.com. No results date
in this data."

`facts.py RELIANCE --changes` printed the CHECK line for the +104 % revenue
jump, so the right summary is "the data is not reliable for the annual
comparison; the TTM growth Yahoo shows is +29.8 %, confirm with the filing".
`facts.py INFY` printed `NOTE: Yahoo reports this company's TTM figures in
USD`.

## What it never does

- Never says a stock is cheap, expensive, a buy or a sell. It reports what a
  data vendor holds.
- Never invents a number the data does not have, or estimates one it cannot
  derive.
- Never presents a vendor figure as the company's own filing.

## Safety

Education and tooling, not financial advice. This skill only reads data.
Nothing places an order except `/trader-broker`, after `/trader-risk` passes
and the user types an explicit yes for that order.
