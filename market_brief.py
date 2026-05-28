#!/usr/bin/env python3
"""
Morning market brief.

Fetches quotes + recent news for your watchlist via Finnhub, USD/CAD via the
free Frankfurter API, formats a brief, and emails it to you. Designed to run on
GitHub Actions on a schedule (no server needed).

Required environment variables (set as GitHub repo secrets):
  FINNHUB_API_KEY     - free key from https://finnhub.io
  EMAIL_ADDRESS       - the Gmail address you send FROM
  EMAIL_APP_PASSWORD  - a Gmail App Password (NOT your normal password)
  EMAIL_TO            - where to send the brief (can be the same address)
"""

import os
import sys
import time
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

# ---------------------------- Config ----------------------------
FINNHUB_KEY        = os.environ["FINNHUB_API_KEY"]
EMAIL_ADDRESS      = os.environ["EMAIL_ADDRESS"]
EMAIL_APP_PASSWORD = os.environ["EMAIL_APP_PASSWORD"]
EMAIL_TO           = os.environ.get("EMAIL_TO", EMAIL_ADDRESS)

# Edit your watchlist here (just add/remove tickers):
WATCHLIST = [
    "AAPL", "MSFT", "GOOG", "NFLX", "TSLA",
    "NVDA", "AMD", "AVGO", "MU", "INTC", "MRVL", "ARM", "QCOM",
    "ALAB", "LITE", "TER", "SNDK", "DRAM", "NOK",
    "PLTR", "ANET", "NBIS", "SNOW", "FIG", "UMAC", "RVI", "ROBO",
]

# ETFs used as a proxy for index direction (index quotes need a paid tier):
TONE_PROXIES     = {"SPY": "S&P 500 (SPY)", "QQQ": "Nasdaq 100 (QQQ)"}
TOP_MOVERS_COUNT = 6
FINNHUB_BASE     = "https://finnhub.io/api/v1"
CALL_DELAY       = 0.8  # seconds between calls, to stay under 60/min on free tier


# ---------------------------- Finnhub helpers ----------------------------
def finnhub_quote(symbol):
    """Return Finnhub quote dict (c=current, dp=pct change, pc=prev close) or None."""
    try:
        r = requests.get(f"{FINNHUB_BASE}/quote",
                         params={"symbol": symbol, "token": FINNHUB_KEY}, timeout=15)
        r.raise_for_status()
        d = r.json()
        # Finnhub returns c=0, pc=0 for unknown / unsupported symbols:
        if not d or (d.get("c", 0) == 0 and d.get("pc", 0) == 0):
            return None
        return d
    except Exception as e:
        print(f"quote error {symbol}: {e}", file=sys.stderr)
        return None


def finnhub_news(symbol, days_back=2, limit=2):
    """Return up to `limit` most recent news items for a symbol."""
    today = dt.date.today()
    frm = today - dt.timedelta(days=days_back)
    try:
        r = requests.get(f"{FINNHUB_BASE}/company-news",
                         params={"symbol": symbol, "from": frm.isoformat(),
                                 "to": today.isoformat(), "token": FINNHUB_KEY},
                         timeout=15)
        r.raise_for_status()
        items = r.json() or []
        items.sort(key=lambda x: x.get("datetime", 0), reverse=True)
        return items[:limit]
    except Exception as e:
        print(f"news error {symbol}: {e}", file=sys.stderr)
        return []


def usd_cad():
    """USD->CAD rate from the free, no-key Frankfurter API."""
    try:
        r = requests.get("https://api.frankfurter.app/latest",
                         params={"from": "USD", "to": "CAD"}, timeout=15)
        r.raise_for_status()
        return r.json().get("rates", {}).get("CAD")
    except Exception as e:
        print(f"fx error: {e}", file=sys.stderr)
        return None


def fmt_pct(dp):
    if dp is None:
        return "n/a"
    return f"{'+' if dp >= 0 else ''}{dp:.2f}%"


# ---------------------------- Build + send ----------------------------
def build_brief():
    today_str = dt.date.today().strftime("%A, %B %d, %Y")

    # Market tone (ETF proxies)
    tone_bits = []
    for sym, label in TONE_PROXIES.items():
        q = finnhub_quote(sym); time.sleep(CALL_DELAY)
        if q:
            tone_bits.append(f"{label} {fmt_pct(q.get('dp'))}")
    tone_line = " &nbsp;·&nbsp; ".join(tone_bits) if tone_bits else "data unavailable"

    # Watchlist quotes
    quotes, no_data = {}, []
    for sym in WATCHLIST:
        q = finnhub_quote(sym); time.sleep(CALL_DELAY)
        (quotes.__setitem__(sym, q) if q else no_data.append(sym))

    # Rank movers by absolute % change
    movers = sorted(quotes.items(), key=lambda kv: abs(kv[1].get("dp", 0)), reverse=True)
    top = movers[:TOP_MOVERS_COUNT]

    # News for top movers only
    news_map = {}
    for sym, _ in top:
        news_map[sym] = finnhub_news(sym); time.sleep(CALL_DELAY)

    fx = usd_cad()

    # Assemble HTML
    h = [f"<h2>Market Brief &mdash; {today_str}</h2>",
         f"<p><b>Market tone:</b> {tone_line}</p>",
         "<p><b>Top movers</b></p><ul>"]
    for sym, q in top:
        links = [f'<a href="{n.get("url","")}">{n.get("headline","").strip()}</a>'
                 for n in news_map.get(sym, []) if n.get("headline")]
        news_str = "".join(f"<br>&nbsp;&nbsp;&#8627; {l}" for l in links)
        h.append(f"<li><b>{sym}</b> {fmt_pct(q.get('dp'))} "
                 f"(${q.get('c'):.2f}){news_str}</li>")
    h.append("</ul><p><b>Watchlist scan</b></p><ul>")
    for sym, q in sorted(quotes.items()):
        h.append(f"<li>{sym}: {fmt_pct(q.get('dp'))} (${q.get('c'):.2f})</li>")
    h.append("</ul>")
    if no_data:
        h.append(f"<p><b>No data (check ticker):</b> {', '.join(no_data)}</p>")
    if fx:
        h.append(f"<p><b>FX:</b> USD/CAD {fx:.4f}</p>")
    h.append("<p style='color:#888;font-size:12px'>Quotes via Finnhub free tier "
             "(may be ~15&ndash;20 min delayed). Indicative only, not investment advice.</p>")

    return f"Market Brief — {today_str}", "\n".join(h)


def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, EMAIL_ADDRESS, EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
        server.sendmail(EMAIL_ADDRESS, [EMAIL_TO], msg.as_string())
    print(f"Sent brief to {EMAIL_TO}")


if __name__ == "__main__":
    subject, body = build_brief()
    send_email(subject, body)
