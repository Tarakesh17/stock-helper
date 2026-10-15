/* Shared helpers: the h() DOM builder, storage and number/date formatting. */
(function () {
  "use strict";
  const SH = (window.SH = window.SH || {});
  SH.state = {}; // things that live while the page is open (watchlist draft, symbol list)

  // h("p", {class: "x"}, "text", child, ...) builds an element. Text always goes in
  // as a text node, so data from outside can never turn into HTML.
  SH.h = function (tag, attrs, ...children) {
    const el = tag.startsWith("svg:")
      ? document.createElementNS("http://www.w3.org/2000/svg", tag.slice(4))
      : document.createElement(tag);
    for (const [name, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (/^on/i.test(name) || name === "style") throw new Error("attribute not allowed: " + name);
      if ((name === "href" || name === "src") && /^\s*javascript:/i.test(String(value))) {
        throw new Error("javascript: URLs are not allowed");
      }
      el.setAttribute(name, value === true ? "" : String(value));
    }
    children.forEach((child) => append(el, child));
    return el;
  };

  function append(el, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) return child.forEach((c) => append(el, c));
    el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }

  SH.clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); };
  SH.empty = (text) => SH.h("p", { class: "muted empty" }, text);

  // localStorage that never throws (private mode, full disk, bad JSON)
  SH.store = {
    get(key) { try { return JSON.parse(localStorage.getItem(key)); } catch (e) { return null; } },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (e) { return false; } },
  };

  // ------------------------------------------------------------ numbers
  const fmt = (digits) => new Intl.NumberFormat("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const two = fmt(2), one = fmt(1), zero = fmt(0);
  const MINUS = "\u2212";

  SH.num = (v, digits = 2) => (v == null || !isFinite(v) ? "–" : fmt(digits).format(v));
  SH.int = (v) => (v == null ? "–" : zero.format(v));
  SH.money = (v) => (v == null ? "–" : (v < 0 ? MINUS : "") + "₹" + two.format(Math.abs(v)));
  SH.crore = (v) => (v == null ? "–" : "₹" + one.format(Math.abs(v) / 1e7) + " Cr");
  SH.pct = (v, digits = 1) => (v == null || !isFinite(v) ? "–" : (v > 0 ? "+" : v < 0 ? MINUS : "") + Math.abs(v).toFixed(digits) + "%");
  SH.plainPct = (v, digits = 1) => (v == null || !isFinite(v) ? "–" : v.toFixed(digits) + "%");
  SH.chance = (p) => (p == null ? "–" : Math.round(p * 100) + "%");
  SH.signClass = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");

  // axis labels: 1.2Cr, 3.4L, 12,345, 56.7
  SH.compact = function (v) {
    const a = Math.abs(v);
    if (a >= 1e7) return (v / 1e7).toFixed(1) + "Cr";
    if (a >= 1e5) return (v / 1e5).toFixed(1) + "L";
    if (a >= 1000) return zero.format(v);
    return a >= 10 ? v.toFixed(0) : v.toFixed(2);
  };

  // -------------------------------------------------------------- dates
  // "2026-10-15" is split by hand: new Date("2026-10-15") would be UTC midnight.
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function parts(text) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(text || "");
    return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
  }
  SH.date = (text) => { const p = parts(text); return p ? `${p[2]} ${MONTHS[p[1] - 1]} ${p[0]}` : "–"; };
  SH.shortDate = (text) => { const p = parts(text); return p ? `${p[2]} ${MONTHS[p[1] - 1]}` : ""; };
  SH.dateTime = (iso) => { const t = /T(\d{2}:\d{2})/.exec(iso || ""); return SH.date(iso) + (t ? ", " + t[1] + " IST" : ""); };

  // ------------------------------------------------------------- pieces
  SH.short = (symbol) => (symbol.endsWith(".NS") ? symbol.slice(0, -3) : symbol);
  SH.badge = (signal) => SH.h("span", { class: "badge " + String(signal || "none").toLowerCase() }, signal || "–");
  SH.stockHref = (symbol) => "#/stock/" + encodeURIComponent(symbol);

  // table(["Date", "Close"], [[cell, cell], ...], {numeric: [1]}); cells are text or nodes
  SH.table = function (headers, rows, opts = {}) {
    const numeric = new Set(opts.numeric || []);
    const cls = (i) => (numeric.has(i) ? "num" : null);
    return SH.h("div", { class: "table-wrap" },
      SH.h("table", null,
        SH.h("thead", null, SH.h("tr", null, headers.map((text, i) => SH.h("th", { scope: "col", class: cls(i) }, text)))),
        SH.h("tbody", null, rows.map((cells) => SH.h("tr", null, cells.map((cell, i) => SH.h("td", { class: cls(i) }, cell)))))));
  };

  SH.copy = function (text) {
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
    return new Promise((resolve, reject) => {
      const area = SH.h("textarea", { class: "offscreen", readonly: true });
      area.value = text;
      document.body.appendChild(area);
      area.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      area.remove();
      ok ? resolve() : reject(new Error("copy failed"));
    });
  };

  // The NSE stock list for the pickers, fetched once and only when needed.
  SH.loadSymbols = function () {
    if (!SH.state.symbols) {
      SH.state.symbols = fetch("symbols.json")
        .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
        .then((list) => (Array.isArray(list) ? list.filter((x) => Array.isArray(x) && typeof x[0] === "string" && typeof x[1] === "string") : []))
        .catch((err) => { SH.state.symbols = null; throw err; });
    }
    return SH.state.symbols;
  };
})();
