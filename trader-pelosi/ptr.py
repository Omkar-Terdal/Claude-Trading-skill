#!/usr/bin/env python3
"""NSE Bulk & Block Deal Tracker for trader-pelosi. Standard library only.

Fetches the most recent bulk-deal and block-deal disclosures from NSE's public
archive. A bulk deal is a day's trade in one stock above 0.5 % of its listed
shares; a block deal is a negotiated trade done in NSE's special windows. NSE
sets the block-deal minimums and changes them, so check nseindia.com for the
current size. This is a quick way to see what big players bought or sold.

    python3 ptr.py                              # all bulk + block deals today
    python3 ptr.py --tickers RELIANCE TCS       # filter by symbol
    python3 ptr.py --bulk                       # bulk deals only
    python3 ptr.py --block                      # block deals only
    python3 ptr.py --json

Sources (NSE public archives, no login needed):
  Bulk deals:  https://archives.nseindia.com/content/equities/bulk.csv
  Block deals: https://archives.nseindia.com/content/equities/block.csv

These files contain the most recent trading day's data as published by NSE.
Data is a tool for context, not financial advice.
"""

import argparse
import csv
import io
import json
import sys
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"
BULK_URL = "https://archives.nseindia.com/content/equities/bulk.csv"
BLOCK_URL = "https://archives.nseindia.com/content/equities/block.csv"


class PTRError(Exception):
    pass


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/csv,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise PTRError("NSE archive answered HTTP %d for %s" % (e.code, url))
    except urllib.error.URLError as e:
        raise PTRError("Could not reach NSE archive: %s" % getattr(e, "reason", e))


def fetch_deals(url, kind):
    """Fetch a CSV from NSE archive -> list of deal dicts."""
    text = _get(url)
    reader = csv.DictReader(io.StringIO(text))
    names = {(n or "").strip().lower() for n in (reader.fieldnames or [])}
    if "symbol" not in names:
        raise PTRError("NSE did not send a deals CSV (no Symbol column); the archive may be blocking "
                       "this request, try again later")
    deals = []
    for row in reader:
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k and not isinstance(v, list)}
        if not (row.get("Symbol") or row.get("SYMBOL") or row.get("symbol")):
            continue
        sym = row.get("Symbol") or row.get("SYMBOL") or row.get("symbol") or ""
        client = row.get("Client Name") or row.get("CLIENT NAME") or row.get("clientName") or ""
        side = (row.get("Buy/Sell") or row.get("Buy / Sell") or row.get("BUY/SELL")
                or row.get("buySell") or "")
        qty = row.get("Quantity Traded") or row.get("QUANTITY") or ""
        price = (row.get("Trade Price / Wght. Avg. Price") or row.get("Wt. Avg. Price")
                 or row.get("PRICE") or "")
        date = row.get("Date") or row.get("DATE") or ""
        security = row.get("Security Name") or row.get("SECURITY NAME") or sym
        deals.append({
            "kind": kind,
            "symbol": sym.upper().strip(),
            "security": security.strip(),
            "client": client.strip(),
            "side": side.strip().lower(),
            "qty": qty.strip().replace(",", ""),
            "price": price.strip().replace(",", ""),
            "date": date.strip(),
        })
    return deals


def render(deals, tickers=None):
    """Human-readable deal list."""
    if tickers:
        tickers_up = {t.upper() for t in tickers}
        deals = [d for d in deals if d["symbol"] in tickers_up]
    if not deals:
        return "No bulk/block deals found" + (" for %s" % ", ".join(tickers) if tickers else "") + "."
    lines = []
    for d in deals:
        side_str = "BUY " if "buy" in d["side"] else "SELL" if "sell" in d["side"] else d["side"].upper()[:4]
        price_str = ("₹" + d["price"]) if d["price"] else "N/A"
        lines.append("[%s] %s %s  %s  qty %s @ %s  %s" % (
            d["kind"].upper(), d["date"], side_str, d["symbol"],
            d["qty"], price_str, d["client"]))
    lines.append("")
    lines.append("%d deal(s). Source: NSE bulk/block deal archive. Data is for context, not advice." % len(deals))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="NSE Bulk & Block Deal Tracker.")
    ap.add_argument("--tickers", nargs="*", help="filter by NSE symbol(s)")
    ap.add_argument("--bulk", action="store_true", help="bulk deals only")
    ap.add_argument("--block", action="store_true", help="block deals only")
    ap.add_argument("--json", action="store_true", help="output JSON")
    a = ap.parse_args(argv)

    deals = []
    errors = []
    want_bulk = not a.block
    want_block = not a.bulk
    if a.bulk and a.block:
        want_bulk = want_block = True

    if want_bulk:
        try:
            deals += fetch_deals(BULK_URL, "bulk")
        except PTRError as e:
            errors.append("bulk deals: %s" % e)
    if want_block:
        try:
            deals += fetch_deals(BLOCK_URL, "block")
        except PTRError as e:
            errors.append("block deals: %s" % e)

    tickers = [t.upper() for t in (a.tickers or [])]
    if tickers:
        deals = [d for d in deals if d["symbol"] in set(tickers)]

    if a.json:
        print(json.dumps({"deals": deals, "errors": errors}, indent=2))
    else:
        print(render(deals, tickers if tickers else None))
        for e in errors:
            print("note: " + e)

    return 0 if deals else 1


if __name__ == "__main__":
    sys.exit(main())
