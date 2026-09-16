// Minimal dependency-free SVG chart helpers, following the mark specs:
// bars <=24px thick with 4px rounded data-ends, hairline gridlines, a
// shared hover tooltip, legend for multi-series charts.

const SVG_NS = "http://www.w3.org/2000/svg";
const SERIES_VARS = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6"];

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

/** Horizontal bar chart: data = [{label, value}], one series. */
function horizontalBarChart(container, data, { valueLabel = "", color } = {}) {
  container.innerHTML = "";
  if (!data.length) {
    container.innerHTML = '<p class="empty-note">No data yet.</p>';
    return;
  }
  const barColor = color || cssVar("--series-1");
  const barH = 16, labelW = 220, lineHeight = 13, width = container.clientWidth || 640;
  const chartW = width - labelW - 60;
  const maxCharsPerLine = Math.floor((labelW - 10) / 6);
  const max = Math.max(...data.map(d => d.value), 1);

  const wrapped = data.map(d => wrapLabel(String(d.label), maxCharsPerLine));
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

/** Vertical column chart: data = [{label, value}], one series, baseline-anchored. */
function columnChart(container, data, { valueLabel = "", color, highlight } = {}) {
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
    const fill = highlight && highlight(d) ? highlight(d) : barColor;
    const rect = el("rect", { x, y, width: bw, height: barH, rx: 4, ry: 4, fill });
    rect.addEventListener("mousemove", (evt) => showTooltip(evt, `<strong>${d.label}</strong><br>${formatNumber(d.value)} ${valueLabel}`));
    rect.addEventListener("mouseleave", hideTooltip);
    svg.appendChild(rect);
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
      dot.addEventListener("mousemove", (evt) => showTooltip(evt, `<strong>${s.name}</strong><br>${p.x}: ${formatNumber(p.y)} ${yLabel}`));
      dot.addEventListener("mouseleave", hideTooltip);
      svg.appendChild(dot);
    });
    if (series.length > 1) {
      const item = document.createElement("span");
      item.innerHTML = `<span class="swatch" style="background:${color}"></span>${s.name}`;
      legend.appendChild(item);
    }
  });

  const xTicks = [...new Set(xs)].sort((a, b) => a - b);
  xTicks.forEach(x => {
    svg.appendChild(el("text", { x: xScale(x), y: height - 8, "text-anchor": "middle", fill: cssVar("--text-muted"), "font-size": 10 })).textContent = x;
  });
  svg.appendChild(el("line", { x1: padLeft, x2: width - padRight, y1: padTop + chartH, y2: padTop + chartH, stroke: cssVar("--baseline"), "stroke-width": 1 }));

  if (series.length > 1) container.appendChild(legend);
  container.appendChild(svg);
}

/** Week-activity strip: cells = [{date, active: bool}] rendered as a compact grid. */
function weekStrip(container, cells) {
  container.innerHTML = "";
  if (!cells.length) {
    container.innerHTML = '<p class="empty-note">No data yet.</p>';
    return;
  }
  const cols = 52;
  const cellSize = 10, gap = 2;
  const rows = Math.ceil(cells.length / cols);
  const width = cols * (cellSize + gap), height = rows * (cellSize + gap);
  const svg = el("svg", { width, height, viewBox: `0 0 ${width} ${height}` });
  cells.forEach((c, i) => {
    const col = i % cols, row = Math.floor(i / cols);
    const rect = el("rect", {
      x: col * (cellSize + gap), y: row * (cellSize + gap),
      width: cellSize, height: cellSize, rx: 2, ry: 2,
      fill: c.active ? cssVar("--series-3") : cssVar("--grid"),
    });
    rect.addEventListener("mousemove", (evt) => showTooltip(evt, `${c.date}<br>${c.active ? "active" : "no event"}`));
    rect.addEventListener("mouseleave", hideTooltip);
    svg.appendChild(rect);
  });
  container.appendChild(svg);
}

function statTile(label, value) {
  const div = document.createElement("div");
  div.className = "stat-tile";
  div.innerHTML = `<div class="label">${label}</div><div class="value">${value}</div>`;
  return div;
}
