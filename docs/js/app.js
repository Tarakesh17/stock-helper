/* Start-up and hash routing: #/, #/stock/SYMBOL, #/holdings, #/agents, #/model, #/deals, #/watchlist */
(function () {
  "use strict";
  const SH = window.SH;
  const h = SH.h;
  const app = document.getElementById("app");
  const TITLES = { overview: "Overview", stock: "Stock", holdings: "Holdings", agents: "Agents", model: "Model", deals: "Big deals", watchlist: "Watchlist" };

  function parseHash() {
    const raw = location.hash.replace(/^#\/?/, "");
    const [path, query] = raw.split("?");
    let pieces;
    try { pieces = path.split("/").filter(Boolean).map(decodeURIComponent); } catch (e) { pieces = ["missing"]; }
    return { page: pieces[0] || "overview", arg: pieces[1] || null, params: new URLSearchParams(query || "") };
  }

  function render() {
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
    document.documentElement.scrollTop = 0;
  }

  function header(data) {
    const asOf = document.getElementById("as-of");
    SH.clear(asOf);
    asOf.appendChild(document.createTextNode("Data " + SH.date(data.as_of) + " "));
    asOf.appendChild(h("span", { class: "pill " + (data.final ? "final" : "live") }, data.final ? "Closing prices" : "Market open: not final"));
    document.getElementById("generated").textContent = "Updated " + SH.dateTime(data.generated_at);
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
    header(SH.data);
    window.addEventListener("hashchange", render);
    render();
  }

  SH.ready = boot();
})();
