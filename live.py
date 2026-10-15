"""
Live tracking during market hours. GitHub runs this every 15 minutes on
weekdays (.github/workflows/live.yml); it does nothing outside 09:10-16:00 IST.

Each run:
  1. reads the watchlist, the price-only list and your price levels
     (live_config.json, edited from the website's Alerts and Holdings pages),
  2. gets live prices for all of them in one or two batched Yahoo requests,
  3. works out what each watchlist stock's rule signal would be if the market
     closed now. That is provisional: the official signal comes at 6 PM,
  4. sends one Telegram message with any new alerts: provisional signal
     changes, big moves (2%, 4%, 6% on the day) and your price levels,
  5. writes live.json for the website (published to the live-data branch).

Each alert goes out once per day; a price level fires only once (add it again
to re-arm it). Simulation and learning only: nothing here places orders.

  python live.py      one run now (outside market hours: prices only, no alerts)
"""

import datetime
import html
import json
import os
import re

import bot_send
import run
import watch

CONFIG_PATH = os.path.join(watch.HERE, "live_config.json")
OUT_DIR = os.environ.get("LIVE_DIR") or os.path.join(os.environ.get("RUNNER_TEMP") or watch.HERE, "live")
SPARK_URL = "https://query1.finance.yahoo.com/v7/finance/spark"
SPARK_BATCH = 20         # Yahoo allows at most 20 symbols per request
INDEX = "^NSEI"
MOVE_LEVELS = (2, 4, 6)  # % up or down on the day
MAX_TRACK = 25
MAX_LEVELS = 50
LOG_KEEP = 60

OPEN, CLOSE = datetime.time(9, 15), datetime.time(15, 30)
ALERT_UNTIL = datetime.time(15, 45)  # one last check just after the close
RUN_FROM, RUN_UNTIL = datetime.time(9, 10), datetime.time(16, 0)
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def short(symbol):
    return symbol[:-3] if symbol.endswith(".NS") else symbol


def inr(amount):
    """1234567.5 -> '₹12,34,567.50' (Indian digit grouping)."""
    whole, frac = f"{abs(amount):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ("-" if amount < 0 else "") + "₹" + ",".join(groups + [tail]) + "." + frac


# ----------------------------------------------------------------- config ----


def load_config(path=None):
    """{"track": [symbols], "levels": [{id, symbol, direction, price}]} from live_config.json."""
    try:
        with open(path or CONFIG_PATH, encoding="utf-8-sig") as handle:
            raw = json.load(handle)
    except (FileNotFoundError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    track = []
    for item in raw.get("track") or []:
        symbol = str(item).strip().upper()
        if watch.SYMBOL_PATTERN.match(symbol) and symbol not in track:
            track.append(symbol)
    levels = []
    for item in raw.get("levels") or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip().upper()
        price = item.get("price")
        if (ID_PATTERN.match(str(item.get("id", ""))) and watch.SYMBOL_PATTERN.match(symbol)
                and item.get("direction") in ("above", "below")
                and isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0):
            levels.append({"id": item["id"], "symbol": symbol, "direction": item["direction"],
                           "price": float(price)})
    return {"track": track[:MAX_TRACK], "levels": levels[:MAX_LEVELS]}


# ------------------------------------------------------------------ fetch ----


def fetch_quotes(symbols):
    """Live price and today's 15-minute path per symbol, in batches of 20."""
    from curl_cffi import requests  # imported lazily so the logic below stays testable

    quotes, errors = {}, []
    for start in range(0, len(symbols), SPARK_BATCH):
        batch = symbols[start:start + SPARK_BATCH]
        try:
            response = requests.get(SPARK_URL, impersonate="chrome", timeout=30,
                                    params={"symbols": ",".join(batch), "range": "1d", "interval": "15m"})
            results = (response.json().get("spark") or {}).get("result") or []
        except Exception as exc:  # network trouble: try again next run
            errors.append(f"live prices: {exc}")
            continue
        for item in results:
            try:
                quotes[item["symbol"]] = parse_quote(item["response"][0])
            except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError):
                pass
    missing = [symbol for symbol in symbols if symbol not in quotes]
    if missing:
        errors.append("no live data for " + ", ".join(missing))
    return quotes, errors


def parse_quote(response):
    meta = response["meta"]
    offset = meta.get("gmtoffset") or 0
    price = float(meta["regularMarketPrice"])
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    stamp = datetime.datetime.fromtimestamp(meta["regularMarketTime"] + offset, datetime.timezone.utc)
    closes = ((response.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    path = []
    for moment, close in zip(response.get("timestamp") or [], closes):
        when = datetime.datetime.fromtimestamp(moment + offset, datetime.timezone.utc)
        if close is not None and when.date() == stamp.date():
            path.append([when.strftime("%H:%M"), round(close, 2)])
    high, low = meta.get("regularMarketDayHigh"), meta.get("regularMarketDayLow")
    return {
        "name": meta.get("longName") or meta.get("shortName") or meta.get("symbol"),
        "price": round(price, 2),
        "prev_close": round(float(previous), 2) if previous else None,
        "change_pct": round((price / float(previous) - 1) * 100, 2) if previous else None,
        "high": round(high, 2) if high else None,
        "low": round(low, 2) if low else None,
        "date": stamp.date().isoformat(),  # the session these prices belong to
        "time": stamp.strftime("%H:%M"),   # last trade, IST
        "path": path,
    }


def closes_before(symbol, today):
    """[[date, close], ...] of final daily candles before `today` (one request, once a day)."""
    return [[bar["date"], bar["close"]] for bar in watch.fetch_chart(symbol)["bars"] if bar["date"] < today]


# --------------------------------------------------------------- signals ----


def provisional(history, quote, today):
    """The official (last final) signal and the one the rule gives if today's
    live price were the close: the same maths the 6 PM run uses."""
    bars = [{"date": day, "close": close} for day, close in history]
    traded_today = quote is not None and quote["date"] == today
    if traded_today:
        bars.append({"date": today, "close": quote["price"]})
    if len(bars) < watch.SMA_SLOW + 2:
        return None
    series = watch.indicators(bars)
    return {
        "official": series["signal"][-2] if traded_today else series["signal"][-1],
        "signal": series["signal"][-1],
        "sma_fast": round(series["sma_fast"][-1], 2),
        "sma_slow": round(series["sma_slow"][-1], 2),
        "rsi": round(series["rsi"][-1], 1),
    }


# ----------------------------------------------------------------- alerts ----


def fresh_memory(memory, today):
    """Daily alert memory; everything except fired price levels resets each day."""
    memory = dict(memory or {})
    if memory.get("day") != today:
        memory.update(day=today, moves={}, signals={}, log=[])
    memory.setdefault("levels", {})
    return memory


def find_alerts(quotes, signals, levels, memory, today, movers):
    """Alerts that haven't been sent yet, as [{kind, symbol, text, ...}]."""
    alerts = []
    for symbol, info in signals.items():
        quote = quotes.get(symbol)
        if not info or quote is None or quote["date"] != today or info["signal"] == info["official"]:
            continue
        if info["signal"] not in memory["signals"].get(symbol, []):
            alerts.append({"kind": "signal", "symbol": symbol, "signal": info["signal"], "text": (
                f"{short(symbol)}: the rule would say {info['signal']} if the market closed now "
                f"(official: {info['official']}), at {inr(quote['price'])}. Provisional until the close.")})
    for symbol in sorted(movers):
        quote = quotes.get(symbol)
        if quote is None or quote["date"] != today or quote["change_pct"] is None:
            continue
        change = quote["change_pct"]
        direction = "up" if change > 0 else "down"
        level = max((step for step in MOVE_LEVELS if abs(change) >= step), default=0)
        if level > memory["moves"].get(symbol, {}).get(direction, 0):
            alerts.append({"kind": "move", "symbol": symbol, "direction": direction, "level": level,
                           "text": f"{short(symbol)} is {direction} {abs(change):.1f}% today, at {inr(quote['price'])}."})
    for item in levels:
        quote = quotes.get(item["symbol"])
        if item["id"] in memory["levels"] or quote is None or quote["date"] != today:
            continue
        above = item["direction"] == "above"
        if (quote["price"] >= item["price"]) if above else (quote["price"] <= item["price"]):
            alerts.append({"kind": "level", "symbol": item["symbol"], "id": item["id"], "price": quote["price"],
                           "text": (f"{short(item['symbol'])} is {item['direction']} your level "
                                    f"{inr(item['price'])}: now {inr(quote['price'])}.")})
    return alerts


def remember(alerts, memory, now):
    """Mark alerts as sent so they don't repeat."""
    for alert in alerts:
        if alert["kind"] == "signal":
            memory["signals"].setdefault(alert["symbol"], []).append(alert["signal"])
        elif alert["kind"] == "move":
            memory["moves"].setdefault(alert["symbol"], {})[alert["direction"]] = alert["level"]
        else:
            memory["levels"][alert["id"]] = {"at": now.strftime("%Y-%m-%d %H:%M"), "price": alert["price"]}
        memory["log"].append({"time": now.strftime("%H:%M"), "kind": alert["kind"],
                              "symbol": alert["symbol"], "text": alert["text"]})
    del memory["log"][:-LOG_KEEP]


def format_message(alerts, now, url):
    """One Telegram message (HTML) for all of a run's alerts."""
    lines = [f"<b>Live alerts · {now:%H:%M} IST</b>"]
    lines += ["• " + html.escape(alert["text"]) for alert in alerts[:20]]
    if len(alerts) > 20:
        lines.append(f"…and {len(alerts) - 20} more on the website")
    if url:
        lines.append(f'<a href="{html.escape(url)}">Live prices and details</a>')
    return "\n".join(lines)


# ------------------------------------------------------------------- main ----


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, ValueError):
        return None


def main():
    now = watch.now_ist()
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and not (
            now.weekday() < 5 and RUN_FROM <= now.time() <= RUN_UNTIL):
        print(f"outside market hours ({now:%a %H:%M} IST): nothing to do")
        return 0
    today = now.date().isoformat()
    symbols, problems = watch.load_watchlist()
    config = load_config()
    state = _read_json(os.path.join(OUT_DIR, "state.json")) or {}
    memory = fresh_memory(state.get("memory"), today)
    wanted = list(dict.fromkeys(symbols + config["track"] + [item["symbol"] for item in config["levels"]] + [INDEX]))
    quotes, errors = fetch_quotes(wanted)
    errors = problems + errors

    # daily history for the provisional signals: fetched once per day per stock
    cache = state.get("history") or {}
    if cache.get("day") != today:
        cache = {"day": today, "closes": {}}
    signals = {}
    for symbol in symbols:
        if symbol not in cache["closes"]:
            try:
                cache["closes"][symbol] = closes_before(symbol, today)
            except Exception as exc:
                errors.append(f"{symbol} history: {exc}")
                continue
        signals[symbol] = provisional(cache["closes"][symbol], quotes.get(symbol), today)
    cache["closes"] = {symbol: rows for symbol, rows in cache["closes"].items() if symbol in symbols}

    traded_today = any(quote["date"] == today for quote in quotes.values())
    in_hours = now.weekday() < 5 and OPEN <= now.time() <= ALERT_UNTIL
    movers = set(symbols) | set(config["track"])
    alerts = find_alerts(quotes, signals, config["levels"], memory, today, movers) if traded_today and in_hours else []
    level_ids = {item["id"] for item in config["levels"]}
    memory["levels"] = {key: value for key, value in memory["levels"].items() if key in level_ids}

    telegram = "no new alerts"
    if alerts:
        settings = bot_send.telegram_config()
        if not settings:
            telegram = "not set up"
            remember(alerts, memory, now)
        else:
            try:
                bot_send.send(format_message(alerts, now, run.site_info()["url"]), *settings)
                telegram = f"sent {len(alerts)} alert(s) at {now:%H:%M}"
                remember(alerts, memory, now)
            except SystemExit as exc:  # not remembered, so the next run tries again
                telegram = f"failed: {exc}"
                errors.append(telegram)

    live = {
        "updated_at": now.isoformat(timespec="seconds"),
        "market": "open" if traded_today and now.weekday() < 5 and OPEN <= now.time() <= CLOSE else "closed",
        "session": max((quote["date"] for quote in quotes.values()), default=None),
        "quotes": {symbol: dict(quote, watch=symbol in symbols, **(signals.get(symbol) or {}))
                   for symbol, quote in quotes.items() if symbol != INDEX},
        "index": dict(quotes[INDEX], name="NIFTY 50") if INDEX in quotes else None,
        "track": config["track"],
        "levels": [dict(item, fired=memory["levels"].get(item["id"])) for item in config["levels"]],
        "move_levels": list(MOVE_LEVELS),
        "alerts_today": list(reversed(memory["log"])),
        "telegram": telegram,
        "errors": errors,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    watch.save_text(os.path.join(OUT_DIR, "live.json"), json.dumps(live, separators=(",", ":")))
    watch.save_text(os.path.join(OUT_DIR, "state.json"),
                    json.dumps({"memory": memory, "history": cache}, separators=(",", ":")))

    print(f"--- live {now:%Y-%m-%d %H:%M} IST · market {live['market']} · {len(quotes)} quotes ---")
    for symbol in symbols:
        quote, info = quotes.get(symbol), signals.get(symbol) or {}
        if quote:
            print(f"{symbol:<14} {quote['price']:>10.2f} {quote['change_pct'] or 0:+6.2f}%  "
                  f"official {info.get('official')}  if closed now {info.get('signal')}")
    for alert in alerts:
        print("alert:", alert["text"])
    print("telegram:", telegram)
    for line in errors:
        print(("::warning::" if os.environ.get("GITHUB_ACTIONS") else "warning: ") + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
