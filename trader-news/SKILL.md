---
name: trader-news
description: >-
  News Monitor. Tracks news on every NSE stock you watch and flags what
  moves it: headlines per ticker (Yahoo Finance, with Google News as the
  fallback because Yahoo's feed is empty for Indian tickers) and how far each
  stock is from yesterday's close. Shows only what is new since the last
  check, newest first, in IST, tags each item (earnings, analyst, deal, halt,
  regulatory, macro...) and never repeats one. Use when the user says "any
  news on RELIANCE", "why is this moving", "what's the news on my
  watchlist", "morning briefing", "pre-market news", "monitor the news",
  "did anything break", or after /trader-scanner finds setups.
argument-hint: "[tickers]"
---

# trader-news

A chart can look perfect and still be about to gap on a results date, a
trading halt or a regulatory order. Before a setup becomes a trade, this
skill reads the headline feeds for every stock on the watchlist and tells you
only what is new. It also prints how far each stock has moved today, so a
headline and a move sit side by side.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                         # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE HDFCBANK --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --all --hours 72 --dry-run --json
```

There is no SEC EDGAR here: that is a US filing system. Indian company
announcements (results, board outcomes, SEBI LODR disclosures) are on
nseindia.com and bseindia.com under Corporate Announcements. This tool does
not read them, so for anything that matters open the exchange page yourself.

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (trader-scanner) |
| `trader/plan.json` | the trade you are planning (trader-exit) |
| `trader/portfolio.json` | what you hold (trader-broker records fills there) |
| Yahoo Finance headline RSS | `https://feeds.finance.yahoo.com/rss/2.0/headline?s=RELIANCE.NS&region=IN&lang=en-IN`. Checked on 2026-11-07: it returned no items for any `.NS` or `.BO` ticker, only for index symbols |
| Google News RSS | the fallback, one query per ticker: `"<name or ticker>" NSE share`, last 7 days, India edition |
| Yahoo Finance chart endpoint | the last price against yesterday's close (unofficial, can lag) |
| `trader/state/news.json` | what has been shown (kept 30 days) |

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story and read it. Quote the source and link it. Never
  summarise a paywalled story you could not read: say so.
- **Google News is a search, not a wire.** Many hits are quote pages ("Share
  Price Today") with no news in them, and a query on one name can return a
  story about another company (a search for HDFC Bank returned HDFC Life and
  IndusInd pages). The `touches` list shows which of your tickers an item
  really mentions. Treat an item that does not name your stock as noise.
- **Exchange filings beat headlines.** If an NSE or BSE announcement and a
  headline disagree, the announcement is what the company told the exchange.
- **Say what it touches.** Every item lists the tickers it touches and its
  tags: earnings, analyst, legal, deal, capital, people, risk, halt,
  regulatory, macro. The regulatory and macro tags know SEBI, RBI, NCLT, CCI
  and the repo rate. The tags are keyword matches, so a "results" tag can fire
  on a sports story.
- **MOVING** marks a stock 3 % or more from yesterday's close (`--move-pct`).
  A move with no headline is worth saying out loud too. On NSE a stock can hit
  its circuit limit (a price band, often 5, 10 or 20 %): a stock stuck at the
  band is a different situation from a free-moving one, and this tool does not
  detect it.
- **No predictions.** Report what was published and how far the price is from
  yesterday's close. Never say what the stock will do next.

## Steps

1. Run the check: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable, `--dry-run` leaves the state alone.
2. Read the items that matter: anything tagged earnings, halt, legal,
   regulatory or risk, and every MOVING ticker first.
3. Write a short brief, per ticker: what happened, the source and link, the
   move, and one line on why it matters to the setup (for example "results
   are due this week: an overnight gap can jump past any stop").
4. If a story changes the picture, say so before `/trader-entry` and
   `/trader-exit`: a results date inside the trade, a halt, a block deal. Never
   act on a headline alone.

During the session: `/loop 15m /trader-news`, or a scheduled task each
morning before the 9:15 open.

## Example: Friday 2026-11-06

Excerpt (4 of the 6 RELIANCE items; 5 HDFCBANK items not shown) from real
output of `news.py --tickers RELIANCE HDFCBANK --all --hours 72 --dry-run`
pinned to 10:30 IST. The price lines ignore the pin: they show the latest
session's close (15:29 IST on Nov 6), and long titles are shortened here:

```
HDFCBANK ₹788.25 +3.72% vs yesterday's close ₹760.00 at 15:29 IST  MOVING
RELIANCE ₹1,320.00 +0.50% vs yesterday's close ₹1,313.40 at 15:29 IST

  2026-11-06 10:15 IST  RELIANCE    Google News   Reliance Industries (RELIANCE) Share Price Today (NSE), Market News - TechGraph
  2026-11-05 13:46 IST  RELIANCE    Google News   Diwali stock picks 2026: Reliance Industries, ICICI Bank, Tata Steel, Eternal among 30 stocks to buy for... - Moneycontrol.com
  2026-11-04 08:21 IST  RELIANCE    Google News   Reliance Industries shares: Another record! RIL biggest on MTF book with 60% jump ... - Livemint
  2026-11-03 13:58 IST  RELIANCE    Google News   India's Reliance posts best day in over six years as oil business, Jio Platforms lift outlook - Reuters
```

The brief: "Reliance has had a strong week by the headlines: Reuters on Nov 3 says
its best day in six years, helped by the oil business and Jio. Most other hits
are quote pages with no news. No results date was found in these headlines:
check nseindia.com before holding anything past the close." HDFC Bank is
flagged MOVING at +3.72 %, but of its five items four are price pages or about
other companies (HDFC Life, IndusInd Bank). The one real story, TradingView on
Nov 3 ("HDFC Bank shares surges 2.86%, lead Nifty 50 gainers"), is three days
old and does not explain Friday's move: say the move has no headline. When a
ticker has no news, say so rather than padding the brief.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a
  headline.
- Never hammers a server: one request per feed per ticker, a pause between
  calls.

## Safety

Education and tooling, not financial advice. This skill only reads and
reports. Nothing places an order except `/trader-broker`, after
`/trader-risk` passes and the user types an explicit yes for that order.
