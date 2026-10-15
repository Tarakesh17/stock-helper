"""
Short Telegram message for each daily run: only the main info, with a link
to the website for charts and stats.

Setup (one time):
  1. Message @BotFather -> /newbot -> copy the token
  2. Message your bot once, then open
     https://api.telegram.org/bot<TOKEN>/getUpdates and read chat.id
  3. Daily run on GitHub:  gh secret set TELEGRAM_BOT_TOKEN
                           gh secret set TELEGRAM_CHAT_ID
     Local runs: copy .env.example to .env and fill both values

Resend the latest summary:  python bot_send.py   (reads docs/data.json)
"""

import datetime
import html
import json
import os
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
DATA_PATH = os.path.join(HERE, "docs", "data.json")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def load_env(path=None):
    """Load KEY=VALUE lines from a .env file into os.environ (no-op if missing).
    Variables already set in the real environment win over the file."""
    path = path or ENV_PATH
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, sep, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not sep or not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            os.environ.setdefault(key, value)


def telegram_config():
    """(token, chat_id) from the environment or .env, or None if either is missing."""
    load_env()
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    return (token, chat_id) if token and chat_id else None


def send(text, token, chat_id):
    """Post an HTML-formatted message with the Bot API's sendMessage (stdlib only)."""
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": json.dumps({"is_disabled": True}),
    }).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30).close()
    except urllib.error.HTTPError as exc:
        # Telegram explains the problem (bad token, wrong chat id) in the body
        try:
            reason = json.load(exc).get("description")
        except ValueError:
            reason = None
        raise SystemExit(f"Telegram error {exc.code}: {reason or exc.reason}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"Telegram unreachable: {exc.reason}") from None


# ---------------------------------------------------------------- message ----


def _day(text):
    date = datetime.date.fromisoformat(text)
    return f"{date.day} {MONTHS[date.month - 1]}"


def _short(symbol):
    return symbol[:-3] if symbol.endswith(".NS") else symbol


def _crore(amount):
    return f"Rs {abs(amount) / 1e7:,.1f} Cr"


def format_message(data):
    """The daily summary as Telegram HTML: main info only, details on the website."""
    stocks = data.get("stocks") or []
    lines = [f"<b>NSE signals · {_day(data['as_of'])}</b>"]
    if not data.get("final", True):
        lines.append("<i>Market still open: these values change until the close.</i>")

    width = max([len(_short(stock["symbol"])) for stock in stocks] + [4])
    table = []
    for stock in stocks:
        was = stock.get("prev_signal")
        note = f"  (was {was})" if was and was != stock["signal"] else ""
        table.append(f"{_short(stock['symbol']):<{width}} {stock['price']:>9.2f} "
                     f"{stock['change_pct']:>+6.1f}%  {stock['signal']}{note}")
    lines.append("<pre>" + html.escape("\n".join(table)) + "</pre>" if table else "No price data.")

    chances = [f"{_short(stock['symbol'])} {stock['p_up']:.0%}" for stock in stocks if stock.get("p_up") is not None]
    if chances:
        lines.append(html.escape("Model up-chance, next day: " + " · ".join(chances)))
    last_day = ((data.get("model") or {}).get("live") or {}).get("last_day")
    if last_day:
        lines.append(f"Model on {_day(last_day['date'])}: {last_day['right']} of {last_day['total']} calls right")

    live = (data.get("agents") or {}).get("live")
    if live:
        parts = [f"{agent['name']} {agent['return_pct']:+.1f}%" for agent in live["agents"]]
        if live.get("benchmark"):
            parts.append(f"{live['benchmark']['name']} {live['benchmark']['return_pct']:+.1f}%")
        lines.append(html.escape(f"Sim since {_day(live['start'])}: " + " · ".join(parts)))

    latest = (data.get("deals") or {}).get("latest") or {}
    net = {}
    for deal in latest.get("deals") or []:
        if deal.get("in_watchlist"):
            total = net.setdefault(deal["symbol"], [0, 0])
            total[0] += deal["value"] if deal["side"] == "BUY" else -deal["value"]
            total[1] += 1
    if net:
        parts = [f"{symbol} net {'buy' if amount >= 0 else 'sell'} {_crore(amount)} ({count} deals)"
                 for symbol, (amount, count) in net.items()]
        lines.append(html.escape(f"Big deals {_day(latest['date'])}: " + "; ".join(parts)))

    problems = list(data.get("issues") or []) + [f"{item['symbol']}: {item['error']}"
                                                 for item in data.get("failed") or []]
    if problems:
        lines.append(html.escape(("Issues: " + "; ".join(problems))[:600]))
    url = (data.get("site") or {}).get("url")
    if url:
        lines.append(f'<a href="{html.escape(url)}">Charts, stats and details</a>')
    return "\n".join(lines)


def main():
    config = telegram_config()
    if not config:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (GitHub secrets, env vars or .env) first.")
    try:
        with open(DATA_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise SystemExit("No docs/data.json yet: run  python run.py  first.") from None
    text = format_message(data)
    print(text)
    send(text, *config)


if __name__ == "__main__":
    main()
