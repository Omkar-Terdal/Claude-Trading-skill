---
name: trader-pelosi
description: >-
  NSE Bulk and Block Deal Tracker. Reads NSE's public daily bulk-deal and
  block-deal files and lists who bought or sold large quantities of which
  stock, at what price: symbol, client, buy or sell, quantity, weighted
  average price, date. Filter by ticker. It is a view of big trades already
  done, not a signal and not inside information. The skill keeps its old
  folder name, "pelosi", from the US version it was built on (US Congress
  stock disclosures); there is no Congress tracking here and no Indian
  politician tracker. Use when the user says "any bulk deals in RELIANCE",
  "who bought", "block deals today", "what are the big funds trading",
  "institutional buying", "check bulk deals for my watchlist".
argument-hint: "[--tickers SYMBOL ...] [--bulk | --block]"
---

# trader-pelosi (NSE bulk and block deals)

The folder name is a leftover. The original skill tracked the stock trades US
members of Congress must disclose. India has no equivalent public feed that
this pack reads, so this skill was repurposed for something India does
publish every trading day: NSE's bulk-deal and block-deal disclosures. Do not
tell the user it tracks politicians.

A **bulk deal** is a day's total trade in one stock that goes above 0.5 % of
its listed shares. A **block deal** is a large negotiated trade done in NSE's
special windows. NSE sets and revises the block-deal minimums, so check
nseindia.com for the current size.

These are public, after-the-fact disclosures. By the time anyone reads them
the trade is done, and a big buyer does not make a stock go up. Nothing here
is inside information. Say that every time.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/ptr.py"                          # all bulk and block deals in the latest file
python3 "${CLAUDE_SKILL_DIR}/ptr.py" --tickers RELIANCE ALIVUS
python3 "${CLAUDE_SKILL_DIR}/ptr.py" --bulk                   # bulk deals only
python3 "${CLAUDE_SKILL_DIR}/ptr.py" --block                  # block deals only
python3 "${CLAUDE_SKILL_DIR}/ptr.py" --json
```

## What it reads and writes

- Reads `https://archives.nseindia.com/content/equities/bulk.csv` and
  `.../block.csv`. Each holds only the most recent trading day's deals, so the
  tool has no history. To keep one, save the `--json` output yourself each day.
- Writes nothing. There is no alert state and no portfolio matching in this
  version: match against the user's holdings by hand.
- Shows `SELL` and `BUY` as the file states them, with the client's name as
  filed. Clients are often funds or brokers acting for others: a broker on the
  buy side is not necessarily the end buyer.

## Rules

- **Quote the file.** Every row is from NSE's archive for that date. Give the
  date shown, because the file is replaced each day.
- **A buy and a sell can be the same shares.** A promoter selling and a fund
  buying the same quantity at the same price (a block deal) is one transfer,
  not two views. Rows come in pairs like that.
- **Price is the weighted average.** For a bulk deal it is the average of
  that client's trades, not a price you could get now.
- **Nothing found is an answer.** "No bulk/block deals found for RELIANCE" is
  normal: large-cap names rarely appear. Say so.

## Steps

1. Run `ptr.py` (optionally with `--tickers` for the watchlist or the planned
   trade).
2. Summarise by stock: who, which side, how much, at what price, on what date.
   Group matching buy and sell rows as one transfer.
3. Say once: this is a disclosure of what has already traded, not a forecast.
4. If the user wants to act, it goes the long way: `/trader-scanner` and
   `/trader-entry` decide whether there is a trade today; then shares only,
   sized by `/trader-budget`, checked by `/trader-risk`, placed by
   `/trader-broker` only after their yes.

## Example: 2026-11-06 file

Real output of `ptr.py --tickers ALIVUS` (shortened). The same stock shows a
bulk sell by NIRMA LIMITED of 2,846,300 shares at ₹1,370.00 and the same
quantity at the same price as a block-deal sell, against block-deal buys at
₹1,370.00 by Bandhan Mutual Fund, Helios Mutual Fund, Param Capital, Norges
Bank (for the Government Pension Fund Global) and others. That is one
holder's exit absorbed by a group of buyers at one price, 16 rows in all, and
the right summary says that: "A large holder sold 28.5 lakh shares at ₹1,370,
and several funds bought them. It is a disclosure of a trade that is already
done." `ptr.py --tickers RELIANCE INFY SBIN HDFCBANK` on the same file printed
"No bulk/block deals found", which is the normal result for large caps. The
unfiltered run listed 116 deals.

## What it never does

- Never says or implies anyone has inside information, or that copying a
  fund beats the market. No return claims.
- Never presents a weighted average price as a price available now.
- Never places an order, and never tracks politicians or US Congress trades.

## Safety

- Education and tooling, not financial advice. Nothing in this pack is a
  recommendation to buy or sell anything.
- Paper trading by default (`"paper": true` in `trader/config.json`).
- Nothing places an order except `/trader-broker`, and only after
  `/trader-risk` passes AND the user types an explicit yes for that order in
  the chat, AND the brokerage tool is set to "Needs approval" in Claude
  (Customize > Connectors > tool permissions) so Claude's own "Allow once /
  Deny" card appears too. Never "Always allow" an order tool.
- No options, F&O, margin, shorting or leverage unless the config allows it
  and the user confirms.
