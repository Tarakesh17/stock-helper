/* Stock box with a short suggestion list under it.
   A native <datalist> holding all ~2,600 NSE stocks froze phone browsers, so this
   filters in JavaScript and never puts more than a handful of suggestions on the page. */
(function () {
  "use strict";
  const SH = window.SH;
  const h = SH.h;
  const SHOW = 6;
  let counter = 0;

  // Best matches for `query` in [[symbol, name, NAME_IN_CAPITALS?], ...]:
  // symbols that start with it first, then names that contain it.
  SH.matchSymbols = function (entries, query, limit) {
    const q = String(query || "").trim().toUpperCase();
    if (!q) return [];
    const starts = [], contains = [], seen = new Set();
    for (const entry of entries) {
      if (starts.length >= limit) break; // enough of the best kind already
      const symbol = entry[0];
      if (seen.has(symbol)) continue;
      if (symbol.startsWith(q)) { starts.push(entry); seen.add(symbol); }
      else if ((entry[2] || String(entry[1]).toUpperCase()).includes(q)) { contains.push(entry); seen.add(symbol); }
    }
    return starts.concat(contains).slice(0, limit);
  };

  /* symbolInput({input attributes}, known, onPick) -> {input, box}
     known: [[symbol, name], ...] suggested before the full NSE list (e.g. the watchlist).
     onPick(symbol) runs after a suggestion is chosen. Put `box` on the page. */
  SH.symbolInput = function (attrs, known, onPick) {
    const id = "sym-" + (++counter);
    const input = h("input", Object.assign({
      id, autocomplete: "off", autocapitalize: "characters", spellcheck: "false",
      role: "combobox", "aria-autocomplete": "list", "aria-expanded": "false", "aria-controls": id + "-list",
    }, attrs));
    const list = h("ul", { class: "suggest", role: "listbox", id: id + "-list", hidden: true });
    let shown = [];
    let active = -1;

    function close() {
      SH.clear(list);
      list.hidden = true;
      shown = [];
      active = -1;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    }

    function pick(symbol) {
      input.value = symbol;
      close();
      if (onPick) onPick(symbol);
    }

    function highlight(index) {
      active = index;
      [...list.children].forEach((li, i) => {
        li.classList.toggle("active", i === index);
        li.setAttribute("aria-selected", i === index ? "true" : "false");
      });
      input.setAttribute("aria-activedescendant", list.children[index].id);
    }

    function update() {
      shown = SH.matchSymbols(known.concat(SH.state.symbolList || []), input.value, SHOW);
      if (!shown.length) { close(); return; }
      SH.clear(list);
      active = -1;
      input.removeAttribute("aria-activedescendant");
      shown.forEach(([symbol, name], i) => {
        const li = h("li", { id: id + "-" + i, role: "option", "aria-selected": "false" },
          h("strong", null, SH.short(symbol)), h("span", { class: "muted" }, name));
        li.addEventListener("mousedown", (evt) => evt.preventDefault()); // keeps the field focused and the keyboard open
        li.addEventListener("click", () => pick(symbol));
        list.appendChild(li);
      });
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }

    input.addEventListener("input", update);
    input.addEventListener("focus", () => {
      // the full NSE list is fetched once, the first time any stock box is used
      SH.loadSymbols().then(() => { if (document.activeElement === input && input.value) update(); }, () => {});
    });
    input.addEventListener("blur", () => setTimeout(close, 150));
    input.addEventListener("keydown", (evt) => {
      if (list.hidden || !shown.length) return;
      if (evt.key === "ArrowDown") { evt.preventDefault(); highlight((active + 1) % shown.length); }
      else if (evt.key === "ArrowUp") { evt.preventDefault(); highlight(active <= 0 ? shown.length - 1 : active - 1); }
      else if (evt.key === "Enter" && active >= 0) { evt.preventDefault(); pick(shown[active][0]); } // pick, don't submit the form
      else if (evt.key === "Escape") close();
    });
    return { input, box: h("div", { class: "suggest-wrap" }, input, list) };
  };
})();
