"""
Daily pipeline for the stock helper. Simulation only: it never places real orders.

  prices -> signals -> model (checks yesterday's calls, retrains, predicts)
         -> sim agents -> big deals -> website data -> short Telegram message

  python run.py                 update everything, then send Telegram if set up
  python run.py --no-telegram   update everything without Telegram

GitHub Actions runs this at 18:00 IST on weekdays and after every push to main.
state/ is the memory between runs (the workflow commits it back to the repo);
docs/data.json feeds the website and is rebuilt on every run.
"""

import argparse
import csv
import datetime
import html
import io
import json
import os
import sys

import agents
import bot_send
import deals
import model
import watch

DATA_PATH = os.path.join(watch.HERE, "docs", "data.json")
SYMBOLS_PATH = os.path.join(watch.HERE, "docs", "symbols.json")
EQUITY_LIST = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
DEFAULT_REPO = "Tarakesh17/stock-helper"
SERIES_DAYS = 250  # about a year of daily points for the website's charts


def _r(number, digits=2):
    return None if number is None else round(number, digits)


def site_info():
    repo = os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO
    owner, _, name = repo.partition("/")
    return {"repo": repo, "url": os.environ.get("SITE_URL") or f"https://{owner.lower()}.github.io/{name}/"}


def warn(message):
    """Print a warning (GitHub shows it on the run's summary page)."""
    print(("::warning::" if os.environ.get("GITHUB_ACTIONS") else "warning: ") + message)


# ------------------------------------------------------------------ fetch ----


def fetch_charts(symbols):
    charts, failed = {}, []
    for symbol in symbols:
        try:
            charts[symbol] = watch.fetch_chart(symbol)
        except Exception as exc:  # one bad symbol shouldn't stop the run
            failed.append({"symbol": symbol, "error": str(exc)[:200]})
    return charts, failed


def fetch_symbol_list():
    """Every NSE-listed equity as [[symbol, name], ...] JSON, for the website's stock picker."""
    from curl_cffi import requests

    response = requests.get(EQUITY_LIST, impersonate="chrome", timeout=30)
    items = []
    for record in csv.DictReader(io.StringIO(response.content.decode("utf-8-sig", "replace"))):
        record = {(key or "").strip(): text.strip() for key, text in record.items() if isinstance(text, str)}
        if record.get("SYMBOL") and record.get("NAME OF COMPANY"):
            items.append([record["SYMBOL"] + ".NS", record["NAME OF COMPANY"]])
    if response.status_code != 200 or len(items) < 500:
        raise ValueError(f"NSE equity list: HTTP {response.status_code}, {len(items)} rows")
    return json.dumps(sorted(items), separators=(",", ":"))


# ---------------------------------------------------------- website data ----


def market_days(charts, histories, p_up):
    """{symbol: {date: {"open", "close", "signal", "p_up"}}} for the sim agents."""
    market = {}
    for symbol, chart in charts.items():
        bars = chart["bars"]
        series = histories[symbol][1] if symbol in histories else watch.indicators(bars)
        chances = p_up.get(symbol, {})
        market[symbol] = {bar["date"]: {"open": bar["open"], "close": bar["close"], "signal": signal,
                                        "p_up": chances.get(bar["date"])}
                          for bar, signal in zip(bars, series["signal"])}
    return market


def stock_entry(symbol, chart, history, row, trained, replay, deal_rows, since, now):
    """Everything the website shows for one stock."""
    bars, series = history
    signals = series["signal"]
    first = max(0, len(bars) - SERIES_DAYS)
    window = bars[first:]
    start = len(bars) - 1
    while start > 0 and signals[start - 1] == signals[-1]:
        start -= 1
    changes = [{"date": bars[t]["date"], "from": signals[t - 1], "to": signals[t], "close": _r(bars[t]["close"])}
               for t in range(max(first, 1), len(bars))
               if signals[t - 1] and signals[t] and signals[t] != signals[t - 1]]
    chances = trained["p"].get(symbol, {})
    nse = deals.nse_symbol(symbol)
    return {
        "symbol": symbol,
        "name": chart["meta"]["name"],
        "currency": chart["meta"]["currency"],
        "date": row["date"],
        "final": watch.is_final(row["date"], now),
        "price": _r(row["price"]),
        "change_pct": _r(row["change_pct"]),
        "sma_fast": _r(row["sma_fast"]),
        "sma_slow": _r(row["sma_slow"]),
        "rsi": _r(row["rsi"], 1),
        "signal": row["signal"],
        "prev_signal": signals[-2],
        "signal_since": bars[start]["date"],
        "high_52w": _r(max(bar["high"] for bar in window)),
        "low_52w": _r(min(bar["low"] for bar in window)),
        "p_up": _r(trained["latest"].get(symbol, {}).get("p_up"), 3),
        "backtest": agents.backtest_stock(bars, series),
        "model_replay": ((replay or {}).get("per_symbol") or {}).get(symbol),
        "signal_changes": changes[::-1][:12],
        "deals": deals.for_symbol(deal_rows, nse, since) if nse else None,
        "series": {
            "dates": [bar["date"] for bar in window],
            "close": [_r(bar["close"]) for bar in window],
            "volume": [bar["volume"] for bar in window],
            "sma_fast": [_r(v) for v in series["sma_fast"][first:]],
            "sma_slow": [_r(v) for v in series["sma_slow"][first:]],
            "rsi": [_r(v, 1) for v in series["rsi"][first:]],
            "signal": signals[first:],
            "p_up": [_r(chances.get(bar["date"]), 3) for bar in window],
        },
    }


def build_data(now, symbols, issues, failed, charts, histories, rows, trained, predictions,
               state, backtest, deal_rows, deal_meta, deal_note):
    replay = model.replay_stats(trained["scored"])
    since = (now.date() - datetime.timedelta(days=30)).isoformat()
    watch_nse = {deals.nse_symbol(symbol) for symbol in symbols} - {None}
    as_of = max(row["date"] for row in rows.values())
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "as_of": as_of,
        "final": watch.is_final(as_of, now),
        "site": site_info(),
        "config": {
            "sma_fast": watch.SMA_FAST, "sma_slow": watch.SMA_SLOW, "rsi_period": watch.RSI_PERIOD,
            "rsi_overbought": watch.RSI_OVERBOUGHT, "max_symbols": watch.MAX_SYMBOLS,
            "symbol_pattern": watch.SYMBOL_PATTERN.pattern, "start_cash": agents.START_CASH,
            "cost_pct": agents.COST_PCT, "benchmark": agents.BENCHMARK_NAME,
        },
        "watchlist": symbols,
        "issues": issues,
        "failed": failed,
        "stocks": [stock_entry(symbol, charts[symbol], histories[symbol], rows[symbol], trained, replay,
                               deal_rows, since, now)
                   for symbol in symbols if symbol in histories],
        "model": {
            "features": model.explain(trained["model"]),
            "replay": {key: item for key, item in replay.items() if key != "per_symbol"} if replay else None,
            "live": model.live_stats(predictions, symbols),
            "settings": {"burn_in_days": model.BURN_IN_DAYS, "confident": model.CONFIDENT,
                         "agent_buy_at": agents.MODEL_BUY, "agent_sell_below": agents.MODEL_SELL},
        },
        "agents": {"live": agents.live_report(state), "backtest": backtest},
        "deals": {
            "note": deal_note,
            "from": deal_meta.get("from"),
            "latest": deals.latest_day(deal_rows, watch_nse),
            "net_30d": deals.net_flow(deal_rows, since),
        },
    }


# ------------------------------------------------------------------- main ----


def main(argv=None):
    parser = argparse.ArgumentParser(description="Update signals, model, sim agents and website data.")
    parser.add_argument("--no-telegram", action="store_true", help="don't send the Telegram message")
    args = parser.parse_args(argv)

    now = watch.now_ist()
    print(f"--- run {now:%Y-%m-%d %H:%M} IST ---")
    symbols, issues = watch.load_watchlist()
    state = agents.load_state()
    held = [symbol for symbol in agents.held_symbols(state) if symbol not in symbols]
    charts, failed = fetch_charts(symbols + held)
    try:
        benchmark_bars = watch.fetch_chart(agents.BENCHMARK)["bars"]
    except Exception as exc:
        benchmark_bars = []
        issues.append(f"{agents.BENCHMARK_NAME}: {exc}")

    histories, rows = {}, {}
    for symbol in symbols:
        if symbol not in charts:
            continue
        bars = charts[symbol]["bars"]
        series = watch.indicators(bars)
        row = watch.signal_row(symbol, bars, series)
        if row is None:
            failed.append({"symbol": symbol, "error": "not enough price history yet"})
        else:
            histories[symbol], rows[symbol] = (bars, series), row

    if not histories:
        message = "NSE signals: no price data today. " + "; ".join(
            f"{item['symbol']}: {item['error']}" for item in failed)
        print(message, file=sys.stderr)
        config = None if args.no_telegram else bot_send.telegram_config()
        if config:
            try:
                bot_send.send(html.escape(message), *config)
            except SystemExit as exc:
                print(exc, file=sys.stderr)
        return 1

    deal_rows, deal_meta, deals_changed, deal_note = deals.update(now.date())
    if deal_note:
        issues.append(deal_note)
    flows = deals.flows(deal_rows)
    trained = model.walk_forward(histories, now, {symbol: flows.get(deals.nse_symbol(symbol), [])
                                                  for symbol in histories})
    predictions = model.update_live_log(model.load_predictions(), trained["latest"], histories, now)

    market = market_days(charts, histories, trained["p"])
    calendar = [bar["date"] for bar in benchmark_bars] or sorted({day for days in market.values() for day in days})
    benchmark = {bar["date"]: {"close": bar["close"]} for bar in benchmark_bars}
    state, stepped = agents.run_live(state, market, calendar, symbols, benchmark, now)
    backtest = agents.backtest(market, calendar, list(histories), benchmark)
    try:
        symbol_list = fetch_symbol_list()
    except Exception as exc:
        symbol_list = None
        print(f"stock list not refreshed: {exc}")

    data = build_data(now, symbols, issues, failed, charts, histories, rows, trained, predictions,
                      state, backtest, deal_rows, deal_meta, deal_note)

    # every file is written here, after all the work above succeeded
    finals = [row for row in rows.values() if watch.is_final(row["date"], now)]
    if finals:
        watch.log_results(finals)
    model.save_predictions(predictions)
    if state:
        agents.save_state(state)
    if deals_changed:
        deals.save(deal_rows, deal_meta)
    if symbol_list:
        watch.save_text(SYMBOLS_PATH, symbol_list)
    watch.save_text(DATA_PATH, json.dumps(data, separators=(",", ":")))

    for row in rows.values():
        print(watch.format_line(row))
    for line in issues + [f"{item['symbol']}: {item['error']}" for item in failed]:
        print("issue:", line)
    print("sim agents stepped:", ", ".join(stepped) or "nothing new")

    if args.no_telegram:
        print("Telegram: skipped (--no-telegram)")
        return 0
    config = bot_send.telegram_config()
    if not config:
        warn("Telegram is not set up (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID), so no message was sent")
        return 0
    try:
        bot_send.send(bot_send.format_message(data), *config)
    except SystemExit as exc:
        print(f"Telegram failed: {exc}", file=sys.stderr)
        return 1
    print("Telegram: sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
