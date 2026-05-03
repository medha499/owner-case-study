window.APP_STATE = {
  user: null, role: "manager", realRole: "manager",
  lang: localStorage.getItem("lang") || "en",
  page: "themes",
  synthesis: null, charts: null,
  overallSynthesis: null, overallCharts: null,
  filters: {cuisine: [], business_type: [], locations: []},
  selectedRestaurant: null, briefs: {},
  queue: null, myStats: null, coaching: null,
  pipelinePoll: null, chartInstances: {},
  teamFilter: "all",
  sliceTab: "works", themesTab: "works",
  segmentOptions: null,
};

async function init() {
  try {
    const res = await fetch("/api/me");
    if (!res.ok) { window.location.href = "/"; return; }
    const user = await res.json();
    APP_STATE.user = user;
    APP_STATE.realRole = user.role;
    APP_STATE.role = user.role;
  } catch (e) { window.location.href = "/"; return; }

  renderUserChip();
  renderNav();
  applyLang();
  applyRoleToggle();
  applyPipelineVisibility();

  APP_STATE.page = APP_STATE.role === "manager" ? "themes" : "my_queue";
  renderActiveNav();

  // Render the page shell IMMEDIATELY — don't block on data fetches.
  // Each page renders a skeleton, then data fills in as it arrives.
  await renderPage();

  // Manager-only: pipeline polling + data fetches happen AFTER first paint
  if (APP_STATE.role === "manager") {
    pollPipelineStatus();
    APP_STATE.pipelinePoll = setInterval(pollPipelineStatus, 2000);

    // Fire data fetches in parallel; re-render whichever page is open when each lands
    Promise.all([loadSynthesis(), loadSegmentOptions()]).then(() => {
      renderPage();
    });
    // NOTE: removed auto-trigger of runPipeline(). On first load with no synthesis,
    // the empty-state CTA prompts the manager to click "Run analysis" explicitly.
    // This avoids the page being immediately busy with streaming + re-renders.
  }
}

function renderUserChip() {
  const u = APP_STATE.user;
  document.getElementById("sidebar-user").innerHTML = `
    <div class="user-avatar">${u.initials}</div>
    <div>
      <div class="user-name">${u.name}</div>
      <div class="user-role">${u.role === "manager" ? "Manager" : "Sales Rep"}</div>
    </div>`;
}

function renderNav() {
  const isManager = APP_STATE.role === "manager";
  const items = isManager ? [
    {key: "themes", label: "nav_themes", icon: "📊", divider: "internal"},
    {key: "slice", label: "nav_slice", icon: "🔍"},
    {key: "team", label: "nav_team", icon: "⚑"},
    {key: "competitors", label: "nav_competitors", icon: "⚔", divider: "external"},
  ] : [
    {key: "my_queue", label: "nav_my_queue", icon: "▶"},
    {key: "brief", label: "nav_brief", icon: "◉"},
    {key: "my_stats", label: "nav_my_stats", icon: "📊"},
    {key: "coaching", label: "nav_coaching", icon: "✉"},
  ];

  let html = "";
  let lastDiv = null;
  items.forEach(it => {
    if (it.divider && it.divider !== lastDiv) {
      lastDiv = it.divider;
      html += `<div class="nav-divider">${t("nav_" + it.divider)}</div>`;
    }
    html += `<button class="nav-item" data-page="${it.key}" onclick="navigate('${it.key}')">
      <span class="nav-icon">${it.icon}</span>${t(it.label)}
    </button>`;
  });

  document.getElementById("sidebar-nav").innerHTML = html;
  renderActiveNav();
}

function renderActiveNav() {
  document.querySelectorAll(".nav-item").forEach(b => {
    b.classList.toggle("active", b.dataset.page === APP_STATE.page);
  });
}

function applyLang() {
  document.querySelectorAll(".lang-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.lang === APP_STATE.lang);
  });
  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
}

function applyRoleToggle() {
  document.querySelectorAll(".role-toggle-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.role === APP_STATE.role);
  });
}

function applyPipelineVisibility() {
  // Pipeline status block is manager-only. Reps don't trigger pipelines.
  const block = document.querySelector(".sidebar-status");
  if (block) block.style.display = APP_STATE.role === "manager" ? "block" : "none";
}

async function navigate(page) {
  APP_STATE.page = page;
  renderActiveNav();
  await renderPage();
}

async function switchLang(lang) {
  APP_STATE.lang = lang;
  localStorage.setItem("lang", lang);
  applyLang();
  renderNav();
  APP_STATE.briefs = {};
  await renderPage();
}

async function switchRole(role) {
  APP_STATE.role = role;
  applyRoleToggle();
  renderNav();
  applyPipelineVisibility();
  APP_STATE.page = role === "manager" ? "themes" : "my_queue";
  renderActiveNav();

  // Manager view starts polling pipeline status; rep view stops polling.
  if (role === "manager") {
    if (!APP_STATE.pipelinePoll) {
      pollPipelineStatus();
      APP_STATE.pipelinePoll = setInterval(pollPipelineStatus, 2000);
    }
    // Lazy: load synthesis only when entering manager view
    if (!APP_STATE.synthesis) {
      await Promise.all([loadSynthesis(), loadSegmentOptions()]);
    } else {
      await loadSegmentOptions();
    }
  } else {
    // Rep view: stop polling
    if (APP_STATE.pipelinePoll) {
      clearInterval(APP_STATE.pipelinePoll);
      APP_STATE.pipelinePoll = null;
    }
  }
  await renderPage();
}

async function logout() {
  await fetch("/api/logout", {method: "POST"});
  window.location.href = "/";
}

async function runPipeline() {
  const btn = document.getElementById("run-pipeline-btn");
  btn.disabled = true;
  try {
    await fetch("/api/pipeline/run", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({language: APP_STATE.lang}),
    });
  } catch (e) { console.error(e); }
}

async function pollPipelineStatus() {
  try {
    const res = await fetch("/api/pipeline/status");
    if (!res.ok) return;
    const s = await res.json();

    const pill = document.getElementById("pipeline-pill");
    const wrap = document.getElementById("pipeline-progress-wrap");
    const bar = document.getElementById("pipeline-progress-bar");
    const msg = document.getElementById("pipeline-msg");
    const btn = document.getElementById("run-pipeline-btn");

    pill.className = "status-pill " + s.status;
    pill.textContent = s.status;

    if (s.status === "extracting" || s.status === "synthesizing") {
      wrap.style.display = "block";
      bar.style.width = s.progress + "%";
      msg.textContent = s.message || "";
      btn.disabled = true;
      const span = btn.querySelector("[data-i18n]");
      if (span) span.textContent = s.status === "extracting" ? "Extracting…" : "Synthesizing…";

      // STREAMING: refresh synthesis at most every 5s during streaming.
      // Each re-render tears down and rebuilds the DOM, which is jank-inducing.
      // 5s cadence still feels live without thrashing the page.
      const now = Date.now();
      const lastRender = APP_STATE.lastStreamRenderAt || 0;
      if (APP_STATE.role === "manager" && now - lastRender > 5000) {
        APP_STATE.lastStreamRenderAt = now;
        loadSynthesis().then(() => {
          if (["themes", "slice", "team", "competitors"].includes(APP_STATE.page)) {
            if (APP_STATE.page === "themes") {
              APP_STATE.overallSynthesis = APP_STATE.synthesis;
              APP_STATE.overallCharts = APP_STATE.charts;
            }
            renderPage();
          }
          if (APP_STATE.synthesis?.overview) renderKpis(APP_STATE.synthesis.overview);
        }).catch(() => {});
      }
    } else {
      btn.disabled = false;
      const span = btn.querySelector("[data-i18n]");
      if (span) span.textContent = t("run_pipeline");
      if (s.status === "done" && s.last_run_at && APP_STATE.lastSeenRun !== s.last_run_at) {
        APP_STATE.lastSeenRun = s.last_run_at;
        APP_STATE.lastStreamRenderAt = 0;
        wrap.style.display = "none";
        APP_STATE.briefs = {};
        APP_STATE.filters = {cuisine: [], business_type: [], locations: []};
        APP_STATE.overallSynthesis = null;
        APP_STATE.overallCharts = null;
        APP_STATE.repCards = null;  // force warm-up fetch on next loadSynthesis
        await loadSynthesis();
        await renderPage();
      } else if (s.status === "idle") {
        wrap.style.display = "none";
      }
    }

    // Adaptive polling: fast during streaming (2s), slow when idle/done (10s).
    // Stops the page from doing background work when nothing's happening.
    const wantStreaming = s.status === "extracting" || s.status === "synthesizing";
    const currentMode = APP_STATE.pollingMode || "idle";
    const wantMode = wantStreaming ? "streaming" : "idle";
    if (wantMode !== currentMode) {
      APP_STATE.pollingMode = wantMode;
      if (APP_STATE.pipelinePoll) clearInterval(APP_STATE.pipelinePoll);
      APP_STATE.pipelinePoll = setInterval(pollPipelineStatus, wantStreaming ? 2000 : 10000);
    }
  } catch (e) {}
}

async function loadSynthesis() {
  const segStr = serializeFilters(APP_STATE.filters);
  // Themes & Slice don't need rep_cards (~3-5x payload reduction). Team does.
  const slim = (APP_STATE.page !== "team") ? 1 : 0;
  try {
    const res = await fetch(`/api/synthesis?segment=${encodeURIComponent(segStr)}&slim=${slim}`);
    if (res.ok) {
      const data = await res.json();
      APP_STATE.synthesis = data;
      if (segStr === "all" && slim === 0) APP_STATE.overallSynthesis = data;
      else if (segStr === "all") {
        // Slim version doesn't have rep_cards; only set as overall if not already set with full data
        APP_STATE.overallSynthesis = APP_STATE.overallSynthesis || data;
      }
      // Persist rep_cards across tabs whenever the full payload arrives.
      // Once cached, switching to Team is instant — no refetch, no flicker.
      if (Array.isArray(data.rep_cards) && data.rep_cards.length) {
        APP_STATE.repCards = data.rep_cards;
      }
    }
  } catch (e) {}
  try {
    const res = await fetch(`/api/charts?segment=${encodeURIComponent(segStr)}`);
    if (res.ok) {
      const data = await res.json();
      APP_STATE.charts = data;
      if (segStr === "all") APP_STATE.overallCharts = data;
    }
  } catch (e) {}

  // Pre-warm rep_cards for the Team tab so it's ready when clicked, even if
  // the user is currently on Themes/Slice (where we use slim=1 above).
  // Fires once per pipeline run for the unfiltered segment only.
  if (!APP_STATE.repCards) {
    fetch("/api/synthesis?segment=all&slim=0").then(r => r.ok ? r.json() : null).then(full => {
      if (full && Array.isArray(full.rep_cards) && full.rep_cards.length) {
        APP_STATE.repCards = full.rep_cards;
        // If user is currently looking at the Team tab while we were warming,
        // re-render so the cards appear without requiring a tab switch.
        if (APP_STATE.page === "team") {
          renderPage();
        }
      }
    }).catch(() => {});
  }
}

async function ensureOverallLoaded() {
  // Themes always uses unfiltered data. Use slim payload (no rep_cards).
  if (APP_STATE.overallSynthesis && APP_STATE.overallCharts) return;
  try {
    const [synRes, chRes] = await Promise.all([
      fetch("/api/synthesis?segment=all&slim=1"),
      fetch("/api/charts?segment=all"),
    ]);
    if (synRes.ok) APP_STATE.overallSynthesis = await synRes.json();
    if (chRes.ok) APP_STATE.overallCharts = await chRes.json();
  } catch (e) {}
}

async function loadSegmentOptions() {
  if (APP_STATE.segmentOptions) return APP_STATE.segmentOptions;
  try {
    const res = await fetch("/api/segments");
    if (res.ok) APP_STATE.segmentOptions = await res.json();
  } catch (e) {
    APP_STATE.segmentOptions = {cuisines: [], business_types: []};
  }
  return APP_STATE.segmentOptions;
}

// Filters are stored as { cuisine: [...], business_type: [...], locations: [...] }
// AND across dimensions, OR within (multi-select within one dimension acts like a union)
function serializeFilters(filters) {
  if (!filters) return "all";
  const parts = [];
  for (const dim of ["cuisine", "business_type", "locations"]) {
    if (filters[dim] && filters[dim].length) {
      parts.push(`${dim}:${filters[dim].join(",")}`);
    }
  }
  return parts.length ? parts.join("|") : "all";
}

function isFiltersEmpty(filters) {
  if (!filters) return true;
  return !["cuisine", "business_type", "locations"].some(dim => (filters[dim] || []).length > 0);
}

async function toggleFilter(dim, val) {
  if (!APP_STATE.filters) APP_STATE.filters = {};
  const arr = APP_STATE.filters[dim] || [];
  const idx = arr.indexOf(val);
  if (idx >= 0) arr.splice(idx, 1);
  else arr.push(val);
  APP_STATE.filters[dim] = arr;

  // Show loading state on Slice & Dice page (where filters apply)
  const root = document.getElementById("page-content");
  if (APP_STATE.page === "slice") {
    root.innerHTML = renderSegmentPills() + `<div class="empty-state">
      <div class="empty-state-spinner"></div>
      <div class="empty-state-title">${APP_STATE.lang === "en"
        ? "Re-synthesizing patterns for this segment…"
        : "Re-sintetizando patrones para este segmento…"}</div>
    </div>`;
  }
  await loadSynthesis();
  if (APP_STATE.synthesis?.overview) renderKpis(APP_STATE.synthesis.overview);
  await renderPage();
}

async function clearAllFilters() {
  APP_STATE.filters = {cuisine: [], business_type: [], locations: []};
  const root = document.getElementById("page-content");
  if (APP_STATE.page === "slice") {
    root.innerHTML = renderSegmentPills() + `<div class="empty-state">
      <div class="empty-state-spinner"></div>
      <div class="empty-state-title">${APP_STATE.lang === "en" ? "Loading…" : "Cargando…"}</div>
    </div>`;
  }
  await loadSynthesis();
  if (APP_STATE.synthesis?.overview) renderKpis(APP_STATE.synthesis.overview);
  await renderPage();
}

function renderSegmentPills() {
  const opts = APP_STATE.segmentOptions || {cuisines: [], business_types: []};
  const f = APP_STATE.filters || {};
  const cuisineActive = f.cuisine || [];
  const bizActive = f.business_type || [];
  const locActive = f.locations || [];
  const isFiltered = !isFiltersEmpty(f);
  const segCount = APP_STATE.synthesis?.overview?.n_calls;

  const cuisinePills = opts.cuisines.map(c =>
    `<button class="seg-pill ${cuisineActive.includes(c) ? "active" : ""}" onclick="toggleFilter('cuisine','${esc(c)}')">${esc(c)}</button>`
  ).join("");
  const bizPills = opts.business_types.map(b =>
    `<button class="seg-pill ${bizActive.includes(b) ? "active" : ""}" onclick="toggleFilter('business_type','${esc(b)}')">${esc(b)}</button>`
  ).join("");
  const locPills = `
    <button class="seg-pill ${locActive.includes("single") ? "active" : ""}" onclick="toggleFilter('locations','single')">${APP_STATE.lang === "en" ? "Single-loc" : "Una ubicación"}</button>
    <button class="seg-pill ${locActive.includes("multi") ? "active" : ""}" onclick="toggleFilter('locations','multi')">${APP_STATE.lang === "en" ? "Multi-loc" : "Multi-ubicación"}</button>`;

  const activeCount = cuisineActive.length + bizActive.length + locActive.length;
  const callCountChip = (segCount !== undefined)
    ? `<span class="seg-count">${segCount} ${APP_STATE.lang === "en" ? "calls" : "llamadas"}</span>` : "";

  return `
    <div class="segment-bar">
      <div class="segment-bar-row">
        <span class="segment-bar-label">${APP_STATE.lang === "en" ? "Filters" : "Filtros"}</span>
        ${isFiltered
          ? `<button class="seg-pill seg-pill-clear" onclick="clearAllFilters()">✕ ${APP_STATE.lang === "en" ? "Clear all" : "Limpiar"} (${activeCount})</button>`
          : `<button class="seg-pill active" disabled>${APP_STATE.lang === "en" ? "All" : "Todos"}</button>`}
        ${callCountChip}
        <span class="seg-hint">${APP_STATE.lang === "en"
          ? "Multi-select \u2014 narrows results (AND across, OR within)"
          : "Multi-selección \u2014 reduce resultados"}</span>
      </div>
      ${opts.cuisines.length ? `
        <div class="segment-bar-row">
          <span class="segment-bar-label">${APP_STATE.lang === "en" ? "Cuisine" : "Cocina"}</span>
          ${cuisinePills}
        </div>` : ""}
      ${opts.business_types.length ? `
        <div class="segment-bar-row">
          <span class="segment-bar-label">${APP_STATE.lang === "en" ? "Type" : "Tipo"}</span>
          ${bizPills}
        </div>` : ""}
      <div class="segment-bar-row">
        <span class="segment-bar-label">${APP_STATE.lang === "en" ? "Locations" : "Ubicaciones"}</span>
        ${locPills}
      </div>
      ${isFiltered ? `<div class="segment-bar-active-note">${APP_STATE.lang === "en"
          ? `Patterns synthesized from ${segCount || 0} call${segCount === 1 ? "" : "s"} matching this segment`
          : `Patrones sintetizados de ${segCount || 0} llamada${segCount === 1 ? "" : "s"} en este segmento`}</div>` : ""}
    </div>`;
}

async function renderPage() {
  const titleMap = {
    themes: ["h_themes", "h_themes_sub"],
    slice: ["h_slice", "h_slice_sub"],
    team: ["h_team", "h_team_sub"],
    competitors: ["h_competitors", "h_competitors_sub"],
    my_queue: ["h_my_queue", "h_my_queue_sub"],
    brief: ["h_brief", ""],
    my_stats: ["h_my_stats", "h_my_stats_sub"],
    coaching: ["h_coaching", "h_coaching_sub"],
  };
  const [tk, sk] = titleMap[APP_STATE.page] || ["", ""];
  document.getElementById("topbar-title").textContent = tk ? t(tk) : "";
  document.getElementById("topbar-sub").textContent = sk ? t(sk) : "";

  const isManager = APP_STATE.role === "manager";
  document.getElementById("kpi-grid").style.display = isManager ? "grid" : "none";

  if (isManager && APP_STATE.synthesis?.overview) {
    renderKpis(APP_STATE.synthesis.overview);
  }

  const root = document.getElementById("page-content");

  if (!isManager && !APP_STATE.synthesis) await loadSynthesis();

  // Themes page needs unfiltered overall data (independent of current filter state)
  if (isManager && APP_STATE.page === "themes") {
    await ensureOverallLoaded();
  }

  switch (APP_STATE.page) {
    case "slice":        return renderSlice(root);
    case "team":         return renderTeam(root);
    case "competitors":  return renderCompetitors(root);
    case "themes":       return renderThemes(root);
    case "my_queue":     return renderQueue(root);
    case "brief":        return renderBrief(root);
    case "my_stats":     return renderMyStats(root);
    case "coaching":     return renderCoaching(root);
  }
}

function renderKpis(o) {
  document.getElementById("kpi-grid").innerHTML = `
    <div class="kpi-card"><div class="kpi-label">${t("kpi_calls")}</div><div class="kpi-value">${o.n_calls || 0}</div></div>
    <div class="kpi-card"><div class="kpi-label">${t("kpi_booked")}</div><div class="kpi-value">${o.n_booked || 0}</div></div>
    <div class="kpi-card"><div class="kpi-label">${t("kpi_rate")}</div><div class="kpi-value">${(o.booking_rate || 0).toFixed(1)}%</div></div>
    <div class="kpi-card"><div class="kpi-label">${t("kpi_reps")}</div><div class="kpi-value">${o.n_reps || 0}</div></div>
  `;
}

function renderSlice(root) {
  const tab = APP_STATE.sliceTab || "works";
  const works = APP_STATE.synthesis?.works?.patterns || [];
  const doesnt = APP_STATE.synthesis?.doesnt?.patterns || [];
  const list = tab === "works" ? works : doesnt;
  const segCount = APP_STATE.synthesis?.overview?.n_calls;
  const isFiltered = !isFiltersEmpty(APP_STATE.filters);

  let html = renderSegmentPills();

  if (isFiltered && segCount === 0) {
    html += `<div class="empty-state" style="padding:48px 20px;">
      <div style="font-size:36px; margin-bottom:12px;">🔍</div>
      <div class="empty-state-title">${APP_STATE.lang === "en"
        ? "No calls match this combination of filters."
        : "Ninguna llamada coincide con esta combinación de filtros."}</div>
      <div class="muted" style="margin-top:8px; font-size:12px;">${APP_STATE.lang === "en"
        ? "Try removing a filter."
        : "Intente quitar un filtro."}</div>
    </div>`;
    root.innerHTML = html;
    return;
  }

  html += `
    <div class="patterns-tabs">
      <button class="patterns-tab ${tab === "works" ? "active works-tab" : ""}" onclick="switchSliceTab('works')">
        <span class="patterns-tab-icon">✓</span>
        <span class="patterns-tab-label">${t("nav_what_works")}</span>
        <span class="patterns-tab-count">${works.length}</span>
      </button>
      <button class="patterns-tab ${tab === "doesnt" ? "active doesnt-tab" : ""}" onclick="switchSliceTab('doesnt')">
        <span class="patterns-tab-icon">✕</span>
        <span class="patterns-tab-label">${t("nav_what_doesnt")}</span>
        <span class="patterns-tab-count">${doesnt.length}</span>
      </button>
    </div>`;

  if (!list.length) {
    html += skeletonGrid();
  } else {
    html += `<div class="grid-2">${list.map((p, i) => patternCard(p, tab, i + 1)).join("")}</div>`;
  }
  root.innerHTML = html;
}

function switchSliceTab(tab) {
  APP_STATE.sliceTab = tab;
  renderSlice(document.getElementById("page-content"));
}

function switchThemesTab(tab) {
  APP_STATE.themesTab = tab;
  renderThemes(document.getElementById("page-content"));
}

function patternCard(p, kind, rank) {
  const whyKey = kind === "works" ? "why_it_works" : "why_it_loses";
  return `
    <div class="pattern-card ${kind}">
      <div class="pattern-rank">#${rank}</div>
      <div class="pattern-name">${esc(p.pattern || "")}</div>
      <div class="pattern-why">${esc(p[whyKey] || "")}</div>
      ${p.example_quote ? `<div class="pattern-quote">"${esc(p.example_quote)}"</div>` : ""}
      <div class="pattern-meta">
        <span>From <strong>${esc(p.source_rep || "—")}</strong> · ${esc(p.source_restaurant || "—")}</span>
        <span><strong>${p.n_calls || 1}</strong> ${p.n_calls === 1 ? "call" : "calls"}</span>
      </div>
    </div>`;
}

function skeletonGrid() {
  return `<div class="grid-2">
    <div class="skeleton-card"></div><div class="skeleton-card"></div>
    <div class="skeleton-card"></div><div class="skeleton-card"></div>
  </div>`;
}

function renderTeam(root) {
  // Prefer the persistent cache (survives tab switches and segment filter changes).
  // Fall back to the current synthesis payload if the cache hasn't been populated yet.
  const cards = APP_STATE.repCards || APP_STATE.synthesis?.rep_cards || [];
  let tier = APP_STATE.teamFilter || "all";

  let html = `<div class="filter-pills">
    ${["all", "top_quartile", "steady", "struggling"].map(k =>
      `<button class="filter-pill ${k === tier ? "active" : ""}" onclick="setTeamFilter('${k}')">${
        t("filter_" + (k === "all" ? "all" : k === "top_quartile" ? "top" : k === "struggling" ? "struggling" : "steady"))
      }</button>`
    ).join("")}
  </div>`;

  if (!cards.length) {
    html += `<p class="loading-text">${t("no_data")}</p>`;
    root.innerHTML = html;
    return;
  }

  const filtered = tier === "all" ? cards : cards.filter(c => c.coaching?.tier === tier);
  html += `<div class="grid-1">${filtered.map((c, i) => repCard(c, i + 1)).join("")}</div>`;
  root.innerHTML = html;
}

function setTeamFilter(tier) {
  APP_STATE.teamFilter = tier;
  renderTeam(document.getElementById("page-content"));
}

function repCard(rc, rank) {
  const initials = (rc.rep_id || "").replace("rep_", "").slice(0, 2).toUpperCase();
  const tier = rc.coaching?.tier || "steady";
  const rankClass = rank === 1 ? "gold" : rank === 2 ? "silver" : rank === 3 ? "bronze" : "";

  return `
    <div class="rep-card">
      <div class="rep-card-head">
        <div class="rep-rank ${rankClass}">${rank}</div>
        <div class="rep-avatar">${initials}</div>
        <div style="flex:1;">
          <div class="rep-name">${esc(rc.rep_id)}</div>
          <div class="rep-stats-line">
            <strong>${rc.stats?.n_calls || 0}</strong> calls ·
            <strong>${rc.stats?.n_booked || 0}</strong> ${t("booked_of")} ·
            <strong>${Math.round((rc.stats?.booking_rate || 0) * 100)}%</strong> rate ·
            <strong>${Math.round((rc.stats?.avg_talk_ratio || 0) * 100)}%</strong> talk
          </div>
        </div>
        <span class="rep-tier tier-${tier}">${tier.replace("_", " ")}</span>
      </div>
      <div class="rep-narrative">${esc(rc.coaching?.summary || "")}</div>
      <div class="coach-blocks">
        <div class="coach-block strength">
          <div class="coach-block-label">${t("strength")}</div>
          <div class="coach-block-text">${esc(rc.coaching?.strength || "—")}</div>
        </div>
        <div class="coach-block weakness">
          <div class="coach-block-label">${t("push_on")}</div>
          <div class="coach-block-text">${esc(rc.coaching?.weakness || "—")}</div>
        </div>
      </div>
      <div class="suggested-action">
        <strong>${t("suggested_action")}</strong> ${esc(rc.coaching?.suggested_action || "—")}
      </div>
      <div class="rep-actions">
        <button class="btn btn-secondary" onclick="composeNote('${rc.rep_id}')">${t("send_note")}</button>
        <button class="btn btn-ghost">${t("live_room")} →</button>
      </div>
    </div>`;
}

function renderCompetitors(root) {
  const comps = APP_STATE.synthesis?.competitor_intel?.competitors || [];
  if (!comps.length) { root.innerHTML = skeletonGrid(); return; }
  const intro = APP_STATE.lang === "en"
    ? "Owner.com's main competitors in restaurant SaaS — AI-tracked news, product moves, and rebuttal angles for reps."
    : "Competidores principales de Owner.com en SaaS para restaurantes — noticias y ángulos rastreados por IA.";
  root.innerHTML = `
    <div class="competitors-intro">${intro}</div>
    <div class="grid-2">${comps.map(competitorCard).join("")}</div>`;
}

function competitorCard(c) {
  const cat = c.category || "competitor";
  return `
    <div class="competitor-card">
      <div class="competitor-head">
        <div class="competitor-logo">${esc((c.name || "").slice(0, 2).toUpperCase())}</div>
        <div style="flex:1; min-width:0;">
          <div class="competitor-name">${esc(c.name)}</div>
          <div class="competitor-tag">${esc(cat)} · ${c.sources?.length ? `${c.sources.length} sources` : (APP_STATE.lang === "en" ? "static intel" : "intel estático")}</div>
        </div>
      </div>
      ${c.headline ? `
        <div class="competitor-headline">
          <span class="competitor-headline-icon">📰</span>
          <span>${esc(c.headline)}</span>
        </div>` : ""}
      <div class="competitor-summary">${esc(c.summary || "")}</div>
      ${c.rebuttal ? `
        <div class="competitor-rebuttal">
          <div class="competitor-rebuttal-label">▸ ${APP_STATE.lang === "en" ? "Rep angle when this competitor comes up" : "Ángulo cuando aparezca este competidor"}</div>
          ${esc(c.rebuttal)}
        </div>` : ""}
      ${c.sources?.length ? `
        <div class="competitor-sources">
          <strong>${t("sources")}</strong>
          ${c.sources.map(s => `<a href="${esc(s.url)}" target="_blank">${esc(s.title)}</a>`).join("")}
        </div>` : ""}
    </div>`;
}

function renderThemes(root) {
  const overall = APP_STATE.overallSynthesis;
  const c = APP_STATE.overallCharts;
  const tab = APP_STATE.themesTab || "works";

  // Empty state: no analysis yet → CTA to run it (replaces the auto-trigger that used to jam the page)
  const hasAnalysis = overall && c && (overall.overview?.n_calls > 0 ||
                                        (overall.works?.patterns?.length > 0));
  if (!hasAnalysis) {
    const isLoading = !overall || !c;
    if (isLoading) {
      // Initial fetch hasn't completed yet — quick spinner
      root.innerHTML = `<div class="empty-state">
        <div class="empty-state-spinner"></div>
        <div class="empty-state-title">${APP_STATE.lang === "en" ? "Loading…" : "Cargando…"}</div>
      </div>`;
    } else {
      // Loaded, but pipeline hasn't run yet — show explicit CTA
      root.innerHTML = `<div class="empty-state empty-state-cta">
        <div class="empty-state-icon">📊</div>
        <div class="empty-state-title">${APP_STATE.lang === "en"
          ? "No analysis yet"
          : "Sin análisis aún"}</div>
        <div class="empty-state-sub">${APP_STATE.lang === "en"
          ? "Run the pipeline to extract patterns from your team's calls. Takes ~10-30 seconds."
          : "Corra el análisis para extraer patrones de las llamadas. Toma ~10-30 segundos."}</div>
        <button class="empty-state-btn" onclick="runPipeline()">
          ${APP_STATE.lang === "en" ? "Run analysis" : "Correr análisis"}
        </button>
      </div>`;
    }
    return;
  }

  const works = overall.works?.patterns || [];
  const doesnt = overall.doesnt?.patterns || [];
  const list = tab === "works" ? works : doesnt;

  // Lightweight inline mini-charts — no Chart.js, no canvas, no 200ms init time.
  // Just CSS bars + numbers. Renders in <5ms total.
  const chartsRow = `
    <div class="themes-charts-row">
      ${miniBarChart(
        APP_STATE.lang === "en" ? "Opener × outcome" : "Apertura × resultado",
        c.opener_outcome.map(d => ({label: d.opener_type, won: d.Won, lost: d.Lost})),
        "stacked"
      )}
      ${miniBarChart(
        APP_STATE.lang === "en" ? "Languages" : "Idiomas",
        c.languages.map(d => ({label: d.language === "en" ? "English" : "Español", value: d.count})),
        "simple"
      )}
      ${miniBarChart(
        APP_STATE.lang === "en" ? "Top objections" : "Objeciones",
        (c.objections || []).slice(0, 5).map(d => ({label: d.category, value: d.raised})),
        "simple"
      )}
      ${miniBarChart(
        APP_STATE.lang === "en" ? "Competitor mentions" : "Competidores",
        (c.competitors || []).slice(0, 5).map(d => ({label: d.name, value: d.mentions})),
        "simple"
      )}
    </div>`;

  const patternsBlock = `
    <div class="patterns-tabs">
      <button class="patterns-tab ${tab === "works" ? "active works-tab" : ""}" onclick="switchThemesTab('works')">
        <span class="patterns-tab-icon">✓</span>
        <span class="patterns-tab-label">${t("nav_what_works")}</span>
        <span class="patterns-tab-count">${works.length}</span>
      </button>
      <button class="patterns-tab ${tab === "doesnt" ? "active doesnt-tab" : ""}" onclick="switchThemesTab('doesnt')">
        <span class="patterns-tab-icon">✕</span>
        <span class="patterns-tab-label">${t("nav_what_doesnt")}</span>
        <span class="patterns-tab-count">${doesnt.length}</span>
      </button>
    </div>
    ${list.length
      ? `<div class="grid-2">${list.map((p, i) => patternCard(p, tab, i + 1)).join("")}</div>`
      : skeletonGrid()}`;

  root.innerHTML = `
    <div class="themes-intro">${APP_STATE.lang === "en"
      ? "Aggregated insights across all calls. For segment-specific drill-down, use Slice & Dice."
      : "Insights agregados de todas las llamadas. Para análisis por segmento, use Slice & Dice."}</div>
    ${chartsRow}
    ${patternsBlock}`;
}

function miniBarChart(title, items, mode = "simple") {
  if (!items || !items.length) {
    return `<div class="mc-card">
      <div class="mc-title">${title}</div>
      <div class="mc-empty">—</div>
    </div>`;
  }

  if (mode === "stacked") {
    // Won/lost stacked horizontal bars
    const max = Math.max(...items.map(i => (i.won || 0) + (i.lost || 0)), 1);
    return `<div class="mc-card">
      <div class="mc-title">${title}</div>
      <div class="mc-rows">
        ${items.slice(0, 5).map(i => {
          const total = (i.won || 0) + (i.lost || 0);
          const wonPct = total ? (i.won / max * 100) : 0;
          const lostPct = total ? (i.lost / max * 100) : 0;
          return `
            <div class="mc-row">
              <div class="mc-label">${esc(i.label)}</div>
              <div class="mc-bar-stack">
                <div class="mc-bar-won" style="width:${wonPct}%"></div>
                <div class="mc-bar-lost" style="width:${lostPct}%"></div>
              </div>
              <div class="mc-val">${i.won}/${total}</div>
            </div>`;
        }).join("")}
      </div>
      <div class="mc-legend">
        <span class="mc-legend-won">● ${APP_STATE.lang === "en" ? "Won" : "Ganadas"}</span>
        <span class="mc-legend-lost">● ${APP_STATE.lang === "en" ? "Lost" : "Perdidas"}</span>
      </div>
    </div>`;
  }

  // simple mode — single bar per row
  const max = Math.max(...items.map(i => i.value || 0), 1);
  return `<div class="mc-card">
    <div class="mc-title">${title}</div>
    <div class="mc-rows">
      ${items.map(i => {
        const pct = (i.value || 0) / max * 100;
        return `
          <div class="mc-row">
            <div class="mc-label">${esc(i.label)}</div>
            <div class="mc-bar"><div class="mc-bar-fill" style="width:${pct}%"></div></div>
            <div class="mc-val">${i.value}</div>
          </div>`;
      }).join("")}
    </div>
  </div>`;
}

async function renderQueue(root) {
  if (!APP_STATE.queue) {
    try {
      const res = await fetch("/api/queue");
      APP_STATE.queue = await res.json();
    } catch (e) {
      root.innerHTML = `<p class="loading-text">Failed to load queue.</p>`;
      return;
    }
  }
  const q = APP_STATE.queue || [];

  let banner = "";
  const sarah = APP_STATE.synthesis?.rep_cards?.find(c => c.rep_id === "rep_sarah");
  if (sarah) {
    banner = `
      <div class="coaching-banner">
        <div class="coaching-banner-icon">M</div>
        <div>
          <div class="coaching-banner-title">${t("latest_coaching")}</div>
          <div class="coaching-banner-body">${esc((sarah.coaching?.summary || "").slice(0, 200))}</div>
        </div>
      </div>`;
  }

  root.innerHTML = banner + `<div class="queue-list">
    ${q.map(r => `
      <div class="queue-row" onclick="openBrief('${r.restaurant_id}')">
        <div class="queue-avatar">${r.initials}</div>
        <div>
          <div class="queue-name">${esc(r.name)}</div>
          <div class="queue-meta">${esc(r.cuisine)} · ${esc(r.city)}, ${esc(r.state)} · attempt #${r.attempt}</div>
        </div>
        <div class="queue-spend">${r.spend ? `<strong>$${(r.spend/1000).toFixed(1)}K/mo</strong> ${t("in_commissions")}` : ""}</div>
        <div class="queue-time ${r.is_now ? "now" : ""}">${r.is_now ? `✓ ${t("now")} · ` : ""}${r.recommended_time}</div>
        <div class="queue-cta">${t("open")} →</div>
      </div>`).join("")}
  </div>`;
}

async function openBrief(rid) {
  APP_STATE.selectedRestaurant = rid;
  APP_STATE.page = "brief";
  renderActiveNav();
  await renderBrief(document.getElementById("page-content"));
}

async function renderBrief(root) {
  if (!APP_STATE.selectedRestaurant) {
    root.innerHTML = `<p class="loading-text">${APP_STATE.lang === "en" ? "Pick a restaurant from My Queue first." : "Elija un restaurante de Mi Cola."}</p>`;
    return;
  }

  const rid = APP_STATE.selectedRestaurant;
  const cacheKey = `${rid}:${APP_STATE.lang}`;

  if (!APP_STATE.briefs[cacheKey]) {
    root.innerHTML = `<div class="empty-state">
      <div class="empty-state-spinner"></div>
      <div class="empty-state-title">${APP_STATE.lang === "en" ? "Building your pre-call brief…" : "Preparando resumen previo a la llamada…"}</div>
    </div>`;
    try {
      const res = await fetch(`/api/brief/${rid}?lang=${APP_STATE.lang}`);
      APP_STATE.briefs[cacheKey] = await res.json();
    } catch (e) {
      root.innerHTML = `<p class="loading-text">Failed to load brief.</p>`;
      return;
    }
  }

  const b = APP_STATE.briefs[cacheKey];
  const r = b.restaurant || {};
  const ci = b.account_intel?.cuisine_intel;
  const pi = b.account_intel?.platform_intel;
  const cuisine = r.cuisine || ci?.cuisine_type || "";
  const cuisineEmoji = ({pizza:"🍕",mexican:"🌮",asian:"🍜",bbq:"🍖",burgers:"🍔",seafood:"🦞",italian:"🍝",thai:"🍜",japanese:"🍣"})[(cuisine||"").toLowerCase()] || "🍽";
  const initials = (r.name || "").split(" ").map(w => w[0]).slice(0, 2).join("").toUpperCase();

  // Pull data inline (one continuous script, no panels)
  const tt = b.talk_track || [];
  const opener = tt.find(s => s.step === 1) || {};
  const discovery = tt.find(s => s.step === 2) || {};
  const pitch = tt.find(s => s.step === 3) || {};
  const close = tt.find(s => s.step === 5) || {};
  const isES = APP_STATE.lang === "es";
  const platforms = pi?.platforms_detected || [];
  const primaryPlatform = platforms[0] || "DoorDash";
  const spend = pi?.estimated_monthly_loss || "$5,000/mo";

  // Pull objections from the talk track (step 4) — single source of truth, server-built
  const ttObjections = (tt.find(s => s.step === 4)?.objections) || [];
  const objections = ttObjections.map(o => ({
    hot: (o.they_say || "").replace(/^["']|["']$/g, "").replace(/\\"/g, '"').slice(0, 80),
    say: o.you_say || "",
    hot_en: (o.they_say_en || "").replace(/^["']|["']$/g, "").slice(0, 80),
    say_en: o.you_say_en || "",
  }));

  root.innerHTML = `
    <!-- Top bar: back + restaurant + labeled site button -->
    <div class="tele-bar">
      <button class="tele-back" onclick="navigate('my_queue')" title="${isES ? 'Volver' : 'Back'}">←</button>
      <span class="tele-name">${esc(r.name || "")}</span>
      <span class="tele-meta">${esc(cuisine)} · ${esc(r.city || "")}, ${esc(r.state || "")} · #${b.attempt || 1}</span>
      ${r.website_url ? `
        <button class="tele-website tele-website-labeled" onclick="openWebsitePanel('${esc(r.website_url)}', '${esc(r.name || "")}')" title="${esc(r.website_url)}">
          🌐 <span>${isES ? "Ver sitio web" : "Visit website"}</span>
        </button>` : ""}
    </div>

    <!-- 2-column layout: script left, details right -->
    <div class="tele-layout">

      <!-- LEFT: the script — read it top to bottom -->
      <div class="tele">

        <div class="tele-stage">${isES ? "▸ EMPIECE CON:" : "▸ START WITH:"}</div>
        <div class="tele-line-spoken">${esc(opener.say || "")}</div>
        ${isES && opener.say_en ? `
          <div class="tele-line-en"><span class="tele-en-tag">EN</span>${esc(opener.say_en)}</div>` : ""}
        <div class="tele-direction">${isES ? "(espere a que digan que sí)" : "(wait for them to say yes)"}</div>

        <div class="tele-stage">${isES ? "▸ DESPUÉS PREGUNTE:" : "▸ THEN ASK:"}</div>
        <div class="tele-line-spoken">${esc((discovery.questions || [""])[0])}</div>
        ${isES && discovery.questions_en?.[0] ? `
          <div class="tele-line-en"><span class="tele-en-tag">EN</span>${esc(discovery.questions_en[0])}</div>` : ""}
        <div class="tele-direction">${isES ? "(deje que respondan · no presente todavía)" : "(let them answer · don't pitch yet)"}</div>

        <div class="tele-stage">${isES ? "▸ DESPUÉS PRESENTE:" : "▸ THEN PITCH:"}</div>
        <div class="tele-line-spoken">${esc(pitch.say || "")}</div>
        ${isES && pitch.say_en ? `
          <div class="tele-line-en"><span class="tele-en-tag">EN</span>${esc(pitch.say_en)}</div>` : ""}

        <div class="tele-stage">${isES ? "▸ SI EMPUJAN ATRÁS:" : "▸ IF THEY PUSH BACK:"}</div>
        <div class="tele-pushback">
          ${objections.map(o => `
            <div class="tele-exchange">
              <div class="tele-they-say">
                <span class="tele-tag tele-tag-they">${isES ? "Dicen:" : "They say:"}</span>
                <span>${esc(o.hot)}</span>
              </div>
              ${isES && o.hot_en ? `
                <div class="tele-line-en tele-line-en-indent"><span class="tele-en-tag">EN</span>${esc(o.hot_en)}</div>` : ""}
              <div class="tele-you-say">
                <span class="tele-tag tele-tag-you">${isES ? "Diga:" : "You say:"}</span>
                <span>${esc(o.say)}</span>
              </div>
              ${isES && o.say_en ? `
                <div class="tele-line-en tele-line-en-indent"><span class="tele-en-tag">EN</span>${esc(o.say_en)}</div>` : ""}
            </div>`).join("")}
        </div>

        <div class="tele-stage tele-stage-close">${isES ? "▸ CIERRE CON:" : "▸ CLOSE WITH:"}</div>
        <div class="tele-line-spoken tele-line-close">${esc(close.say || "")}</div>
        ${isES && close.say_en ? `
          <div class="tele-line-en"><span class="tele-en-tag">EN</span>${esc(close.say_en)}</div>` : ""}
        <div class="tele-direction">${isES ? "(espere · que elijan)" : "(wait · let them pick)"}</div>

      </div>

      <!-- RIGHT: side panel with restaurant context + voicemail -->
      <aside class="tele-side">
        <div class="tele-side-block">
          <div class="tele-side-head">${isES ? "Contexto del restaurante" : "Restaurant context"}</div>
          <div class="tele-side-body" id="tele-side-body">
            <div class="tele-side-row">
              <span class="tele-side-icon">🏪</span>
              <span class="tele-side-text"><b>${esc(r.business_type || "—")}</b> · ${r.locations || 1} ${(r.locations || 1) === 1 ? (isES ? "ubicación" : "location") : (isES ? "ubicaciones" : "locations")}</span>
            </div>
            <div class="tele-side-row tele-side-row-loading" id="tele-intel-loading">
              <span class="tele-side-spinner"></span>
              <span class="tele-side-text muted">${isES ? "Cargando detalles…" : "Loading details…"}</span>
            </div>
          </div>
        </div>

        <div class="tele-side-block">
          <div class="tele-side-head">📵 ${isES ? "Si llega al buzón" : "If voicemail"}</div>
          <div class="tele-side-vm">${esc(b.voicemail || "")}</div>
        </div>

        ${b.touch_history?.length ? `
          <div class="tele-side-block">
            <div class="tele-side-head">${isES ? "Historial" : "Recent calls"}</div>
            ${b.touch_history.slice(0, 3).map(tx => `
              <div class="tele-side-history">
                <span class="tele-side-history-rep">${esc(tx.rep_id || "")}</span>
                <span class="tele-side-history-time">${esc((tx.timestamp || "").slice(0, 10))}</span>
                <span class="tele-side-history-out outcome-${tx.outcome || ""}">${esc(tx.outcome || "—")}</span>
              </div>`).join("")}
          </div>` : ""}

        <button class="tele-side-similar" onclick="openSimilarCalls('${rid}')">
          🎧 ${isES ? "Escuchar éxitos similares" : "Hear similar wins"}
        </button>
      </aside>

    </div>

    <!-- Website panel (off-screen) -->
    <div id="website-panel" class="website-panel">
      <div class="website-panel-head">
        <div>
          <div class="website-panel-title" id="website-panel-title"></div>
          <a class="website-panel-url" id="website-panel-url" target="_blank"></a>
        </div>
        <button class="modal-close" onclick="closeWebsitePanel()">×</button>
      </div>
      <div class="website-panel-body">
        <iframe id="website-iframe" src="about:blank" sandbox="allow-same-origin allow-scripts allow-popups allow-forms"></iframe>
        <div class="website-panel-fallback" id="website-panel-fallback" style="display:none;">
          <div class="iframe-fallback-card">
            <div class="iframe-fallback-icon">🔒</div>
            <h3 class="iframe-fallback-title">${isES ? "Este sitio no se puede incrustar" : "This site can't be embedded"}</h3>
            <p class="iframe-fallback-sub">${isES ? "Haga clic abajo para abrir en una nueva pestaña." : "Click below to open in a new tab."}</p>
            <a class="iframe-fallback-link" id="iframe-fallback-link" target="_blank" rel="noopener">
              <span class="iframe-fallback-link-icon">🌐</span>
              <span class="iframe-fallback-link-url" id="iframe-fallback-url"></span>
              <span class="iframe-fallback-link-cta">${isES ? "Abrir →" : "Open →"}</span>
            </a>
            <button class="iframe-fallback-copy" id="iframe-fallback-copy">
              📋 ${isES ? "Copiar URL" : "Copy URL"}
            </button>
          </div>
        </div>
      </div>
    </div>`;

  // Auto-load enriched intel into the side panel right after first paint.
  // The script renders instantly; intel fills in within ~100ms (or up to a few seconds
  // if Tavily is enabled).
  setTimeout(async () => {
    const sideBody = document.getElementById("tele-side-body");
    const loadingEl = document.getElementById("tele-intel-loading");
    if (!sideBody || sideBody.dataset.loaded) return;
    sideBody.dataset.loaded = "1";
    try {
      const res = await fetch(`/api/intel/${rid}?lang=${APP_STATE.lang}`);
      const intel = await res.json();
      const ic = intel.cuisine_intel || {};
      const ip = intel.platform_intel || {};
      if (loadingEl) loadingEl.remove();
      const html = `
        ${ip.estimated_monthly_loss ? `
          <div class="tele-side-row tele-side-row-pain">
            <span class="tele-side-icon">📉</span>
            <span class="tele-side-text">${isES ? "Pierde" : "Losing"} <b>${esc(ip.estimated_monthly_loss)}</b> ${isES ? "a apps" : "to apps"}</span>
          </div>` : ""}
        ${ip.platforms_detected?.length ? `
          <div class="tele-side-row">
            <span class="tele-side-icon">🏢</span>
            <span class="tele-side-text">${ip.platforms_detected.map(p => `<span class="tele-side-pill">${esc(p)}</span>`).join("")}</span>
          </div>` : ""}
        ${ic.top_menu_item ? `
          <div class="tele-side-row">
            <span class="tele-side-icon">⭐</span>
            <span class="tele-side-text"><b>${isES ? "Plato top:" : "Top item:"}</b> ${esc(ic.top_menu_item)}</span>
          </div>` : ""}
        ${ic.review_themes?.length ? `
          <div class="tele-side-row">
            <span class="tele-side-icon">💬</span>
            <span class="tele-side-text">${ic.review_themes.slice(0,3).map(rt => `<span class="tele-side-pill">${esc(rt)}</span>`).join("")}</span>
          </div>` : ""}
        ${ic.price_range ? `
          <div class="tele-side-row">
            <span class="tele-side-icon">💵</span>
            <span class="tele-side-text">${esc(ic.price_range)}</span>
          </div>` : ""}
      `;
      sideBody.insertAdjacentHTML("beforeend", html);
    } catch (e) {
      if (loadingEl) {
        loadingEl.innerHTML = `<span class="tele-side-text muted">${isES ? "Detalles no disponibles" : "Details unavailable"}</span>`;
      }
    }
  }, 50);
}

function toggleMoreContext() {
  const body = document.getElementById("more-context-body");
  const arrow = document.getElementById("more-context-arrow");
  if (!body) return;
  if (body.style.display === "none") {
    body.style.display = "block";
    arrow.textContent = "▼";
  } else {
    body.style.display = "none";
    arrow.textContent = "▶";
  }
}


function normalizeUrl(url) {
  if (!url) return "";
  url = String(url).trim();
  if (!url) return "";
  // If already has a scheme (http://, https://, ftp://...), keep as-is
  if (/^[a-z]+:\/\//i.test(url)) return url;
  // Otherwise prepend https://
  return "https://" + url.replace(/^\/+/, "");
}

function openWebsitePanel(url, name) {
  if (!url) return;
  url = normalizeUrl(url);
  const panel = document.getElementById("website-panel");
  const iframe = document.getElementById("website-iframe");
  const fallback = document.getElementById("website-panel-fallback");
  document.getElementById("website-panel-title").textContent = name || "";
  const urlEl = document.getElementById("website-panel-url");
  urlEl.href = url;
  urlEl.textContent = url.replace(/^https?:\/\//, "").replace(/\/$/, "");

  // Wire up new fallback elements
  const fallbackLink = document.getElementById("iframe-fallback-link");
  const fallbackUrlEl = document.getElementById("iframe-fallback-url");
  const fallbackCopy = document.getElementById("iframe-fallback-copy");
  if (fallbackLink) fallbackLink.href = url;
  if (fallbackUrlEl) fallbackUrlEl.textContent = url;
  if (fallbackCopy) {
    fallbackCopy.onclick = async () => {
      try {
        await navigator.clipboard.writeText(url);
        const original = fallbackCopy.innerHTML;
        fallbackCopy.innerHTML = `✓ ${APP_STATE.lang === "en" ? "Copied!" : "¡Copiado!"}`;
        fallbackCopy.classList.add("copied");
        setTimeout(() => {
          fallbackCopy.innerHTML = original;
          fallbackCopy.classList.remove("copied");
        }, 1800);
      } catch (e) {
        // Fallback for environments without clipboard API
        const ta = document.createElement("textarea");
        ta.value = url;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
    };
  }

  iframe.style.display = "block";
  fallback.style.display = "none";
  iframe.src = url;

  panel.classList.add("show");
  document.body.classList.add("panel-open");

  // If iframe fails to load (X-Frame-Options / CSP), open in new tab + close panel.
  // The fallback card is the secondary path (in case popup is blocked).
  clearTimeout(window._iframeFallbackTimer);
  window._iframeFallbackTimer = setTimeout(() => {
    try {
      const blocked = !iframe.contentWindow ||
                      !iframe.contentWindow.location ||
                      iframe.contentWindow.location.href === "about:blank";
      if (blocked) {
        // Try opening in a new tab automatically
        const newTab = window.open(url, "_blank", "noopener");
        if (newTab) {
          // Popup succeeded — close the in-app panel since the user is now on the real site
          closeWebsitePanel();
          showToast(`${APP_STATE.lang === "en" ? "Opened in new tab" : "Abierto en nueva pestaña"}: ${url.replace(/^https?:\/\//, "")}`);
        } else {
          // Popup blocked — show the styled fallback so they can click it manually
          iframe.style.display = "none";
          fallback.style.display = "block";
        }
      }
    } catch (e) {
      // Cross-origin block lands here — that's actually a successful load
    }
  }, 4000);
}

function closeWebsitePanel() {
  const panel = document.getElementById("website-panel");
  if (panel) {
    panel.classList.remove("show");
    document.getElementById("website-iframe").src = "about:blank";
  }
  document.body.classList.remove("panel-open");
  clearTimeout(window._iframeFallbackTimer);
}

async function openSimilarCalls(rid) {
  document.getElementById("modal-title").textContent = APP_STATE.lang === "en"
    ? "Similar successful calls" : "Llamadas exitosas similares";
  document.getElementById("modal-body").innerHTML = `<div class="empty-state">
    <div class="empty-state-spinner"></div>
    <div class="empty-state-title">${APP_STATE.lang === "en" ? "Finding similar won calls…" : "Buscando llamadas ganadas similares…"}</div>
  </div>`;
  document.getElementById("modal").classList.add("show");

  try {
    const res = await fetch(`/api/similar_calls/${rid}`);
    const data = await res.json();
    const calls = data.calls || [];

    if (!calls.length) {
      document.getElementById("modal-body").innerHTML = `<p class="loading-text">${APP_STATE.lang === "en" ? "No similar won calls in the dataset yet." : "Aún no hay llamadas ganadas similares."}</p>`;
      return;
    }

    document.getElementById("modal-body").innerHTML = calls.map(c => `
      <div style="background:var(--surface-2); border:1px solid var(--border); border-radius:10px; padding:14px 16px; margin-bottom:10px;">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px;">
          <div>
            <div style="font-weight:700; font-size:14px; color:var(--text);">${esc(c.restaurant_name)}</div>
            <div style="font-size:12px; color:var(--text-3); margin-top:2px;">${esc(c.cuisine || "—")} · ${esc(c.city || "")}, ${esc(c.state || "")} · by <strong style="color:var(--text-2);">${esc(c.rep_id)}</strong></div>
          </div>
          <span style="background:var(--green-soft); color:var(--green); padding:3px 9px; border-radius:999px; font-size:10px; font-weight:700; text-transform:uppercase;">WON</span>
        </div>
        ${c.key_moment ? `
          <div style="background:var(--accent-soft); border-left:3px solid var(--accent); padding:9px 12px; border-radius:0 6px 6px 0; margin-bottom:10px; font-size:12.5px; line-height:1.55; font-style:italic; color:var(--text);">
            <div style="font-size:9px; font-weight:700; color:var(--accent); text-transform:uppercase; letter-spacing:0.5px; margin-bottom:4px; font-style:normal;">▶ ${APP_STATE.lang === "en" ? "Key moment" : "Momento clave"}</div>
            "${esc(c.key_moment)}"
          </div>` : ""}
        <div style="display:flex; gap:8px; align-items:center;">
          <button class="btn btn-secondary" style="font-size:12px; padding:6px 12px;" onclick="playCallAudio('${c.call_id}', this)">
            🔊 ${APP_STATE.lang === "en" ? "Play audio" : "Reproducir audio"}
          </button>
          <button class="btn btn-ghost" style="font-size:12px; padding:6px 12px;" onclick="toggleTranscript('${c.call_id}', this)">
            📄 ${APP_STATE.lang === "en" ? "Transcript" : "Transcripción"}
          </button>
          <span style="font-size:11px; color:var(--text-3); margin-left:auto;">${Math.floor((c.duration_seconds||0)/60)}:${String((c.duration_seconds||0)%60).padStart(2,"0")}</span>
        </div>
        <div id="audio-${c.call_id}" style="margin-top:10px;"></div>
        <div id="transcript-${c.call_id}" style="display:none; margin-top:10px; max-height:240px; overflow-y:auto; background:var(--bg); border:1px solid var(--border); border-radius:6px; padding:10px 12px; font-size:12px; line-height:1.6; color:var(--text-2); white-space:pre-wrap; font-family:ui-monospace, monospace;">${esc(c.transcript_snippet)}…</div>
      </div>
    `).join("");
  } catch (e) {
    document.getElementById("modal-body").innerHTML = `<p class="loading-text">Error: ${e.message}</p>`;
  }
}

async function playCallAudio(callId, btn) {
  const wrap = document.getElementById(`audio-${callId}`);
  if (wrap.querySelector("audio")) {
    wrap.innerHTML = "";
    btn.innerHTML = `🔊 ${APP_STATE.lang === "en" ? "Play audio" : "Reproducir audio"}`;
    return;
  }
  btn.disabled = true;
  btn.innerHTML = `⏳ ${APP_STATE.lang === "en" ? "Generating with ElevenLabs…" : "Generando con ElevenLabs…"}`;
  try {
    const res = await fetch(`/api/audio/${callId}`);
    if (!res.ok) {
      const text = await res.text();
      let msg = APP_STATE.lang === "en" ? "Audio unavailable" : "Audio no disponible";
      if (res.status === 503) {
        msg = APP_STATE.lang === "en"
          ? "Set ELEVENLABS_API_KEY in .env to enable audio playback."
          : "Configure ELEVENLABS_API_KEY en .env para activar audio.";
      }
      wrap.innerHTML = `<div style="color:var(--amber); font-size:12px; padding:8px 0;">⚠ ${msg}</div>`;
      btn.disabled = false;
      btn.innerHTML = `🔊 ${APP_STATE.lang === "en" ? "Retry" : "Reintentar"}`;
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    wrap.innerHTML = `<audio controls autoplay style="width:100%;"><source src="${url}" type="audio/mpeg"></audio>`;
    btn.disabled = false;
    btn.innerHTML = `⏸ ${APP_STATE.lang === "en" ? "Hide player" : "Ocultar"}`;
  } catch (e) {
    wrap.innerHTML = `<div style="color:var(--red); font-size:12px; padding:8px 0;">${e.message}</div>`;
    btn.disabled = false;
  }
}

async function toggleTranscript(callId, btn) {
  const el = document.getElementById(`transcript-${callId}`);
  if (el.style.display === "none") {
    // Fetch full transcript on demand
    try {
      const res = await fetch(`/api/transcript/${callId}`);
      const data = await res.json();
      el.textContent = data.transcript || "";
      el.style.display = "block";
    } catch (e) {
      el.style.display = "block";
    }
  } else {
    el.style.display = "none";
  }
}

async function renderMyStats(root) {
  if (!APP_STATE.myStats) {
    try {
      const res = await fetch("/api/my_stats");
      APP_STATE.myStats = await res.json();
    } catch (e) {
      root.innerHTML = `<p class="loading-text">Failed to load.</p>`;
      return;
    }
  }
  const d = APP_STATE.myStats;

  let html = `
    <div class="stats-comparison">
      <div class="stat-block you">
        <div class="stat-block-label">👤 ${t("you")}</div>
        <div class="big-stat">${d.rep.booking_rate}%</div>
        <div class="big-stat-sub">${d.rep.n_booked} ${t("booked_of")} / ${d.rep.n_calls} ${t("of_calls")}</div>
        <div class="little-stats">
          <div class="stat-cell"><div class="label">${t("talk_ratio")}</div><div class="value">${Math.round(d.rep.avg_talk_ratio * 100)}%</div></div>
          <div class="stat-cell"><div class="label">${t("questions")}</div><div class="value">${d.rep.avg_questions}</div></div>
        </div>
      </div>
      <div class="stat-block team">
        <div class="stat-block-label">👥 ${t("team_avg")}</div>
        <div class="big-stat">${d.team.booking_rate}%</div>
        <div class="big-stat-sub">${d.team.n_booked} ${t("booked_of")} / ${d.team.n_calls} ${t("of_calls")}</div>
        <div class="little-stats">
          <div class="stat-cell"><div class="label">${t("talk_ratio")}</div><div class="value">${Math.round(d.team.avg_talk_ratio * 100)}%</div></div>
          <div class="stat-cell"><div class="label">${t("questions")}</div><div class="value">${d.team.avg_questions}</div></div>
        </div>
      </div>
    </div>`;

  const sarah = APP_STATE.synthesis?.rep_cards?.find(c => c.rep_id === "rep_sarah");
  if (sarah) {
    html += `<div class="section-h" style="margin-top:28px;">${t("coach_read")}</div>`;
    html += repCard(sarah, 1);
  }

  root.innerHTML = html;
}

async function renderCoaching(root) {
  if (!APP_STATE.coaching) {
    try {
      const res = await fetch("/api/coaching");
      APP_STATE.coaching = await res.json();
    } catch (e) {
      root.innerHTML = `<p class="loading-text">Failed to load.</p>`;
      return;
    }
  }
  const c = APP_STATE.coaching;
  const notes = c.notes || [];

  if (!notes.length && !c.coaching) {
    root.innerHTML = `<p class="loading-text">${t("no_data")}</p>`;
    return;
  }

  let html = "";
  if (c.coaching) {
    html += `<div class="note-thread">
      <div class="note-meta">From Maria Lopez · today · auto-generated from your call patterns</div>
      <div class="note-subject">${esc((c.coaching.strength || "").slice(0, 70) || "Weekly coaching")}</div>
      <div class="note-body">
        <p><strong>What's working:</strong> ${esc(c.coaching.strength || "—")}</p>
        <p><strong>Push on:</strong> ${esc(c.coaching.weakness || "—")}</p>
        <p><strong>This week's action:</strong> ${esc(c.coaching.suggested_action || "—")}</p>
      </div>
    </div>`;
  }

  notes.forEach(n => {
    html += `<div class="note-thread">
      <div class="note-meta">From ${esc(n.from_user)} · ${esc(n.created_at)}</div>
      <div class="note-subject">${esc(n.subject || "")}</div>
      <div class="note-body">${n.body || ""}</div>
      <div class="note-actions">
        <button class="btn btn-secondary" onclick="ackNote(${n.note_id}, this)" ${n.acknowledged ? "disabled" : ""}>${n.acknowledged ? "✓ Acknowledged" : "Mark as read"}</button>
      </div>
    </div>`;
  });

  root.innerHTML = html;
}

async function ackNote(id, btn) {
  await fetch(`/api/notes/${id}/ack`, {method: "POST"});
  btn.textContent = "✓ Acknowledged";
  btn.disabled = true;
  showToast("Acknowledged");
}

function composeNote(repId) {
  document.getElementById("modal-title").textContent = `Send coaching note to ${repId}`;
  document.getElementById("modal-body").innerHTML = `
    <label>Subject</label>
    <input type="text" id="note-subject" value="Quick feedback">
    <label>Note</label>
    <textarea id="note-body">Hey - wanted to flag something I noticed in your calls this week...</textarea>
    <div style="display:flex; gap:8px; justify-content:flex-end;">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn" onclick="sendNote('${repId}')">Send</button>
    </div>`;
  document.getElementById("modal").classList.add("show");
}

async function sendNote(repId) {
  await fetch("/api/note", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      to_user: repId,
      subject: document.getElementById("note-subject").value,
      body: document.getElementById("note-body").value,
    })
  });
  closeModal();
  showToast(`Note sent to ${repId}`);
}

function closeModal() {
  document.getElementById("modal").classList.remove("show");
}

function showToast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2400);
}

function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

init();
