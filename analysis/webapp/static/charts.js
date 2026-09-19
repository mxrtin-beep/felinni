// Minimal dependency-free SVG chart helpers, following the mark specs:
// bars <=24px thick with 4px rounded data-ends, hairline gridlines, a
// shared hover tooltip, legend for multi-series charts.

const SVG_NS = "http://www.w3.org/2000/svg";
const SERIES_VARS = [
  "--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6",
  "--series-7", "--series-8", "--series-9", "--series-10", "--series-11", "--series-12",
];

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function seriesColor(i) {
  return cssVar(SERIES_VARS[i % SERIES_VARS.length]);
}

function el(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function ensureTooltip() {
  let tip = document.getElementById("viz-tooltip");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "viz-tooltip";
    tip.className = "viz-tooltip";
    document.body.appendChild(tip);
  }
  return tip;
}

function showTooltip(evt, html) {
  const tip = ensureTooltip();
  tip.innerHTML = html;
  tip.style.display = "block";
  tip.style.left = evt.clientX + 14 + "px";
  tip.style.top = evt.clientY + 12 + "px";
}

function hideTooltip() {
  const tip = document.getElementById("viz-tooltip");
  if (tip) tip.style.display = "none";
}

function formatNumber(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  if (Math.abs(n) >= 1000) return (n / 1000).toFixed(1) + "K";
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

/** Wraps text to at most 2 lines within ~maxChars per line, word by word;
 * a third line's worth of content is truncated with an ellipsis instead of
 * overflowing. Approximate (char-count based), not measured - good enough
 * at the fixed 12px label size these charts use. */
function wrapLabel(text, maxChars) {
  const words = text.split(/\s+/);
  const lines = [];
  let current = "";
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word;
    if (candidate.length > maxChars && current) {
      lines.push(current);
      current = word;
      if (lines.length === 2) break;
    } else {
      current = candidate;
    }
  }
  if (lines.length < 2 && current) lines.push(current);
  if (lines.length === 2) {
    const consumed = lines.join(" ").length;
    if (consumed < text.length) {
      lines[1] = lines[1].length > maxChars - 1 ? lines[1].slice(0, maxChars - 1) + "…" : lines[1] + "…";
    }
  }
  return lines;
}

/** Splits a free-text location/address into up to 4 display lines by
 * splitting at the first digit (name -> street), the first comma after it
 * (street -> city/state), and the next digit after that (city/state ->
 * zip/country). Matches how Apple Calendar's Location field reads when
 * picked from Maps, e.g. "eaves Woodland Hills, 22122 Ventura Blvd,
 * Woodland Hills, CA 91367, United States" splits into:
 *   "eaves Woodland Hills" / "22122 Ventura Blvd" / "Woodland Hills, CA" / "91367, United States"
 * A name-less address ("22122 Ventura Blvd, ...") just yields 3 lines
 * (empty leading line dropped). Falls back to generic word-wrap when the
 * string has no digits at all - no address structure to key off of. */
function splitLocationLines(location, maxCharsPerLine) {
  const str = String(location);
  const firstDigit = str.search(/\d/);
  if (firstDigit === -1) return wrapLabel(str, maxCharsPerLine);

  const name = str.slice(0, firstDigit).trim().replace(/[,\s]+$/, "");
  const rest = str.slice(firstDigit);

  const commaIdx = rest.indexOf(",");
  if (commaIdx === -1) {
    return [name, rest.trim()].filter(Boolean).map(l => truncateLine(l, maxCharsPerLine));
  }
  const street = rest.slice(0, commaIdx).trim();
  const afterStreet = rest.slice(commaIdx + 1).replace(/^[,\s]+/, "");

  const zipIdx = afterStreet.search(/\d/);
  if (zipIdx === -1) {
    return [name, street, afterStreet.trim()].filter(Boolean).map(l => truncateLine(l, maxCharsPerLine));
  }
  const cityState = afterStreet.slice(0, zipIdx).trim().replace(/[,\s]+$/, "");
  const zipCountry = afterStreet.slice(zipIdx).trim();
  return [name, street, cityState, zipCountry].filter(Boolean).map(l => truncateLine(l, maxCharsPerLine));
}

function truncateLine(text, maxChars) {
  return text.length > maxChars ? text.slice(0, maxChars - 1) + "…" : text;
}

/** Horizontal bar chart: data = [{label, value}], one series. Pass
 * `addressLines: true` to split each label into address/city+state/zip+country
 * lines (see splitLocationLines) instead of generic word-wrapping. */
function horizontalBarChart(container, data, { valueLabel = "", color, addressLines = false } = {}) {
  container.innerHTML = "";
  if (!data.length) {
    container.innerHTML = '<p class="empty-note">No data yet.</p>';
    return;
  }
  const barColor = color || cssVar("--series-1");
  const barH = 16, labelW = 220, lineHeight = 13, width = container.clientWidth || 640;
  const chartW = width - labelW - 60;
  // ~7px/char is a safe estimate for this font at 12px - err conservative
  // since text-anchor:end grows leftward and an overlong line clips off
  // the left edge rather than visibly truncating.
  const maxCharsPerLine = Math.floor((labelW - 10) / 7);
  const max = Math.max(...data.map(d => d.value), 1);

  const wrapped = data.map(d => addressLines
    ? splitLocationLines(d.label, maxCharsPerLine)
    : wrapLabel(String(d.label), maxCharsPerLine));
  const rowHeights = wrapped.map(lines => Math.max(barH, lines.length * lineHeight) + 12);
  const rowTops = [];
  let cursor = 4;
  rowHeights.forEach(h => { rowTops.push(cursor); cursor += h; });
  const height = cursor + 4;

  const svg = el("svg", { width, height, viewBox: `0 0 ${width} ${height}` });
  data.forEach((d, i) => {
    const rowTop = rowTops[i], rowH = rowHeights[i];
    const y = rowTop + (rowH - barH) / 2;
    const barW = Math.max((d.value / max) * chartW, 2);

    const lines = wrapped[i];
    const textBlockH = lines.length * lineHeight;
    const firstLineY = rowTop + (rowH - textBlockH) / 2 + lineHeight - 3;
    const label = el("text", { x: labelW - 8, y: firstLineY, "text-anchor": "end", fill: cssVar("--text-secondary"), "font-size": 12 });
    lines.forEach((line, li) => {
      const tspan = el("tspan", { x: labelW - 8, dy: li === 0 ? 0 : lineHeight });
      tspan.textContent = line;
      label.appendChild(tspan);
    });
    svg.appendChild(label);

    const rect = el("rect", {
      x: labelW, y, width: barW, height: barH, rx: 4, ry: 4, fill: barColor, class: "bar-mark",
    });
    rect.addEventListener("mousemove", (evt) => showTooltip(evt, `<strong>${d.label}</strong><br>${formatNumber(d.value)} ${valueLabel}`));
    rect.addEventListener("mouseleave", hideTooltip);
    svg.appendChild(rect);
    const valText = el("text", { x: labelW + barW + 6, y: y + barH / 2 + 4, fill: cssVar("--text-secondary"), "font-size": 11 });
    valText.textContent = formatNumber(d.value);
    svg.appendChild(valText);
  });
  container.appendChild(svg);
}

/** Vertical column chart: data = [{label, value}], one series, baseline-anchored.
 * `flagColor(d)` (distinct from `highlight`, which recolors every bar for
 * per-category coloring) marks a handful of specific data points as
 * noteworthy - returns a color to flag that point, or a falsy value for
 * an ordinary one. A flagged point gets a fixed-size marker dot above its
 * bar, so it stays visible even when the bar itself is only 1-2px wide
 * (a long history) or barely tall (a near-zero value) - a color-only
 * change on a thin bar is easy to miss entirely. */
function columnChart(container, data, { valueLabel = "", color, highlight, flagColor, separators = false } = {}) {
  container.innerHTML = "";
  if (!data.length) {
    container.innerHTML = '<p class="empty-note">No data yet.</p>';
    return;
  }
  const barColor = color || cssVar("--series-1");
  const width = container.clientWidth || 640, height = 220;
  const padTop = 10, padBottom = 26, padLeft = 34;
  const chartH = height - padTop - padBottom;
  const max = Math.max(...data.map(d => d.value), 1);
  const step = (width - padLeft - 10) / data.length;
  const bw = Math.max(1, Math.min(24, step - Math.min(6, step * 0.3)));

  const svg = el("svg", { width, height, viewBox: `0 0 ${width} ${height}` });
  // gridlines (3 steps)
  for (let g = 0; g <= 2; g++) {
    const gy = padTop + (chartH / 2) * g;
    svg.appendChild(el("line", { x1: padLeft, x2: width - 10, y1: gy, y2: gy, stroke: cssVar("--grid"), "stroke-width": 1 }));
  }
  // Skip labels when there isn't room for every one, spacing them out instead
  // of letting them overlap into an unreadable smear.
  const minLabelGap = 52;
  const labelEvery = Math.max(1, Math.ceil((minLabelGap * data.length) / (width - padLeft - 10)));

  data.forEach((d, i) => {
    const barH = Math.max((d.value / max) * chartH, d.value > 0 ? 2 : 0);
    const x = padLeft + i * step + (step - bw) / 2;
    const y = padTop + chartH - barH;
    // A light divider before each bar (skipping the very first) - a visual
    // separator between periods, e.g. one per year.
    if (separators && i > 0) {
      const sepX = padLeft + i * step;
      svg.appendChild(el("line", { x1: sepX, x2: sepX, y1: padTop, y2: padTop + chartH, stroke: cssVar("--grid"), "stroke-width": 1, opacity: 0.6 }));
    }
    const flagged = flagColor && flagColor(d);
    const fill = flagged || (highlight && highlight(d)) || barColor;
    const rect = el("rect", { x, y, width: bw, height: barH, rx: 4, ry: 4, fill });
    rect.addEventListener("mousemove", (evt) => showTooltip(evt, `<strong>${d.label}</strong><br>${formatNumber(d.value)} ${valueLabel}`));
    rect.addEventListener("mouseleave", hideTooltip);
    svg.appendChild(rect);
    if (flagged) {
      const dot = el("circle", {
        cx: x + bw / 2, cy: Math.max(y - 6, padTop + 4), r: 4,
        fill: flagged, stroke: cssVar("--surface-1"), "stroke-width": 1.5,
      });
      dot.addEventListener("mousemove", (evt) => showTooltip(evt, `<strong>${d.label}</strong><br>${formatNumber(d.value)} ${valueLabel}`));
      dot.addEventListener("mouseleave", hideTooltip);
      svg.appendChild(dot);
    }
    if (i % labelEvery === 0) {
      svg.appendChild(el("text", { x: x + bw / 2, y: height - 8, "text-anchor": "middle", fill: cssVar("--text-muted"), "font-size": 10 })).textContent = d.label;
    }
  });
  svg.appendChild(el("line", { x1: padLeft, x2: width - 10, y1: padTop + chartH, y2: padTop + chartH, stroke: cssVar("--baseline"), "stroke-width": 1 }));
  container.appendChild(svg);
}

/** Multi-series line chart: series = [{name, points: [{x, y}]}], x numeric/year. */
function lineChart(container, series, { yLabel = "" } = {}) {
  container.innerHTML = "";
  const allPoints = series.flatMap(s => s.points);
  if (!allPoints.length) {
    container.innerHTML = '<p class="empty-note">No data yet.</p>';
    return;
  }
  const width = container.clientWidth || 640, height = 240;
  const padTop = 10, padBottom = 26, padLeft = 36, padRight = 14;
  const chartW = width - padLeft - padRight, chartH = height - padTop - padBottom;
  const xs = allPoints.map(p => p.x), ys = allPoints.map(p => p.y);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const yMax = Math.max(...ys, 1);
  const xScale = x => padLeft + (xMax === xMin ? 0.5 : (x - xMin) / (xMax - xMin)) * chartW;
  const yScale = y => padTop + chartH - (y / yMax) * chartH;

  const svg = el("svg", { width, height, viewBox: `0 0 ${width} ${height}` });
  for (let g = 0; g <= 2; g++) {
    const gy = padTop + (chartH / 2) * g;
    svg.appendChild(el("line", { x1: padLeft, x2: width - padRight, y1: gy, y2: gy, stroke: cssVar("--grid"), "stroke-width": 1 }));
  }

  const legend = document.createElement("div");
  legend.className = "legend";

  series.forEach((s, i) => {
    const color = seriesColor(i);
    const pts = s.points.slice().sort((a, b) => a.x - b.x);
    const d = pts.map((p, idx) => `${idx === 0 ? "M" : "L"} ${xScale(p.x)} ${yScale(p.y)}`).join(" ");
    svg.appendChild(el("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    pts.forEach(p => {
      const dot = el("circle", { cx: xScale(p.x), cy: yScale(p.y), r: 4, fill: color, stroke: cssVar("--surface-1"), "stroke-width": 2 });
      dot.addEventListener("mousemove", (evt) => showTooltip(evt, `<strong>${s.name}</strong><br>${p.xLabel ?? p.x}: ${formatNumber(p.y)} ${yLabel}`));
      dot.addEventListener("mouseleave", hideTooltip);
      svg.appendChild(dot);
    });
    if (series.length > 1) {
      const item = document.createElement("span");
      item.innerHTML = `<span class="swatch" style="background:${color}"></span>${s.name}`;
      legend.appendChild(item);
    }
  });

  // x ticks: one label per unique x, but thinned out (not skipped from the
  // data - just from what gets drawn) so labels never overlap into a smear.
  const tickLabels = new Map();
  allPoints.forEach(p => { if (!tickLabels.has(p.x)) tickLabels.set(p.x, p.xLabel ?? p.x); });
  const xTicks = [...tickLabels.keys()].sort((a, b) => a - b);
  const minLabelGap = 46;
  const labelEvery = Math.max(1, Math.ceil((minLabelGap * xTicks.length) / chartW));
  let lastDrawnX = -Infinity;
  xTicks.forEach((x, i) => {
    const isLast = i === xTicks.length - 1;
    if (i % labelEvery !== 0 && !isLast) return;
    const px = xScale(x);
    if (isLast && px - lastDrawnX < minLabelGap) return; // too close to the previous label - skip rather than overlap
    lastDrawnX = px;
    svg.appendChild(el("text", { x: px, y: height - 8, "text-anchor": "middle", fill: cssVar("--text-muted"), "font-size": 10 })).textContent = tickLabels.get(x);
  });
  svg.appendChild(el("line", { x1: padLeft, x2: width - padRight, y1: padTop + chartH, y2: padTop + chartH, stroke: cssVar("--baseline"), "stroke-width": 1 }));

  if (series.length > 1) container.appendChild(legend);
  container.appendChild(svg);
}

/** Week-activity strip: cells = [{date, count}] rendered as a compact grid,
 * one sequential hue (--series-1) whose opacity scales with count - darker
 * (more opaque) = more events that week; zero-count weeks stay neutral gray. */
const MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Cumulative day-of-year each month starts on (non-leap; close enough for
// label placement), used to convert a fixed 53-week-per-year grid into
// month tick positions that don't depend on which weeks the data happens
// to include - critical when the data itself is date-filtered to start
// mid-year, otherwise the first available week gets placed in column 0
// (i.e. under "Jan") regardless of which month it's actually in.
const MONTH_START_DAY = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
const WEEKS_PER_YEAR = 53;

function weekOfYear(dateStr) {
  const d = new Date(dateStr + "T00:00:00Z");
  const yearStart = Date.UTC(d.getUTCFullYear(), 0, 1);
  return Math.floor((d.getTime() - yearStart) / (7 * 86400000));
}

/** Week-activity strip: cells = [{date (Sunday-of-week, ISO), count}],
 * laid out as one row per year and one column per week-of-year (like a
 * GitHub contribution graph), with the column position derived from the
 * actual calendar week - not from array order - so a date-filtered range
 * (e.g. starting in June) still lines up under the right month instead of
 * sliding everything back to column 0. One sequential hue (`color`,
 * default --series-1) whose opacity scales with count - darker = more
 * events that week; zero-count weeks stay neutral gray. */
function weekStrip(container, cells, { color } = {}) {
  container.innerHTML = "";
  if (!cells.length) {
    container.innerHTML = '<p class="empty-note">No data yet.</p>';
    return;
  }
  const cellSize = 10, gap = 2, rowLabelW = 34, colLabelH = 14;
  const step = cellSize + gap;

  const byYear = new Map();
  cells.forEach(c => {
    const year = c.date.slice(0, 4);
    if (!byYear.has(year)) byYear.set(year, []);
    byYear.get(year).push(c);
  });
  const years = [...byYear.keys()].sort();

  const width = rowLabelW + WEEKS_PER_YEAR * step + 4;
  const height = colLabelH + years.length * step + 4;
  const svg = el("svg", { width, height, viewBox: `0 0 ${width} ${height}` });

  const maxCount = Math.max(...cells.map(c => c.count), 1);
  const activeColor = color || cssVar("--series-1");

  // Month labels at fixed columns - always aligned to real calendar time.
  MONTH_ABBR.forEach((m, i) => {
    const col = Math.round(MONTH_START_DAY[i] / 7);
    svg.appendChild(el("text", {
      x: rowLabelW + col * step, y: colLabelH - 4, "font-size": 9, fill: cssVar("--text-muted"),
    })).textContent = m;
  });

  years.forEach((year, row) => {
    svg.appendChild(el("text", {
      x: 0, y: colLabelH + row * step + cellSize - 1, "font-size": 10, fill: cssVar("--text-muted"),
    })).textContent = year;

    byYear.get(year).forEach(c => {
      const col = weekOfYear(c.date);
      const intensity = c.count > 0 ? 0.3 + 0.7 * (c.count / maxCount) : 0;
      const rect = el("rect", {
        x: rowLabelW + col * step, y: colLabelH + row * step,
        width: cellSize, height: cellSize, rx: 2, ry: 2,
        fill: c.count > 0 ? activeColor : cssVar("--grid"),
        "fill-opacity": c.count > 0 ? intensity.toFixed(2) : 1,
      });
      rect.addEventListener("mousemove", (evt) => showTooltip(evt, `Week of ${c.date}<br>${c.count} event${c.count === 1 ? "" : "s"}`));
      rect.addEventListener("mouseleave", hideTooltip);
      svg.appendChild(rect);
    });
  });
  container.appendChild(svg);
}

/** Gantt-style timeline: groups = [{label, color, segments: [{start, end}]}],
 * one row per group, each segment a bar spanning its date range on a
 * shared time axis with year gridlines - for "which phases were active
 * when" at a glance, instead of a flat list of date ranges. */
function timelineChart(container, groups) {
  container.innerHTML = "";
  const allSegments = groups.flatMap(g => g.segments);
  if (!allSegments.length) {
    container.innerHTML = '<p class="empty-note">No data yet.</p>';
    return;
  }
  const rowLabelW = 110, rowH = 30, barH = 14, padTop = 10, padBottom = 24, padRight = 14;
  const width = container.clientWidth || 640;
  const chartW = width - rowLabelW - padRight;
  const height = padTop + groups.length * rowH + padBottom;

  const allTimes = allSegments.flatMap(s => [new Date(s.start).getTime(), new Date(s.end).getTime()]);
  const minTime = Math.min(...allTimes), maxTime = Math.max(...allTimes);
  const xScale = t => rowLabelW + (maxTime === minTime ? 0 : ((t - minTime) / (maxTime - minTime)) * chartW);

  const svg = el("svg", { width, height, viewBox: `0 0 ${width} ${height}` });

  const minYear = new Date(minTime).getUTCFullYear();
  const maxYear = new Date(maxTime).getUTCFullYear();
  const yearSpan = Math.max(maxYear - minYear, 1);
  const yearStep = Math.max(1, Math.ceil((yearSpan * 40) / chartW)); // thin out labels on a long span
  // Quarter separators (season boundaries) - lighter/dashed so the solid
  // year gridlines still read as the primary structure. Skipped on a long
  // span where every quarter tick would just be visual noise.
  const showQuarters = yearStep === 1 && chartW / Math.max(yearSpan * 4, 1) > 6;
  if (showQuarters) {
    for (let y = minYear; y <= maxYear + 1; y++) {
      for (const month of [3, 6, 9]) {
        const t = Date.UTC(y, month, 1);
        if (t < minTime || t > maxTime) continue;
        const x = xScale(t);
        svg.appendChild(el("line", {
          x1: x, x2: x, y1: padTop, y2: height - padBottom,
          stroke: cssVar("--grid"), "stroke-width": 1, "stroke-dasharray": "2,3", opacity: 0.5,
        }));
      }
    }
  }
  for (let y = minYear; y <= maxYear; y++) {
    const t = Date.UTC(y, 0, 1);
    if (t < minTime || t > maxTime) continue;
    const x = xScale(t);
    svg.appendChild(el("line", { x1: x, x2: x, y1: padTop, y2: height - padBottom, stroke: cssVar("--grid"), "stroke-width": 1 }));
    if ((y - minYear) % yearStep === 0) {
      svg.appendChild(el("text", {
        x, y: height - padBottom + 14, "text-anchor": "middle", "font-size": 10, fill: cssVar("--text-muted"),
      })).textContent = y;
    }
  }

  groups.forEach((g, row) => {
    const y = padTop + row * rowH;
    svg.appendChild(el("text", {
      x: rowLabelW - 8, y: y + barH / 2 + 4, "text-anchor": "end", "font-size": 12, fill: cssVar("--text-secondary"),
    })).textContent = g.label;

    const barColor = g.color || cssVar("--series-1");
    g.segments.forEach(s => {
      const x1 = xScale(new Date(s.start).getTime());
      const x2 = xScale(new Date(s.end).getTime());
      const w = Math.max(x2 - x1, 3);
      const rect = el("rect", { x: x1, y, width: w, height: barH, rx: 4, ry: 4, fill: barColor });
      rect.addEventListener("mousemove", (evt) => showTooltip(evt, `<strong>${g.label}</strong><br>${s.start.slice(0, 10)} – ${s.end.slice(0, 10)}`));
      rect.addEventListener("mouseleave", hideTooltip);
      svg.appendChild(rect);
    });
  });

  svg.appendChild(el("line", {
    x1: rowLabelW, x2: width - padRight, y1: height - padBottom, y2: height - padBottom,
    stroke: cssVar("--baseline"), "stroke-width": 1,
  }));
  container.appendChild(svg);
}

function statTile(label, value) {
  const div = document.createElement("div");
  div.className = "stat-tile";
  div.innerHTML = `<div class="label">${label}</div><div class="value">${value}</div>`;
  return div;
}

// --- Friend network graph ---
// A simple force-directed layout (Fruchterman-Reingold-ish: every pair of
// nodes repels, every edge pulls its two ends together, cooled over a
// fixed number of iterations to a settled static layout) rendered once,
// not animated continuously - consistent with every other chart here
// being a one-shot render, not a live simulation. Dragging a node just
// repositions it and its own edges; it doesn't restart the simulation.
//
// The simulation itself runs in a "world" coordinate space sized well
// beyond the container's own pixel size (simW x simH below), Obsidian-
// graph-style: the visible <svg> pans/zooms into it via an SVG transform
// on a wrapping <g>, defaulting to a view that fits the whole world so
// nothing starts clipped, with plenty of room to zoom into any cluster.
function networkGraph(container, nodes, edges, { height = 480, getColor } = {}) {
  container.innerHTML = "";
  if (!nodes.length) {
    container.innerHTML = '<p class="empty-note">No data yet.</p>';
    return;
  }
  const w = container.clientWidth || 640;
  const h = height;
  const aspect = w / h;

  // The repulsion/spring layout below settles every node pair this far
  // apart (in world units) at equilibrium - it has to comfortably clear a
  // long name's label width ("Leo Tavera-Montiel" at 11px font is ~120
  // world-units wide) on both sides, or two barely/unconnected people
  // will render with overlapping labels regardless of how much total
  // canvas area surrounds them. Total world area is just this spacing
  // squared per node (never smaller than the container itself, so a
  // handful of people don't end up zoomed in oddly far), which is what
  // actually gives real room to pan around rather than a "world" that
  // silently matches the viewport 1:1 like it did before.
  const K_TARGET = 170;
  const worldArea = K_TARGET * K_TARGET * nodes.length;
  const simH = Math.max(h, Math.sqrt(worldArea / aspect));
  const simW = Math.max(w, simH * aspect);

  const maxEvents = Math.max(...nodes.map(n => n.events), 1);
  const sim = nodes.map(n => ({
    person: n.person,
    events: n.events,
    total_hours: n.total_hours,
    last_seen: n.last_seen,
    first_seen: n.first_seen,
    x: simW / 2 + (Math.random() - 0.5) * simW * 0.6,
    y: simH / 2 + (Math.random() - 0.5) * simH * 0.6,
    vx: 0, vy: 0,
    r: 9 + 13 * Math.sqrt(n.events / maxEvents),
  }));
  // The force layout's spring/repulsion equilibrium (K_TARGET) is a
  // target, not a guarantee - two nodes pulled toward the same wall or
  // corner by the boundary clamp below can still end up closer than
  // that, circle-to-circle. minSeparation() is a hard floor enforced by
  // the collision-resolution pass after the force layout settles, sized
  // to clear both node radii and a rough label footprint (~6.2px/char at
  // this font size) so two adjacent names don't overlap either.
  function minSeparation(a, b) {
    const labelHalf = n => (n.person.length * 6.2) / 2 + 6;
    return Math.max(a.r + b.r + 16, labelHalf(a) + labelHalf(b));
  }
  const indexByPerson = new Map(sim.map((n, i) => [n.person, i]));
  const edgeList = edges
    .map(e => ({ a: indexByPerson.get(e.person_a), b: indexByPerson.get(e.person_b), w: e.shared_events }))
    .filter(e => e.a !== undefined && e.b !== undefined);
  let draggingIndex = null; // declared up front - referenced by node listeners wired below, before the drag handlers further down

  // Margins account for the name label (below each node, roughly
  // 6-7px/char) overflowing past the node's own radius, not just the
  // circle itself - without this, a node near the wall renders fine but
  // its label clips off the edge of the box.
  const marginX = 55, marginTop = 20, marginBottom = 36;
  const cx = simW / 2, cy = simH / 2;
  const k = Math.sqrt((simW * simH) / sim.length); // ideal inter-node spacing
  const iterations = 250;
  for (let iter = 0; iter < iterations; iter++) {
    const temp = k * (1 - iter / iterations); // cooling: big jumps early, tiny by the end
    sim.forEach(n => { n.vx = 0; n.vy = 0; });
    for (let i = 0; i < sim.length; i++) {
      for (let j = i + 1; j < sim.length; j++) {
        let dx = sim[i].x - sim[j].x, dy = sim[i].y - sim[j].y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const force = (k * k) / dist;
        const fx = (dx / dist) * force, fy = (dy / dist) * force;
        sim[i].vx += fx; sim[i].vy += fy;
        sim[j].vx -= fx; sim[j].vy -= fy;
      }
    }
    edgeList.forEach(e => {
      const a = sim[e.a], b = sim[e.b];
      const dx = a.x - b.x, dy = a.y - b.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const force = ((dist * dist) / k) * Math.min(e.w, 5) * 0.3;
      const fx = (dx / dist) * force, fy = (dy / dist) * force;
      a.vx -= fx; a.vy -= fy;
      b.vx += fx; b.vy += fy;
    });
    // A gentle pull toward the center for every node, not just connected
    // ones - otherwise unbounded pairwise repulsion (above) just pushes
    // everyone as far apart as the box allows, which in a finite box
    // means piled up against the walls/corners rather than spread across
    // the actual middle of the canvas, and an isolated node with no
    // edges has nothing else pulling it inward at all.
    sim.forEach(n => {
      n.vx += (cx - n.x) * 0.02;
      n.vy += (cy - n.y) * 0.02;
    });
    sim.forEach(n => {
      const disp = Math.sqrt(n.vx * n.vx + n.vy * n.vy) || 0.01;
      const capped = Math.min(disp, temp);
      n.x += (n.vx / disp) * capped;
      n.y += (n.vy / disp) * capped;
      n.x = Math.max(marginX, Math.min(simW - marginX, n.x));
      n.y = Math.max(marginTop, Math.min(simH - marginBottom, n.y));
    });
  }

  // The force layout above settles toward K_TARGET spacing on average,
  // but that's an equilibrium, not a guarantee - a cluster of nodes all
  // pulled toward the same wall or corner by the boundary clamp can still
  // land closer together than that, circle-to-circle. Directly push apart
  // any pair still under minSeparation() until none are, same idea as the
  // "resolve collisions" pass in a physics engine.
  for (let pass = 0; pass < 60; pass++) {
    let moved = false;
    for (let i = 0; i < sim.length; i++) {
      for (let j = i + 1; j < sim.length; j++) {
        const a = sim[i], b = sim[j];
        const dx = a.x - b.x, dy = a.y - b.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const minDist = minSeparation(a, b);
        if (dist < minDist) {
          moved = true;
          const push = (minDist - dist) / 2;
          const ux = dx / dist, uy = dy / dist;
          a.x += ux * push; a.y += uy * push;
          b.x -= ux * push; b.y -= uy * push;
        }
      }
    }
    sim.forEach(n => {
      n.x = Math.max(marginX, Math.min(simW - marginX, n.x));
      n.y = Math.max(marginTop, Math.min(simH - marginBottom, n.y));
    });
    if (!moved) break;
  }

  // The visible <svg> is pinned to the container's own pixel size; a <g>
  // ("world") holding every edge/node/label is panned and zoomed via an
  // SVG transform, so dragging/zooming never has to touch node positions
  // themselves - just the transform.
  const svg = el("svg", { width: w, height: h, viewBox: `0 0 ${w} ${h}`, style: "cursor:grab" });
  const world = el("g", {});
  svg.appendChild(world);

  const fitScale = Math.min(w / simW, h / simH);
  // maxScale is generous since the world is now sized well beyond the
  // viewport - reading a label inside a dense, mutually-connected cluster
  // (which the spring force pulls tighter than K_TARGET regardless of
  // total world size) means zooming in well past the default fit.
  const minScale = fitScale * 0.6, maxScale = 8;
  const view = { x: (w - simW * fitScale) / 2, y: (h - simH * fitScale) / 2, k: fitScale };
  function applyTransform() {
    world.setAttribute("transform", `translate(${view.x.toFixed(1)},${view.y.toFixed(1)}) scale(${view.k.toFixed(4)})`);
  }
  applyTransform();

  const edgeColor = cssVar("--grid");
  const lineEls = edgeList.map(e => {
    const a = sim[e.a], b = sim[e.b];
    const line = el("line", {
      x1: a.x.toFixed(1), y1: a.y.toFixed(1), x2: b.x.toFixed(1), y2: b.y.toFixed(1),
      stroke: edgeColor, "stroke-width": Math.min(1 + e.w, 6), opacity: 0.6,
    });
    world.appendChild(line);
    return line;
  });

  // Colored per the caller's getColor (defaults to the same green/orange/
  // red "time since last seen" scale as the People table's ring, so it
  // reads consistently across the tab even with no colorBy control).
  const colorFn = getColor || (n => typeof lastSeenColor === "function" ? lastSeenColor(daysSince(n.last_seen) ?? 0) : cssVar("--series-1"));
  const circleEls = [], labelEls = [];
  sim.forEach((n, i) => {
    const circle = el("circle", {
      cx: n.x.toFixed(1), cy: n.y.toFixed(1), r: n.r.toFixed(1),
      fill: colorFn(n), stroke: cssVar("--surface-1"), "stroke-width": 1.5,
      style: "cursor:grab;transition:fill 0.3s ease",
    });
    const label = el("text", {
      x: n.x.toFixed(1), y: (n.y + n.r + 12).toFixed(1), "text-anchor": "middle",
      fill: cssVar("--text-secondary"), "font-size": 11,
    });
    label.textContent = n.person;
    world.appendChild(label);
    world.appendChild(circle);
    circleEls.push(circle);
    labelEls.push(label);

    const tooltipHtml = () => `<strong>${n.person}</strong><br>${n.events} events &middot; ${Math.round(n.total_hours)}h`;
    circle.addEventListener("mouseenter", evt => showTooltip(evt, tooltipHtml()));
    circle.addEventListener("mousemove", evt => { if (draggingIndex === null) showTooltip(evt, tooltipHtml()); });
    circle.addEventListener("mouseleave", () => { if (draggingIndex === null) hideTooltip(); });
    circle.addEventListener("mousedown", evt => {
      draggingIndex = i;
      circle.style.cursor = "grabbing";
      evt.preventDefault();
      evt.stopPropagation(); // don't also start a background pan (see mousedown below)
    });
  });

  function updateNode(i) {
    const n = sim[i];
    circleEls[i].setAttribute("cx", n.x.toFixed(1));
    circleEls[i].setAttribute("cy", n.y.toFixed(1));
    labelEls[i].setAttribute("x", n.x.toFixed(1));
    labelEls[i].setAttribute("y", (n.y + n.r + 12).toFixed(1));
    edgeList.forEach((e, idx) => {
      if (e.a === i) { lineEls[idx].setAttribute("x1", n.x.toFixed(1)); lineEls[idx].setAttribute("y1", n.y.toFixed(1)); }
      if (e.b === i) { lineEls[idx].setAttribute("x2", n.x.toFixed(1)); lineEls[idx].setAttribute("y2", n.y.toFixed(1)); }
    });
  }

  // Screen pixels -> world coordinates, accounting for the current pan/zoom.
  function toWorld(evt, rect) {
    return {
      x: (evt.clientX - rect.left - view.x) / view.k,
      y: (evt.clientY - rect.top - view.y) / view.k,
    };
  }

  let panStart = null; // {mouseX, mouseY, viewX, viewY} while dragging the background
  svg.addEventListener("mousedown", evt => {
    if (draggingIndex !== null) return;
    panStart = { mouseX: evt.clientX, mouseY: evt.clientY, viewX: view.x, viewY: view.y };
    svg.style.cursor = "grabbing";
  });
  svg.addEventListener("mousemove", evt => {
    if (draggingIndex !== null) {
      const rect = svg.getBoundingClientRect();
      const n = sim[draggingIndex];
      const world_pt = toWorld(evt, rect);
      n.x = Math.max(marginX, Math.min(simW - marginX, world_pt.x));
      n.y = Math.max(marginTop, Math.min(simH - marginBottom, world_pt.y));
      updateNode(draggingIndex);
      return;
    }
    if (panStart) {
      view.x = panStart.viewX + (evt.clientX - panStart.mouseX);
      view.y = panStart.viewY + (evt.clientY - panStart.mouseY);
      applyTransform();
    }
  });
  const endDrag = () => {
    if (draggingIndex !== null) circleEls[draggingIndex].style.cursor = "grab";
    draggingIndex = null;
    panStart = null;
    svg.style.cursor = "grab";
  };
  svg.addEventListener("mouseup", endDrag);
  svg.addEventListener("mouseleave", endDrag);

  // Zoom toward the cursor (or pinch-zoom, which browsers report as a
  // ctrlKey wheel event) rather than always toward the canvas center, so
  // the part of the graph the user's actually pointing at stays put.
  svg.addEventListener("wheel", evt => {
    evt.preventDefault();
    const rect = svg.getBoundingClientRect();
    const mx = evt.clientX - rect.left, my = evt.clientY - rect.top;
    const wx = (mx - view.x) / view.k, wy = (my - view.y) / view.k;
    const factor = evt.deltaY < 0 ? 1.12 : 1 / 1.12;
    view.k = Math.max(minScale, Math.min(maxScale, view.k * factor));
    view.x = mx - wx * view.k;
    view.y = my - wy * view.k;
    applyTransform();
  }, { passive: false });

  container.appendChild(svg);
  container._networkResetView = () => {
    view.x = (w - simW * fitScale) / 2;
    view.y = (h - simH * fitScale) / 2;
    view.k = fitScale;
    applyTransform();
  };
  // Lets a caller (e.g. the People tab's "color by" dropdown) recolor
  // nodes in place - a CSS transition on `fill` above makes it a smooth
  // fade rather than a snap - without rerunning the layout or resetting
  // the current pan/zoom, which a full re-render would otherwise do.
  container._networkSetColor = newGetColor => {
    sim.forEach((n, i) => circleEls[i].setAttribute("fill", newGetColor(n)));
  };
}
