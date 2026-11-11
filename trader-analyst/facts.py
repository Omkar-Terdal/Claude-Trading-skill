#!/usr/bin/env python3
"""Fundamental facts for trader-analyst: revenue, income, EPS from Yahoo Finance.
Standard library only.

Pulls a company's reported numbers from Yahoo Finance's quoteSummary API
and lays out what changed: revenue, gross/operating/net margins, net income,
diluted EPS, and growth against the same period a year earlier.

    python3 facts.py RELIANCE      # NSE: auto-adds .NS suffix
    python3 facts.py RELIANCE.NS   # explicit
    python3 facts.py TCS INFY --quarters 8
    python3 facts.py RELIANCE --json
    python3 facts.py TCS HDFCBANK --metrics trader/metrics.json

Source: Yahoo Finance quoteSummary (free, no key required).
Numbers only: this tool never says whether a stock is a buy.
"""

import argparse
import datetime
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"
HOSTS = ("query2", "query1")
BACKOFF = 5.0
MODULES = "financialData,incomeStatementHistory,defaultKeyStatistics"


class FactsError(Exception):
    pass


def _nse_yahoo_sym(sym):
    """RELIANCE -> RELIANCE.NS; already suffixed tickers pass through."""
    s = sym.upper().strip()
    if s.endswith(".NS") or s.endswith(".BO"):
        return s
    return s + ".NS"


def _inr(x):
    """Format as INR with crore/lakh suffix."""
    if x is None:
        return "N/A"
    x = float(x)
    if abs(x) >= 1e7:
        return "₹{:,.2f} Cr".format(x / 1e7)
    if abs(x) >= 1e5:
        return "₹{:,.2f} L".format(x / 1e5)
    return "₹{:,.2f}".format(x)


def _ttm_money(x, cur):
    """TTM figures come in Yahoo's financialCurrency, which is USD for some Indian IT exporters."""
    if cur in (None, "", "INR"):
        return _inr(x)
    if x is None:
        return "N/A"
    x = float(x)
    for div, tag in ((1e9, "B"), (1e6, "M")):
        if abs(x) >= div:
            return "%s %.2f%s" % (cur, x / div, tag)
    return "%s %.2f" % (cur, x)


def _inr_m(x):
    """Short form in millions-equivalent for table columns."""
    if x is None:
        return "-"
    x = float(x)
    if abs(x) >= 1e7:
        return "₹{:.0f}Cr".format(x / 1e7)
    if abs(x) >= 1e5:
        return "₹{:.0f}L".format(x / 1e5)
    return "₹{:.0f}".format(x)


def _p(v):
    return "-" if v is None else "%+.1f%%" % v if v != 0 else "0.0%"


def _pm(v):
    return "-" if v is None else "%.1f%%" % v


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return round(100.0 * (a - b) / abs(b), 1)


def margin(part, whole):
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 1)


_SESSION = {}


def _opener():
    """One cookie-carrying opener plus Yahoo's crumb. quoteSummary answers 401 without them."""
    if "opener" not in _SESSION:
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.addheaders = [("User-Agent", UA)]
        try:
            opener.open("https://fc.yahoo.com", timeout=15).read()
        except urllib.error.HTTPError:
            pass   # fc.yahoo.com answers 404 but still sets the cookie
        except urllib.error.URLError as exc:
            raise FactsError("could not reach Yahoo Finance: %s" % exc.reason)
        try:
            crumb = opener.open("https://query1.finance.yahoo.com/v1/test/getcrumb",
                                timeout=15).read().decode("utf-8").strip()
        except (urllib.error.URLError, OSError) as exc:
            raise FactsError("could not get a Yahoo Finance crumb: %s" % exc)
        _SESSION["opener"], _SESSION["crumb"] = opener, crumb
    return _SESSION["opener"], _SESSION["crumb"]


def _fetch(ticker_yahoo, pause=0.3):
    """Fetch quoteSummary JSON for a Yahoo Finance symbol (e.g. RELIANCE.NS)."""
    last_exc = None
    opener, crumb = _opener()
    for host in HOSTS:
        url = ("https://{host}.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
               "?modules={modules}&crumb={crumb}").format(
                   host=host, sym=urllib.parse.quote(ticker_yahoo), modules=MODULES,
                   crumb=urllib.parse.quote(crumb))
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            time.sleep(pause)
            with opener.open(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FactsError("Yahoo Finance: no data for %s (check the ticker)" % ticker_yahoo)
            last_exc = FactsError("HTTP %d for %s" % (exc.code, ticker_yahoo))
            continue
        except urllib.error.URLError as exc:
            last_exc = FactsError("could not reach Yahoo Finance: %s" % exc.reason)
            continue
        err = (data.get("quoteSummary") or {}).get("error")
        if err:
            raise FactsError("Yahoo Finance error for %s: %s" % (ticker_yahoo, err))
        result = (data.get("quoteSummary") or {}).get("result")
        if not result:
            raise FactsError("empty result from Yahoo Finance for %s" % ticker_yahoo)
        return result[0]
    raise last_exc or FactsError("failed to fetch %s" % ticker_yahoo)


def _val(node, *keys):
    """Drill into a nested dict by keys, return raw value or None."""
    for k in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    if isinstance(node, dict):
        return node.get("raw")
    return node


def facts_for(sym):
    """Fetch and parse fundamentals for sym. Returns a structured dict."""
    yahoo_sym = _nse_yahoo_sym(sym)
    raw = _fetch(yahoo_sym)

    fin = raw.get("financialData") or {}
    inc_hist = raw.get("incomeStatementHistory") or {}
    key_stats = raw.get("defaultKeyStatistics") or {}

    name = _val(raw, "price", "longName") or _val(raw, "quoteType", "longName") or sym
    currency = fin.get("financialCurrency") or "INR"

    # Current / trailing metrics from financialData
    revenue_ttm = _val(fin, "totalRevenue")
    gross_profit_ttm = _val(fin, "grossProfits")
    ebitda_ttm = _val(fin, "ebitda")
    net_income_ttm = _val(fin, "netIncomeToCommon") or _val(key_stats, "netIncomeToCommon")   # Yahoo keeps it in key stats
    operating_margin = _val(fin, "operatingMargins")
    gross_margin = _val(fin, "grossMargins")
    net_margin = _val(fin, "profitMargins")
    eps_ttm = _val(fin, "epsTrailingTwelveMonths") or _val(key_stats, "trailingEps")
    eps_fwd = _val(fin, "epsForward") or _val(key_stats, "forwardEps")
    revenue_growth = _val(fin, "revenueGrowth")      # YoY as decimal
    earnings_growth = _val(fin, "earningsGrowth")    # YoY as decimal
    return_on_equity = _val(fin, "returnOnEquity")
    debt_to_equity = _val(fin, "debtToEquity")
    current_ratio = _val(fin, "currentRatio")
    free_cash_flow = _val(fin, "freeCashflow")
    target_price = _val(fin, "targetMeanPrice")
    recommendation = fin.get("recommendationKey") or ""

    # Annual income statements (up to 4 years)
    statements = inc_hist.get("incomeStatementHistory") or []
    annual = []
    for s in statements:
        end_date = None
        d = s.get("endDate")
        if isinstance(d, dict):
            end_date = d.get("fmt")
        elif d:
            end_date = str(d)
        rev = _val(s, "totalRevenue")
        ni = _val(s, "netIncome")
        gp = _val(s, "grossProfit") or None      # Yahoo sends 0 for "not reported"
        oi = _val(s, "operatingIncome") or _val(s, "ebit") or None
        eps = _val(s, "dilutedEPS") or _val(s, "basicEPS")
        annual.append({
            "end": end_date, "revenue": rev, "gross_profit": gp,
            "operating_income": oi, "net_income": ni, "eps_diluted": eps,
            "gross_margin": margin(gp, rev),
            "operating_margin": margin(oi, rev),
            "net_margin": margin(ni, rev),
        })

    # YoY growth between annual periods
    for i, row in enumerate(annual):
        prior = annual[i + 1] if i + 1 < len(annual) else None
        row["revenue_yoy"] = pct(row["revenue"], prior["revenue"]) if prior else None
        row["net_income_yoy"] = pct(row["net_income"], prior["net_income"]) if prior else None
        row["eps_yoy"] = pct(row["eps_diluted"], prior["eps_diluted"]) if prior else None

    return {
        "sym": sym, "yahoo_sym": yahoo_sym, "name": name, "currency": currency,
        "ttm": {
            "revenue": revenue_ttm, "gross_profit": gross_profit_ttm, "ebitda": ebitda_ttm,
            "net_income": net_income_ttm, "eps": eps_ttm, "eps_forward": eps_fwd,
            "gross_margin": gross_margin, "operating_margin": operating_margin, "net_margin": net_margin,
            "revenue_growth_yoy": round(revenue_growth * 100, 1) + 0.0 if revenue_growth is not None else None,
            "earnings_growth_yoy": round(earnings_growth * 100, 1) + 0.0 if earnings_growth is not None else None,
            "return_on_equity": round(return_on_equity * 100, 1) if return_on_equity is not None else None,
            "debt_to_equity": debt_to_equity, "current_ratio": current_ratio,
            "free_cash_flow": free_cash_flow, "target_price": target_price,
            "recommendation": recommendation,
        },
        "annual": annual,
    }


def render(facts):
    """Human-readable output with ₹ and crore/lakh formatting."""
    out = []
    out.append("%s (%s)  |  Yahoo: %s  |  Currency: %s" % (
        facts["name"], facts["sym"], facts["yahoo_sym"], facts["currency"]))
    out.append("")

    t = facts["ttm"]
    out.append("=== Trailing Twelve Months (TTM) ===")
    cur = facts["currency"]
    sign = "₹" if cur in (None, "", "INR") else cur + " "
    if sign != "₹":
        out.append("  NOTE: Yahoo reports this company's TTM figures in %s; the annual table below is in ₹." % cur)
    out.append("  Revenue        : %s" % _ttm_money(t["revenue"], cur))
    out.append("  Net Income     : %s" % _ttm_money(t["net_income"], cur))
    out.append("  EBITDA         : %s" % _ttm_money(t["ebitda"], cur))
    out.append("  Free Cash Flow : %s" % _ttm_money(t["free_cash_flow"], cur))
    # Yahoo's EPS is per share in rupees even when the company's totals are in USD (INFY: 75.10 against a ₹1,181 price)
    out.append("  EPS (TTM)      : %s" % ("₹%.2f" % t["eps"] if t["eps"] is not None else "N/A"))
    out.append("  EPS (Forward)  : %s" % ("₹%.2f" % t["eps_forward"] if t["eps_forward"] is not None else "N/A"))
    out.append("")
    out.append("=== Margins ===")
    out.append("  Gross    : %s" % _pm(round(t["gross_margin"] * 100, 1) if t["gross_margin"] is not None else None))
    out.append("  Operating: %s" % _pm(round(t["operating_margin"] * 100, 1) if t["operating_margin"] is not None else None))
    out.append("  Net      : %s" % _pm(round(t["net_margin"] * 100, 1) if t["net_margin"] is not None else None))
    out.append("")
    out.append("=== Growth (YoY) ===")
    out.append("  Revenue  : %s" % _p(t["revenue_growth_yoy"]))
    out.append("  Earnings : %s" % _p(t["earnings_growth_yoy"]))
    out.append("")
    out.append("=== Balance Sheet / Efficiency ===")
    out.append("  Return on Equity : %s" % _pm(t["return_on_equity"]))
    out.append("  Debt/Equity      : %s" % (("%.2f" % t["debt_to_equity"]) if t["debt_to_equity"] is not None else "N/A"))
    out.append("  Current Ratio    : %s" % (("%.2f" % t["current_ratio"]) if t["current_ratio"] is not None else "N/A"))
    if t["target_price"]:
        out.append("  Analyst mean target (Yahoo's survey, not our view): ₹%.2f" % t["target_price"])
    out.append("")

    if facts["annual"]:
        out.append("=== Annual History ===")
        out.append("%-12s %12s %12s %9s %9s %8s" % (
            "year end", "revenue", "net income", "rev yoy", "ni yoy", "EPS dil"))
        for r in facts["annual"]:
            out.append("%-12s %12s %12s %9s %9s %8s" % (
                r["end"] or "-", _inr_m(r["revenue"]), _inr_m(r["net_income"]),
                _p(r["revenue_yoy"]), _p(r["net_income_yoy"]),
                "₹%.2f" % r["eps_diluted"] if r["eps_diluted"] is not None else "-"))
    out.append("")
    out.append("Source: Yahoo Finance quoteSummary. Numbers only, not financial advice.")
    return "\n".join(out)


def changes(facts):
    """Plain sentences: what changed in the latest annual period vs the one before."""
    annual = facts["annual"]
    if not annual:
        return []
    cur = annual[0]
    out = []
    label = "Year ending %s" % (cur["end"] or "?")
    line = "%s: revenue %s" % (label, _inr(cur["revenue"]))
    if cur["revenue_yoy"] is not None:
        line += " (%+.1f%% vs a year earlier)" % cur["revenue_yoy"]
    line += ", net income %s" % _inr(cur["net_income"])
    if cur["net_income_yoy"] is not None:
        line += " (%+.1f%%)" % cur["net_income_yoy"]
    if cur["eps_diluted"] is not None:
        line += ", diluted EPS ₹%.2f" % cur["eps_diluted"]
        if cur["eps_yoy"] is not None:
            line += " (%+.1f%%)" % cur["eps_yoy"]
    out.append(line + ".")
    for key, name in (("gross_margin", "Gross margin"), ("operating_margin", "Operating margin"),
                      ("net_margin", "Net margin")):
        v = cur.get(key)
        if v is None:
            continue
        s = "%s %.1f%%" % (name, v)
        if len(annual) > 1 and annual[1].get(key) is not None:
            s += " (%+.1f points vs prior year)" % (v - annual[1][key])
        out.append(s + ".")
    if cur["revenue_yoy"] is not None and abs(cur["revenue_yoy"]) > 50:
        out.append("CHECK: revenue moved %+.0f%% in a year. Yahoo's annual rows can mix standalone and "
                   "consolidated figures: open the company's filing on nseindia.com before using this." % cur["revenue_yoy"])
    t = facts["ttm"]
    if t["revenue_growth_yoy"] is not None:
        out.append("TTM revenue growth (YoY): %+.1f%%." % t["revenue_growth_yoy"])
    if t["earnings_growth_yoy"] is not None:
        out.append("TTM earnings growth (YoY): %+.1f%%." % t["earnings_growth_yoy"])
    return out


def metrics_row(facts):
    cur = facts["annual"][0] if facts["annual"] else {}
    t = facts["ttm"]
    return {
        "ticker": facts["sym"], "yahoo_sym": facts["yahoo_sym"], "name": facts["name"],
        "currency": facts["currency"],
        "period_end": cur.get("end"),
        "revenue_ttm": t["revenue"], "net_income_ttm": t["net_income"], "eps_ttm": t["eps"],
        "eps_forward": t["eps_forward"],
        "revenue_growth_yoy": t["revenue_growth_yoy"], "earnings_growth_yoy": t["earnings_growth_yoy"],
        "gross_margin": round(t["gross_margin"] * 100, 1) if t["gross_margin"] is not None else None,
        "operating_margin": round(t["operating_margin"] * 100, 1) if t["operating_margin"] is not None else None,
        "net_margin": round(t["net_margin"] * 100, 1) if t["net_margin"] is not None else None,
        "return_on_equity": t["return_on_equity"], "debt_to_equity": t["debt_to_equity"],
        "recommendation": t["recommendation"], "target_price": t["target_price"],
        "source": "Yahoo Finance quoteSummary",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tickers", nargs="*", help="NSE tickers, e.g. RELIANCE TCS")
    ap.add_argument("--quarters", type=int, default=4, help="annual periods to show (default 4)")
    ap.add_argument("--metrics", metavar="FILE", help="merge a row per ticker into this JSON file")
    ap.add_argument("--json", action="store_true", help="output raw JSON")
    ap.add_argument("--changes", action="store_true", help="show what changed in plain English")
    args = ap.parse_args(argv)
    if not args.tickers:
        ap.print_help()
        return 2

    all_facts, rows, failed = [], [], 0
    for t in args.tickers:
        try:
            f = facts_for(t)
            # Trim annual to requested number of periods
            f["annual"] = f["annual"][:args.quarters]
            all_facts.append(f)
            rows.append(metrics_row(f))
            if not args.json:
                print(render(f))
                if args.changes:
                    print("What changed:")
                    for line in changes(f):
                        print("  - " + line)
                    print()
        except FactsError as exc:
            print("facts: %s: %s" % (t, exc), file=sys.stderr)
            failed += 1

    if args.json:
        print(json.dumps(all_facts, indent=2))

    if args.metrics and rows:
        existing = {}
        if os.path.isfile(args.metrics):
            try:
                with open(args.metrics, encoding="utf-8") as fh:
                    existing = {r["ticker"]: r for r in json.load(fh).get("companies", [])}
            except (OSError, ValueError):
                pass
        for r in rows:
            existing[r["ticker"]] = r
        folder = os.path.dirname(args.metrics)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.metrics, "w", encoding="utf-8") as fh:
            json.dump({"as_of": datetime.date.today().isoformat(),
                       "companies": sorted(existing.values(), key=lambda r: r["ticker"])},
                      fh, indent=2)
            fh.write("\n")
        if not args.json:
            print("wrote %s (%d companies)" % (args.metrics, len(existing)))

    return 0 if all_facts and not failed else (1 if all_facts else 2)


if __name__ == "__main__":
    sys.exit(main())
