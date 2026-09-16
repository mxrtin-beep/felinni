const api = (path) => fetch(`/api/${path}`).then(r => r.json());

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

// --- Tabs ---
document.getElementById("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-panel]");
  if (!btn) return;
  document.querySelectorAll("nav.tabs button").forEach(b => b.classList.toggle("active", b === btn));
  document.querySelectorAll(".panel").forEach(p => p.classList.toggle("active", p.id === `panel-${btn.dataset.panel}`));
});

// --- Overview ---
async function loadOverview() {
  const meta = await api("meta");
  document.getElementById("subtitle").textContent =
    `${meta.total_events.toLocaleString()} events, ${meta.min_year}–${meta.max_year}, ${Math.round(meta.total_hours).toLocaleString()} tracked hours, ${meta.n_people} people tagged`;

  const statGrid = document.getElementById("overview-stats");
  statGrid.innerHTML = "";
  statGrid.appendChild(statTile("Total events", meta.total_events.toLocaleString()));
  statGrid.appendChild(statTile("Years covered", `${meta.min_year}–${meta.max_year}`));
  statGrid.appendChild(statTile("Tracked hours", Math.round(meta.total_hours).toLocaleString()));
  statGrid.appendChild(statTile("Top category", meta.categories[0] || "-"));

  const places = await api("places?limit=10");
  horizontalBarChart(document.getElementById("overview-places-chart"),
    places.map(p => ({ label: p.location, value: p.visits })), { valueLabel: "visits" });

  return meta;
}

// --- Places ---
async function loadPlaces() {
  const places = await api("places?limit=20");
  horizontalBarChart(document.getElementById("places-chart"),
    places.map(p => ({ label: p.location, value: p.visits })), { valueLabel: "visits" });
  await refreshStoppedGoing();
}

async function refreshStoppedGoing() {
  const minVisits = document.getElementById("stopped-min-visits").value;
  const inactiveMonths = document.getElementById("stopped-inactive-months").value;
  const rows = await api(`stopped-going?min_visits=${minVisits}&inactive_months=${inactiveMonths}`);
  table(document.getElementById("stopped-going-table"),
    [
      { key: "location", label: "Location" },
      { key: "visits", label: "Visits", num: true },
      { key: "last_seen", label: "Last seen", format: fmtDate },
      { key: "months_since_last_visit", label: "Months ago", num: true, format: v => v?.toFixed(1) },
    ], rows);
}
document.getElementById("stopped-min-visits").addEventListener("change", refreshStoppedGoing);
document.getElementById("stopped-inactive-months").addEventListener("change", refreshStoppedGoing);

// --- People ---
async function loadPeople() {
  const people = await api("people?limit=15");
  horizontalBarChart(document.getElementById("people-chart"),
    people.map(p => ({ label: p.person, value: p.total_hours })), { valueLabel: "hours" });

  const yearRows = await api("person-year-trend?top_n=6");
  const byPerson = {};
  yearRows.forEach(r => {
    (byPerson[r.person] = byPerson[r.person] || []).push({ x: r.year, y: r.count });
  });
  lineChart(document.getElementById("people-year-chart"),
    Object.entries(byPerson).map(([name, points]) => ({ name, points })), { yLabel: "events" });

  const trends = await api("trends");
  table(document.getElementById("trends-table"),
    [
      { key: "person", label: "Person" },
      { key: "total_events", label: "Total events", num: true },
      { key: "slope_events_per_year", label: "Trend (events/yr)", num: true, format: v => v?.toFixed(2) },
    ], trends);
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

  weekStrip(document.getElementById("habit-strip"), data.weekly.map(w => ({ date: w.week.slice(0, 10), active: w.count > 0 })));

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
}

// --- Travel ---
async function loadTravel() {
  const data = await api("travel");
  table(document.getElementById("travel-timeline"),
    [
      { key: "destination", label: "Destination" },
      { key: "start", label: "Start", format: fmtDate },
      { key: "end", label: "End", format: fmtDate },
      { key: "duration_days", label: "Days", num: true, format: v => v?.toFixed(1) },
    ], data.trips);
  table(document.getElementById("travel-places-table"),
    [
      { key: "destination", label: "Destination" },
      { key: "trips", label: "Trips", num: true },
      { key: "first_visit", label: "First visit", format: fmtDate },
      { key: "last_visit", label: "Last visit", format: fmtDate },
    ], data.places);
}

// --- Time & Spend ---
async function loadTime() {
  const rows = await api("time-by-category");
  columnChart(document.getElementById("time-chart"),
    rows.map(r => ({ label: r.category, value: r.total_hours })), { valueLabel: "hours" });
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
  const flaggedWeeks = new Set(data.anomalies.map(a => a.week));
  columnChart(document.getElementById("anomalies-chart"),
    data.weekly.map(w => ({ label: w.week.slice(0, 7), value: w.total_hours, week: w.week })),
    { valueLabel: "hours", highlight: d => flaggedWeeks.has(d.week) ? cssVarSafe("--series-2") : null });
  table(document.getElementById("anomalies-table"),
    [
      { key: "week", label: "Week of", format: fmtDate },
      { key: "total_hours", label: "Hours", num: true, format: v => Math.round(v) },
      { key: "label", label: "Type", format: v => `<span class="badge ${v}">${v}</span>` },
      { key: "z_score", label: "Z-score", num: true, format: v => v?.toFixed(2) },
    ], data.anomalies);
}
function cssVarSafe(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

// --- Map ---
let categoryColors = {};
let leafletMap, markerLayer;

function buildCategoryColors(categories) {
  // Fixed order = overall frequency order from meta, so a category's color
  // never repaints when a filter changes the set of series on screen.
  const colors = {};
  categories.forEach((cat, i) => {
    colors[cat] = i < 6 ? seriesColor(i) : cssVarSafe("--text-muted");
  });
  return colors;
}

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
    categoryColors = buildCategoryColors(meta.categories);
    renderMapLegend(meta.categories);

    ["map-category", "map-person", "map-start-year", "map-end-year"].forEach(id =>
      document.getElementById(id).addEventListener("change", refreshMap));

    // Leaflet measures the container on init; the Map tab may have been
    // hidden (display:none) at that point, so its size reads as zero.
    document.querySelector('button[data-panel="map"]').addEventListener("click", () => {
      setTimeout(() => leafletMap.invalidateSize(), 0);
    });
  }
  await refreshMap();
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

  const note = document.getElementById("map-note");
  if (data.geocoded_places === 0) {
    note.innerHTML = data.total_places === 0
      ? "No locations match this filter."
      : `None of your ${data.total_places} matching locations are geocoded yet. On your Mac, run <code>python cli.py geocode --events ../events.json</code> (needs network) and reload.`;
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
  if (bounds.length) leafletMap.fitBounds(bounds, { padding: [30, 30], maxZoom: 12 });
}

// --- Boot ---
let meta;
(async function init() {
  meta = await loadOverview();
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
})();
