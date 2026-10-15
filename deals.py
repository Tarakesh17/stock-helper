"""
NSE bulk and block deals: the big trades NSE publishes after every session.

  bulk deal   one client trades more than 0.5% of a company's shares in a day
  block deal  a large trade done in the separate block-deal window

NSE names the client (funds, promoters, big investors), so this is the
closest free public view of what large players bought and sold. It is
end-of-day data, published after the market closes, not real time.

The store is one CSV per month in state/deals/ (about a year deep).
"""

import csv
import datetime
import io
import json
import os

import watch

DEALS_DIR = os.path.join(watch.STATE_DIR, "deals")
FIELDS = ["date", "kind", "symbol", "name", "client", "side", "qty", "price"]
BACKFILL_DAYS = 365  # the first run fetches a year of history
OVERLAP_DAYS = 5     # later runs re-fetch a few days so late filings are caught
KEEP_DAYS = 400

REPORT_PAGE = "https://www.nseindia.com/report-detail/display-bulk-and-block-deals"
HISTORY_API = "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"
DAILY_FILE = "https://nsearchives.nseindia.com/content/equities/{kind}.csv"
KINDS = {"bulk": "bulk_deals", "block": "block_deals"}
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def nse_symbol(symbol):
    """RELIANCE.NS -> RELIANCE; None for BSE (.BO) symbols, which NSE data doesn't cover."""
    return symbol[:-3] if symbol.endswith(".NS") else None


def value(row):
    return row["qty"] * row["price"]


# ------------------------------------------------------------------ parse ----


def _date(text):
    """'15-SEP-2026' -> '2026-09-15' (None if it isn't a date)."""
    try:
        day, month, year = text.strip().split("-")
        return datetime.date(int(year), MONTHS.index(month.upper()) + 1, int(day)).isoformat()
    except ValueError:
        return None


def parse_csv(text, kind):
    """Deals from an NSE CSV (report API or daily file). Columns are read by
    position because the two sources name them slightly differently."""
    rows = []
    for record in csv.reader(io.StringIO(text)):
        if len(record) < 7:
            continue
        day, side = _date(record[0]), record[4].strip().upper()
        try:
            qty = int(float(record[5].replace(",", "")))
            price = round(float(record[6].replace(",", "")), 2)
        except ValueError:
            continue  # header, "NO RECORDS" and other non-deal lines
        if day and side in ("BUY", "SELL") and qty > 0 and price > 0:
            rows.append({"date": day, "kind": kind, "symbol": record[1].strip().upper(),
                         "name": " ".join(record[2].split()), "client": " ".join(record[3].split()),
                         "side": side, "qty": qty, "price": price})
    return rows


# ------------------------------------------------------------------ fetch ----


def _session():
    from curl_cffi import requests  # imported lazily so parsing stays testable offline

    return requests.Session(impersonate="chrome")


def _csv_text(response, label):
    text = response.content.decode("utf-8-sig", "replace")
    if response.status_code != 200 or not text.lstrip().lstrip('"').lower().startswith("date"):
        raise ValueError(f"{label}: HTTP {response.status_code}")
    return text


def fetch_history(start, end, session=None):
    """All bulk and block deals from `start` to `end` (dates, inclusive)."""
    session = session or _session()
    session.get(REPORT_PAGE, timeout=30)  # the API wants the cookies this page sets
    rows = []
    for kind, option in KINDS.items():
        response = session.get(HISTORY_API, headers={"Referer": REPORT_PAGE}, timeout=120, params={
            "optionType": option, "from": start.strftime("%d-%m-%Y"),
            "to": end.strftime("%d-%m-%Y"), "csv": "true"})
        rows += parse_csv(_csv_text(response, f"NSE {kind} deals"), kind)
    return rows


def fetch_latest(session=None):
    """The newest session's deals from NSE's daily files (used if the API fails)."""
    session = session or _session()
    rows = []
    for kind in KINDS:
        response = session.get(DAILY_FILE.format(kind=kind), timeout=30)
        rows += parse_csv(_csv_text(response, f"NSE {kind}.csv"), kind)
    return rows


# ------------------------------------------------------------------ store ----


def load(directory=None):
    """(rows, meta) from the store; empty when there is none yet."""
    directory = directory or DEALS_DIR
    rows, meta = [], {}
    if os.path.isdir(directory):
        for name in sorted(os.listdir(directory)):
            if name.endswith(".csv"):
                with open(os.path.join(directory, name), newline="", encoding="utf-8") as handle:
                    for row in csv.DictReader(handle):
                        rows.append(dict(row, qty=int(row["qty"]), price=float(row["price"])))
        meta_path = os.path.join(directory, "meta.json")
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as handle:
                meta = json.load(handle)
    return rows, meta


def _key(row):
    return (row["date"], row["kind"], row["symbol"], row["client"].upper(),
            row["side"], row["qty"], row["price"])


def merge(old, new, today):
    """Stored + new deals without duplicates, oldest first, at most KEEP_DAYS old."""
    cutoff = (today - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    rows = {}
    for row in old + new:
        if row["date"] >= cutoff:
            rows.setdefault(_key(row), row)
    return [rows[key] for key in sorted(rows)]


def update(today, session=None):
    """Fetch new deals and merge them with the stored ones (nothing is written).

    Returns (rows, meta, changed, note); note says what went wrong, if anything.
    """
    old, meta = load()
    backfill = today - datetime.timedelta(days=BACKFILL_DAYS)
    start = backfill
    if meta.get("from") and meta.get("fetched_to"):
        start = max(backfill, datetime.date.fromisoformat(meta["fetched_to"])
                    - datetime.timedelta(days=OVERLAP_DAYS))
    note = None
    try:
        new = fetch_history(start, today, session)
        oldest_kept = (today - datetime.timedelta(days=KEEP_DAYS)).isoformat()
        covered = backfill.isoformat() if start == backfill else max(meta["from"], oldest_kept)
        new_meta = {"from": covered, "fetched_to": today.isoformat()}
    except Exception as exc:
        try:
            new = fetch_latest(session)
        except Exception:
            return old, meta, False, f"Big deals not updated: {exc}"
        note = f"NSE deal history unavailable ({exc}); added the latest day only"
        new_meta = dict(meta) or {"from": min((row["date"] for row in new), default=today.isoformat()),
                                  "fetched_to": ""}  # empty, so the next run tries the full history
    rows = merge(old, new, today)
    return rows, new_meta, rows != old or new_meta != meta, note


def save(rows, meta, directory=None):
    """One CSV per month; only months whose content changed are rewritten."""
    directory = directory or DEALS_DIR
    months = {}
    for row in rows:
        months.setdefault(row["date"][:7], []).append(row)
    os.makedirs(directory, exist_ok=True)
    for name in os.listdir(directory):
        if name.endswith(".csv") and name[:-4] not in months:
            os.remove(os.path.join(directory, name))  # older than KEEP_DAYS
    for month, items in months.items():
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(dict(row, price=f"{row['price']:.2f}") for row in items)
        watch.save_text(os.path.join(directory, month + ".csv"), buffer.getvalue())
    watch.save_text(os.path.join(directory, "meta.json"), json.dumps(meta, indent=1) + "\n")


# -------------------------------------------------------------- summaries ----


def _item(row, watch_nse=()):
    return {"date": row["date"], "kind": row["kind"], "symbol": row["symbol"], "name": row["name"],
            "client": row["client"], "side": row["side"], "qty": row["qty"], "price": row["price"],
            "value": round(value(row)), "in_watchlist": row["symbol"] in watch_nse}


def latest_day(rows, watch_nse, top=60):
    """The newest session's deals: the `top` biggest plus every watchlist one."""
    if not rows:
        return None
    day = max(row["date"] for row in rows)
    items = sorted((row for row in rows if row["date"] == day), key=value, reverse=True)
    picked = items[:top] + [row for row in items[top:] if row["symbol"] in watch_nse]
    return {"date": day, "count": len(items), "deals": [_item(row, watch_nse) for row in picked]}


def net_flow(rows, since, top=15):
    """Stocks that big players bought or sold the most (net value) since `since`."""
    totals = {}
    for row in rows:
        if row["date"] >= since:
            item = totals.setdefault(row["symbol"], {"symbol": row["symbol"], "name": row["name"],
                                                     "buy": 0.0, "sell": 0.0, "count": 0})
            item["buy" if row["side"] == "BUY" else "sell"] += value(row)
            item["count"] += 1
    items = [dict(item, buy=round(item["buy"]), sell=round(item["sell"]),
                  net=round(item["buy"] - item["sell"])) for item in totals.values()]
    return {"from": since,
            "buys": sorted((i for i in items if i["net"] > 0), key=lambda i: -i["net"])[:top],
            "sells": sorted((i for i in items if i["net"] < 0), key=lambda i: i["net"])[:top]}


def for_symbol(rows, symbol, since, limit=100):
    """Deals in one NSE symbol: totals since `since` plus the newest `limit` deals."""
    mine = [row for row in rows if row["symbol"] == symbol]
    recent = [row for row in mine if row["date"] >= since]
    buy = sum(value(row) for row in recent if row["side"] == "BUY")
    sell = sum(value(row) for row in recent if row["side"] == "SELL")
    newest = sorted(mine, key=lambda row: (row["date"], value(row)), reverse=True)[:limit]
    return {"nse_symbol": symbol, "count": len(mine), "since": since, "buy": round(buy),
            "sell": round(sell), "net": round(buy - sell), "deals": [_item(row) for row in newest]}


def flows(rows):
    """{nse_symbol: [(date, signed value), ...]} with buys +, sells -, for the model."""
    out = {}
    for row in rows:
        out.setdefault(row["symbol"], []).append(
            (row["date"], value(row) if row["side"] == "BUY" else -value(row)))
    return out
