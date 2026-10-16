/* Start-up, live updates and hash routing:
   #/, #/stock/SYMBOL, #/holdings, #/agents, #/model, #/deals, #/alerts, #/watchlist */
(function () {
  "use strict";
  const SH = window.SH;
  const h = SH.h;
  const app = document.getElementById("app");
  const TITLES = { overview: "Overview", stock: "Stock", holdings: "Holdings", agents: "Agents", model: "Model",
    deals: "Big deals", alerts: "Alerts", watchlist: "Watchlist" };
  const LIVE_EVERY = 2 * 60 * 1000; // live.json changes every 15 min in market hours; checking every 2 min is plenty

  // ------------------------------------------------------------------ live
  function istNow() { // the market's clock, whatever the phone's time zone is
    const d = new Date(Date.now() + 5.5 * 3600 * 1000);
    return { weekday: d.getUTCDay(), minutes: d.getUTCHours() * 60 + d.getUTCMinutes() };
  }
  const marketWindow = () => { const t = istNow(); return t.weekday >= 1 && t.weekday <= 5 && t.minutes >= 540 && t.minutes <= 990; };

  // The live quote for a stock, but only when it is newer than the daily data
  // (after 6 PM the official closing data wins).
  SH.liveQuote = function (symbol) {
    const q = SH.live && SH.live.quotes && SH.live.quotes[symbol];
    const d = SH.data;
    if (!q || !d) return null;
    return q.date > d.as_of || (q.date === d.as_of && !d.final) ? q : null;
  };

  async function loadLive() {
    const repo = SH.data && SH.data.site && SH.data.site.repo;
    if (!repo || !/^[\w.-]+\/[\w.-]+$/.test(repo)) return false;
    try {
      const response = await fetch("https://raw.githubusercontent.com/" + repo + "/live-data/live.json", { cache: "no-store" });
      if (!response.ok) return false;
      const live = await response.json();
      if (!live || typeof live !== "object" || !live.quotes || typeof live.quotes !== "object") return false;
      const changed = !SH.live || SH.live.updated_at !== live.updated_at;
      SH.live = live;
      return changed;
    } catch (err) {
      return false; // live prices are a bonus; the daily data still works
    }
  }

  // Don't redraw the page while someone is typing into it.
  function busy() {
    const active = document.activeElement;
    if (active && app.contains(active) && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) return true;
    return [...app.querySelectorAll("input")].some((input) => !["checkbox", "file"].includes(input.type) && input.value);
  }

  function poll() {
    if (document.visibilityState !== "visible" || !marketWindow()) return;
    loadLive().then((changed) => {
      if (!changed) return;
      header(SH.data);
      if (!busy()) render(true);
    });
  }

  // --------------------------------------------------------------- routing
  function parseHash() {
    const raw = location.hash.replace(/^#\/?/, "");
    const [path, query] = raw.split("?");
    let pieces;
    try { pieces = path.split("/").filter(Boolean).map(decodeURIComponent); } catch (e) { pieces = ["missing"]; }
    return { page: pieces[0] || "overview", arg: pieces[1] || null, params: new URLSearchParams(query || "") };
  }

  // keepScroll: redraw in place (live refresh) instead of jumping to the top (new page)
  function render(keepScroll) {
    const route = parseHash();
    const page = SH.pages[route.page];
    const navKey = route.page === "stock" ? "overview" : route.page;
    document.querySelectorAll(".nav a").forEach((a) => {
      const active = a.getAttribute("data-route") === navKey;
      a.classList.toggle("active", active);
      if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    SH.clear(app);
    try {
      const content = page
        ? page(SH.data, route.arg, route.params)
        : [h("h1", null, "Page not found"), h("p", null, h("a", { href: "#/" }, "Back to the overview"))];
      [].concat(content).forEach((node) => { if (node) app.appendChild(node); });
    } catch (err) {
      app.appendChild(h("div", { class: "banner warn" }, "Something went wrong drawing this page: " + err.message));
    }
    document.title = (route.page === "stock" && route.arg ? SH.short(route.arg) : TITLES[route.page] || "Stock helper") + " · Stock helper";
    if (keepScroll !== true) document.documentElement.scrollTop = 0;
  }

  function header(data) {
    const asOf = document.getElementById("as-of");
    SH.clear(asOf);
    asOf.appendChild(document.createTextNode("Data " + SH.date(data.as_of) + " "));
    asOf.appendChild(h("span", { class: "pill " + (data.final ? "final" : "live") }, data.final ? "Closing prices" : "Market open: not final"));
    const live = SH.live;
    if (live && live.market === "open") {
      const time = /T(\d{2}:\d{2})/.exec(live.updated_at || "");
      asOf.appendChild(document.createTextNode(" "));
      asOf.appendChild(h("span", { class: "pill on" }, h("span", { class: "live-dot", "aria-hidden": "true" }), "Live " + (time ? time[1] : "")));
    }
    document.getElementById("generated").textContent = "Updated " + SH.dateTime(data.generated_at) +
      (live ? " · live prices " + SH.dateTime(live.updated_at) : "");
  }

  async function boot() {
    try {
      const response = await fetch("data.json", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      SH.data = await response.json();
    } catch (err) {
      SH.clear(app);
      app.appendChild(h("div", { class: "banner warn" },
        "Couldn't load the data (" + err.message + "). If the project was just set up, the first GitHub run may still be going: try again in a few minutes."));
      return;
    }
    await loadLive();
    header(SH.data);
    window.addEventListener("hashchange", () => render());
    render();
    setInterval(poll, LIVE_EVERY);
    document.addEventListener("visibilitychange", poll);
  }

  SH.ready = boot();
})();
