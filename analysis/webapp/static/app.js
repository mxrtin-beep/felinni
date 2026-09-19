// Global filters (Overview tab) - every /api/ call includes them, so
// narrowing the date range or unchecking a category there applies
// everywhere else too.
const GLOBAL_FILTERS = { startDate: "", endDate: "", excludeCategories: [] };

function api(path, { skipGlobalFilters = false } = {}) {
  const [base, query] = path.split("?");
  const params = new URLSearchParams(query || "");
  if (!skipGlobalFilters) {
    if (GLOBAL_FILTERS.startDate) params.set("start_date", GLOBAL_FILTERS.startDate);
    if (GLOBAL_FILTERS.endDate) params.set("end_date", GLOBAL_FILTERS.endDate);
    if (GLOBAL_FILTERS.excludeCategories.length) params.set("exclude_categories", GLOBAL_FILTERS.excludeCategories.join(","));
  }
  const qs = params.toString();
  return fetch(`/api/${base}${qs ? `?${qs}` : ""}`).then(r => r.json());
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

/** Renders a sortable table: click a header to sort by it (ascending),
 * click again to reverse. Sort state is remembered on the container
 * itself, so it survives the next `table()` call for the same container
 * (e.g. a data refresh) instead of resetting on every reload. Pass a
 * column with `sortable: false` to exclude just that one from sorting
 * (e.g. a column whose value is a rendering detail, not real data). */
function table(container, columns, rows) {
  if (!rows.length) {
    container.innerHTML = '<p class="empty-note">Nothing here yet.</p>';
    return;
  }
  const sortState = container._sortState || { key: null, dir: 1 };
  container._sortState = sortState;

  let sortedRows = rows;
  if (sortState.key) {
    const key = sortState.key, dir = sortState.dir;
    sortedRows = [...rows].sort((a, b) => {
      const av = a[key], bv = b[key];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string") return av.localeCompare(bv) * dir;
      return (av > bv ? 1 : av < bv ? -1 : 0) * dir;
    });
  }

  container.innerHTML = "";
  const t = document.createElement("table");
  const thead = document.createElement("thead");
  thead.innerHTML = "<tr>" + columns.map(c => {
    const sortable = c.sortable !== false;
    const isSorted = sortable && sortState.key === c.key;
    const arrow = isSorted ? (sortState.dir === 1 ? " ▲" : " ▼") : "";
    const classes = [c.num ? "num" : "", sortable ? "sortable" : ""].filter(Boolean).join(" ");
    return `<th class="${classes}"${sortable ? ` data-key="${c.key}"` : ""}>${c.label}${arrow}</th>`;
  }).join("") + "</tr>";
  const tbody = document.createElement("tbody");
  sortedRows.forEach(row => {
    const tr = document.createElement("tr");
    tr.innerHTML = columns.map(c => `<td class="${c.num ? 'num' : ''}">${c.format ? c.format(row[c.key]) : (row[c.key] ?? "-")}</td>`).join("");
    tbody.appendChild(tr);
  });
  t.appendChild(thead);
  t.appendChild(tbody);
  container.appendChild(t);

  thead.querySelectorAll("th[data-key]").forEach(th => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (sortState.key === key) sortState.dir *= -1;
      else { sortState.key = key; sortState.dir = 1; }
      table(container, columns, rows);
    });
  });
}

function fmtHours(h) { return h == null ? "-" : `${Math.round(h)}h`; }
function fmtDate(d) { return d ? d.slice(0, 10) : "-"; }
function fmtDateTime(d) {
  if (!d) return "-";
  const dt = new Date(d);
  if (isNaN(dt)) return d;
  return dt.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
function fmtDuration(hours) {
  if (hours == null) return null;
  const h = Math.floor(hours);
  const m = Math.round((hours - h) * 60);
  return h > 0 ? (m > 0 ? `${h}h ${m}m` : `${h}h`) : `${m}m`;
}
function fmtPct(p) { return p == null ? "-" : `${(p * 100).toFixed(0)}%`; }
function cssVarSafe(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

// --- "Time since you last saw them" ring tracker (People tab) ---
function daysSince(dateStr) {
  if (!dateStr) return null;
  const ms = Date.now() - new Date(dateStr).getTime();
  return Math.max(0, Math.floor(ms / 86400000));
}

// Ring is fully filled (and fully "critical") by this many days out -
// beyond this, longer just means longer, the visual has already made
// its point.
const LAST_SEEN_RING_CAP_DAYS = 60;
const LAST_SEEN_GOOD_DAYS = 14;
const LAST_SEEN_WARNING_DAYS = 45;

function lastSeenColor(days) {
  if (days <= LAST_SEEN_GOOD_DAYS) return cssVarSafe("--status-good") || "#0ca30c";
  if (days <= LAST_SEEN_WARNING_DAYS) return cssVarSafe("--status-warning") || "#fab219";
  return cssVarSafe("--status-critical") || "#d03b3b";
}

function lastSeenRingSvg(days) {
  const fraction = Math.min(days / LAST_SEEN_RING_CAP_DAYS, 1);
  const color = lastSeenColor(days);
  const r = 16, c = 2 * Math.PI * r;
  const offset = (c * (1 - fraction)).toFixed(2);
  return `
    <svg width="40" height="40" viewBox="0 0 40 40" class="last-seen-ring" aria-hidden="true">
      <circle cx="20" cy="20" r="${r}" fill="none" stroke="var(--grid)" stroke-width="4"></circle>
      <circle cx="20" cy="20" r="${r}" fill="none" stroke="${color}" stroke-width="4"
        stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${offset}"
        stroke-linecap="round" transform="rotate(-90 20 20)"></circle>
    </svg>`;
}

function lastSeenLabel(days) {
  if (days === 0) return "Today";
  if (days === 1) return "1 day ago";
  return `${days} days ago`;
}

function renderLastSeenTracker(people) {
  const container = document.getElementById("people-last-seen-list");
  if (!container) return;
  const withDays = people
    .map(p => ({ ...p, daysSince: daysSince(p.last_seen) }))
    .filter(p => p.daysSince !== null)
    .sort((a, b) => b.daysSince - a.daysSince); // longest overdue first
  container.innerHTML = withDays.length
    ? withDays.slice(0, 20).map(p => `
        <div class="last-seen-card">
          ${lastSeenRingSvg(p.daysSince)}
          <div class="last-seen-info">
            <div class="last-seen-name">${escapeHtml(p.person)}</div>
            <div class="last-seen-days">${lastSeenLabel(p.daysSince)}</div>
          </div>
        </div>`).join("")
    : '<p class="empty-note">No data yet.</p>';
}

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
  populateCategoryFilterList(meta.categories);
  return meta;
}

function populateCategoryFilterList(categories) {
  const categoryList = document.getElementById("category-filter-list");
  const isFirstBuild = !categoryList.dataset.populated;
  // A category that existed before keeps whatever the user set it to;
  // a brand-new one (e.g. from a calendar just imported) defaults to
  // checked, same as on first load.
  const previousCategories = new Set([...categoryList.querySelectorAll("input")].map(cb => cb.value));
  const previouslyChecked = new Set(
    [...categoryList.querySelectorAll("input")].filter(cb => cb.checked).map(cb => cb.value)
  );
  categoryList.innerHTML = categories.map(c => {
    const checked = isFirstBuild || !previousCategories.has(c) || previouslyChecked.has(c);
    return `
      <label style="display:flex;align-items:center;gap:4px;font-weight:normal">
        <input type="checkbox" class="category-checkbox" value="${c}" ${checked ? "checked" : ""}>
        <span class="swatch" style="background:${categoryColors[c] || cssVarSafe("--text-muted")}"></span>${c}
      </label>
    `;
  }).join("");
  categoryList.dataset.populated = "1";
  GLOBAL_FILTERS.excludeCategories = [...categoryList.querySelectorAll("input")].filter(cb => !cb.checked).map(cb => cb.value);
}

// --- Calendar sources ---
async function loadCalendarSources() {
  const kindSelect = document.getElementById("source-kind");
  if (!kindSelect.dataset.wired) {
    kindSelect.addEventListener("change", updateSourceFormFields);
    document.getElementById("source-add-btn").addEventListener("click", addCalendarSource);
    kindSelect.dataset.wired = "1";
    updateSourceFormFields();
  }
  await refreshSourcesList();
}

function updateSourceFormFields() {
  const isUrl = document.getElementById("source-kind").value === "ics_url";
  document.getElementById("source-url-row").style.display = isUrl ? "flex" : "none";
  document.getElementById("source-file-row").style.display = isUrl ? "none" : "flex";
}

function sourceKindLabel(kind) {
  if (kind === "ics_url") return "ICS link";
  if (kind === "ics_file") return "ICS file";
  return "events.json";
}

async function refreshSourcesList() {
  const sources = await fetch("/api/sources").then(r => r.json());
  const container = document.getElementById("sources-list");
  if (!sources.length) {
    container.innerHTML = '<p class="empty-note">No calendars imported yet.</p>';
    return;
  }
  container.innerHTML = sources.map(s => {
    const status = s.last_sync_error
      ? `<span style="color:var(--status-critical)">Error: ${escapeHtml(s.last_sync_error)}</span>`
      : s.last_synced
        ? `${s.event_count} events - last synced ${fmtDate(s.last_synced)}`
        : "Not synced yet";
    return `
      <div class="source-row">
        <label><input type="checkbox" class="source-visible-toggle" data-id="${s.id}" ${s.visible ? "checked" : ""}> <strong>${escapeHtml(s.name)}</strong></label>
        <span class="source-meta">${escapeHtml(s.provider)} · ${sourceKindLabel(s.kind)}</span>
        <span class="source-status">${status}</span>
        ${s.kind === "ics_url" ? `<button type="button" class="source-sync-btn" data-id="${s.id}">Refresh now</button>` : ""}
        <button type="button" class="source-delete-btn" data-id="${s.id}">Delete</button>
      </div>
    `;
  }).join("");

  container.querySelectorAll(".source-visible-toggle").forEach(cb => cb.addEventListener("change", async () => {
    await fetch(`/api/sources/${cb.dataset.id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visible: cb.checked }),
    });
    await refreshAfterSourceChange();
  }));
  container.querySelectorAll(".source-sync-btn").forEach(btn => btn.addEventListener("click", async () => {
    btn.disabled = true;
    await fetch(`/api/sources/${btn.dataset.id}/sync`, { method: "POST" });
    await refreshAfterSourceChange();
  }));
  container.querySelectorAll(".source-delete-btn").forEach(btn => btn.addEventListener("click", async () => {
    if (!confirm("Remove this calendar? Its events will no longer be counted anywhere.")) return;
    await fetch(`/api/sources/${btn.dataset.id}`, { method: "DELETE" });
    await refreshAfterSourceChange();
  }));
}

async function refreshAfterSourceChange() {
  // A source change can add categories or widen the date range, so the
  // meta-driven filters need re-syncing too, not just the analysis tabs.
  const meta = await api("meta");
  const startInput = document.getElementById("global-start-date");
  const endInput = document.getElementById("global-end-date");
  startInput.min = endInput.min = meta.min_date;
  startInput.max = endInput.max = meta.max_date;
  if (!startInput.value || startInput.value > meta.min_date) startInput.value = meta.min_date;
  if (!endInput.value || endInput.value < meta.max_date) endInput.value = meta.max_date;
  GLOBAL_FILTERS.startDate = startInput.value;
  GLOBAL_FILTERS.endDate = endInput.value;
  categoryColors = buildCategoryColors(meta.categories, meta.category_colors);
  populateCategoryFilterList(meta.categories);

  await refreshSourcesList();
  await reloadAll();
}

async function addCalendarSource() {
  const status = document.getElementById("source-add-status");
  const name = document.getElementById("source-name").value.trim();
  const provider = document.getElementById("source-provider").value;
  const kind = document.getElementById("source-kind").value;
  if (!name) {
    status.textContent = "Enter a name first.";
    return;
  }

  const btn = document.getElementById("source-add-btn");
  btn.disabled = true;
  status.textContent = "Adding…";
  try {
    let resp;
    if (kind === "ics_url") {
      const url = document.getElementById("source-url").value.trim();
      if (!url) {
        status.textContent = "Enter an ICS link.";
        return;
      }
      resp = await fetch("/api/sources", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, provider, kind, url }),
      });
    } else {
      const file = document.getElementById("source-file").files[0];
      if (!file) {
        status.textContent = "Choose a file.";
        return;
      }
      const form = new FormData();
      form.append("name", name);
      form.append("provider", provider);
      form.append("kind", kind);
      form.append("file", file);
      resp = await fetch("/api/sources", { method: "POST", body: form });
    }
    const body = await resp.json();
    if (!resp.ok) {
      status.textContent = body.error || "Couldn't add that calendar.";
      return;
    }
    status.textContent = body.last_sync_error
      ? `Added, but syncing failed: ${body.last_sync_error}`
      : `Added ${body.event_count} events from "${body.name}".`;
    document.getElementById("source-name").value = "";
    document.getElementById("source-url").value = "";
    document.getElementById("source-file").value = "";
    await refreshAfterSourceChange();
  } catch (e) {
    status.textContent = "Request failed - is the server running?";
  } finally {
    btn.disabled = false;
  }
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
  // loadFuture()'s own comment already says the global date filter has
  // nothing to do with a forward-looking event search - but calling it
  // with no arguments here undid that in practice: any global-filter
  // change (the date range, its reset button, a category checkbox)
  // triggers reloadAll(), which silently reset the Future tab back to
  // its default region/window, discarding whatever the user had actually
  // searched (confirmed directly: a search for "Los Angeles, CA" got
  // wiped back to the default "Thousand Oaks, CA" this way). Re-reads
  // the Future tab's own inputs so a global-filter change refreshes it
  // for the same region/window already showing, not a different one.
  const futureRegionInput = document.getElementById("future-region-input");
  const futureDaysSelect = document.getElementById("future-days-select");
  await Promise.all([
    refreshOverviewStats(),
    loadPlaces(),
    refreshMap(),
    loadPeople(),
    refreshHabit(),
    loadTravel(),
    loadTime(),
    refreshSeasonality(),
    // Anomalies tab is hidden for now (see index.html) - skip its fetch too.
    loadFuture(futureRegionInput?.value.trim() || undefined, futureDaysSelect?.value || undefined),
  ]);
}

// --- Places ---
async function loadPlaces() {
  const places = await api("places?limit=20");
  horizontalBarChart(document.getElementById("places-chart"),
    places.map(p => ({ label: p.location, value: p.visits })), { valueLabel: "visits", addressLines: true });
}

// --- People ---
function updatePeoplePickerToggleLabel() {
  const total = document.querySelectorAll("#people-picker-list input").length;
  const checked = document.querySelectorAll("#people-picker-list input:checked").length;
  document.getElementById("people-picker-toggle").textContent = `${checked} of ${total} people ▾`;
}

function populatePeoplePickerList(people) {
  const container = document.getElementById("people-picker-list");
  // Keep whatever the user already had checked across a data refresh;
  // only the very first render defaults to the top 6 (matching the old
  // fixed top_n=6 behavior) - everyone else is available in the dropdown
  // but starts unchecked, since checking every single person by default
  // would make the trend chart an unreadable tangle of lines.
  const isFirstRender = !container.dataset.rendered;
  const previouslyChecked = new Set(
    [...container.querySelectorAll("input:checked")].map(cb => cb.value)
  );
  container.innerHTML = people.map((p, i) => {
    const checked = isFirstRender ? i < 6 : previouslyChecked.has(p.person);
    return `<label><input type="checkbox" class="people-picker-checkbox" value="${escapeHtml(p.person)}" ${checked ? "checked" : ""}> ${escapeHtml(p.person)}</label>`;
  }).join("");
  container.dataset.rendered = "1";
  updatePeoplePickerToggleLabel();
}

async function loadPeople() {
  const select = document.getElementById("people-granularity");
  if (!select.dataset.wired) {
    select.addEventListener("change", refreshPeopleTrend);
    select.dataset.wired = "1";
  }
  const pickerList = document.getElementById("people-picker-list");
  if (!pickerList.dataset.wired) {
    pickerList.addEventListener("change", () => {
      updatePeoplePickerToggleLabel();
      refreshPeopleTrend();
    });
    pickerList.dataset.wired = "1";
    document.getElementById("people-picker-all").addEventListener("click", () => {
      pickerList.querySelectorAll("input").forEach(cb => { cb.checked = true; });
      updatePeoplePickerToggleLabel();
      refreshPeopleTrend();
    });
    document.getElementById("people-picker-none").addEventListener("click", () => {
      pickerList.querySelectorAll("input").forEach(cb => { cb.checked = false; });
      updatePeoplePickerToggleLabel();
      refreshPeopleTrend();
    });

    // A dropdown, not an always-open list - toggled on the button, closed
    // on an outside click (but not a click inside the panel itself).
    const panel = document.getElementById("people-picker-panel");
    const toggle = document.getElementById("people-picker-toggle");
    toggle.addEventListener("click", () => {
      panel.style.display = panel.style.display === "none" ? "block" : "none";
    });
    document.addEventListener("click", (e) => {
      if (!document.getElementById("people-picker-dropdown").contains(e.target)) {
        panel.style.display = "none";
      }
    });
  }

  // Independent pieces of this tab, run with allSettled rather than
  // sequential awaits: one endpoint failing (or an empty result) must
  // never silently prevent the others from rendering.
  const results = await Promise.allSettled([
    (async () => {
      // Everyone, not just a top slice - the dropdown should let you pick
      // any person, and the bar chart above still only shows the top 15.
      const people = await api("people?limit=1000");
      const sorted = [...people].sort((a, b) => b.total_hours - a.total_hours);
      horizontalBarChart(document.getElementById("people-chart"),
        sorted.slice(0, 15).map(p => ({ label: p.person, value: p.total_hours })), { valueLabel: "hours" });
      populatePeoplePickerList(sorted);
      renderLastSeenTracker(sorted);
      // The slider spans your full calendar history (not just what's
      // currently in view), so it always has room to zoom into any part
      // of it - fetched fresh in case a calendar source added new history.
      const meta = await api("meta");
      initPeopleTrendRangeSlider(meta.min_date, meta.max_date);
      await refreshPeopleTrend();
    })(),
    (async () => {
      const trends = await api("trends");
      table(document.getElementById("trends-table"),
        [
          { key: "person", label: "Person" },
          { key: "total_events", label: "Total events", num: true },
          { key: "slope_events_per_year", label: "Trend (events/yr)", num: true, format: v => v?.toFixed(2) },
        ], trends);
    })(),
    (async () => {
      const network = await api("social/network");
      networkGraph(document.getElementById("friend-network-graph"), network.nodes, network.edges);
    })(),
  ]);
  results.forEach(r => { if (r.status === "rejected") console.error("People tab section failed:", r.reason); });
}

function formatPeriodLabel(period, granularity) {
  if (granularity === "year") return period.slice(0, 4);
  if (granularity === "month") return period.slice(0, 7);
  return period.slice(0, 10);
}

// --- Events-over-time range slider: a chart-local zoom, independent of
// the Overview tab's global date filter, for narrowing just this chart to
// a sub-range without changing what every other tab shows. Two plain
// draggable handles on a track (dateToPct/pctToDate map a date to/from a
// 0-100 position), spanning your full calendar history so you can always
// zoom into any part of it regardless of the current view. ---
const peopleTrendRange = { min: null, max: null, low: null, high: null };

function ptrDateToPct(dateStr) {
  const { min, max } = peopleTrendRange;
  const minT = new Date(min).getTime(), maxT = new Date(max).getTime();
  if (maxT === minT) return 0;
  return ((new Date(dateStr).getTime() - minT) / (maxT - minT)) * 100;
}

function ptrPctToDate(pct) {
  const { min, max } = peopleTrendRange;
  const minT = new Date(min).getTime(), maxT = new Date(max).getTime();
  const t = minT + (pct / 100) * (maxT - minT);
  return new Date(t).toISOString().slice(0, 10);
}

function renderPeopleTrendRangeSlider() {
  const container = document.getElementById("people-trend-range");
  const lowPct = ptrDateToPct(peopleTrendRange.low);
  const highPct = ptrDateToPct(peopleTrendRange.high);
  container.querySelector('[data-handle="low"]').style.left = lowPct + "%";
  container.querySelector('[data-handle="high"]').style.left = highPct + "%";
  const fill = container.querySelector(".range-slider-fill");
  fill.style.left = lowPct + "%";
  fill.style.width = Math.max(highPct - lowPct, 0) + "%";
  document.getElementById("people-trend-range-low-label").textContent = peopleTrendRange.low;
  document.getElementById("people-trend-range-high-label").textContent = peopleTrendRange.high;
}

function initPeopleTrendRangeSlider(minDate, maxDate) {
  const container = document.getElementById("people-trend-range");
  const isFirstInit = !container.dataset.wired;
  const previousLow = peopleTrendRange.low, previousHigh = peopleTrendRange.high;
  peopleTrendRange.min = minDate;
  peopleTrendRange.max = maxDate;
  // First time: default to the full range. On a later meta refresh (e.g.
  // a calendar source added new history), keep whatever the user already
  // dragged to, clamped to the new bounds.
  peopleTrendRange.low = isFirstInit ? minDate : (previousLow < minDate ? minDate : previousLow);
  peopleTrendRange.high = isFirstInit ? maxDate : (previousHigh > maxDate ? maxDate : previousHigh);
  renderPeopleTrendRangeSlider();
  if (!isFirstInit) return;
  container.dataset.wired = "1";

  function startDrag(handleKey) {
    const onMove = (evt) => {
      const rect = container.getBoundingClientRect();
      const clientX = evt.touches ? evt.touches[0].clientX : evt.clientX;
      const pct = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
      const date = ptrPctToDate(pct);
      if (handleKey === "low") {
        if (date >= peopleTrendRange.high) return;
        peopleTrendRange.low = date;
      } else {
        if (date <= peopleTrendRange.low) return;
        peopleTrendRange.high = date;
      }
      renderPeopleTrendRangeSlider();
      evt.preventDefault();
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.removeEventListener("touchmove", onMove);
      document.removeEventListener("touchend", onUp);
      refreshPeopleTrend();
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    document.addEventListener("touchmove", onMove, { passive: false });
    document.addEventListener("touchend", onUp);
  }

  container.querySelector('[data-handle="low"]').addEventListener("mousedown", () => startDrag("low"));
  container.querySelector('[data-handle="low"]').addEventListener("touchstart", () => startDrag("low"));
  container.querySelector('[data-handle="high"]').addEventListener("mousedown", () => startDrag("high"));
  container.querySelector('[data-handle="high"]').addEventListener("touchstart", () => startDrag("high"));
}

async function refreshPeopleTrend() {
  const granularity = document.getElementById("people-granularity").value;
  const selected = [...document.querySelectorAll(".people-picker-checkbox:checked")].map(cb => cb.value);
  const rows = await api(`person-trend?granularity=${granularity}&people=${encodeURIComponent(selected.join(","))}`);
  const inRange = peopleTrendRange.low && peopleTrendRange.high
    ? rows.filter(r => r.period >= peopleTrendRange.low && r.period <= peopleTrendRange.high)
    : rows;
  const periods = [...new Set(inRange.map(r => r.period))].sort();
  const indexOf = Object.fromEntries(periods.map((p, i) => [p, i]));
  const byPerson = {};
  inRange.forEach(r => {
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
    data.consistency.map(c => ({ label: String(c.year), value: c.consistency * 100 })), { valueLabel: "% weeks active", separators: true });

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
      { key: "streak_days", label: "Streak (days)", num: true },
      { key: "last_seen", label: "Last seen", format: fmtDate },
      { key: "days_since_last", label: "Days since", num: true, format: v => Math.round(v) },
      { key: "status", label: "Status", format: v => `<span class="badge ${v.replace(/\s+/g, "-")}">${v}</span>` },
    ], rows);
}

// --- Travel ---
function populateTravelMetroSelect(regionVisits, homeRegion) {
  const select = document.getElementById("travel-metro-select");
  if (!select.dataset.wired) {
    select.addEventListener("change", refreshTravelNeighborhoods);
    select.dataset.wired = "1";
  }
  const previous = select.value;
  select.innerHTML = regionVisits.map(r => `<option value="${escapeHtml(r.region)}">${escapeHtml(r.region)}</option>`).join("");
  const metros = regionVisits.map(r => r.region);
  if (metros.includes(previous)) select.value = previous;
  else if (metros.includes(homeRegion)) select.value = homeRegion;
}

async function refreshTravelNeighborhoods() {
  const select = document.getElementById("travel-metro-select");
  const metro = select.value;
  const data = await api(`travel/neighborhoods${metro ? `?metro=${encodeURIComponent(metro)}` : ""}`);
  table(document.getElementById("travel-neighborhoods-table"),
    [
      { key: "neighborhood", label: "Neighborhood" },
      { key: "visits", label: "Events", num: true },
      { key: "total_hours", label: "Hours", num: true, format: v => Math.round(v) },
      { key: "n_locations", label: "Places", num: true },
    ], data.neighborhoods);
}

async function loadTravel() {
  const data = await api("travel");
  const note = document.getElementById("travel-note");
  note.textContent = data.message || "";
  note.style.display = data.message ? "block" : "none";

  const statGrid = document.getElementById("travel-stats");
  statGrid.innerHTML = "";
  statGrid.appendChild(statTile("Home metro", data.home_region || "-"));
  statGrid.appendChild(statTile("Metro areas visited", data.region_visits.length));
  statGrid.appendChild(statTile("Trips away from home", data.region_trips.length));

  table(document.getElementById("travel-region-trips"),
    [
      { key: "region", label: "Metro area" },
      { key: "start", label: "Start", format: fmtDate },
      { key: "end", label: "End", format: fmtDate },
      { key: "duration_days", label: "Days", num: true, format: v => v?.toFixed(1) },
    ], data.region_trips);

  table(document.getElementById("travel-regions-table"),
    [
      { key: "region", label: "Metro area" },
      { key: "visits", label: "Events", num: true },
      { key: "total_hours", label: "Hours", num: true, format: v => Math.round(v) },
      { key: "n_locations", label: "Places", num: true },
      { key: "first_seen", label: "First seen", format: fmtDate },
      { key: "last_seen", label: "Last seen", format: fmtDate },
    ], data.region_visits);

  populateTravelMetroSelect(data.region_visits, data.home_region);
  await refreshTravelNeighborhoods();
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
      flagColor: d => {
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

// --- Future (Eventbrite/Luma/Meetup/other-site events found via a DuckDuckGo search) ---
const FUTURE_CONFLICTS_SHOWN = 4;

function _futureConflictNote(e) {
  if (!e.conflicts || !e.conflicts.length) return "";
  const shown = e.conflicts.slice(0, FUTURE_CONFLICTS_SHOWN).map(c => {
    const label = c.type === "calendar" ? "your calendar" : `another suggestion (${escapeHtml(c.source || "")})`;
    return `${escapeHtml(c.title)} on ${label}`;
  }).join("; ");
  const more = e.conflicts.length > FUTURE_CONFLICTS_SHOWN ? ` (+${e.conflicts.length - FUTURE_CONFLICTS_SHOWN} more)` : "";
  return `<p class="future-conflict">&#9888; Conflicts with ${shown}${more}</p>`;
}

function _futureEventCard(e) {
  if (e.is_ai_suggestion) {
    return `
      <div class="future-event future-ai-idea">
        <p><span class="badge planned">AI idea, not a live listing</span></p>
        <p>${escapeHtml(e.title)}</p>
      </div>`;
  }

  const when = e.start
    ? `${fmtDateTime(e.start)}${e.end ? ` – ${fmtDateTime(e.end)}` : ""}${e.duration_hours ? ` (${fmtDuration(e.duration_hours)})` : ""}`
    : "Date/time unknown";
  const people = e.suggested_people && e.suggested_people.length
    ? `<p class="card-note">Consider inviting: ${e.suggested_people.map(escapeHtml).join(", ")} (${escapeHtml(e.people_reason || "")})</p>`
    : "";
  const fit = e.fit_reasons && e.fit_reasons.length
    ? `<p class="card-note">${e.fit_reasons.map(escapeHtml).join(" · ")}</p>`
    : "";
  return `
    <div class="future-event${e.has_conflict ? " has-conflict" : ""}">
      <p>
        <a href="${escapeHtml(e.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(e.title)}</a>
        <span class="source-meta">${escapeHtml(e.source || "")}</span>
      </p>
      <p class="card-note">${escapeHtml(when)}${e.location ? ` &middot; ${escapeHtml(e.location)}` : ""}</p>
      ${e.snippet ? `<p class="card-note">${escapeHtml(e.snippet)}</p>` : ""}
      ${fit}
      ${people}
      ${_futureConflictNote(e)}
    </div>`;
}

async function loadFuture(region, days) {
  // A forward-looking event search has nothing to do with the Overview
  // tab's global date-range filter (that filters your past calendar
  // history) - sending it here was just confusing noise in the request.
  const params = new URLSearchParams();
  if (region) params.set("region", region);
  if (days) params.set("days", days);
  const qs = params.toString();
  const data = await api(`future${qs ? `?${qs}` : ""}`, { skipGlobalFilters: true });
  document.getElementById("future-note").textContent = data.message || "";

  const regionInput = document.getElementById("future-region-input");
  if (regionInput && !regionInput.value) regionInput.value = data.region || "";
  const daysSelect = document.getElementById("future-days-select");
  if (daysSelect && data.days) daysSelect.value = String(data.days);

  const events = document.getElementById("future-events");
  events.innerHTML = data.events.length
    ? data.events.map(_futureEventCard).join("")
    : '<p class="empty-note">Nothing here yet.</p>';
}

function wireFutureRegionSearch() {
  const btn = document.getElementById("future-region-btn");
  const input = document.getElementById("future-region-input");
  const daysSelect = document.getElementById("future-days-select");
  if (!btn || !input) return;
  const run = () => loadFuture(input.value.trim() || undefined, daysSelect ? daysSelect.value : undefined);
  btn.addEventListener("click", run);
  input.addEventListener("keydown", e => { if (e.key === "Enter") run(); });
  if (daysSelect) daysSelect.addEventListener("change", run);
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
  // frequency, cycling through all 12 series colors (seriesColor wraps
  // via modulo) rather than graying out anything past the 6th, so every
  // category gets a real, distinct color instead of being lumped into a
  // catch-all gray "Other".
  const colors = {};
  let fallbackSlot = 0;
  categories.forEach(cat => {
    if (realColors && realColors[cat]) {
      colors[cat] = realColors[cat];
    } else {
      colors[cat] = seriesColor(fallbackSlot);
      fallbackSlot++;
    }
  });
  return colors;
}

// --- Map ---
let leafletMap, markerLayer;
let hasSetInitialMapView = false;
// The points fitBounds should show once the map is actually visible. The
// Map panel is `display:none` until its tab is clicked, so the very first
// fitBounds call (fired from the initial reloadAll(), before any tab click)
// runs against a zero-size container - Leaflet computes a bogus zoom/center
// from that (an over-zoomed view on an arbitrary patch of ocean) rather than
// erroring, so the initial fit needs to be redone once the container has a
// real size.
let lastMapFitTargets = null;
let hasFitMapWhileVisible = false;

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
let lastLocationsData = null;

function renderMapLegend(categories) {
  const legend = document.getElementById("map-legend");
  legend.innerHTML = "";
  categories.forEach(cat => {
    const item = document.createElement("span");
    item.innerHTML = `<span class="swatch" style="background:${categoryColors[cat]}"></span>${escapeHtml(cat)}`;
    legend.appendChild(item);
  });
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
      const force = document.getElementById("map-geocode-force").checked;
      const resp = await fetch("/api/geocode/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force }),
      });
      if (resp.ok) await pollGeocodeStatus();
    });

    document.getElementById("fix-location-btn").addEventListener("click", fixSelectedLocation);

    document.getElementById("map-geocode-force").addEventListener("change", () => updateGeocodeButton(lastLocationsData));

    // Leaflet measures the container on init; the Map tab may have been
    // hidden (display:none) at that point, so its size reads as zero.
    document.querySelector('button[data-panel="map"]').addEventListener("click", () => {
      setTimeout(() => {
        leafletMap.invalidateSize();
        // First time the tab is actually visible: redo the initial fit,
        // since the one that ran on page load (container hidden, 0x0) was
        // computed against a bogus viewport size.
        if (!hasFitMapWhileVisible && lastMapFitTargets && lastMapFitTargets.length) {
          leafletMap.fitBounds(lastMapFitTargets, { padding: [30, 30], maxZoom: 12 });
          hasFitMapWhileVisible = true;
        }
      }, 0);
    });
  }
  await refreshMap();
  await loadGeocodeFailures();
  await pollGeocodeStatus(); // resumes the progress bar if a job was already running
}

async function loadGeocodeFailures() {
  const data = await fetch("/api/geocode/failures").then(r => r.json());
  const card = document.getElementById("geocode-failures-card");
  const hasApproximate = data.approximate && data.approximate.length;
  if (!data.total_failed && !hasApproximate) {
    card.style.display = "none";
    return;
  }
  card.style.display = "block";

  const failuresSummary = document.getElementById("geocode-failures-summary");
  const failuresList = document.getElementById("geocode-failures-list");
  if (data.total_failed) {
    failuresSummary.style.display = "block";
    failuresList.style.display = "block";
    failuresSummary.textContent =
      `${data.total_failed} location${data.total_failed === 1 ? "" : "s"} failed on ` +
      `their last geocode attempt: ${data.by_reason.map(([reason, count]) => `${count} ${reason}`).join("; ")}.`;
    failuresList.innerHTML = data.failures
      .map(f => `<p><strong>${escapeHtml(f.location)}</strong> — ${escapeHtml(f.reason)}</p>`)
      .join("");
  } else {
    failuresSummary.style.display = "none";
    failuresList.style.display = "none";
  }

  const approxSummary = document.getElementById("geocode-approximate-summary");
  const approxList = document.getElementById("geocode-approximate-list");
  if (hasApproximate) {
    approxSummary.style.display = "block";
    approxList.style.display = "block";
    approxSummary.textContent =
      `${data.approximate.length} location${data.approximate.length === 1 ? "" : "s"} couldn't be pinned exactly, ` +
      `so ${data.approximate.length === 1 ? "it's" : "they're"} placed at the nearest known campus/workplace instead:`;
    approxList.innerHTML = data.approximate
      .map(a => `<p><strong>${escapeHtml(a.location)}</strong> — placed at ${escapeHtml(a.placed_at)}</p>`)
      .join("");
  } else {
    approxSummary.style.display = "none";
    approxList.style.display = "none";
  }
}

function updateGeocodeButton(locationsData) {
  const btn = document.getElementById("map-geocode-btn");
  if (btn.disabled && geocodePollTimer) return; // a job is running - leave it to the poller
  const forceChecked = document.getElementById("map-geocode-force").checked;
  const allDone = locationsData && locationsData.total_places > 0 && locationsData.total_places === locationsData.geocoded_places;
  if (allDone && !forceChecked) {
    btn.disabled = true;
    btn.textContent = "All locations geocoded";
  } else {
    btn.disabled = false;
    btn.textContent = "Geocode locations";
  }
}

let geocodePollTicks = 0;
const GEOCODE_FAILURES_REFRESH_EVERY_N_TICKS = 5; // ~5s at the 1s poll interval - live, but not refetching every single tick

async function pollGeocodeStatus() {
  const status = await fetch("/api/geocode/status").then(r => r.json());
  const btn = document.getElementById("map-geocode-btn");
  const progressWrap = document.getElementById("map-geocode-progress");
  const errorNote = document.getElementById("map-geocode-error");

  if (!status.running) {
    if (geocodePollTimer) {
      clearInterval(geocodePollTimer);
      geocodePollTimer = null;
      geocodePollTicks = 0;
      await refreshMap(); // job just finished - show the newly-geocoded points
      await loadGeocodeFailures(); // ...and why anything left over still isn't
    }
    progressWrap.style.display = "none";
    // A run that stopped early (rather than working through every
    // location) leaves an error here - surface it instead of silently
    // showing a partial result with no explanation.
    if (status.error) {
      errorNote.textContent = `Geocoding stopped early: ${status.error}. Click "Geocode locations" again to pick up where it left off.`;
      errorNote.style.display = "block";
    } else {
      errorNote.style.display = "none";
    }
    return;
  }

  errorNote.style.display = "none";
  btn.disabled = true;
  btn.textContent = "Geocoding…";
  progressWrap.style.display = "flex";
  const pct = status.total ? Math.round((status.done / status.total) * 100) : 0;
  document.getElementById("map-geocode-fill").style.width = pct + "%";
  document.getElementById("map-geocode-label").textContent = status.total ? `${status.done} / ${status.total}` : "Starting…";

  // A run already in progress (e.g. this tab was reloaded mid-run) still
  // has a live diagnostics file worth showing, not just once the whole
  // batch finishes - refreshed periodically rather than every single tick.
  geocodePollTicks += 1;
  if (geocodePollTicks % GEOCODE_FAILURES_REFRESH_EVERY_N_TICKS === 0) {
    await loadGeocodeFailures();
  }

  if (!geocodePollTimer) {
    geocodePollTimer = setInterval(pollGeocodeStatus, 1000);
  }
}

function populateFixLocationSelect(allLocations) {
  const select = document.getElementById("fix-location-select");
  const previous = select.value;
  select.innerHTML = allLocations.map(loc => `<option value="${escapeHtml(loc)}">${escapeHtml(loc)}</option>`).join("");
  if (allLocations.includes(previous)) select.value = previous;
}

async function fixSelectedLocation() {
  const status = document.getElementById("fix-location-status");
  const location = document.getElementById("fix-location-select").value;
  const query = document.getElementById("fix-location-query").value.trim();
  if (!location) {
    status.textContent = "Pick a location first.";
    return;
  }
  if (!query) {
    status.textContent = "Enter a corrected address to look up.";
    return;
  }
  status.textContent = "Looking up…";
  const btn = document.getElementById("fix-location-btn");
  btn.disabled = true;
  try {
    const resp = await fetch("/api/geocode/override", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ location, query }),
    });
    const body = await resp.json();
    if (!resp.ok) {
      status.textContent = body.error || "Couldn't fix that location.";
      return;
    }
    status.textContent = `Fixed: ${location} → ${body.entry.display_name || `${body.entry.lat}, ${body.entry.lon}`}`;
    document.getElementById("fix-location-query").value = "";
    await refreshMap();
  } catch (e) {
    status.textContent = "Request failed - is the server running?";
  } finally {
    btn.disabled = false;
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
  lastLocationsData = data;
  updateGeocodeButton(data);
  populateFixLocationSelect(data.all_locations || []);

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
    const titlesHtml = (loc.titles || []).length
      ? `<br><em>${loc.titles.map(escapeHtml).join(", ")}</em>`
      : "";
    const addressHtml = loc.display_name ? `<br><span style="color:var(--text-secondary)">${escapeHtml(loc.display_name)}</span>` : "";
    marker.bindPopup(
      `<strong>${escapeHtml(loc.location)}</strong>${addressHtml}<br>${loc.visits} visits (${fmtDate(loc.first_seen)} – ${fmtDate(loc.last_seen)})<br>${(loc.categories || []).join(", ")}${titlesHtml}`
    );
    marker.addTo(markerLayer);
    bounds.push([loc.lat, loc.lon]);
  });

  if (!hasSetInitialMapView && bounds.length) {
    // First render: default to the densest cluster of places (typically
    // "home") rather than zooming out to fit every far-flung trip too.
    const dense = densestClusterPoints(data.locations);
    lastMapFitTargets = dense.length ? dense : bounds;
    leafletMap.fitBounds(lastMapFitTargets, { padding: [30, 30], maxZoom: 12 });
    hasSetInitialMapView = true;
  } else if (bounds.length) {
    lastMapFitTargets = bounds;
    leafletMap.fitBounds(bounds, { padding: [30, 30], maxZoom: 12 });
  }
}

// --- Boot ---
(async function init() {
  const meta = await loadMeta();
  await loadCalendarSources();
  await refreshOverviewStats();
  await Promise.all([
    loadPlaces(),
    loadMap(meta),
    loadPeople(),
    loadHabits(meta),
    loadTravel(),
    loadTime(),
    loadSeasonality(meta),
    loadFuture(),
  ]);
  wireGlobalDateFilter();
  wireCategoryFilter();
  wireFutureRegionSearch();
})();
