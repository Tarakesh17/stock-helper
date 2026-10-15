"""
Paper-trading agents for the simulation: Rs 1,00,000 of virtual money each.

  Rules agent  buys on a BUY signal and sells on a SELL signal
  Model agent  buys when the model's up-chance is high, sells when it drops

Each 6 PM run decides from that day's closing data, and the orders fill at
the NEXT trading day's opening price (nobody can trade at a close they only
see after the market shuts). Every buy and every sell pays COST_PCT for
taxes, fees and slippage. Results are compared with just holding NIFTY 50.

The live agents start on the first 6 PM run and only ever move forward.
The backtest replays the past year with the same rules, for context only:
backtests always look better than live trading.
"""

import json
import os

import watch

AGENTS_PATH = os.path.join(watch.STATE_DIR, "agents.json")
START_CASH = 100000.0
COST_PCT = 0.2       # paid on every buy and every sell
MODEL_BUY = 0.53     # the model agent buys at or above this up-chance...
MODEL_SELL = 0.49    # ...and sells below this one
BENCHMARK = "^NSEI"
BENCHMARK_NAME = "NIFTY 50"
BACKTEST_DAYS = 250
KEEP_TRADES = 300

AGENTS = [
    {"id": "rules", "name": "Rules agent",
     "description": "Buys on a BUY signal and sells on a SELL signal."},
    {"id": "model", "name": "Model agent",
     "description": (f"Buys when the model's up-chance is {MODEL_BUY:.0%} or more "
                     f"and sells when it drops below {MODEL_SELL:.0%}.")},
]

# -------------------------------------------------------------- decisions ----


def decide(agent_id, day, holding):
    """(side, reason) for one stock on one day; side is 'BUY', 'SELL' or None."""
    if agent_id == "rules":
        if day["signal"] == "BUY" and not holding:
            return "BUY", "BUY signal"
        if day["signal"] == "SELL" and holding:
            return "SELL", "SELL signal"
        return None, None
    p_up = day.get("p_up")
    if p_up is None:
        return None, None
    if p_up >= MODEL_BUY and not holding:
        return "BUY", f"up-chance {p_up:.0%}"
    if p_up < MODEL_SELL and holding:
        return "SELL", f"up-chance {p_up:.0%}"
    return None, None


def last_close(days, date):
    """Close on `date`, or the latest one before it (None if there is none)."""
    if date in days:
        return days[date]["close"]
    earlier = [day for day in days if day <= date]
    return days[max(earlier)]["close"] if earlier else None


# -------------------------------------------------------------- portfolio ----


def new_portfolio():
    return {"cash": START_CASH, "positions": {}, "orders": [], "curve": [], "trades": [],
            "counts": {"trades": 0, "closed": 0, "wins": 0}}  # the trade list is trimmed; counts aren't


def step(portfolio, agent_id, date, market, watchlist):
    """One trading day: fill queued orders at today's open (sells first, so
    their cash can fund the buys), value everything at today's close, then
    queue tomorrow's orders from today's data.

    market: {symbol: {date: {"open", "close", "signal", "p_up"}}}
    """
    cost = COST_PCT / 100
    equity = portfolio["curve"][-1][1] if portfolio["curve"] else START_CASH
    slot = equity / max(len(watchlist), 1)  # each watchlist stock gets an equal slice

    waiting = []
    for order in sorted(portfolio["orders"], key=lambda o: o["side"] != "SELL"):
        today = (market.get(order["symbol"]) or {}).get(date)
        if today is None:  # no candle today (halt or missing data): try again tomorrow
            waiting.append(order)
            continue
        price = today["open"]
        position = portfolio["positions"].get(order["symbol"])
        if order["side"] == "SELL" and position:
            value = position["qty"] * price * (1 - cost)
            portfolio["cash"] += value
            del portfolio["positions"][order["symbol"]]
            portfolio["trades"].append(_trade(date, order, position["qty"], price, value,
                                              value - position["cost"]))
            counts = portfolio["counts"]
            counts["trades"] += 1
            counts["closed"] += 1
            counts["wins"] += int(value > position["cost"])
        elif order["side"] == "BUY" and not position:
            qty = int(min(slot, portfolio["cash"]) // (price * (1 + cost)))
            if qty >= 1:
                spent = qty * price * (1 + cost)
                portfolio["cash"] -= spent
                portfolio["positions"][order["symbol"]] = {"qty": qty, "cost": spent,
                                                           "since": date, "last": price}
                portfolio["trades"].append(_trade(date, order, qty, price, spent, None))
                portfolio["counts"]["trades"] += 1
    portfolio["orders"] = waiting

    value = portfolio["cash"]
    for symbol, position in portfolio["positions"].items():
        close = last_close(market.get(symbol) or {}, date)
        if close is not None:
            position["last"] = close
        value += position["qty"] * position["last"]
    portfolio["curve"].append([date, round(value, 2)])

    queued = {order["symbol"] for order in portfolio["orders"]}
    for symbol in sorted(set(watchlist) | set(portfolio["positions"])):
        if symbol in queued:
            continue
        holding = symbol in portfolio["positions"]
        if symbol not in watchlist:
            side, reason = ("SELL", "removed from watchlist") if holding else (None, None)
        else:
            today = (market.get(symbol) or {}).get(date)
            side, reason = decide(agent_id, today, holding) if today else (None, None)
        if side:
            portfolio["orders"].append({"date": date, "symbol": symbol, "side": side, "reason": reason})
    del portfolio["trades"][:-KEEP_TRADES]


def _trade(date, order, qty, price, value, pnl):
    return {"date": date, "symbol": order["symbol"], "side": order["side"], "qty": qty,
            "price": round(price, 2), "value": round(value, 2),
            "pnl": None if pnl is None else round(pnl, 2), "reason": order["reason"]}


# ------------------------------------------------------------------- live ----


def load_state(path=None):
    try:
        with open(path or AGENTS_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None


def save_state(state, path=None):
    path = path or AGENTS_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=1)
    os.replace(tmp_path, path)


def held_symbols(state):
    """Stocks the live agents hold or have orders for (they must stay priced)."""
    symbols = set()
    for portfolio in ((state or {}).get("agents") or {}).values():
        symbols.update(portfolio["positions"])
        symbols.update(order["symbol"] for order in portfolio["orders"])
    return sorted(symbols)


def run_live(state, market, calendar, watchlist, benchmark, now=None):
    """Step the live agents through each new final trading day.

    Returns (state, dates stepped). A day is never stepped twice, so running
    again on the same day changes nothing; missed days are caught up in order.
    benchmark: {date: {"close"}} for NIFTY 50.
    """
    now = now or watch.now_ist()
    final = [date for date in calendar if watch.is_final(date, now)]
    if not final:
        return state, []
    if state is None:
        state = {"version": 1, "start_date": final[-1], "last_date": "", "cost_pct": COST_PCT,
                 "benchmark": {"symbol": BENCHMARK, "closes": []}, "agents": {}}
        todo = [final[-1]]
    else:
        todo = [date for date in final if date > state["last_date"]]
    for agent in AGENTS:
        state["agents"].setdefault(agent["id"], new_portfolio())
    for date in todo:
        for agent in AGENTS:
            step(state["agents"][agent["id"]], agent["id"], date, market, watchlist)
        state["benchmark"]["closes"].append([date, last_close(benchmark, date)])
        state["last_date"] = date
    return state, todo


def backtest(market, calendar, watchlist, benchmark, days=BACKTEST_DAYS):
    """The same agents replayed over the last `days` trading days of `calendar`."""
    dates = calendar[-days:]
    if len(dates) < 20:
        return None
    portfolios = {agent["id"]: new_portfolio() for agent in AGENTS}
    closes = []
    for date in dates:
        for agent in AGENTS:
            step(portfolios[agent["id"]], agent["id"], date, market, watchlist)
        closes.append([date, last_close(benchmark, date)])
    return report(dates[0], dates[-1], portfolios, closes)


def backtest_stock(bars, series, days=BACKTEST_DAYS):
    """One stock over the last `days`: all in on a BUY signal, all out on SELL,
    filled at the next day's open with costs, next to buy-and-hold."""
    n = len(bars)
    start = max(n - days, watch.SMA_SLOW)
    if n - start < 20:
        return None
    cost = COST_PCT / 100
    cash, shares, entry = 1.0, 0.0, 0.0
    buys = wins = days_in = 0
    pending, peak, worst = None, 1.0, 0.0
    for t in range(start, n):
        if pending == "BUY":
            entry, shares, cash = cash, cash / (bars[t]["open"] * (1 + cost)), 0.0
            buys += 1
        elif pending == "SELL":
            cash, shares = shares * bars[t]["open"] * (1 - cost), 0.0
            wins += cash > entry
        value = cash + shares * bars[t]["close"]
        days_in += shares > 0
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
        signal = series["signal"][t]
        pending = ("BUY" if signal == "BUY" and shares == 0
                   else "SELL" if signal == "SELL" and shares > 0 else None)
    closed = buys - (1 if shares > 0 else 0)
    final = cash + shares * bars[-1]["close"]
    hold = bars[-1]["close"] / bars[start]["close"] * (1 - cost) / (1 + cost)
    return {
        "start": bars[start]["date"],
        "strategy_pct": round((final - 1) * 100, 2),
        "buy_hold_pct": round((hold - 1) * 100, 2),
        "trades": buys,
        "win_rate_pct": round(100 * wins / closed, 1) if closed else None,
        "max_drawdown_pct": round(worst * 100, 2),
        "in_market_pct": round(100 * days_in / (n - start), 1),
    }


# ----------------------------------------------------------------- report ----


def report(start, end, portfolios, closes):
    """Website-ready summary of a set of portfolios next to the benchmark."""
    return {
        "start": start,
        "end": end,
        "start_cash": START_CASH,
        "cost_pct": COST_PCT,
        "benchmark": _benchmark(closes),
        "agents": [dict(agent, **_summary(portfolios[agent["id"]]))
                   for agent in AGENTS if agent["id"] in portfolios],
    }


def live_report(state):
    if not state or not state.get("last_date"):
        return None
    return report(state["start_date"], state["last_date"], state["agents"], state["benchmark"]["closes"])


def _summary(portfolio):
    curve = portfolio["curve"]
    equity = curve[-1][1] if curve else START_CASH
    peak, worst = START_CASH, 0.0
    for _, value in curve:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    counts = portfolio["counts"]
    positions = []
    for symbol, position in sorted(portfolio["positions"].items()):
        value = position["qty"] * position["last"]
        positions.append({
            "symbol": symbol, "qty": position["qty"], "since": position["since"],
            "avg_price": round(position["cost"] / position["qty"], 2),
            "last": round(position["last"], 2), "value": round(value, 2),
            "pnl": round(value - position["cost"], 2),
            "pnl_pct": round((value / position["cost"] - 1) * 100, 2),
        })
    return {
        "cash": round(portfolio["cash"], 2),
        "equity": round(equity, 2),
        "return_pct": round((equity / START_CASH - 1) * 100, 2),
        "max_drawdown_pct": round(worst * 100, 2),
        "trades": counts["trades"],
        "closed_trades": counts["closed"],
        "win_rate_pct": round(100 * counts["wins"] / counts["closed"], 1) if counts["closed"] else None,
        "positions": positions,
        "orders": list(portfolio["orders"]),
        "recent_trades": list(reversed(portfolio["trades"][-25:])),
        "curve": curve,
    }


def _benchmark(closes):
    known = [(date, close) for date, close in closes if close]
    if not known:
        return None
    base = known[0][1]
    return {
        "symbol": BENCHMARK,
        "name": BENCHMARK_NAME,
        "return_pct": round((known[-1][1] / base - 1) * 100, 2),
        "curve": [[date, round(START_CASH * close / base, 2)] for date, close in known],
    }
