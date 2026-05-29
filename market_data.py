#!/usr/bin/env python3
"""
Fetch market data for the watchlist and write it to data/latest.json.

Runs on GitHub Actions. The committed JSON is the "bridge": a Claude routine
reads it, writes the analysis, and posts the brief to Slack. This script does
NOT do any analysis or delivery itself — it only gathers data.

Required environment variable (GitHub repo secret):
  FINNHUB_API_KEY - free key from https://finnhub.io
"""

import os
import sys
import time
import json
import datetime as dt

import requests

FINNHUB_KEY = os.environ["FINNHUB_API_KEY"]

# Edit your watchlist here:
WATCHLIST = [
    "AAPL", "MSFT", "GOOG", "NFLX", "TSLA",
    "NVDA", "AMD", "AVGO", "MU", "INTC", "MRVL", "ARM", "QCOM",
    "ALAB", "LITE", "TER", "SNDK", "DRAM", "NOK",
    "PLTR", "ANET", "NBIS", "SNOW", "FIG", "UMAC", "RVI", "ROBO",
]

TONE_PROXIES     = {"SPY": "S&P 500", "QQQ": "Nasdaq 100"}  # ETF proxies for index direction
TOP_MOVERS_COUNT = 6
BASE             = "https://finnhub.io/api/v1"
DELAY            = 0.8       # seconds between calls (stay under 60/min free limit)
DATA_DIR         = "data"   # writes both data/latest.json and data/YYYY-MM-DD.json


def quote(symbol):
    """Finnhub quote dict (c=current, dp=pct change, pc=prev close) or None."""
    try:
        r = requests.get(f"{BASE}/quote",
                         params={"symbol": symbol, "token": FINNHUB_KEY}, timeout=15)
        r.raise_for_status()
        d = r.json()
        if not d or (d.get("c", 0) == 0 and d.get("pc", 0) == 0):
            return None  # unknown / unsupported symbol
        return d
    except Exception as e:
        print(f"quote error {symbol}: {e}", file=sys.stderr)
        return None


def news(symbol, days_back=2, limit=2):
    """Up to `limit` recent {headline, url} items for a symbol."""
    today = dt.date.today()
    frm = today - dt.timedelta(days=days_back)
    try:
        r = requests.get(f"{BASE}/company-news",
                         params={"symbol": symbol, "from": frm.isoformat(),
                                 "to": today.isoformat(), "token": FINNHUB_KEY}, timeout=15)
        r.raise_for_status()
        items = r.json() or []
        items.sort(key=lambda x: x.get("datetime", 0), reverse=True)
        return [{"headline": n.get("headline", "").strip(), "url": n.get("url", "")}
                for n in items[:limit] if n.get("headline")]
    except Exception as e:
        print(f"news error {symbol}: {e}", file=sys.stderr)
        return []


def usd_cad():
    """USD->CAD from the free, no-key Frankfurter API."""
    try:
        r = requests.get("https://api.frankfurter.app/latest",
                         params={"from": "USD", "to": "CAD"}, timeout=15)
        r.raise_for_status()
        return r.json().get("rates", {}).get("CAD")
    except Exception as e:
        print(f"fx error: {e}", file=sys.stderr)
        return None


def main():
    # Index tone via ETF proxies
    tone = {}
    for sym in TONE_PROXIES:
        q = quote(sym); time.sleep(DELAY)
        if q:
            tone[sym] = round(q.get("dp", 0), 2)

    # Watchlist quotes
    watch, no_data = {}, []
    for sym in WATCHLIST:
        q = quote(sym); time.sleep(DELAY)
        if q:
            watch[sym] = {"pct": round(q.get("dp", 0), 2), "price": round(q.get("c", 0), 2)}
        else:
            no_data.append(sym)

    # Top movers + their news
    ranked = sorted(watch.items(), key=lambda kv: abs(kv[1]["pct"]), reverse=True)
    movers = []
    for sym, v in ranked[:TOP_MOVERS_COUNT]:
        movers.append({"symbol": sym, "pct": v["pct"], "price": v["price"],
                       "news": news(sym)})
        time.sleep(DELAY)

    fx = usd_cad()

    data = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "tone": tone,
        "tone_labels": TONE_PROXIES,
        "movers_ranked": movers,
        "watchlist": watch,
        "no_data": no_data,
        "usd_cad": round(fx, 4) if fx else None,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()  # e.g. 2026-05-28
    dated_path  = os.path.join(DATA_DIR, f"{today}.json")
    latest_path = os.path.join(DATA_DIR, "latest.json")
    payload = json.dumps(data, indent=2)
    for path in (dated_path, latest_path):
        with open(path, "w") as f:
            f.write(payload)
    print(f"Wrote {dated_path} and {latest_path}: "
          f"{len(watch)} quotes, {len(movers)} movers, {len(no_data)} no-data.")


if __name__ == "__main__":
    main()
