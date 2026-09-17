// Global filters (Overview tab) - every /api/ call includes them, so
// narrowing the date range or unchecking a category there applies
// everywhere else too.
const GLOBAL_FILTERS = { startDate: "", endDate: "", excludeCategories: [] };

function api(path) {
  const [base, query] = path.split("?");
  const params = new URLSearchParams(query || "");
  if (GLOBAL_FILTERS.startDate) params.set("start_date", GLOBAL_FILTERS.startDate);
  if (GLOBAL_FILTERS.endDate) params.set("end_date", GLOBAL_FILTERS.endDate);
  if (GLOBAL_FILTERS.excludeCategories.length) params.set("exclude_categories", GLOBAL_FILTERS.excludeCategories.join(","));
  const qs = params.toString();
  return fetch(`/api/${base}${qs ? `?${qs}` : ""}`).then(r => r.json());
}

function table(container, columns, rows) {
  container.innerHTML = "";
  if (!rows.length) {
    container.innerHTML = '<p class="empty-note">Nothing here yet.</p>';
    return;
  }
  const t = document.createElement("table");
  const thead = document.createElement("thead");
  thead.innerHTML = "<tr>" + columns.map(c => `<th class="${c.num ? 'num' : ''}">${c.label}</th>`).join("") + "</tr>";
  const tbody = document.createElement("tbody");
  rows.forEach(row => {
    const tr = document.createElement("tr");
    tr.innerHTML = columns.map(c => `<td class="${c.num ? 'num' : ''}">${c.format ? c.format(row[c.key]) : (row[c.key] ?? "-")}</td>`).join("");
    tbody.appendChild(tr);
  });
  t.appendChild(thead);
  t.appendChild(tbody);
  container.appendChild(t);
}

function fmtHours(h) { return h == null ? "-" : `${Math.round(h)}h`; }
function fmtDate(d) { return d ? d.slice(0, 10) : "-"; }
function fmtPct(p) { return p == null ? "-" : `${(p * 100).toFixed(0)}%`; }
function cssVarSafe(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

// --- Tabs ---
document.getElementById("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-panel]");
  if (!btn) return;
  document.querySelectorAll("nav.tabs button").forEach(b => b.classList.toggle("active", b === btn));
  document.querySelectorAll(".panel").forEach(p => p.classList.toggle("active", p.id === `panel-${btn.dataset.panel}`));
});

// --- Overview ---
async function loadMeta() {
  const meta = await api("meta"); // unfiltered - populates filter bounds/dropdowns
  const startInput = document.getElementById("global-start-date");
  const endInput = document.getElementById("global-end-date");
  startInput.min = endInput.min = meta.min_date;
  startInput.max = endInput.max = meta.max_date;
  if (!startInput.value) {
    startInput.value = meta.min_date;
    endInput.value = meta.max_date;
    GLOBAL_FILTERS.startDate = meta.min_date;
    GLOBAL_FILTERS.endDate = meta.max_date;
  }
  categoryColors = buildCategoryColors(meta.categories, meta.category_colors);

  const categoryList = document.getElementById("category-filter-list");
  if (!categoryList.dataset.populated) {
    categoryList.innerHTML = meta.categories.map(c => `
      <label style="display:flex;align-items:center;gap:4px;font-weight:normal">
        <input type="checkbox" class="category-checkbox" value="${c}" checked>
        <span class="swatch" style="background:${categoryColors[c] || cssVarSafe("--text-muted")}"></span>${c}
      </label>
    `).join("");
    categoryList.dataset.populated = "1";
  }
  return meta;
}

async function refreshOverviewStats() {
  const summary = await api("summary");
  document.getElementById("subtitle").textContent =
    `${summary.total_events.toLocaleString()} events, ${Math.round(summary.total_hours).toLocaleString()} tracked hours, ${summary.n_people} people tagged`;

  const statGrid = document.getElementById("overview-stats");
  statGrid.innerHTML = "";
  statGrid.appendChild(statTile("Total events", summary.total_events.toLocaleString()));
  statGrid.appendChild(statTile("Tracked hours", Math.round(summary.total_hours).toLocaleString()));
  statGrid.appendChild(statTile("Top category", summary.top_category || "-"));
  statGrid.appendChild(statTile("People tagged", summary.n_people));

  const places = await api("places?limit=10");
  horizontalBarChart(document.getElementById("overview-places-chart"),
    places.map(p => ({ label: p.location, value: p.visits })), { valueLabel: "visits", addressLines: true });

  await loadPhasesOfLife();
}

async function loadPhasesOfLife() {
  const data = await api("breaks");
  const phases = data.category_phases.filter(p => p.type === "active");

  const container = document.getElementById("phases-list");
  if (!phases.length) {
    container.innerHTML = '<p class="empty-note">Nothing here yet.</p>';
    return;
  }

  const byCategory = new Map();
  phases.forEach(p => {
    if (!byCategory.has(p.category)) byCategory.set(p.category, []);
    byCategory.get(p.category).push({ start: p.start, end: p.end });
  });

  // Most time-covered categories on top - the ones that actually define a
  // "phase of life" rather than a brief blip.
  const groups = [...byCategory.entries()]
    .map(([label, segments]) => ({
      label,
      segments,
      color: categoryColors[label],
      totalDays: segments.reduce((sum, s) => sum + (new Date(s.end) - new Date(s.start)), 0),
    }))
    .sort((a, b) => b.totalDays - a.totalDays);

  timelineChart(container, groups);
}

function wireGlobalDateFilter() {
  const startInput = document.getElementById("global-start-date");
  const endInput = document.getElementById("global-end-date");
  const onChange = () => {
    GLOBAL_FILTERS.startDate = startInput.value;
    GLOBAL_FILTERS.endDate = endInput.value;
    reloadAll();
  };
  startInput.addEventListener("change", onChange);
  endInput.addEventListener("change", onChange);
  document.getElementById("global-range-reset").addEventListener("click", async () => {
    const meta = await api("meta");
    startInput.value = meta.min_date;
    endInput.value = meta.max_date;
    onChange();
  });
}

function wireCategoryFilter() {
  const checkboxes = () => [...document.querySelectorAll(".category-checkbox")];
  const onChange = () => {
    GLOBAL_FILTERS.excludeCategories = checkboxes().filter(cb => !cb.checked).map(cb => cb.value);
    reloadAll();
  };
  document.getElementById("category-filter-list").addEventListener("change", onChange);
  document.getElementById("category-filter-all").addEventListener("click", () => {
    checkboxes().forEach(cb => { cb.checked = true; });
    onChange();
  });
  document.getElementById("category-filter-none").addEventListener("click", () => {
    checkboxes().forEach(cb => { cb.checked = false; });
    onChange();
  });
}

async function reloadAll() {
  await Promise.all([
    refreshOverviewStats(),
    loadPlaces(),
    refreshMap(),
    loadPeople(),
    refreshHabit(),
    loadTravel(),
    loadTime(),
    refreshSeasonality(),
    loadAnomalies(),
  ]);
}

// --- Places ---
async function loadPlaces() {
  const places = await api("places?limit=20");
  horizontalBarChart(document.getElementById("places-chart"),
    places.map(p => ({ label: p.location, value: p.visits })), { valueLabel: "visits", addressLines: true });
}

// --- People ---
async function loadPeople() {
  const select = document.getElementById("people-granularity");
  if (!select.dataset.wired) {
    select.addEventListener("change", refreshPeopleTrend);
    select.dataset.wired = "1";
  }

  // Independent pieces of this tab, run with allSettled rather than
  // sequential awaits: one endpoint failing (or an empty result) must
  // never silently prevent the others from rendering.
  const results = await Promise.allSettled([
    (async () => {
      const people = await api("people?limit=15");
      horizontalBarChart(document.getElementById("people-chart"),
        people.map(p => ({ label: p.person, value: p.total_hours })), { valueLabel: "hours" });
    })(),
    refreshPeopleTrend(),
    (async () => {
      const trends = await api("trends");
      table(document.getElementById("trends-table"),
        [
          { key: "person", label: "Person" },
          { key: "total_events", label: "Total events", num: true },
          { key: "slope_events_per_year", label: "Trend (events/yr)", num: true, format: v => v?.toFixed(2) },
        ], trends);
    })(),
  ]);
  results.forEach(r => { if (r.status === "rejected") console.error("People tab section failed:", r.reason); });
}

function formatPeriodLabel(period, granularity) {
  if (granularity === "year") return period.slice(0, 4);
  if (granularity === "month") return period.slice(0, 7);
  return period.slice(0, 10);
}

async function refreshPeopleTrend() {
  const granularity = document.getElementById("people-granularity").value;
  const rows = await api(`person-trend?top_n=6&granularity=${granularity}`);
  const periods = [...new Set(rows.map(r => r.period))].sort();
  const indexOf = Object.fromEntries(periods.map((p, i) => [p, i]));
  const byPerson = {};
  rows.forEach(r => {
    (byPerson[r.person] = byPerson[r.person] || []).push({
      x: indexOf[r.period], xLabel: formatPeriodLabel(r.period, granularity), y: r.count,
    });
  });
  lineChart(document.getElementById("people-year-chart"),
    Object.entries(byPerson).map(([name, points]) => ({ name, points })), { yLabel: "events" });
}

// --- Habits ---
async function loadHabits(meta) {
  const select = document.getElementById("habit-category");
  if (!select.dataset.populated) {
    select.innerHTML = meta.categories.map(c => `<option value="${c}">${c}</option>`).join("");
    select.dataset.populated = "1";
    select.addEventListener("change", refreshHabit);
  }
  await refreshHabit();
}

async function refreshHabit() {
  const category = document.getElementById("habit-category").value;
  if (!category) return;
  const data = await api(`habit?category=${encodeURIComponent(category)}`);

  weekStrip(document.getElementById("habit-strip"), data.weekly.map(w => ({ date: w.week.slice(0, 10), count: w.count })),
    { color: categoryColors[category] });

  const statGrid = document.getElementById("habit-stats");
  statGrid.innerHTML = "";
  const corr = data.correlation.correlation;
  statGrid.appendChild(statTile("Weeks tracked", data.weekly.length));
  statGrid.appendChild(statTile("Correlation w/ busy weeks", corr == null ? "n/a" : corr.toFixed(2)));
  const longestActive = data.streaks.filter(s => s.active).sort((a, b) => b.weeks - a.weeks)[0];
  statGrid.appendChild(statTile("Longest streak", longestActive ? `${longestActive.weeks} wks` : "-"));
  const longestGap = data.streaks.filter(s => !s.active).sort((a, b) => b.weeks - a.weeks)[0];
  statGrid.appendChild(statTile("Longest gap", longestGap ? `${longestGap.weeks} wks` : "-"));

  columnChart(document.getElementById("habit-consistency-chart"),
    data.consistency.map(c => ({ label: String(c.year), value: c.consistency * 100 })), { valueLabel: "% weeks active" });

  table(document.getElementById("habit-streaks-table"),
    [
      { key: "active", label: "Type", format: v => v ? "Active" : "Gap" },
      { key: "start", label: "Start", format: fmtDate },
      { key: "end", label: "End", format: fmtDate },
      { key: "weeks", label: "Weeks", num: true },
    ], data.streaks.slice(0, 12));

  // Independent of the category picker above - runs alongside it, but a
  // failure here shouldn't take the rest of the tab down with it.
  loadRecurringEvents().catch(err => console.error("Recurring events section failed:", err));
}

async function loadRecurringEvents() {
  const rows = await api("recurring");
  table(document.getElementById("recurring-table"),
    [
      { key: "title", label: "Event" },
      { key: "category", label: "Category" },
      { key: "cadence", label: "Usual cadence" },
      { key: "last_seen", label: "Last seen", format: fmtDate },
      { key: "days_since_last", label: "Days since", num: true, format: v => Math.round(v) },
      { key: "status", label: "Status", format: v => `<span class="badge ${v.replace(/\s+/g, "-")}">${v}</span>` },
    ], rows);
}

// --- Travel ---
async function loadTravel() {
  const data = await api("travel");
  const note = document.getElementById("travel-note");
  note.textContent = data.message || "";
  note.style.display = data.message ? "block" : "none";

  const statGrid = document.getElementById("travel-stats");
  statGrid.innerHTML = "";
  statGrid.appendChild(statTile("Home region", data.home_region || "-"));
  statGrid.appendChild(statTile("Regions visited", data.region_visits.length));
  statGrid.appendChild(statTile("Trips away from home", data.region_trips.length));

  table(document.getElementById("travel-region-trips"),
    [
      { key: "region", label: "Region" },
      { key: "start", label: "Start", format: fmtDate },
      { key: "end", label: "End", format: fmtDate },
      { key: "duration_days", label: "Days", num: true, format: v => v?.toFixed(1) },
    ], data.region_trips);

  table(document.getElementById("travel-regions-table"),
    [
      { key: "region", label: "Region" },
      { key: "visits", label: "Events", num: true },
      { key: "total_hours", label: "Hours", num: true, format: v => Math.round(v) },
      { key: "n_locations", label: "Places", num: true },
      { key: "first_seen", label: "First seen", format: fmtDate },
      { key: "last_seen", label: "Last seen", format: fmtDate },
    ], data.region_visits);

  table(document.getElementById("travel-timeline"),
    [
      { key: "destination", label: "Destination" },
      { key: "start", label: "Start", format: fmtDate },
      { key: "end", label: "End", format: fmtDate },
      { key: "duration_days", label: "Days", num: true, format: v => v?.toFixed(1) },
    ], data.tagged_trips);
}

// --- Time & Spend ---
async function loadTime() {
  const rows = await api("time-by-category");
  columnChart(document.getElementById("time-chart"),
    rows.map(r => ({ label: r.category, value: r.total_hours })),
    { valueLabel: "hours", highlight: d => categoryColors[d.label] || null });
  table(document.getElementById("time-table"),
    [
      { key: "category", label: "Category" },
      { key: "events", label: "Events", num: true },
      { key: "total_hours", label: "Hours", num: true, format: v => Math.round(v) },
      { key: "share_of_hours", label: "Share", num: true, format: fmtPct },
      { key: "estimated_spend", label: "Est. spend", num: true, format: v => v == null ? "-" : `$${Math.round(v).toLocaleString()}` },
    ], rows);
}

// --- Seasonality ---
async function loadSeasonality(meta) {
  const select = document.getElementById("season-category");
  if (!select.dataset.populated) {
    select.innerHTML += meta.categories.map(c => `<option value="${c}">${c}</option>`).join("");
    select.dataset.populated = "1";
    select.addEventListener("change", refreshSeasonality);
  }
  await refreshSeasonality();
}

async function refreshSeasonality() {
  const category = document.getElementById("season-category").value;
  const data = await api(`seasonality${category ? `?category=${encodeURIComponent(category)}` : ""}`);
  const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  columnChart(document.getElementById("season-month-chart"),
    data.monthly.map(m => ({ label: monthNames[m.month - 1], value: m.avg_events_per_month })), { valueLabel: "avg events" });
  columnChart(document.getElementById("season-season-chart"),
    data.seasonal.map(s => ({ label: s.season, value: s.avg_events_per_season })), { valueLabel: "avg events" });
}

// --- Anomalies ---
async function loadAnomalies() {
  const data = await api("anomalies?z=2.0");
  const flagByWeek = new Map(data.anomalies.map(a => [a.week, a.label]));
  columnChart(document.getElementById("anomalies-chart"),
    data.weekly.map(w => ({ label: w.week.slice(0, 7), value: w.total_hours, week: w.week })),
    {
      valueLabel: "hours",
      highlight: d => {
        const label = flagByWeek.get(d.week);
        if (label === "packed") return cssVarSafe("--series-2");
        if (label === "empty") return cssVarSafe("--series-3");
        return null;
      },
    });
  table(document.getElementById("anomalies-table"),
    [
      { key: "week", label: "Week of", format: fmtDate },
      { key: "total_hours", label: "Hours", num: true, format: v => Math.round(v) },
      { key: "label", label: "Type", format: v => `<span class="badge ${v}">${v}</span>` },
      { key: "z_score", label: "Z-score", num: true, format: v => v?.toFixed(2) },
    ], data.anomalies);
  table(document.getElementById("anomalies-category-table"),
    [
      { key: "week", label: "Week of", format: fmtDate },
      { key: "category", label: "Category" },
      { key: "hours", label: "Hours", num: true, format: v => Math.round(v) },
      { key: "label", label: "Type", format: v => `<span class="badge ${v}">${v}</span>` },
      { key: "z_score", label: "Z-score", num: true, format: v => v?.toFixed(2) },
    ], data.by_category.slice(0, 30));
}

// --- Category colors (shared across Map, Habits, Time & Spend) ---
let categoryColors = {};

function buildCategoryColors(categories, realColors) {
  // Prefer the color you already picked for that Calendar in Calendar.app
  // (captured by CalendarExporter, keyed by category in meta.category_colors)
  // over the fixed palette, so the dashboard's colors match what you
  // already associate with each category. Categories without a captured
  // color (older exports, or a category set via a note tag rather than a
  // dedicated calendar) fall back to the fixed order - by overall
  // frequency, so a category's color never repaints when a filter changes
  // the set of series on screen.
  const colors = {};
  let fallbackSlot = 0;
  categories.forEach(cat => {
    if (realColors && realColors[cat]) {
      colors[cat] = realColors[cat];
    } else {
      colors[cat] = fallbackSlot < 6 ? seriesColor(fallbackSlot) : cssVarSafe("--text-muted");
      fallbackSlot++;
    }
  });
  return colors;
}

// --- Map ---
let leafletMap, markerLayer;
let hasSetInitialMapView = false;

/** Coarse grid-clusters locations (by degree-sized bins) and returns the
 * [lat, lon] points belonging to whichever bin holds the most visits - a
 * simple "zoom to where most of your life happens" default, so one trip to
 * the other side of the world doesn't force the initial view out to a
 * whole-world scale where the actual cluster of regular places is a speck. */
function densestClusterPoints(locations, binSizeDegrees = 5) {
  const bins = new Map();
  locations.forEach(loc => {
    const key = `${Math.round(loc.lat / binSizeDegrees)},${Math.round(loc.lon / binSizeDegrees)}`;
    if (!bins.has(key)) bins.set(key, { visits: 0, points: [] });
    const bin = bins.get(key);
    bin.visits += loc.visits;
    bin.points.push([loc.lat, loc.lon]);
  });
  let best = null;
  for (const bin of bins.values()) {
    if (!best || bin.visits > best.visits) best = bin;
  }
  return best ? best.points : [];
}
let geocodePollTimer = null;

function renderMapLegend(categories) {
  const legend = document.getElementById("map-legend");
  legend.innerHTML = "";
  const shown = categories.slice(0, 6);
  const rest = categories.length > 6;
  shown.forEach(cat => {
    const item = document.createElement("span");
    item.innerHTML = `<span class="swatch" style="background:${categoryColors[cat]}"></span>${cat}`;
    legend.appendChild(item);
  });
  if (rest) {
    const item = document.createElement("span");
    item.innerHTML = `<span class="swatch" style="background:${cssVarSafe("--text-muted")}"></span>Other`;
    legend.appendChild(item);
  }
}

function populateYearSelects(minYear, maxYear) {
  const startSel = document.getElementById("map-start-year");
  const endSel = document.getElementById("map-end-year");
  const years = [];
  for (let y = minYear; y <= maxYear; y++) years.push(y);
  startSel.innerHTML = years.map(y => `<option value="${y}">${y}</option>`).join("");
  endSel.innerHTML = years.map(y => `<option value="${y}">${y}</option>`).join("");
  startSel.value = minYear;
  endSel.value = maxYear;
}

async function loadMap(meta) {
  if (!leafletMap) {
    leafletMap = L.map("map").setView([20, 0], 2);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(leafletMap);
    markerLayer = L.layerGroup().addTo(leafletMap);

    document.getElementById("map-category").innerHTML += meta.categories.map(c => `<option value="${c}">${c}</option>`).join("");
    document.getElementById("map-person").innerHTML += meta.people.map(p => `<option value="${p}">${p}</option>`).join("");
    populateYearSelects(meta.min_year, meta.max_year);
    renderMapLegend(meta.categories);

    ["map-category", "map-person", "map-start-year", "map-end-year"].forEach(id =>
      document.getElementById(id).addEventListener("change", refreshMap));

    document.getElementById("map-geocode-btn").addEventListener("click", async () => {
      const resp = await fetch("/api/geocode/start", { method: "POST" });
      if (resp.ok) await pollGeocodeStatus();
    });

    // Leaflet measures the container on init; the Map tab may have been
    // hidden (display:none) at that point, so its size reads as zero.
    document.querySelector('button[data-panel="map"]').addEventListener("click", () => {
      setTimeout(() => leafletMap.invalidateSize(), 0);
    });
  }
  await refreshMap();
  await pollGeocodeStatus(); // resumes the progress bar if a job was already running
}

function updateGeocodeButton(locationsData) {
  const btn = document.getElementById("map-geocode-btn");
  if (btn.disabled && geocodePollTimer) return; // a job is running - leave it to the poller
  if (locationsData && locationsData.total_places > 0 && locationsData.total_places === locationsData.geocoded_places) {
    btn.disabled = true;
    btn.textContent = "All locations geocoded";
  } else {
    btn.disabled = false;
    btn.textContent = "Geocode locations";
  }
}

async function pollGeocodeStatus() {
  const status = await fetch("/api/geocode/status").then(r => r.json());
  const btn = document.getElementById("map-geocode-btn");
  const progressWrap = document.getElementById("map-geocode-progress");

  if (!status.running) {
    if (geocodePollTimer) {
      clearInterval(geocodePollTimer);
      geocodePollTimer = null;
      await refreshMap(); // job just finished - show the newly-geocoded points
    }
    progressWrap.style.display = "none";
    return;
  }

  btn.disabled = true;
  btn.textContent = "Geocoding…";
  progressWrap.style.display = "flex";
  const pct = status.total ? Math.round((status.done / status.total) * 100) : 0;
  document.getElementById("map-geocode-fill").style.width = pct + "%";
  document.getElementById("map-geocode-label").textContent = status.total ? `${status.done} / ${status.total}` : "Starting…";

  if (!geocodePollTimer) {
    geocodePollTimer = setInterval(pollGeocodeStatus, 1000);
  }
}

async function refreshMap() {
  const category = document.getElementById("map-category").value;
  const person = document.getElementById("map-person").value;
  const startYear = document.getElementById("map-start-year").value;
  const endYear = document.getElementById("map-end-year").value;
  const params = new URLSearchParams();
  if (category) params.set("category", category);
  if (person) params.set("person", person);
  if (startYear) params.set("start_year", startYear);
  if (endYear) params.set("end_year", endYear);

  const data = await api(`locations?${params.toString()}`);
  markerLayer.clearLayers();
  updateGeocodeButton(data);

  const note = document.getElementById("map-note");
  if (data.geocoded_places === 0) {
    note.innerHTML = data.total_places === 0
      ? "No locations match this filter."
      : `None of your ${data.total_places} matching locations are geocoded yet. Click "Geocode locations" below (needs network - it calls OpenStreetMap).`;
    return;
  }
  note.textContent = data.total_places > data.geocoded_places
    ? `Showing ${data.geocoded_places} of ${data.total_places} matching locations (the rest aren't geocoded yet).`
    : `Showing all ${data.geocoded_places} matching locations. Circle size = visit count.`;

  const maxVisits = Math.max(...data.locations.map(l => l.visits), 1);
  const bounds = [];
  data.locations.forEach(loc => {
    const primaryCategory = (loc.categories && loc.categories[0]) || "Other";
    const color = categoryColors[primaryCategory] || cssVarSafe("--text-muted");
    const radius = 5 + 15 * Math.sqrt(loc.visits / maxVisits);
    const marker = L.circleMarker([loc.lat, loc.lon], {
      radius, color, fillColor: color, fillOpacity: 0.6, weight: 1,
    });
    marker.bindPopup(
      `<strong>${loc.location}</strong><br>${loc.visits} visits (${fmtDate(loc.first_seen)} – ${fmtDate(loc.last_seen)})<br>${(loc.categories || []).join(", ")}`
    );
    marker.addTo(markerLayer);
    bounds.push([loc.lat, loc.lon]);
  });

  if (!hasSetInitialMapView && bounds.length) {
    // First render: default to the densest cluster of places (typically
    // "home") rather than zooming out to fit every far-flung trip too.
    const dense = densestClusterPoints(data.locations);
    leafletMap.fitBounds(dense.length ? dense : bounds, { padding: [30, 30], maxZoom: 12 });
    hasSetInitialMapView = true;
  } else if (bounds.length) {
    leafletMap.fitBounds(bounds, { padding: [30, 30], maxZoom: 12 });
  }
}

// --- Boot ---
(async function init() {
  const meta = await loadMeta();
  await refreshOverviewStats();
  await Promise.all([
    loadPlaces(),
    loadMap(meta),
    loadPeople(),
    loadHabits(meta),
    loadTravel(),
    loadTime(),
    loadSeasonality(meta),
    loadAnomalies(),
  ]);
  wireGlobalDateFilter();
  wireCategoryFilter();
})();
