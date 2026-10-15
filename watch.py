"""
Daily NSE signal engine for a simulation-trading app.

Reads daily candles from Yahoo Finance, computes SMA-20 / SMA-50 / RSI-14 and
emits a mechanical BUY / SELL / HOLD label per symbol for the latest close.
These labels are raw indicator output for a simulator, not recommendations.

The watchlist lives in watchlist.json (the website edits it).
Needs one library:  pip install -r requirements.txt   (curl_cffi)
Quick report:       python watch.py    (prints only; changes no files)
Full pipeline:      python run.py      (see run.py)
"""

import csv
import datetime
import json
import os
import re
from urllib.parse import quote

# ---------------------------------------------------------------- config ----

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HERE, "state")
WATCHLIST_PATH = os.path.join(HERE, "watchlist.json")
LOG_PATH = os.path.join(STATE_DIR, "paper.csv")

DEFAULT_WATCHLIST = ["RELIANCE.NS", "INFY.NS", "TCS.NS"]
MAX_SYMBOLS = 25
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9&_-]{0,19}\.(NS|BO)$")

HISTORY_RANGE = "2y"  # SMA-50 warm-up plus two years of training data for the model
SMA_FAST = 20
SMA_SLOW = 50
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
FINAL_AFTER = datetime.time(16, 0)  # NSE shuts at 15:30; today's candle is final after this
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"

# -------------------------------------------------------------- watchlist ----


def load_watchlist(path=None):
    """(symbols, problems) from watchlist.json, or the default list if it's missing."""
    try:
        with open(path or WATCHLIST_PATH, encoding="utf-8-sig") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return list(DEFAULT_WATCHLIST), []
    except ValueError as exc:
        return list(DEFAULT_WATCHLIST), [f"watchlist.json is not valid JSON ({exc}); using the default list"]

    items = raw.get("symbols") if isinstance(raw, dict) else raw
    symbols, problems = [], []
    for item in items if isinstance(items, list) else []:
        symbol = str(item).strip().upper()
        if not SYMBOL_PATTERN.match(symbol):
            problems.append(f"skipped {str(item)[:30]!r}: use the Yahoo form, like RELIANCE.NS")
        elif symbol not in symbols:
            symbols.append(symbol)
    if len(symbols) > MAX_SYMBOLS:
        problems.append(f"only the first {MAX_SYMBOLS} symbols are tracked")
        symbols = symbols[:MAX_SYMBOLS]
    if not symbols:
        problems.append("watchlist.json has no valid symbols; using the default list")
        symbols = list(DEFAULT_WATCHLIST)
    return symbols, problems


# ------------------------------------------------------------------ files ----


def save_text(path, text):
    """Write `text` to `path` atomically, only if it changed. True if written."""
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            if handle.read() == text:
                return False
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.replace(tmp_path, path)  # a crash can't leave a half-written file
    return True


# ------------------------------------------------------------------- time ----


def now_ist():
    return datetime.datetime.now(IST)


def is_final(date_text, now=None):
    """True once a daily candle can't change any more (its session has closed)."""
    now = now or now_ist()
    today = now.date().isoformat()
    return date_text < today or (date_text == today and now.time() >= FINAL_AFTER)


# ------------------------------------------------------------------ fetch ----


def fetch_chart(symbol, range_=HISTORY_RANGE):
    """Daily candles from Yahoo's chart API, oldest first.

    Returns {"meta": {"name", "currency", "high52", "low52"},
             "bars": [{"date", "open", "high", "low", "close", "volume"}, ...]}.
    Yahoo turns away plain Python and curl requests (HTTP 429), so this uses
    curl_cffi, which connects the way a Chrome browser does.
    """
    from curl_cffi import requests  # imported lazily so the math below stays testable

    response = requests.get(
        CHART_URL + quote(symbol),
        params={"range": range_, "interval": "1d"},
        impersonate="chrome",
        timeout=30,
    )
    try:
        chart = response.json().get("chart") or {}
    except ValueError:  # not JSON, e.g. a rate-limit page
        chart = {}
    if not chart.get("result"):
        error = chart.get("error") or {}
        raise ValueError(error.get("description") or f"Yahoo returned HTTP {response.status_code}")

    result = chart["result"][0]
    meta = result.get("meta") or {}
    offset = meta.get("gmtoffset") or 0  # exchange offset in seconds (IST = 19800)
    candles = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    by_date = {}
    for i, stamp in enumerate(result.get("timestamp") or []):
        close = _at(candles, "close", i)
        if close is None:  # Yahoo leaves gaps as null
            continue
        day = datetime.datetime.fromtimestamp(stamp + offset, datetime.timezone.utc).date().isoformat()
        prices = {key: _at(candles, key, i) for key in ("open", "high", "low")}
        by_date[day] = {  # a repeated date (live candle + daily candle) keeps the later one
            "date": day,
            "open": float(close if prices["open"] is None else prices["open"]),
            "high": float(close if prices["high"] is None else prices["high"]),
            "low": float(close if prices["low"] is None else prices["low"]),
            "close": float(close),
            "volume": int(_at(candles, "volume", i) or 0),
        }
    return {
        "meta": {
            "name": meta.get("longName") or meta.get("shortName") or symbol,
            "currency": meta.get("currency") or "INR",
            "high52": meta.get("fiftyTwoWeekHigh"),
            "low52": meta.get("fiftyTwoWeekLow"),
        },
        "bars": [by_date[day] for day in sorted(by_date)],
    }


def _at(candles, key, i):
    column = candles.get(key) or []
    return column[i] if i < len(column) else None


# ------------------------------------------------------------- indicators ----


def sma_series(values, period):
    """Simple moving average for every day (None until `period` values exist)."""
    return [sum(values[i + 1 - period:i + 1]) / period if i + 1 >= period else None
            for i in range(len(values))]


def rsi_series(closes, period=RSI_PERIOD):
    """Wilder's RSI for every day (the standard one charting sites show).

    Starts from the plain average gain/loss of the first `period` changes, then
    smooths each new change in: avg = (prev_avg * (period - 1) + change) / period.
    """
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    changes = [today - before for before, today in zip(closes, closes[1:])]
    avg_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    avg_loss = sum(max(-change, 0.0) for change in changes[:period]) / period
    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + max(changes[i], 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-changes[i], 0.0)) / period
        out[i + 1] = _rsi(avg_gain, avg_loss)
    return out


def _rsi(avg_gain, avg_loss):
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def sma(values, period):
    """Simple moving average of the last `period` values."""
    return sum(values[-period:]) / period


def rsi(closes, period=RSI_PERIOD):
    """Latest Wilder RSI, or None if there are too few closes."""
    return rsi_series(closes, period)[-1] if len(closes) > period else None


def classify(price, sma_fast, sma_slow, rsi_value):
    """Mechanical label from the three indicator values. Pure function."""
    if price > sma_fast > sma_slow and rsi_value < RSI_OVERBOUGHT:
        return "BUY"
    if price < sma_fast < sma_slow or rsi_value > RSI_OVERBOUGHT:
        return "SELL"
    return "HOLD"


def indicators(bars):
    """Per-day close, SMAs, RSI and rule signal (None until defined)."""
    closes = [bar["close"] for bar in bars]
    fast, slow, rsis = sma_series(closes, SMA_FAST), sma_series(closes, SMA_SLOW), rsi_series(closes)
    signals = [classify(c, f, s, r) if None not in (f, s, r) else None
               for c, f, s, r in zip(closes, fast, slow, rsis)]
    return {"close": closes, "sma_fast": fast, "sma_slow": slow, "rsi": rsis, "signal": signals}


def signal_row(symbol, bars, series=None):
    """Latest values for one symbol, or None if there is too little history."""
    if len(bars) < SMA_SLOW + 2:
        return None
    series = series or indicators(bars)
    price, previous = series["close"][-1], series["close"][-2]
    return {
        "symbol": symbol,
        "date": bars[-1]["date"],
        "price": price,
        "change_pct": (price / previous - 1) * 100,
        "sma_fast": series["sma_fast"][-1],
        "sma_slow": series["sma_slow"][-1],
        "rsi": series["rsi"][-1],
        "signal": series["signal"][-1],
    }


def analyze(symbol):
    """Fetch candles and return the latest indicator row, or None if too few."""
    return signal_row(symbol, fetch_chart(symbol)["bars"])


# ------------------------------------------------------------ paper log ----


LOG_FIELDS = ["date", "symbol", "close", "sma_fast", "sma_slow", "rsi", "signal"]


def log_results(results):
    """Record signals in state/paper.csv: one row per (candle date, symbol).

    Re-running on the same day, or on a market holiday, updates the existing
    row instead of adding a duplicate, so the sim's track record stays clean.
    """
    rows = {}
    if os.path.exists(LOG_PATH):
        # utf-8-sig tolerates the BOM Excel adds when re-saving a CSV
        with open(LOG_PATH, newline="", encoding="utf-8-sig") as handle:
            for old in csv.DictReader(handle):
                rows[(old["date"], old["symbol"])] = old
    for row in results:
        rows[(row["date"], row["symbol"])] = {
            "date": row["date"],
            "symbol": row["symbol"],
            "close": f"{row['price']:.2f}",
            "sma_fast": f"{row['sma_fast']:.2f}",
            "sma_slow": f"{row['sma_slow']:.2f}",
            "rsi": f"{row['rsi']:.1f}",
            "signal": row["signal"],
        }
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    tmp_path = LOG_PATH + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOG_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows.values())
    os.replace(tmp_path, LOG_PATH)  # atomic swap: a crash can't half-write the log


# ----------------------------------------------------------------- report ----


def format_line(row):
    return (
        f"{row['symbol']:<14} close={row['price']:>9.2f}  "
        f"SMA{SMA_FAST}={row['sma_fast']:>9.2f}  "
        f"SMA{SMA_SLOW}={row['sma_slow']:>9.2f}  "
        f"RSI={row['rsi']:>5.1f}  ->  {row['signal']}"
    )


def build_report(symbols=None):
    symbols = symbols or load_watchlist()[0]
    results, errors = [], []
    for symbol in symbols:
        try:
            row = analyze(symbol)
        except Exception as exc:  # network / symbol problems shouldn't kill the run
            errors.append(f"{symbol}: {exc}")
            continue
        if row is None:
            errors.append(f"{symbol}: not enough data")
        else:
            results.append(row)

    # latest candle date, so a holiday run doesn't pass off old data as today's
    as_of = max((row["date"] for row in results), default=datetime.date.today().isoformat())
    header = f"NSE signals - {as_of}"
    body = [format_line(row) for row in results] or ["no data"]
    if errors:
        body.append("")
        body.append("issues: " + "; ".join(errors))
    return results, header + "\n" + "\n".join(body)


def main():
    return build_report()[1]


if __name__ == "__main__":
    print(main())
