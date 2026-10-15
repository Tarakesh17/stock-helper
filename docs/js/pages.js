/* One function per page. Each gets the data.json object and returns elements to show. */
(function () {
  "use strict";
  const SH = window.SH;
  const h = SH.h;
  const pages = (SH.pages = {});

  // ------------------------------------------------------------ small parts
  const card = (title, ...children) => h("section", { class: "card" }, title ? h("h2", null, title) : null, children);
  const note = (text) => h("p", { class: "note" }, text);
  const stat = (label, value, sub, cls) => h("div", { class: "stat" },
    h("span", { class: "label" }, label), h("span", { class: "value " + (cls || "") }, value), sub ? h("span", { class: "sub" }, sub) : null);
  const external = (href, text) => h("a", { href, target: "_blank", rel: "noopener noreferrer" }, text);
  const addLink = (symbol, text) => h("a", { class: "small", href: "#/watchlist?add=" + encodeURIComponent(symbol) }, text);
  const best = (r) => Math.max(...[r.always_up_pct, r.always_down_pct, r.same_as_today_pct].filter((v) => v != null));

  function problems(data) {
    const items = (data.issues || []).concat((data.failed || []).map((f) => f.symbol + ": " + f.error));
    if (!items.length) return null;
    return h("div", { class: "banner warn", role: "note" },
      h("strong", null, "Issues in the last update"), h("ul", null, items.map((text) => h("li", null, text))));
  }

  // ================================================================ overview
  pages.overview = function (data) {
    const cards = data.stocks.map((s) => {
      const was = s.prev_signal && s.prev_signal !== s.signal ? " · was " + s.prev_signal : "";
      return h("a", { class: "card stock-card", href: SH.stockHref(s.symbol) },
        h("div", { class: "row" }, h("strong", { class: "sym" }, SH.short(s.symbol)), SH.badge(s.signal)),
        h("div", { class: "name muted" }, s.name),
        h("div", { class: "row" }, h("span", { class: "price" }, SH.money(s.price)),
          h("span", { class: "chg " + SH.signClass(s.change_pct) }, SH.pct(s.change_pct))),
        SH.sparkline(s.series.close.slice(-60), SH.signClass(s.change_pct)),
        h("div", { class: "meta" }, "RSI " + SH.num(s.rsi, 1) + " · up-chance " + SH.chance(s.p_up)),
        h("div", { class: "meta muted" }, "Signal since " + SH.date(s.signal_since) + was));
    });
    return [
      problems(data),
      h("h1", null, "Signals"),
      cards.length ? h("div", { class: "cards" }, cards) : SH.empty("No stocks yet. Add some on the Watchlist page."),
      copyBox(data),
      h("div", { class: "strips" }, agentsStrip(data), modelStrip(data)),
    ];
  };

  function copyBox(data) {
    const text = ["date,symbol,close,signal"].concat(
      data.stocks.map((s) => [s.date, s.symbol, s.price.toFixed(2), s.signal].join(","))).join("\n");
    const status = h("span", { class: "muted", role: "status" });
    const button = h("button", { type: "button", class: "btn" }, "Copy signals for the sim app");
    button.addEventListener("click", () => SH.copy(text).then(
      () => { status.textContent = "Copied"; },
      () => { status.textContent = "Copy didn't work here: select the text below instead"; }));
    return h("div", { class: "copy" }, button, " ", status,
      h("details", null, h("summary", null, "Show the text"), h("pre", null, text)));
  }

  function agentsStrip(data) {
    const live = data.agents && data.agents.live;
    if (!live) return h("a", { class: "strip", href: "#/agents" }, h("strong", null, "Sim agents"), h("span", null, "Start after the first market-close run."));
    const parts = live.agents.map((a) => a.name + " " + SH.pct(a.return_pct));
    if (live.benchmark) parts.push(live.benchmark.name + " " + SH.pct(live.benchmark.return_pct));
    return h("a", { class: "strip", href: "#/agents" }, h("strong", null, "Sim agents since " + SH.date(live.start)), h("span", null, parts.join(" · ")));
  }

  function modelStrip(data) {
    const live = data.model && data.model.live;
    if (!live || !live.n) {
      const waiting = live && live.pending.length ? " · " + live.pending.length + " calls waiting" : "";
      return h("a", { class: "strip", href: "#/model" }, h("strong", null, "Model"), h("span", null, "First results after the next trading day" + waiting));
    }
    return h("a", { class: "strip", href: "#/model" }, h("strong", null, "Model, live"),
      h("span", null, `${SH.plainPct(live.hit_rate_pct)} right over ${live.n} calls (always up ${SH.plainPct(live.always_up_pct)}, always down ${SH.plainPct(live.always_down_pct)})`));
  }

  // =================================================================== stock
  pages.stock = function (data, symbol) {
    const s = data.stocks.find((x) => x.symbol === symbol);
    if (!s) {
      return [h("h1", null, "Not found"),
        h("p", null, (symbol || "That stock") + " isn't in the latest data. ", addLink(symbol || "", "Add it to the watchlist"), " or ", h("a", { href: "#/" }, "go back"), ".")];
    }
    const cfg = data.config, sr = s.series;
    const markers = [];
    sr.signal.forEach((sig, i) => {
      if (i > 0 && sr.signal[i - 1] && sig !== sr.signal[i - 1] && (sig === "BUY" || sig === "SELL")) markers.push({ index: i, kind: sig.toLowerCase() });
    });
    const day = (i) => SH.date(sr.dates[i]);
    const price = SH.lineChart({
      label: "Closing price with moving averages", dates: sr.dates, markers,
      series: [{ name: "Close", values: sr.close, cls: "s-close" },
        { name: "SMA" + cfg.sma_fast, values: sr.sma_fast, cls: "s-fast" },
        { name: "SMA" + cfg.sma_slow, values: sr.sma_slow, cls: "s-slow" }],
      tooltip: (i) => [day(i), "Close " + SH.money(sr.close[i]), "SMA" + cfg.sma_fast + " " + SH.num(sr.sma_fast[i]),
        "SMA" + cfg.sma_slow + " " + SH.num(sr.sma_slow[i]), "RSI " + SH.num(sr.rsi[i], 1), "Signal " + (sr.signal[i] || "–")],
    });
    const rsi = SH.lineChart({
      label: "RSI", dates: sr.dates, height: 150, min: 0, max: 100, legend: false, format: (v) => v.toFixed(0),
      series: [{ name: "RSI", values: sr.rsi, cls: "s-rsi" }],
      guides: [{ value: 30 }, { value: cfg.rsi_overbought }],
      tooltip: (i) => [day(i), "RSI " + SH.num(sr.rsi[i], 1)],
    });
    const chance = SH.lineChart({
      label: "Model up-chance", dates: sr.dates, height: 150, legend: false, format: (v) => Math.round(v * 100) + "%",
      series: [{ name: "Up-chance", values: sr.p_up, cls: "s-model" }], guides: [{ value: 0.5 }],
      tooltip: (i) => [day(i), "Up-chance " + SH.chance(sr.p_up[i])],
    });

    const fromHigh = s.high_52w ? (s.price / s.high_52w - 1) * 100 : null;
    const fromLow = s.low_52w ? (s.price / s.low_52w - 1) * 100 : null;
    const was = s.prev_signal && s.prev_signal !== s.signal ? " (was " + s.prev_signal + ")" : "";
    return [
      h("div", { class: "stock-head" },
        h("h1", null, SH.short(s.symbol), " ", SH.badge(s.signal)),
        h("p", { class: "muted" }, s.name + " · " + s.symbol),
        h("p", { class: "big" }, SH.money(s.price), " ", h("span", { class: "chg " + SH.signClass(s.change_pct) }, SH.pct(s.change_pct))),
        h("p", { class: "muted" }, "Signal " + s.signal + " since " + SH.date(s.signal_since) + was + " · data " + SH.date(s.date) + (s.final ? "" : " (not final)"))),
      card("Price", price, note("Triangles mark where the rule switched to BUY (up) or SELL (down). Tap or hover for values.")),
      card("RSI (" + cfg.rsi_period + ")", rsi, note("Above " + cfg.rsi_overbought + " the rule calls SELL (overbought); 30 is the usual oversold line.")),
      card("Model up-chance", chance, note("The model's chance that the next close is higher, as it stood each day. 50% = no idea.")),
      card("Key numbers", h("div", { class: "stats" },
        stat("RSI", SH.num(s.rsi, 1)),
        stat("SMA" + cfg.sma_fast, SH.money(s.sma_fast)),
        stat("SMA" + cfg.sma_slow, SH.money(s.sma_slow)),
        stat("52-week high", SH.money(s.high_52w), SH.pct(fromHigh) + " from it"),
        stat("52-week low", SH.money(s.low_52w), SH.pct(fromLow) + " from it"),
        stat("Model up-chance", SH.chance(s.p_up), "next trading day"))),
      backtestCard(s.backtest, cfg),
      stockReplayCard(s.model_replay),
      card("Signal changes", s.signal_changes.length
        ? SH.table(["Date", "Change", "Close"], s.signal_changes.map((c) =>
          [SH.date(c.date), h("span", null, SH.badge(c.from), " → ", SH.badge(c.to)), SH.money(c.close)]), { numeric: [2] })
        : SH.empty("No changes in the last year.")),
      stockDealsCard(s),
    ];
  };

  function backtestCard(b, cfg) {
    if (!b) return card("Rule vs buy & hold", SH.empty("Not enough history yet."));
    return card("Rule vs buy & hold",
      h("p", { class: "muted" }, "Since " + SH.date(b.start) + ": following the BUY/SELL rule on this stock alone (fills at the next open, " +
        cfg.cost_pct + "% cost per trade) compared with buying once and holding."),
      h("div", { class: "stats" },
        stat("Rule", SH.pct(b.strategy_pct), null, SH.signClass(b.strategy_pct)),
        stat("Buy & hold", SH.pct(b.buy_hold_pct), null, SH.signClass(b.buy_hold_pct)),
        stat("Trades", SH.int(b.trades)),
        stat("Win rate", SH.plainPct(b.win_rate_pct)),
        stat("Worst drop", SH.pct(b.max_drawdown_pct)),
        stat("Time in market", SH.plainPct(b.in_market_pct, 0))),
      note("Past-year replay; live results are usually worse."));
  }

  function stockReplayCard(r) {
    if (!r || !r.n) return card("Model on this stock (replay)", SH.empty("Not enough history yet."));
    const top = best(r);
    return card("Model on this stock (replay)",
      h("div", { class: "stats" },
        stat("Model right", SH.plainPct(r.hit_rate_pct)),
        stat("Always up", SH.plainPct(r.always_up_pct)),
        stat("Always down", SH.plainPct(r.always_down_pct)),
        stat("Same as today", SH.plainPct(r.same_as_today_pct))),
      h("p", null, (r.hit_rate_pct > top ? "Beats" : "Does not beat") + " the best simple guess (" + SH.plainPct(top) + ") over " + r.n + " scored days."));
  }

  function stockDealsCard(s) {
    const d = s.deals;
    if (!d) return card("Big deals", SH.empty("NSE deals aren't available for BSE symbols."));
    return card("Big deals in " + SH.short(s.symbol),
      h("p", null, "Last 30 days (since " + SH.date(d.since) + "): bought " + SH.crore(d.buy) + ", sold " + SH.crore(d.sell) +
        ", net " + (d.net >= 0 ? "buying " : "selling ") + SH.crore(d.net) + ". " + d.count + " deals in the stored history."),
      d.deals.length ? dealTable(d.deals, false) : SH.empty("No bulk or block deals in the stored history."));
  }

  // ------------------------------------------------------------ deal tables
  function symbolCell(deal, watch) {
    const symbol = deal.symbol + ".NS";
    const tracked = deal.in_watchlist || watch.has(symbol);
    return h("span", { class: "sym-cell" },
      tracked ? h("a", { href: SH.stockHref(symbol) }, deal.symbol) : h("strong", null, deal.symbol),
      deal.name ? h("span", { class: "muted small" }, " " + deal.name) : null,
      tracked ? null : [" ", addLink(symbol, "+ watch")]);
  }

  function dealTable(deals, withSymbol, watch) {
    const headers = (withSymbol ? ["Stock"] : []).concat(["Date", "Client", "Side", "Qty", "Price", "Value", "Kind"]);
    const rows = deals.map((d) => (withSymbol ? [symbolCell(d, watch)] : []).concat([
      SH.date(d.date), d.client, h("span", { class: "side " + (d.side === "BUY" ? "up" : "down") }, d.side),
      SH.int(d.qty), SH.num(d.price), SH.crore(d.value), d.kind]));
    const k = withSymbol ? 1 : 0;
    return SH.table(headers, rows, { numeric: [3 + k, 4 + k, 5 + k] });
  }

  // ================================================================ holdings
  const HOLDINGS_KEY = "stock-helper.holdings";

  function validHolding(x, pattern) {
    return !!x && typeof x === "object" && typeof x.symbol === "string" && pattern.test(x.symbol) &&
      typeof x.qty === "number" && isFinite(x.qty) && x.qty > 0 &&
      typeof x.avg_price === "number" && isFinite(x.avg_price) && x.avg_price > 0 &&
      (x.buy_date == null || (typeof x.buy_date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(x.buy_date)));
  }

  function normaliseSymbol(text, pattern) {
    let symbol = String(text || "").trim().toUpperCase();
    if (symbol && !/\.(NS|BO)$/.test(symbol)) symbol += ".NS";
    return pattern.test(symbol) ? symbol : null;
  }

  const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);

  pages.holdings = function (data) {
    const pattern = new RegExp(data.config.symbol_pattern);
    const prices = {};
    data.stocks.forEach((s) => { prices[s.symbol] = s; });
    const stored = SH.store.get(HOLDINGS_KEY);
    let list = Array.isArray(stored) ? stored.filter((x) => validHolding(x, pattern)) : [];
    let editing = null;
    const status = h("p", { class: "status", role: "status" });
    const holder = h("div");
    const save = () => SH.store.set(HOLDINGS_KEY, list);

    // ---- form
    const field = (label, input) => h("label", { class: "field" }, h("span", null, label), input);
    const symbol = h("input", { list: "holding-symbols", autocomplete: "off", placeholder: "RELIANCE", name: "symbol" });
    const options = h("datalist", { id: "holding-symbols" }, data.watchlist.map((s) => h("option", { value: s })));
    symbol.addEventListener("focus", () => SH.loadSymbols().then((all) => {
      all.forEach(([s, name]) => { if (!data.watchlist.includes(s)) options.appendChild(h("option", { value: s }, name)); });
    }).catch(() => {}), { once: true });
    const qty = h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", name: "qty" });
    const price = h("input", { type: "number", min: "0", step: "any", inputmode: "decimal", name: "avg_price" });
    const date = h("input", { type: "date", name: "buy_date" });
    const submit = h("button", { type: "submit", class: "btn primary" }, "Add holding");
    const cancel = h("button", { type: "button", class: "btn ghost hidden" }, "Cancel edit");
    const form = h("form", { class: "form-grid", novalidate: true },
      field("Stock symbol", symbol), options, field("Quantity", qty), field("Average buy price (₹)", price),
      field("Buy date (optional)", date), h("div", { class: "controls" }, submit, cancel));

    function resetForm() {
      editing = null;
      form.reset();
      submit.textContent = "Add holding";
      cancel.classList.add("hidden");
    }
    function startEdit(x) {
      editing = x.id;
      symbol.value = x.symbol; qty.value = x.qty; price.value = x.avg_price; date.value = x.buy_date || "";
      submit.textContent = "Save changes";
      cancel.classList.remove("hidden");
    }
    cancel.addEventListener("click", () => { resetForm(); status.textContent = "Edit cancelled."; });
    form.addEventListener("submit", (evt) => {
      evt.preventDefault();
      const row = { id: editing || newId(), symbol: normaliseSymbol(symbol.value, pattern) || "",
        qty: Number(qty.value), avg_price: Number(price.value), buy_date: date.value || null };
      if (!validHolding(row, pattern)) {
        status.textContent = "Check the fields: a valid symbol (like RELIANCE or RELIANCE.NS), a quantity above 0 and a price above 0.";
        return;
      }
      list = editing ? list.map((x) => (x.id === editing ? row : x)) : list.concat([row]);
      save();
      status.textContent = (editing ? "Updated " : "Added ") + row.symbol + ".";
      resetForm();
      draw();
    });

    // ---- table
    function draw() {
      SH.clear(holder);
      if (!list.length) { holder.appendChild(SH.empty("No holdings yet. Add one above.")); return; }
      const rows = list.map((x) => {
        const s = prices[x.symbol];
        const invested = x.qty * x.avg_price;
        const value = s ? x.qty * s.price : null;
        return { x, s, invested, value, pnl: value == null ? null : value - invested };
      });
      const tracked = rows.filter((r) => r.s);
      const totalValue = tracked.reduce((sum, r) => sum + r.value, 0);
      const totalInvested = tracked.reduce((sum, r) => sum + r.invested, 0);
      const totalPnl = totalValue - totalInvested;
      const body = rows.map((r) => {
        const edit = h("button", { type: "button", class: "btn small ghost" }, "Edit");
        edit.addEventListener("click", () => startEdit(r.x));
        const remove = h("button", { type: "button", class: "btn small ghost" }, "Delete");
        remove.addEventListener("click", () => {
          list = list.filter((y) => y.id !== r.x.id);
          save();
          if (editing === r.x.id) resetForm();
          status.textContent = "Deleted " + r.x.symbol + ".";
          draw();
        });
        const name = r.s ? h("a", { href: SH.stockHref(r.x.symbol) }, SH.short(r.x.symbol))
          : h("span", null, SH.short(r.x.symbol), " ", addLink(r.x.symbol, "Not tracked: add to watchlist"));
        const colored = (v, text) => h("span", { class: SH.signClass(v) }, text);
        return [name, SH.num(r.x.qty, Number.isInteger(r.x.qty) ? 0 : 2), SH.num(r.x.avg_price), r.s ? SH.num(r.s.price) : "–",
          SH.money(r.invested), SH.money(r.value),
          r.pnl == null ? "–" : colored(r.pnl, SH.money(r.pnl)),
          r.pnl == null ? "–" : colored(r.pnl, SH.pct((r.pnl / r.invested) * 100)),
          r.value == null || !totalValue ? "–" : SH.plainPct((r.value / totalValue) * 100),
          r.s ? SH.badge(r.s.signal) : "–", h("span", { class: "row-actions" }, edit, remove)];
      });
      body.push([h("strong", null, "Total (tracked)"), "", "", "", SH.money(totalInvested), SH.money(totalValue),
        h("span", { class: SH.signClass(totalPnl) }, SH.money(totalPnl)),
        totalInvested ? h("span", { class: SH.signClass(totalPnl) }, SH.pct((totalPnl / totalInvested) * 100)) : "–", "", "", ""]);
      holder.appendChild(SH.table(["Stock", "Qty", "Avg price", "Last", "Invested", "Value", "P/L", "P/L %", "Weight", "Rule signal", ""],
        body, { numeric: [1, 2, 3, 4, 5, 6, 7, 8] }));
      if (tracked.length < rows.length) holder.appendChild(note("Totals include only stocks in your watchlist, since only those have prices here."));
      holder.appendChild(note("Prices are closes from " + SH.date(data.as_of) + (data.final ? "." : " (market still open, not final).")));
    }

    // ---- backup
    const exportButton = h("button", { type: "button", class: "btn ghost" }, "Export JSON");
    exportButton.addEventListener("click", () => {
      const url = URL.createObjectURL(new Blob([JSON.stringify(list, null, 2)], { type: "application/json" }));
      const link = h("a", { href: url, download: "holdings.json" });
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    const file = h("input", { type: "file", accept: "application/json,.json", class: "file", "aria-label": "Import holdings JSON" });
    file.addEventListener("change", () => {
      const chosen = file.files && file.files[0];
      if (chosen) importText(chosen.text());
    });
    function importText(promise) {
      return Promise.resolve(promise).then((text) => {
        let rows;
        try { rows = JSON.parse(text); } catch (e) { rows = null; }
        if (!Array.isArray(rows)) throw new Error("the file isn't a JSON list");
        const bad = rows.findIndex((x) => !validHolding(x, pattern));
        if (bad >= 0) throw new Error("row " + (bad + 1) + " is not a valid holding");
        list = rows.map((x) => ({ id: typeof x.id === "string" ? x.id : newId(), symbol: x.symbol, qty: x.qty, avg_price: x.avg_price, buy_date: x.buy_date || null }));
        save();
        resetForm();
        status.textContent = "Imported " + list.length + " holdings.";
        draw();
      }).catch((err) => { status.textContent = "Import failed: " + err.message + ". Nothing was changed."; });
    }
    pages.holdings.importText = importText; // used by the tests

    draw();
    return [
      h("h1", null, "My holdings"),
      note("Saved only in this browser. Nothing is uploaded. The signal column is the site's mechanical SMA/RSI rule, not advice for your own money."),
      card("Add or edit", form, status),
      card("Holdings", holder),
      card("Backup", h("div", { class: "controls" }, exportButton, h("label", { class: "btn ghost" }, "Import JSON", file)),
        note("Importing replaces the list in this browser.")),
    ];
  };

  // ================================================================== agents
  pages.agents = function (data) {
    const a = data.agents || {};
    const st = data.model.settings;
    return [
      h("h1", null, "Sim agents"),
      card("How they trade", h("ul", null,
        h("li", null, "Each agent gets ₹" + SH.int(data.config.start_cash) + " of virtual money and an equal slice per watchlist stock."),
        h("li", null, "Rules agent: buys on a BUY signal, sells on a SELL signal."),
        h("li", null, "Model agent: buys when the up-chance is " + Math.round(st.agent_buy_at * 100) + "% or more, sells below " + Math.round(st.agent_sell_below * 100) + "%."),
        h("li", null, "Decisions use the day's close; orders fill at the next trading day's open, with " + data.config.cost_pct + "% cost on every buy and sell."),
        h("li", null, "Both are compared with simply holding the " + data.config.benchmark + ".")),
      note("Virtual money only. No real orders are ever placed.")),
      report("Live", a.live, "The live agents start with the first run after a market close.", null),
      report("Backtest", a.backtest, "Not enough history for a backtest yet.",
        "Backtest: the same rules replayed over the past year. Backtests usually look better than live trading."),
    ];
  };

  function report(title, rep, emptyText, caveat) {
    if (!rep) return card(title, SH.empty(emptyText));
    const dates = rep.agents[0].curve.map((p) => p[0]);
    const lines = rep.agents.map((ag) => ({ name: ag.name, curve: ag.curve, cls: "s-" + ag.id }));
    if (rep.benchmark) lines.push({ name: rep.benchmark.name, curve: rep.benchmark.curve, cls: "s-bench" });
    lines.forEach((line) => {
      const byDate = new Map(line.curve);
      line.values = dates.map((d) => (byDate.has(d) ? byDate.get(d) : null));
    });
    const chart = dates.length > 1
      ? SH.lineChart({ label: title + " equity", dates, series: lines,
        tooltip: (i) => [SH.date(dates[i])].concat(lines.map((l) => l.name + " " + SH.money(l.values[i]))) })
      : SH.empty("The chart appears after the second trading day.");
    return card(title + " · since " + SH.date(rep.start), caveat ? note(caveat) : null, chart,
      h("div", { class: "agent-cards" }, rep.agents.map((ag) => agentCard(ag, rep))),
      rep.agents.map(agentDetails));
  }

  function agentCard(ag, rep) {
    const vs = rep.benchmark ? ag.return_pct - rep.benchmark.return_pct : null;
    return h("div", { class: "card inner" }, h("h3", null, ag.name), h("p", { class: "muted small" }, ag.description),
      h("div", { class: "stats" },
        stat("Equity", SH.money(ag.equity)),
        stat("Return", SH.pct(ag.return_pct), vs == null ? null : SH.pct(vs) + " vs " + rep.benchmark.name, SH.signClass(ag.return_pct)),
        stat("Worst drop", SH.pct(ag.max_drawdown_pct)),
        stat("Trades", SH.int(ag.trades), SH.int(ag.closed_trades) + " closed"),
        stat("Win rate", SH.plainPct(ag.win_rate_pct)),
        stat("Cash", SH.money(ag.cash))));
  }

  function agentDetails(ag) {
    const short = SH.short;
    return h("details", { class: "more" }, h("summary", null, ag.name + ": positions, orders and trades"),
      h("h4", null, "Positions"),
      ag.positions.length ? SH.table(["Stock", "Qty", "Avg price", "Last", "Value", "P/L"], ag.positions.map((p) => [
        short(p.symbol), SH.int(p.qty), SH.num(p.avg_price), SH.num(p.last), SH.money(p.value),
        h("span", { class: SH.signClass(p.pnl) }, SH.money(p.pnl) + " (" + SH.pct(p.pnl_pct) + ")")]), { numeric: [1, 2, 3, 4, 5] })
        : SH.empty("No positions."),
      h("h4", null, "Orders for the next open"),
      ag.orders.length ? h("ul", null, ag.orders.map((o) => h("li", null, o.side + " " + short(o.symbol) + " (" + o.reason + ", decided " + SH.date(o.date) + ")")))
        : SH.empty("No orders queued."),
      h("h4", null, "Recent trades"),
      ag.recent_trades.length ? SH.table(["Date", "Stock", "Side", "Qty", "Price", "P/L", "Why"], ag.recent_trades.map((t) => [
        SH.date(t.date), short(t.symbol), t.side, SH.int(t.qty), SH.num(t.price),
        t.pnl == null ? "–" : h("span", { class: SH.signClass(t.pnl) }, SH.money(t.pnl)), t.reason]), { numeric: [3, 4, 5] })
        : SH.empty("No trades yet."));
  }

  // =================================================================== model
  pages.model = function (data) {
    const m = data.model;
    return [
      h("h1", null, "Model"),
      card("What it does", h("ol", null,
        h("li", null, "After each close it checks yesterday's calls against the real closing prices."),
        h("li", null, "It retrains from scratch on all the history, walking forward one day at a time so it never sees the future."),
        h("li", null, "It predicts each stock's chance of closing higher on the next trading day.")),
      note("Day-ahead calls are close to a coin flip. The model only adds something if it beats the best of three simple guesses: always up, always down, and same direction as today. Simulation only.")),
      liveCard(m.live),
      replayCard(m.replay, m.settings),
      card("What the model leans on", m.features.length ? SH.barChart(m.features.map((f) => ({ label: f.label, value: f.weight }))) : SH.empty("No weights yet."),
        note("Weights on standardised inputs: positive pushes the up-chance up, negative pushes it down. They shift as the model retrains.")),
    ];
  };

  function liveCard(live) {
    if (!live) return card("Live scoreboard", SH.empty("No live data yet."));
    const body = [];
    if (live.n) {
      const top = Math.max(live.always_up_pct, live.always_down_pct);
      body.push(h("div", { class: "stats" },
        stat("Calls scored", SH.int(live.n), "since " + SH.date(live.since)),
        stat("Model right", SH.plainPct(live.hit_rate_pct)),
        stat("Always up", SH.plainPct(live.always_up_pct)),
        stat("Always down", SH.plainPct(live.always_down_pct))));
      body.push(h("p", null, live.hit_rate_pct > top ? "So far it beats both constant guesses." : "So far it does not beat the better constant guess."));
      if (live.last_day) body.push(h("p", null, "Last scored day " + SH.date(live.last_day.date) + ": " + live.last_day.right + " of " + live.last_day.total + " right."));
      body.push(SH.table(["Date", "Stock", "Up-chance", "Call", "Actual", "Result"], live.recent.map((r) => [
        SH.date(r.date), SH.short(r.symbol), SH.chance(r.p_up), r.call, r.actual || "–",
        r.correct == null ? "–" : h("span", { class: r.correct ? "up" : "down" }, r.correct ? "✓ right" : "✗ wrong")]), { numeric: [2] }));
    } else {
      body.push(SH.empty("No calls scored yet. The first results come after the next trading day's close."));
    }
    if (live.pending.length) {
      body.push(h("h3", null, "Waiting for the next close"),
        SH.table(["Made on", "Stock", "Up-chance", "Call"], live.pending.map((r) => [SH.date(r.date), SH.short(r.symbol), SH.chance(r.p_up), r.call]), { numeric: [2] }));
    }
    return card("Live scoreboard", body);
  }

  function replayCard(rp, st) {
    if (!rp) return card("Replay of past days", SH.empty("Not enough history yet."));
    const rows = [["All days", rp.all], ["Last 60 days", rp.last_60_days],
      ["Confident calls (" + Math.round(st.confident * 100) + "+ points from 50%)", rp.confident]];
    return card("Replay of past days",
      note("Walk-forward replay from " + SH.date(rp.from) + " to " + SH.date(rp.to) + ", after skipping the first " + st.burn_in_days + " trading days while the model warms up."),
      SH.table(["Window", "Days", "Model", "Always up", "Always down", "Same as today", "Beats best?"], rows.map(([name, r]) => [
        name, SH.int(r.n), SH.plainPct(r.hit_rate_pct), SH.plainPct(r.always_up_pct), SH.plainPct(r.always_down_pct),
        SH.plainPct(r.same_as_today_pct), r.n ? (r.hit_rate_pct > best(r) ? "Yes" : "No") : "–"]), { numeric: [1, 2, 3, 4, 5] }));
  }

  // =================================================================== deals
  pages.deals = function (data) {
    const d = data.deals || {};
    const watch = new Set(data.watchlist);
    const out = [h("h1", null, "Big deals"),
      note("NSE publishes bulk and block deals after the market closes, so this is end-of-day data, not real time. A bulk deal is one client trading more than 0.5% of a company's shares in a day; a block deal is a large trade in the separate block window." +
        (d.from ? " History since " + SH.date(d.from) + "." : ""))];
    if (d.note) out.push(h("div", { class: "banner warn" }, d.note));
    const latest = d.latest;
    if (!latest) {
      out.push(card("Latest session", SH.empty("No deals stored yet.")));
    } else {
      const only = h("input", { type: "checkbox", id: "only-watch" });
      const filter = h("input", { type: "search", placeholder: "Filter by client, stock or name", "aria-label": "Filter deals" });
      const count = h("p", { class: "muted", role: "status" });
      const holder = h("div");
      const draw = () => {
        const q = filter.value.trim().toLowerCase();
        const rows = latest.deals.filter((x) => (!only.checked || x.in_watchlist) &&
          (!q || (x.client + " " + x.symbol + " " + x.name).toLowerCase().includes(q)));
        count.textContent = "Showing " + rows.length + " of " + latest.count + " deals (the biggest, plus all in your watchlist).";
        SH.clear(holder);
        holder.appendChild(rows.length ? dealTable(rows, true, watch) : SH.empty("No deals match."));
      };
      only.addEventListener("change", draw);
      filter.addEventListener("input", draw);
      draw();
      out.push(card("Latest session · " + SH.date(latest.date),
        h("div", { class: "controls" }, h("label", { class: "check", for: "only-watch" }, only, " Only my watchlist"), filter), count, holder));
    }
    const net = d.net_30d;
    if (net) {
      const list = (title, items) => h("div", null, h("h3", null, title), items.length
        ? SH.table(["Stock", "Net", "Deals"], items.map((x) => [symbolCell(x, watch), SH.crore(x.net), SH.int(x.count)]), { numeric: [1, 2] })
        : SH.empty("None."));
      out.push(card("Net buying and selling, last 30 days",
        note("Since " + SH.date(net.from) + ". Net = value bought minus value sold in bulk and block deals."),
        h("div", { class: "two" }, list("Most bought", net.buys), list("Most sold", net.sells))));
    }
    return out;
  };

  // =============================================================== watchlist
  pages.watchlist = function (data, _arg, params) {
    const cfg = data.config;
    const pattern = new RegExp(cfg.symbol_pattern);
    const repo = /^[\w.-]+\/[\w.-]+$/.test(data.site.repo) ? data.site.repo : "Tarakesh17/stock-helper";
    const st = SH.state;
    if (!st.saved) st.saved = data.watchlist.slice();
    if (!st.draft) st.draft = st.saved.slice();
    const names = {};
    data.stocks.forEach((s) => { names[s.symbol] = s.name; });
    const listStatus = h("p", { class: "status", role: "status" });
    const saveStatus = h("p", { class: "status", role: "status" });
    const listBox = h("div");

    function addSymbol(symbol) {
      if (st.draft.includes(symbol)) { listStatus.textContent = symbol + " is already in the list."; return; }
      if (st.draft.length >= cfg.max_symbols) { listStatus.textContent = "The list is full (" + cfg.max_symbols + " stocks max)."; return; }
      st.draft.push(symbol);
      listStatus.textContent = "Added " + symbol + " (not saved yet).";
      drawList();
    }

    function drawList() {
      SH.clear(listBox);
      const added = st.draft.filter((s) => !st.saved.includes(s));
      const removed = st.saved.filter((s) => !st.draft.includes(s));
      listBox.appendChild(h("ul", { class: "watch-list" }, st.draft.map((symbol) => {
        const remove = h("button", { type: "button", class: "btn small ghost", "aria-label": "Remove " + symbol }, "Remove");
        remove.addEventListener("click", () => {
          st.draft = st.draft.filter((x) => x !== symbol);
          listStatus.textContent = "Removed " + symbol + " (not saved yet).";
          drawList();
        });
        return h("li", null, h("span", null,
          names[symbol] ? h("a", { href: SH.stockHref(symbol) }, symbol) : h("strong", null, symbol), " ",
          h("span", { class: "muted small" }, names[symbol] || ""),
          added.includes(symbol) ? h("span", { class: "tag" }, "new") : null), remove);
      })));
      if (!st.draft.length) listBox.appendChild(SH.empty("The list is empty. Add at least one stock before saving."));
      listBox.appendChild(h("p", { class: "muted small" }, st.draft.length + " of " + cfg.max_symbols + " stocks."));
      if (added.length || removed.length) {
        const discard = h("button", { type: "button", class: "btn small ghost" }, "Discard changes");
        discard.addEventListener("click", () => { st.draft = st.saved.slice(); listStatus.textContent = "Changes discarded."; drawList(); });
        const parts = [added.length ? "adding " + added.join(", ") : "", removed.length ? "removing " + removed.join(", ") : ""].filter(Boolean);
        listBox.appendChild(h("div", { class: "banner info" }, "Unsaved changes: " + parts.join("; ") + ". ", discard));
      }
    }

    // ---- search
    const search = h("input", { type: "search", placeholder: "Search NSE stocks by symbol or name", "aria-label": "Search stocks", autocomplete: "off" });
    const results = h("div");
    const resultRow = (symbol, label) => {
      const add = h("button", { type: "button", class: "btn small" }, "Add");
      add.addEventListener("click", () => addSymbol(symbol));
      return h("li", null, h("span", null, h("strong", null, symbol), " ", h("span", { class: "muted small" }, label)), add);
    };
    function showResults(all, query) {
      SH.clear(results);
      const q = query.trim().toUpperCase();
      if (!q) return;
      const starts = [], contains = [];
      for (const [symbol, name] of all) {
        if (symbol.startsWith(q)) starts.push([symbol, name]);
        else if (name.toUpperCase().includes(q)) contains.push([symbol, name]);
      }
      const top = starts.concat(contains).slice(0, 20);
      const rows = top.map(([symbol, name]) => resultRow(symbol, name));
      const typed = normaliseSymbol(q, pattern);
      if (typed && !top.some(([symbol]) => symbol === typed)) rows.unshift(resultRow(typed, "add exactly what you typed"));
      results.appendChild(rows.length ? h("ul", { class: "result-list" }, rows) : SH.empty("No matches."));
    }
    search.addEventListener("input", () => {
      const query = search.value;
      SH.loadSymbols().then((all) => showResults(all, query), () => {
        SH.clear(results);
        const typed = normaliseSymbol(query, pattern);
        results.appendChild(typed ? h("ul", { class: "result-list" }, resultRow(typed, "stock list unavailable; add as typed")) : SH.empty("Couldn't load the stock list."));
      });
    });

    // ---- token + save
    const tokenBox = h("div");
    function drawToken() {
      SH.clear(tokenBox);
      if (SH.token.get()) {
        const forget = h("button", { type: "button", class: "btn small ghost" }, "Forget token");
        forget.addEventListener("click", () => { SH.token.forget(); saveStatus.textContent = "Token removed from this browser."; drawToken(); });
        tokenBox.appendChild(h("p", null, "A GitHub token is saved in this browser. ", forget));
        return;
      }
      const input = h("input", { type: "password", id: "gh-token", autocomplete: "off", spellcheck: "false", placeholder: "github_pat_…" });
      const keep = h("button", { type: "button", class: "btn" }, "Save token");
      keep.addEventListener("click", () => {
        const value = input.value.trim();
        if (!SH.token.looksValid(value)) { saveStatus.textContent = "That doesn't look like a GitHub token (it starts with github_pat_)."; return; }
        SH.token.set(value);
        input.value = "";
        saveStatus.textContent = "Token saved in this browser.";
        drawToken();
      });
      tokenBox.appendChild(h("div", { class: "controls" }, h("label", { for: "gh-token" }, "GitHub token"), input, keep));
    }
    const saveButton = h("button", { type: "button", class: "btn primary" }, "Save watchlist to GitHub");
    saveButton.addEventListener("click", async () => {
      const token = SH.token.get();
      if (!token) { saveStatus.textContent = "Add a GitHub token first (steps below)."; return; }
      if (!st.draft.length) { saveStatus.textContent = "Add at least one stock before saving."; return; }
      saveButton.disabled = true;
      saveStatus.textContent = "Saving…";
      try {
        const result = await SH.saveWatchlist(repo, st.draft.slice(), token);
        if (result.ok) {
          st.saved = st.draft.slice();
          SH.clear(saveStatus);
          saveStatus.appendChild(document.createTextNode("Saved. GitHub is refreshing the data; this site updates in about 2 minutes. "));
          saveStatus.appendChild(external("https://github.com/" + repo + "/actions", "Watch the run"));
          drawList();
        } else {
          saveStatus.textContent = result.message;
        }
      } catch (err) {
        saveStatus.textContent = "Couldn't reach GitHub: " + err.message;
      }
      saveButton.disabled = false;
    });

    const added = params && params.get("add");
    if (added) {
      const symbol = normaliseSymbol(added, pattern);
      if (symbol) addSymbol(symbol); else listStatus.textContent = "“" + added + "” isn't a valid symbol.";
    }
    drawList();
    drawToken();
    return [
      h("h1", null, "Watchlist"),
      note("Up to " + cfg.max_symbols + " stocks. Changes apply after you save: GitHub re-runs the update and refreshes this site in about 2 minutes."),
      card("Your stocks", listStatus, listBox),
      card("Add stocks", search, results),
      card("Save", tokenBox, saveButton, saveStatus,
        h("details", { class: "more" }, h("summary", null, "How to create the token (one time)"),
          h("ol", null,
            h("li", null, "Open ", external("https://github.com/settings/personal-access-tokens/new", "GitHub: new fine-grained token"), "."),
            h("li", null, "Expiration: 90 days. Repository access: Only select repositories, then pick stock-helper."),
            h("li", null, "Permissions: Repository permissions, Contents: Read and write. Nothing else."),
            h("li", null, "Generate, copy the token, paste it above and press Save token.")),
          note("Anyone with this token can change this repo. It's kept only in this browser; remove it with Forget token. Never share it.")),
        h("p", { class: "muted small" }, "No token? ", external("https://github.com/" + repo + "/edit/main/watchlist.json", "Edit watchlist.json on GitHub instead"), ".")),
    ];
  };
})();
