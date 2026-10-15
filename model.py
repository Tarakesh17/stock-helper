"""
Next-day direction model for the simulation (pure Python, no numpy).

Every 6 PM run it:
  1. checks the previous run's predictions against the real closing prices,
  2. retrains from scratch on all the history it has, the newest real result
     included, walking forward one day at a time so it never sees the future,
  3. predicts each stock's chance of closing higher on the next trading day.

A day-ahead up/down call is close to a coin flip for large caps. The
scoreboard puts the model next to two naive baselines ("always up" and
"same as today") so you can see whether it really adds anything.
Simulation only.
"""

import csv
import math
import os

import watch

PREDICTIONS_PATH = os.path.join(watch.STATE_DIR, "predictions.csv")
PREDICTION_FIELDS = ["date", "symbol", "close", "p_up", "call",
                     "next_date", "next_close", "actual", "correct"]

FEATURES = [
    ("ret_1d", "1-day return"),
    ("ret_5d", "5-day return"),
    ("ret_20d", "20-day return"),
    ("vs_sma_fast", f"Price vs SMA{watch.SMA_FAST}"),
    ("vs_sma_slow", f"Price vs SMA{watch.SMA_SLOW}"),
    ("rsi", "RSI"),
    ("volatility", "20-day volatility"),
    ("volume", "Volume vs 20-day average"),
    ("deals", "Big-deal net buying, previous 5 days"),
]
LEARNING_RATE = 0.02
L2 = 0.001
SCALE_CLIP = 5.0    # standardised inputs are clipped to +-5 so one odd day can't swing it
BURN_IN_DAYS = 60   # the first trading days (model still learning) are left out of its scores
CONFIDENT = 0.05    # an up-chance at least this far from 50% counts as a confident call

# --------------------------------------------------------------- features ----


def features(bars, series, t, deal_flow=()):
    """Inputs for day t, using prices up to and including day t only. Big deals
    count up to the day before, since NSE publishes them after the close."""
    if t < watch.SMA_SLOW or series["sma_slow"][t] is None or series["rsi"][t] is None:
        return None
    closes = series["close"]
    returns = [closes[i] / closes[i - 1] - 1 for i in range(t - 19, t + 1)]
    mean = sum(returns) / len(returns)
    volatility = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
    volume = bars[t]["volume"]
    average = sum(bar["volume"] for bar in bars[t - 20:t]) / 20
    first, last = bars[t - 5]["date"], bars[t - 1]["date"]
    net = sum(amount for day, amount in deal_flow if first <= day <= last)
    traded = sum(bar["close"] * bar["volume"] for bar in bars[t - 20:t]) / 20
    return [
        closes[t] / closes[t - 1] - 1,
        closes[t] / closes[t - 5] - 1,
        closes[t] / closes[t - 20] - 1,
        closes[t] / series["sma_fast"][t] - 1,
        closes[t] / series["sma_slow"][t] - 1,
        series["rsi"][t] / 100 - 0.5,
        volatility,
        math.log(volume / average) if volume > 0 and average > 0 else 0.0,
        net / traded if traded > 0 else 0.0,
    ]


# ------------------------------------------------------------------ model ----


class Model:
    """Logistic regression trained one example at a time (SGD), on inputs
    standardised with running means and variances."""

    def __init__(self, size):
        self.weights = [0.0] * size
        self.bias = 0.0
        self.seen = 0
        self.mean = [0.0] * size
        self.m2 = [0.0] * size

    def observe(self, x):
        """Update the running mean/variance used for scaling (inputs only, no outcomes)."""
        self.seen += 1
        for i, value in enumerate(x):
            delta = value - self.mean[i]
            self.mean[i] += delta / self.seen
            self.m2[i] += delta * (value - self.mean[i])

    def scale(self, x):
        if self.seen < 2:
            return [0.0] * len(x)
        out = []
        for value, mean, m2 in zip(x, self.mean, self.m2):
            std = math.sqrt(m2 / (self.seen - 1))
            out.append(max(-SCALE_CLIP, min(SCALE_CLIP, (value - mean) / std)) if std > 0 else 0.0)
        return out

    def _p(self, scaled):
        z = self.bias + sum(w * v for w, v in zip(self.weights, scaled))
        return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))

    def p_up(self, x):
        return self._p(self.scale(x))

    def learn(self, x, went_up):
        scaled = self.scale(x)
        error = self._p(scaled) - went_up
        self.weights = [w - LEARNING_RATE * (error * v + L2 * w) for w, v in zip(self.weights, scaled)]
        self.bias -= LEARNING_RATE * error


def walk_forward(histories, now=None, flows=None):
    """Train day by day across all stocks, always predicting before learning.

    On each date the model first predicts every stock, then learns from the
    ones whose next close is already final. So each prediction comes from a
    model that had never seen that day's outcome, exactly like a live run.

    histories: {symbol: (bars, series)} with series from watch.indicators().
    flows: {symbol: [(date, signed big-deal value), ...]} (optional).
    Returns {"model", "scored": [(date, symbol, p_up, went_up, up_today)],
             "p": {symbol: {date: p_up}}, "latest": {symbol: {"date", "p_up", "final"}}}.
    """
    now = now or watch.now_ist()
    days = {}
    for symbol, (bars, series) in histories.items():
        deal_flow = (flows or {}).get(symbol, ())
        for t in range(len(bars)):
            x = features(bars, series, t, deal_flow)
            if x is None:
                continue
            went_up = None
            if t + 1 < len(bars) and watch.is_final(bars[t + 1]["date"], now):
                went_up = 1 if bars[t + 1]["close"] > bars[t]["close"] else 0
            up_today = 1 if bars[t]["close"] > bars[t - 1]["close"] else 0
            days.setdefault(bars[t]["date"], []).append((symbol, x, went_up, up_today))

    learner = Model(len(FEATURES))
    scored, p_by_symbol, latest = [], {}, {}
    for date in sorted(days):
        rows = days[date]
        for _, x, _, _ in rows:
            learner.observe(x)
        for symbol, x, went_up, up_today in rows:
            p = learner.p_up(x)
            p_by_symbol.setdefault(symbol, {})[date] = p
            latest[symbol] = {"date": date, "p_up": p}
            if went_up is not None:
                scored.append((date, symbol, p, went_up, up_today))
        for _, x, went_up, _ in rows:  # learn only after every stock got its prediction
            if went_up is not None:
                learner.learn(x, went_up)
    for item in latest.values():
        item["final"] = watch.is_final(item["date"], now)
    return {"model": learner, "scored": scored, "p": p_by_symbol, "latest": latest}


# ------------------------------------------------------------- scoreboard ----


def _pct(hits, total):
    return round(100 * hits / total, 1) if total else None


def _summary(rows):
    """Hit rate next to naive baselines. In a falling market "always down" is
    the one to beat, so the model has to beat the better of the two."""
    n = len(rows)
    ups = sum(up for _, _, _, up, _ in rows)
    return {
        "n": n,
        "hit_rate_pct": _pct(sum((p >= 0.5) == (up == 1) for _, _, p, up, _ in rows), n),
        "always_up_pct": _pct(ups, n),
        "always_down_pct": _pct(n - ups, n),
        "same_as_today_pct": _pct(sum(up == today for _, _, _, up, today in rows), n),
    }


def replay_stats(scored):
    """Walk-forward hit rates (after the burn-in) next to the two baselines."""
    dates = sorted({row[0] for row in scored})
    if len(dates) <= BURN_IN_DAYS:
        return None
    start, recent = dates[BURN_IN_DAYS], dates[-60]
    rows = [row for row in scored if row[0] >= start]
    per_symbol = {}
    for row in rows:
        per_symbol.setdefault(row[1], []).append(row)
    return {
        "from": start,
        "to": dates[-1],
        "all": _summary(rows),
        "last_60_days": _summary([row for row in rows if row[0] >= recent]),
        "confident": _summary([row for row in rows if abs(row[2] - 0.5) >= CONFIDENT]),
        "per_symbol": {symbol: _summary(items) for symbol, items in per_symbol.items()},
    }


def explain(learner):
    """Learned weight per input (on standardised inputs); + pushes the up-chance up."""
    items = [{"feature": key, "label": label, "weight": round(weight, 4)}
             for (key, label), weight in zip(FEATURES, learner.weights)]
    return sorted(items, key=lambda item: -abs(item["weight"]))


# --------------------------------------------------------------- live log ----


def load_predictions(path=None):
    rows = {}
    path = path or PREDICTIONS_PATH
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                rows[(row["date"], row["symbol"])] = {field: row.get(field) or "" for field in PREDICTION_FIELDS}
    return rows


def save_predictions(rows, path=None):
    path = path or PREDICTIONS_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PREDICTION_FIELDS)
        writer.writeheader()
        writer.writerows(rows[key] for key in sorted(rows))
    os.replace(tmp_path, path)


def update_live_log(rows, latest, histories, now=None):
    """Check waiting predictions against real closes, then add today's.

    Only final candles count and rows are keyed by (date, symbol), so running
    twice on the same day changes nothing. Returns `rows` (updated in place).
    """
    now = now or watch.now_ist()
    for row in rows.values():
        if row["actual"] or row["symbol"] not in histories:
            continue
        bars = histories[row["symbol"]][0]
        base = next((bar for bar in bars if bar["date"] == row["date"]), None)
        after = next((bar for bar in bars if bar["date"] > row["date"]), None)
        if base is None or after is None or not watch.is_final(after["date"], now):
            continue
        went_up = after["close"] > base["close"]
        row.update(next_date=after["date"], next_close=f"{after['close']:.2f}",
                   actual="UP" if went_up else "DOWN",
                   correct="1" if (row["call"] == "UP") == went_up else "0")
    for symbol, item in latest.items():
        key = (item["date"], symbol)
        if not item["final"] or rows.get(key, {}).get("actual"):
            continue
        close = next(bar["close"] for bar in histories[symbol][0] if bar["date"] == item["date"])
        rows[key] = {"date": item["date"], "symbol": symbol, "close": f"{close:.2f}",
                     "p_up": f"{item['p_up']:.4f}", "call": "UP" if item["p_up"] >= 0.5 else "DOWN",
                     "next_date": "", "next_close": "", "actual": "", "correct": ""}
    return rows


def live_stats(rows, watchlist):
    """Scoreboard of the predictions made live (logged before the outcome)."""
    def item(row):
        return {"date": row["date"], "symbol": row["symbol"], "p_up": float(row["p_up"]),
                "call": row["call"], "next_date": row["next_date"] or None,
                "actual": row["actual"] or None,
                "correct": None if not row["correct"] else row["correct"] == "1"}

    ordered = sorted(rows.values(), key=lambda row: (row["date"], row["symbol"]))
    scored = [row for row in ordered if row["actual"]]
    waiting = [row for row in ordered if not row["actual"] and row["symbol"] in watchlist]
    newest_wait = max((row["date"] for row in waiting), default=None)
    last_date = scored[-1]["date"] if scored else None
    last_rows = [row for row in scored if row["date"] == last_date]
    return {
        "since": ordered[0]["date"] if ordered else None,
        "n": len(scored),
        "hit_rate_pct": _pct(sum(row["correct"] == "1" for row in scored), len(scored)),
        "always_up_pct": _pct(sum(row["actual"] == "UP" for row in scored), len(scored)),
        "always_down_pct": _pct(sum(row["actual"] == "DOWN" for row in scored), len(scored)),
        "last_day": {"date": last_date, "right": sum(row["correct"] == "1" for row in last_rows),
                     "total": len(last_rows)} if last_date else None,
        "recent": [item(row) for row in reversed(scored[-30:])],
        "pending": [item(row) for row in waiting if row["date"] == newest_wait],
    }
