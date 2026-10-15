/* Small SVG charts drawn in a fixed coordinate box (viewBox), so they scale to any width. */
(function () {
  "use strict";
  const SH = window.SH;
  const h = SH.h;
  const W = 640;
  const PAD = { top: 12, right: 10, bottom: 28, left: 64 };

  // "M x y L x y ..." through the values; a null breaks the line
  function pathFor(values, x, y) {
    let d = "";
    let pen = false;
    values.forEach((v, i) => {
      if (v == null || !isFinite(v)) { pen = false; return; }
      d += (pen ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1);
      pen = true;
    });
    return d;
  }

  /* lineChart({dates, series: [{name, values, cls}], height, min, max, guides: [{value, label}],
                markers: [{index, kind: "buy"|"sell"}], format(v), tooltip(i) -> [lines], legend, label}) */
  SH.lineChart = function (opts) {
    const H = opts.height || 240;
    const n = opts.dates.length;
    const all = [];
    opts.series.forEach((s) => s.values.forEach((v) => { if (v != null && isFinite(v)) all.push(v); }));
    if (!all.length || n < 2) return SH.empty("Not enough data for this chart yet.");
    (opts.guides || []).forEach((g) => all.push(g.value));
    let min = opts.min != null ? opts.min : Math.min(...all);
    let max = opts.max != null ? opts.max : Math.max(...all);
    if (min === max) { min -= 1; max += 1; }
    const room = (max - min) * 0.06;
    if (opts.min == null) min -= room;
    if (opts.max == null) max += room;

    const x = (i) => PAD.left + (i / (n - 1)) * (W - PAD.left - PAD.right);
    const y = (v) => PAD.top + (1 - (v - min) / (max - min)) * (H - PAD.top - PAD.bottom);
    const format = opts.format || SH.compact;
    const xLabel = opts.xFormat || SH.shortDate; // dates by default; the intraday chart passes times
    const svg = h("svg:svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": opts.label || "chart" });

    for (let k = 0; k <= 4; k++) {
      const v = min + (k / 4) * (max - min);
      svg.appendChild(h("svg:line", { x1: PAD.left, x2: W - PAD.right, y1: y(v), y2: y(v), class: "grid" }));
      svg.appendChild(h("svg:text", { x: PAD.left - 8, y: y(v) + 5, class: "axis", "text-anchor": "end" }, format(v)));
    }
    const ticks = 4;
    for (let k = 0; k < ticks; k++) {
      const i = Math.round((k / (ticks - 1)) * (n - 1));
      const anchor = k === 0 ? "start" : k === ticks - 1 ? "end" : "middle";
      svg.appendChild(h("svg:text", { x: x(i), y: H - 6, class: "axis", "text-anchor": anchor }, xLabel(opts.dates[i])));
    }
    (opts.guides || []).forEach((g) => {
      svg.appendChild(h("svg:line", { x1: PAD.left, x2: W - PAD.right, y1: y(g.value), y2: y(g.value), class: "guide" }));
    });
    opts.series.forEach((s) => {
      const d = pathFor(s.values, x, y);
      if (d) svg.appendChild(h("svg:path", { d, class: "line " + (s.cls || "") }));
    });
    (opts.markers || []).forEach((m) => {
      const v = opts.series[0].values[m.index];
      if (v == null) return;
      const cx = x(m.index), cy = y(v);
      const points = m.kind === "buy"
        ? `${cx},${cy + 5} ${cx - 8},${cy + 18} ${cx + 8},${cy + 18}`
        : `${cx},${cy - 5} ${cx - 8},${cy - 18} ${cx + 8},${cy - 18}`;
      svg.appendChild(h("svg:polygon", { points, class: "marker " + m.kind },
        h("svg:title", null, m.kind.toUpperCase() + " signal, " + SH.date(opts.dates[m.index]))));
    });

    const figure = h("figure", { class: "chart-wrap" }, svg);
    if (opts.legend !== false) {
      figure.appendChild(h("figcaption", { class: "legend" }, opts.series.map((s) =>
        h("span", { class: "key" }, h("span", { class: "swatch " + (s.cls || "") }), s.name))));
    }
    if (opts.tooltip) addTooltip(figure, svg, n, x, H, opts.tooltip);
    return figure;
  };

  // A crosshair and a small text box that follow the finger or mouse.
  function addTooltip(figure, svg, n, x, H, describe) {
    const cross = h("svg:line", { x1: 0, x2: 0, y1: PAD.top, y2: H - PAD.bottom, class: "cross hidden" });
    svg.appendChild(cross);
    const tip = h("div", { class: "tip hidden", "aria-live": "polite" });
    figure.appendChild(tip);
    function show(evt) {
      const box = svg.getBoundingClientRect();
      if (!box.width) return; // not laid out (hidden tab, tests)
      const vx = ((evt.clientX - box.left) / box.width) * W;
      const i = Math.max(0, Math.min(n - 1, Math.round(((vx - PAD.left) / (W - PAD.left - PAD.right)) * (n - 1))));
      cross.setAttribute("x1", x(i));
      cross.setAttribute("x2", x(i));
      cross.classList.remove("hidden");
      SH.clear(tip);
      describe(i).forEach((line) => tip.appendChild(h("div", null, line)));
      const left = (x(i) / W) * box.width;
      tip.style.left = left + "px"; // CSSOM, which the page's CSP allows
      tip.classList.toggle("flip", left > box.width / 2);
      tip.classList.remove("hidden");
    }
    svg.addEventListener("pointermove", show);
    svg.addEventListener("pointerdown", show);
    svg.addEventListener("pointerleave", () => { cross.classList.add("hidden"); tip.classList.add("hidden"); });
  }

  // Tiny trend line for the overview cards.
  SH.sparkline = function (values, cls) {
    const known = values.filter((v) => v != null);
    if (known.length < 2) return null;
    const min = Math.min(...known), max = Math.max(...known);
    const w = 120, ht = 32;
    const x = (i) => (i / (values.length - 1)) * w;
    const y = (v) => ht - 3 - ((v - min) / (max - min || 1)) * (ht - 6);
    return h("svg:svg", { viewBox: `0 0 ${w} ${ht}`, preserveAspectRatio: "none", class: "spark " + (cls || ""), "aria-hidden": "true" },
      h("svg:path", { d: pathFor(values, x, y) }));
  };

  // Horizontal bars around a centre line (HTML, so labels stay readable on phones).
  SH.barChart = function (items) {
    const max = Math.max(1e-9, ...items.map((item) => Math.abs(item.value)));
    return h("div", { class: "bars" }, items.map((item) => {
      const bar = h("span", { class: "bar " + (item.value >= 0 ? "pos" : "neg") });
      bar.style.width = ((Math.abs(item.value) / max) * 50).toFixed(1) + "%";
      return h("div", { class: "bar-row" },
        h("span", { class: "bar-label" }, item.label),
        h("span", { class: "bar-track" }, bar),
        h("span", { class: "bar-value num" }, (item.value >= 0 ? "+" : "\u2212") + Math.abs(item.value).toFixed(3)));
    }));
  };
})();
