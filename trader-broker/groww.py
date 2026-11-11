#!/usr/bin/env python3
"""Groww Trade API client for trader-broker. Standard library only.

Reads (safe): token, quote, ltp, candles, margin, positions, status.
Writes (real money): place, oco, cancel. Every write is a DRY RUN that prints the
exact request and sends nothing, unless ALL of these hold:
  * "paper" is false in trader/config.json and you pass --send
  * place/oco: ticket.py approve recorded your yes for this exact order today
    (same fingerprint, trader-risk PASS), read from trader/state/broker.json

Credentials come from the environment or from ~/.groww.env (KEY=VALUE lines, mode 600,
outside the repo), never from chat or files in the repo:
  GROWW_ACCESS_TOKEN               a token from the Groww dashboard, or
  GROWW_API_KEY + GROWW_API_SECRET  then `groww.py token` makes one (needs your daily
                                   approval on the Groww Cloud API Keys page)
  python3 groww.py setup           creates ~/.groww.env for you to fill in, checks its mode
  python3 groww.py check           confirms the credentials work; prints no secrets

    python3 groww.py token
    python3 groww.py ltp RELIANCE TCS
    python3 groww.py quote RELIANCE
    python3 groww.py candles RELIANCE --days 5 --interval 5minute
    python3 groww.py margin
    python3 groww.py positions
    python3 groww.py place --index 1                # dry run
    python3 groww.py place --index 1 --send         # real order, live config only
    python3 groww.py oco --symbol RELIANCE --qty 285 --target 1330 --stop 1300.80 --side sell
    python3 groww.py status --order-id GMK...

API: https://groww.in/trade-api/docs (base https://api.groww.in/v1). Groww has no paper
trading, so a sent order is a live order. Quotes may be delayed or limited by your plan.
"""

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.groww.in/v1"
UA = "day-trader-skill-nse/2.0"
IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
MAX_DAYS = {"1minute": 30, "2minute": 30, "3minute": 30, "5minute": 30,
            "10minute": 90, "15minute": 90, "30minute": 90}
TOKEN_FILE = os.path.join("trader", "state", "groww_token.json")
ENV_FILE = os.path.expanduser("~/.groww.env")
ENV_KEYS = ("GROWW_ACCESS_TOKEN", "GROWW_API_KEY", "GROWW_API_SECRET")
ENV_TEMPLATE = """# Groww Trade API credentials. Keep this file private (mode 600). Never paste these into a chat.
# Option A: the token from the Trading APIs section of your Groww profile (it lapses daily at 6:00 AM)
GROWW_ACCESS_TOKEN=
# Option B: an API key and secret, then run `python3 groww.py token` (approve the key on the
# Groww Cloud API Keys page first)
GROWW_API_KEY=
GROWW_API_SECRET=
"""


class GrowwError(Exception):
    pass


# ---------------------------------------------------------------- http

def _headers(token, post=False):
    h = {"Authorization": "Bearer " + token, "Accept": "application/json",
         "X-API-VERSION": "1.0", "User-Agent": UA}
    if post:
        h["Content-Type"] = "application/json"
    return h


def call(method, path, token, params=None, body=None, timeout=20, envelope=True):
    """One API call -> payload. Raises GrowwError with Groww's own error text.
    envelope=False: the endpoint answers with the fields at the top level (the token endpoint does)."""
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params, safe=",")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_headers(token, data is not None))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if hasattr(e, "read") else ""
        try:
            err = (json.loads(raw).get("error") or {})
        except ValueError:
            err = {}
        raise GrowwError("Groww answered HTTP %d%s" % (
            e.code, (" %s: %s" % (err.get("code"), err.get("message"))) if err else ""))
    except urllib.error.URLError as e:
        raise GrowwError("could not reach Groww: %s" % getattr(e, "reason", e))
    try:
        doc = json.loads(raw)
    except ValueError:
        raise GrowwError("Groww sent something that is not JSON")
    if not envelope:
        if not isinstance(doc, dict) or not doc.get("token"):
            raise GrowwError("Groww answered without a token (fields: %s)" % ", ".join(sorted(doc or {})))
        return doc
    if doc.get("status") != "SUCCESS":
        err = doc.get("error") or {}
        detail = ("%s %s" % (err.get("code", ""), err.get("message", ""))).strip()
        raise GrowwError("Groww refused: %s" % (detail or "no reason given (response fields: %s)"
                                               % ", ".join(sorted(doc))))
    return doc.get("payload")


# ---------------------------------------------------------------- credentials file

def parse_env_text(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in ENV_KEYS and v:
            out[k] = v
    return out


def env_file_problem(path=ENV_FILE):
    """None if the file is private enough, else why not. Other users must not be able to read it."""
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        return None
    if mode & 0o077:
        return "%s is readable by other users (mode %s): run `chmod 600 %s`" % (path, oct(mode)[2:], path)
    return None


def load_env_file(path=ENV_FILE, environ=None):
    """Fill credentials missing from the environment out of the private file. Refuses a file others can read."""
    environ = os.environ if environ is None else environ
    if not os.path.exists(path):
        return []
    bad = env_file_problem(path)
    if bad:
        raise GrowwError(bad)
    with open(path, encoding="utf-8") as fh:
        found = parse_env_text(fh.read())
    loaded = []
    for k, v in found.items():
        if not environ.get(k):
            environ[k] = v
            loaded.append(k)
    return loaded


def setup_env_file(path=ENV_FILE):
    if os.path.exists(path):
        return False
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(ENV_TEMPLATE)
    return True


# ---------------------------------------------------------------- token

def checksum(secret, timestamp):
    return hashlib.sha256((secret + timestamp).encode("utf-8")).hexdigest()


def make_token(key, secret, now=None):
    ts = str(int(now if now is not None else time.time()))
    body = {"key_type": "approval", "checksum": checksum(secret, ts), "timestamp": ts}
    return call("POST", "/token/api/access", key, body=body, envelope=False)


def get_token(env=None, token_file=TOKEN_FILE):
    env = os.environ if env is None else env
    if env.get("GROWW_ACCESS_TOKEN"):
        return env["GROWW_ACCESS_TOKEN"]
    try:
        with open(token_file, encoding="utf-8") as fh:
            cached = json.load(fh)
        exp = datetime.datetime.fromisoformat(str(cached.get("expiry", "")).replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=datetime.timezone.utc)
        if cached.get("token") and exp > datetime.datetime.now(datetime.timezone.utc):
            return cached["token"]
    except (OSError, ValueError):
        pass
    raise GrowwError("no Groww token: set GROWW_ACCESS_TOKEN, or GROWW_API_KEY and GROWW_API_SECRET "
                     "and run `groww.py token` (approve the key on the Groww Cloud API Keys page first)")


def save_token(payload, token_file=TOKEN_FILE):
    os.makedirs(os.path.dirname(token_file), exist_ok=True)
    fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"token": payload.get("token"), "expiry": payload.get("expiry")}, fh)


# ---------------------------------------------------------------- symbols and data

def clean_symbol(sym):
    s = str(sym).strip().upper()
    for suf in (".NS", ".BO"):
        if s.endswith(suf):
            s = s[:-len(suf)]
    if not re.match(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$", s):
        raise GrowwError("%r is not a valid NSE trading symbol" % sym)
    return s


def exchange_of(sym):
    return "BSE" if str(sym).strip().upper().endswith(".BO") else "NSE"


def ltp(token, symbols):
    syms = ["%s_%s" % (exchange_of(s), clean_symbol(s)) for s in symbols]
    if not syms or len(syms) > 50:
        raise GrowwError("ltp takes 1 to 50 symbols")
    return call("GET", "/live-data/ltp", token, {"segment": "CASH", "exchange_symbols": ",".join(syms)})


def quote(token, symbol):
    return call("GET", "/live-data/quote", token, {"exchange": exchange_of(symbol), "segment": "CASH",
                                                  "trading_symbol": clean_symbol(symbol)})


def _fmt_ist(utc_naive):
    return (utc_naive + IST_OFFSET).strftime("%Y-%m-%d %H:%M:%S")


def parse_candle_time(v):
    """Epoch seconds, or "2025-09-24T10:30:00" (taken as IST wall clock) -> naive IST datetime."""
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        return datetime.datetime.fromtimestamp(int(v), datetime.timezone.utc).replace(tzinfo=None) + IST_OFFSET
    return datetime.datetime.fromisoformat(str(v).replace(" ", "T")).replace(tzinfo=None)


def candles(token, symbol, start_utc, end_utc, interval="5minute"):
    """-> [{"t": IST datetime, "o","h","l","c","v"}], oldest first."""
    if interval not in MAX_DAYS:
        raise GrowwError("interval must be one of %s" % ", ".join(sorted(MAX_DAYS)))
    if (end_utc - start_utc).days > MAX_DAYS[interval]:
        raise GrowwError("%s candles: at most %d days per request" % (interval, MAX_DAYS[interval]))
    exch = exchange_of(symbol)
    p = call("GET", "/historical/candles", token, {
        "exchange": exch, "segment": "CASH", "groww_symbol": "%s-%s" % (exch, clean_symbol(symbol)),
        "start_time": _fmt_ist(start_utc), "end_time": _fmt_ist(end_utc), "candle_interval": interval})
    out = []
    for c in (p or {}).get("candles") or []:
        if len(c) < 6:
            continue
        out.append({"t": parse_candle_time(c[0]), "o": float(c[1]), "h": float(c[2]), "l": float(c[3]),
                    "c": float(c[4]), "v": int(c[5] or 0)})
    return sorted(out, key=lambda b: b["t"])


def margin(token):
    return call("GET", "/margins/detail/user", token)


def positions(token):
    return call("GET", "/positions/user", token, {"segment": "CASH"})


def order_status(token, order_id=None, reference=None):
    if order_id:
        return call("GET", "/order/status/" + urllib.parse.quote(order_id, safe=""), token, {"segment": "CASH"})
    if reference:
        return call("GET", "/order/status/reference/" + urllib.parse.quote(reference, safe=""), token,
                    {"segment": "CASH"})
    raise GrowwError("give --order-id or --reference")


# ---------------------------------------------------------------- orders

def reference_id(fingerprint, tag="TR"):
    """8-20 alphanumeric, at most two hyphens. Derived from the order fingerprint, so sending the
    same order twice is refused by Groww as a duplicate (GA007) instead of doubling the position."""
    ref = "%s-%s" % (tag, re.sub(r"[^A-Za-z0-9]", "", fingerprint)[:16])
    if not 8 <= len(ref) <= 20:
        raise GrowwError("cannot build an order reference id from %r" % fingerprint)
    return ref


def build_order(order, fingerprint, product="MIS"):
    """Our ticket order -> Groww /order/create body. Refuses what Groww cannot express."""
    if str(order.get("side", "")).lower() not in ("buy", "sell"):
        raise GrowwError("side must be buy or sell")
    typ = str(order.get("type", "market")).lower()
    if typ not in ("market", "limit"):
        raise GrowwError("Groww orders here support market and limit only, not %r" % typ)
    if order.get("notional") is not None and order.get("qty") is None:
        raise GrowwError("Groww needs a share quantity, not a rupee amount")
    try:
        qty = int(float(order["qty"]))
    except (KeyError, TypeError, ValueError):
        raise GrowwError("the order has no usable qty")
    if qty <= 0 or qty != float(order["qty"]):
        raise GrowwError("qty must be a whole number of shares")
    if product not in ("MIS", "CNC"):
        raise GrowwError("product must be MIS or CNC")
    body = {"trading_symbol": clean_symbol(order["symbol"]), "quantity": qty, "validity": "DAY",
            "exchange": exchange_of(order["symbol"]), "segment": "CASH", "product": product,
            "order_type": typ.upper(), "transaction_type": str(order["side"]).upper(),
            "order_reference_id": reference_id(fingerprint)}
    if typ == "limit":
        if not order.get("limit_price"):
            raise GrowwError("a limit order needs limit_price")
        body["price"] = float(order["limit_price"])
    return body


def build_oco(symbol, qty, side, target, stop, product="MIS", ref_seed=""):
    """Exit pair for an OPEN position: `side` is the exit direction (sell closes a long)."""
    qty = int(qty)
    if qty <= 0:
        raise GrowwError("qty must be positive")
    side = side.lower()
    if side not in ("sell", "buy"):
        raise GrowwError("side must be sell (closing a long) or buy (closing a short)")
    target, stop = float(target), float(stop)
    if side == "sell" and not stop < target:
        raise GrowwError("closing a long: the stop must be below the target")
    if side == "buy" and not target < stop:
        raise GrowwError("closing a short: the target must be below the stop")
    sym = clean_symbol(symbol)
    seed = ref_seed or "%s%s%s%s" % (sym, qty, target, stop)
    ref = "OCO-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:14]
    return {"reference_id": ref, "smart_order_type": "OCO", "segment": "CASH", "trading_symbol": sym,
            "quantity": qty, "net_position_quantity": qty if side == "sell" else -qty,
            "transaction_type": side.upper(),
            "target": {"trigger_price": "%.2f" % target, "order_type": "LIMIT", "price": "%.2f" % target},
            "stop_loss": {"trigger_price": "%.2f" % stop, "order_type": "SL_M", "price": None},
            "product_type": product, "exchange": exchange_of(symbol), "duration": "DAY"}


def _ticket():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ticket.py")
    spec = importlib.util.spec_from_file_location("trader_ticket_for_groww", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def may_send(cfg, send):
    """(send_for_real, why_not). Sending needs --send AND "paper": false."""
    if not send:
        return False, "dry run: add --send to place it"
    if cfg.get("paper", True):
        return False, 'dry run: config has "paper": true; set it to false only when you mean real money'
    return True, ""


def cmd_place(a):
    t = _ticket()
    doc = _read(a.orders)
    orders = (doc.get("orders", doc) if isinstance(doc, dict) else doc) if doc else None
    if not isinstance(orders, list) or not orders:
        raise GrowwError("no orders in %s" % a.orders)
    if not 1 <= a.index <= len(orders):
        raise GrowwError("there is no order %d (there are %d)" % (a.index, len(orders)))
    order = orders[a.index - 1]
    cfg = _read(a.config) or {}
    risk, state = _read(a.risk), t.load_state(a.state)
    now = t.parse_now(a.now)
    today = t.to_ist(now).date().isoformat()
    fp = t.fingerprint(order)
    rv, why = t.risk_verdict(risk, fp, today)
    if rv is None or rv.get("verdict") != "PASS":
        raise GrowwError("trader-risk has not passed this exact order today (%s)" % (why or "verdict is not PASS"))
    rec = t.latest(state, fp, today)
    if not rec or rec.get("status") != "approved":
        raise GrowwError("no recorded yes for this order today: run ticket.py approve first")
    body = build_order(order, fp, product=str(cfg.get("groww_product", "MIS")).upper())
    real, note = may_send(cfg, a.send)
    print("Groww order (%s):" % ("LIVE" if real else "NOT SENT"))
    print(json.dumps(body, indent=2))
    if not real:
        print(note)
        return 0
    try:
        payload = call("POST", "/order/create", get_token(), body=body)
    except GrowwError as e:
        if "403" in str(e):
            raise GrowwError("%s. Groww says order placement must come from a static IP you registered at "
                             "groww.in/trade-api/api-keys; a home connection with a changing IP will be refused" % e)
        raise
    oid = (payload or {}).get("groww_order_id", "")
    print("Groww accepted it: id %s, status %s." % (oid, (payload or {}).get("order_status")))
    args = ["result", "--index", str(a.index), "--status", "placed", "--orders", a.orders,
            "--config", a.config, "--risk", a.risk, "--state", a.state]
    if oid:
        args += ["--broker-id", oid]
    if a.now:
        args += ["--now", a.now]
    t.main(args)
    print("When it fills, place the exit pair: groww.py oco --symbol %s --qty %s --side %s --target T --stop S"
          % (body["trading_symbol"], body["quantity"], "sell" if body["transaction_type"] == "BUY" else "buy"))
    return 0


def cmd_oco(a):
    cfg = _read(a.config) or {}
    body = build_oco(a.symbol, a.qty, a.side, a.target, a.stop,
                     product=str(cfg.get("groww_product", "MIS")).upper())
    real, note = may_send(cfg, a.send)
    print("Groww OCO exit (%s):" % ("LIVE" if real else "NOT SENT"))
    print(json.dumps(body, indent=2))
    if not real:
        print(note)
        return 0
    print(json.dumps(call("POST", "/order-advance/create", get_token(), body=body), indent=2))
    return 0


def cmd_cancel(a):
    cfg = _read(a.config) or {}
    real, note = may_send(cfg, a.send)
    if a.oco:
        path, body = "/order-advance/cancel/CASH/OCO/" + urllib.parse.quote(a.id, safe=""), {}
    else:
        path, body = "/order/cancel", {"segment": "CASH", "groww_order_id": a.id}
    print("Cancel %s (%s)" % (a.id, "LIVE" if real else "NOT SENT: " + note))
    if real:
        print(json.dumps(call("POST", path, get_token(), body=body), indent=2))
    return 0


# ---------------------------------------------------------------- cli

def main(argv=None):
    ap = argparse.ArgumentParser(description="Groww Trade API for trader-broker. Writes are dry runs unless --send.")
    sub = ap.add_subparsers(dest="cmd")

    def paths(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/broker.json")
        p.add_argument("--now")

    sub.add_parser("setup", help="create ~/.groww.env for you to fill in (mode 600)")
    sub.add_parser("check", help="confirm the credentials work; prints no secrets")
    sub.add_parser("token", help="make a token from GROWW_API_KEY and GROWW_API_SECRET and cache it")
    p = sub.add_parser("ltp"); p.add_argument("symbols", nargs="+")
    p = sub.add_parser("quote"); p.add_argument("symbol")
    p = sub.add_parser("candles"); p.add_argument("symbol")
    p.add_argument("--days", type=int, default=5); p.add_argument("--interval", default="5minute")
    sub.add_parser("margin")
    sub.add_parser("positions")
    p = sub.add_parser("status"); p.add_argument("--order-id"); p.add_argument("--reference")
    p = sub.add_parser("place"); paths(p); p.add_argument("--index", type=int, required=True)
    p.add_argument("--send", action="store_true")
    p = sub.add_parser("oco"); p.add_argument("--config", default="trader/config.json")
    p.add_argument("--symbol", required=True); p.add_argument("--qty", required=True)
    p.add_argument("--side", default="sell", choices=["sell", "buy"])
    p.add_argument("--target", required=True); p.add_argument("--stop", required=True)
    p.add_argument("--send", action="store_true")
    p = sub.add_parser("cancel"); p.add_argument("id"); p.add_argument("--oco", action="store_true")
    p.add_argument("--config", default="trader/config.json"); p.add_argument("--send", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        if a.cmd == "setup":
            made = setup_env_file()
            print("%s %s" % ("Created" if made else "Already exists:", ENV_FILE))
            print("Open it in a text editor, fill in ONE option, save it. Do not paste the values into any chat.")
            bad = env_file_problem()
            if bad:
                print("Warning: " + bad)
            return 0
        load_env_file()
        if a.cmd == "check":
            have = [k for k in ENV_KEYS if os.environ.get(k)]
            print("Credentials found: %s" % (", ".join(have) if have else "none"))
            tok = get_token()
            ok = True
            for label, fn in (("account margin", lambda: margin(tok)), ("positions", lambda: positions(tok)),
                              ("live prices", lambda: ltp(tok, ["RELIANCE"])),
                              ("5-minute candles", lambda: candles(
                                  tok, "RELIANCE", datetime.datetime.utcnow() - datetime.timedelta(days=3),
                                  datetime.datetime.utcnow()))):
                try:
                    out = fn()
                    extra = ""
                    if label == "account margin":
                        extra = ": cash ₹%s, MIS balance ₹%s" % (
                            out.get("clear_cash"), (out.get("equity_margin_details") or {}).get("mis_balance_available"))
                    print("  OK      %s%s" % (label, extra))
                except GrowwError as e:
                    ok = False
                    print("  FAILED  %s: %s" % (label, e))
            if not ok:
                print("Some calls failed. Account calls and data calls can be permitted separately on Groww.")
            return 0 if ok else 1
        if a.cmd == "token":
            key, secret = os.environ.get("GROWW_API_KEY"), os.environ.get("GROWW_API_SECRET")
            if not key or not secret:
                raise GrowwError("set GROWW_API_KEY and GROWW_API_SECRET in the environment first")
            payload = make_token(key, secret)
            save_token(payload)
            print("Token saved to %s, expires %s." % (TOKEN_FILE, payload.get("expiry")))
        elif a.cmd == "ltp":
            for k, v in sorted((ltp(get_token(), a.symbols) or {}).items()):
                print("%-18s ₹%s" % (k, v))
        elif a.cmd == "quote":
            print(json.dumps(quote(get_token(), a.symbol), indent=2))
        elif a.cmd == "candles":
            end = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)
            for b in candles(get_token(), a.symbol, end - datetime.timedelta(days=a.days), end, a.interval):
                print("%s  O %.2f H %.2f L %.2f C %.2f V %d" % (b["t"].strftime("%Y-%m-%d %H:%M"), b["o"],
                                                              b["h"], b["l"], b["c"], b["v"]))
        elif a.cmd == "margin":
            print(json.dumps(margin(get_token()), indent=2))
        elif a.cmd == "positions":
            print(json.dumps(positions(get_token()), indent=2))
        elif a.cmd == "status":
            print(json.dumps(order_status(get_token(), a.order_id, a.reference), indent=2))
        elif a.cmd == "place":
            return cmd_place(a)
        elif a.cmd == "oco":
            return cmd_oco(a)
        elif a.cmd == "cancel":
            return cmd_cancel(a)
        return 0
    except GrowwError as e:
        print("groww.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
