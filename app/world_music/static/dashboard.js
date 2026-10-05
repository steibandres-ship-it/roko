const $ = (selector) => document.querySelector(selector);
const safeText = (value, fallback = "—") => value === null || value === undefined || value === "" ? fallback : String(value);
const fmtDate = (value) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("es-CL", { dateStyle: "medium", timeStyle: "short" });
};
const fmtNumber = (value) => value === null || value === undefined ? "—" : Number(value).toLocaleString("es-CL", { maximumFractionDigits: 0 });

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" }, cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function postJson(path, body = undefined) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method: "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return response.json();
}

const providerDocs = {
  "Soundcharts": ["API Soundcharts ↗", "https://developers.soundcharts.com/api/v2/doc"],
  "Chartmetric": ["API Chartmetric ↗", "https://api.chartmetric.com/apidocs/"],
  "Last.fm": ["API Last.fm ↗", "https://www.last.fm/api/account/create"],
};
function setMetricsAttribution(provider) {
  const attribution = $("#metrics-attribution");
  const values = {
    "Last.fm": ["Powered by AudioScrobbler · Last.fm ↗", "https://www.last.fm/"],
    "Soundcharts": ["Powered by Soundcharts ↗", "https://www.soundcharts.com/"],
    "Chartmetric": ["Fuente: Chartmetric ↗", "https://chartmetric.com/"],
  };
  const [label, href] = values[provider] || values["Last.fm"];
  attribution.textContent = label;
  attribution.href = href;
}
const stateLabels = {
  CONNECTED: "Conectado",
  DEGRADED: "Degradado",
  PROVIDER_NOT_CONNECTED: "Sin credenciales API",
  LICENSE_SCOPE_REQUIRED: "Derechos por confirmar",
  CONFIGURED_UNVERIFIED: "Configuración sin verificar",
  CONNECTED_UNVERIFIED_RIGHTS: "Conectado · licencia pendiente",
};

function renderProvider(payload, overview) {
  const providers = payload.providers || [];
  const cards = $("#provider-cards");
  cards.replaceChildren();
  const activity = overview.provider_activity || {};
  const linked = providers.filter((entry) => ["CONNECTED", "DEGRADED"].includes(entry.state)).length;
  $("#provider-count").textContent = `${linked} / ${providers.length || 3}`;
  $("#provider-hero-summary").textContent = linked
    ? `${linked} de ${providers.length} fuentes verificadas`
    : "Soundcharts, Chartmetric y Last.fm aún requieren acceso de API y alcance de uso";
  $("#provider-pip").style.background = linked ? "var(--teal)" : "var(--orange)";
  $("#provider-pip").style.boxShadow = linked ? "0 0 9px #7bf1cf66" : "0 0 9px #ffc07866";

  for (const provider of providers) {
    const card = element("article", `provider-card ${provider.state === "CONNECTED" ? "is-connected" : "needs-action"}`);
    const header = element("div", "provider-card-head");
    header.append(element("strong", "", provider.provider_name));
    header.append(element("span", "provider-state", stateLabels[provider.state] || provider.state));
    card.append(header);
    if (provider.provider_name === "Soundcharts") card.append(element("small", "provider-attribution", "Powered by Soundcharts"));

    const metrics = (provider.metrics_available || []).slice(0, 3);
    card.append(element("p", "provider-metrics", metrics.join(" · ") || "Sin métricas declaradas"));
    const setupAction = provider.provider_name === "Last.fm" && provider.state === "PROVIDER_NOT_CONNECTED"
      ? "Verifica el correo de tu cuenta Last.fm y luego crea una API key gratuita. El alcance personal/no comercial ya está registrado; todavía no se han consultado datos."
      : provider.setup_action;
    if (setupAction) card.append(element("p", "provider-action", setupAction));
    if (provider.access_note) card.append(element("p", "provider-access-note", provider.access_note));
    if (provider.freshness_note) card.append(element("p", "provider-freshness", provider.freshness_note));

    const providerActivity = activity[provider.provider_name] || {};
    const run = providerActivity.latest_sync;
    const details = [];
    if (run) details.push(`Última captura ${fmtDate(run.finished_at || run.started_at)}`);
    else if (provider.last_refresh) details.push(`Conexión verificada ${fmtDate(provider.last_refresh)}`);
    details.push(`${fmtNumber(providerActivity.observations || 0)} observaciones locales`);
    card.append(element("small", "provider-activity", details.join(" · ")));

    if (provider.provider_name === "Last.fm" && provider.state === "PROVIDER_NOT_CONNECTED") {
      const verifyLink = element("a", "provider-link", "Verificar correo Last.fm ↗");
      verifyLink.href = "https://www.last.fm/join/verify";
      verifyLink.target = "_blank";
      verifyLink.rel = "noreferrer";
      card.append(verifyLink);
    }
    const [linkLabel, href] = providerDocs[provider.provider_name] || ["Documentación ↗", "#"];
    const link = element("a", "provider-link", linkLabel);
    link.href = href;
    link.target = "_blank";
    link.rel = "noreferrer";
    card.append(link);
    cards.append(card);
  }
  const lastfm = providers.find((entry) => entry.provider_name === "Last.fm");
  return lastfm || {};
}

function renderMarkets(overview, catalog, provider) {
  const observed = overview.observed_markets || [];
  const markets = [...new Set(observed)];
  $("#market-count").textContent = String(markets.length).padStart(2, "0");
  const pills = $("#market-pills");
  pills.replaceChildren();
  const known = new Map((catalog || []).map((market) => [market.iso_code, market.name]));
  const shown = markets.slice(0, 4).map((code) => `${code}${known.has(code) ? ` · ${known.get(code)}` : ""}`);
  pills.textContent = shown.length ? shown.join("   ") : "Esperando capturas";

  const selector = $("#market-select");
  const previous = selector.value || "GLOBAL";
  const selectorMarkets = [...new Set([...(provider.country_coverage || []), ...observed])]
    .filter((code) => code && code !== "GLOBAL")
    .sort();
  selector.replaceChildren();
  const globalOption = element("option", "", "GLOBAL · mundo");
  globalOption.value = "GLOBAL";
  selector.append(globalOption);
  for (const code of selectorMarkets) {
    const option = element("option", "", `${code}${known.has(code) ? ` · ${known.get(code)}` : ""}`);
    option.value = code;
    selector.append(option);
  }
  selector.value = [...selector.options].some((option) => option.value === previous) ? previous : "GLOBAL";

  $("#map-empty").style.display = markets.length ? "none" : "flex";
  $("#market-map-note").textContent = markets.length ? `${markets.length} mercado${markets.length === 1 ? "" : "s"} con datos` : "sin datos";
  const markers = $("#map-markers");
  markers.replaceChildren();
  const coords = { CL: [29, 67], MX: [24, 39], AR: [34, 77], CO: [30, 53], BR: [42, 63], US: [25, 29], ES: [53, 38] };
  markets.forEach((code) => {
    if (!coords[code]) return;
    const [left, top] = coords[code];
    const dot = element("span", "map-marker");
    dot.style.left = `${left}%`;
    dot.style.top = `${top}%`;
    dot.title = known.get(code) || code;
    markers.append(dot);
  });
}

function renderMetrics(payload) {
  setMetricsAttribution("Last.fm");
  const rows = payload.items || [];
  const tbody = $("#metrics-rows");
  tbody.replaceChildren();
  $("#metrics-empty").style.display = rows.length ? "none" : "block";
  $("#metrics-empty .empty-checklist").style.display = "flex";
  $("#metrics-empty-title").textContent = rows.length ? "" : "Sin métricas capturadas";
  $("#metrics-empty-copy").textContent = rows.length
    ? ""
    : "Last.fm aún no tiene API key: verifica el correo de la cuenta, crea la clave gratuita y comprueba la conexión antes de capturar charts.";
  const playcountLabel = payload.playcount_label === "not_available"
    ? payload.market_code === "GLOBAL"
      ? "Last.fm reproducciones · período no especificado"
      : "Last.fm reproducciones · última semana"
    : payload.playcount_label || "REPRODUCCIONES LAST.FM";
  $("#playcount-heading").textContent = playcountLabel;
  $("#signal-heading").textContent = "PUESTO";
  $("#rank-change-heading").textContent = payload.rank_change_label || "Δ PUESTO";
  $("#metrics-title").textContent = "Charts y cambio de puesto";
  $("#track-count").textContent = String(rows.length).padStart(2, "0");
  $("#chart-market-note").textContent = `Last.fm · ${payload.market_code || "GLOBAL"}`;
  $("#metrics-description").textContent = payload.market_code === "GLOBAL"
    ? "Puesto y reproducciones informadas por Last.fm; la ventana de reproducciones global no está especificada por el proveedor."
    : "Chart de Last.fm por país para la última semana; las reproducciones no representan streams de Spotify.";

  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.append(element("td", "score-value", `#${fmtNumber(row.position)}`));
    const trackCell = element("td", "track-cell");
    const link = element("a", "track-link", safeText(row.title, "Título no informado"));
    try {
      const trackUrl = new URL(row.lastfm_url);
      if (["www.last.fm", "last.fm"].includes(trackUrl.hostname)) {
        link.href = trackUrl.href;
        link.target = "_blank";
        link.rel = "noreferrer";
      }
    } catch { /* Keep the label unlinked if a source URL is malformed. */ }
    trackCell.append(link);
    trackCell.append(element("small", "", safeText(row.artist, "Artista no informado")));
    tr.append(trackCell);
    tr.append(element("td", "", fmtNumber(row.playcount)));
    const movement = row.rank_change;
    const movementText = movement === null || movement === undefined
      ? "— · sin base"
      : movement > 0
        ? `+${fmtNumber(movement)} ↑`
        : movement < 0
          ? `${fmtNumber(movement)} ↓`
          : "0 · igual";
    tr.append(element("td", movement > 0 ? "movement-up" : movement < 0 ? "movement-down" : "", movementText));
    tr.append(element("td", "", fmtDate(row.captured_at)));
    tbody.append(tr);
  }
  $("#metrics-footer").textContent = rows.length
    ? `${rows.length} pistas · captura ${fmtDate(payload.captured_at)} · ${payload.chart_window === "last_week" ? "chart semanal Last.fm" : "ventana global no especificada"}`
    : "Sin capturas de charts";
}

const metricLabels = {
  chart_position: "Puesto de chart",
  lastfm_chart_rank: "Puesto de chart Last.fm",
  lastfm_chart_playcount: "Reproducciones reportadas por Last.fm",
  weekly_growth_percent_spotify_plays: "Cambio semanal de reproducciones Spotify",
  weekly_growth_percent_tiktok_posts: "Cambio semanal de publicaciones TikTok",
  weekly_growth_percent_youtube_views: "Cambio semanal de vistas YouTube",
  weekly_growth_percent_shazam_count: "Cambio semanal de Shazam",
};

function renderProviderSnapshots(payload) {
  setMetricsAttribution(payload.source || "");
  const rows = payload.items || [];
  const tbody = $("#metrics-rows");
  tbody.replaceChildren();
  $("#metrics-empty").style.display = rows.length ? "none" : "block";
  $("#metrics-empty-title").textContent = rows.length ? "" : `Sin capturas de ${payload.source || "esta fuente"}`;
  $("#metrics-empty-copy").textContent = rows.length
    ? ""
    : "Las métricas aparecen después de conectar la API, confirmar los derechos aplicables y ejecutar una captura autorizada.";
  $("#metrics-empty .empty-checklist").style.display = "none";
  $("#metrics-title").textContent = "Observaciones por pista";
  $("#metrics-description").textContent = "Valor, unidad, fuente y fecha observada se muestran como los reporta el proveedor; el cambio compara capturas locales.";
  $("#signal-heading").textContent = "SEÑAL";
  $("#playcount-heading").textContent = "VALOR / UNIDAD";
  $("#rank-change-heading").textContent = "Δ VS PREVIA";
  $("#track-count").textContent = String(rows.length).padStart(2, "0");
  $("#chart-market-note").textContent = `${payload.source || "Proveedor"} · ${payload.market_code || "GLOBAL"}`;

  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.append(element("td", "", metricLabels[row.metric_code] || row.metric_code));
    const trackCell = element("td", "track-cell");
    trackCell.append(element("strong", "", safeText(row.title, "Título no informado")));
    trackCell.append(element("small", "", safeText(row.artist, "Artista no informado")));
    tr.append(trackCell);
    const unit = row.unit === "provider_weekly_diff_percent" ? "% semanal del proveedor" : safeText(row.unit);
    tr.append(element("td", "", `${fmtNumber(row.value)} · ${unit}`));
    const change = row.change === null || row.change === undefined ? "— · sin captura previa" : `${row.change > 0 ? "+" : ""}${Number(row.change).toLocaleString("es-CL", { maximumFractionDigits: 2 })}`;
    tr.append(element("td", "", change));
    tr.append(element("td", "", fmtDate(row.captured_at)));
    tbody.append(tr);
  }
  $("#metrics-footer").textContent = rows.length
    ? `${rows.length} observaciones · captura ${fmtDate(payload.captured_at)} · datos de ${payload.source}`
    : "Sin observaciones guardadas para esta fuente";
}

const signalStateLabels = {
  WAITING_FOR_AUTHORIZED_DATA: "ESPERANDO FUENTES",
  BLOCKED: "SEÑALES BLOQUEADAS",
  REVIEW_ONLY: "LISTO PARA REVISIÓN",
};
const stageStateLabels = { PASS: "OK", READY: "LISTO", WAITING: "EN ESPERA", BLOCKED: "BLOQUEADO" };
const metricSignalLabels = {
  lastfm_chart_rank: "Puesto de chart Last.fm",
  lastfm_chart_playcount: "Reproducciones Last.fm",
  chart_position: "Puesto de chart",
  weekly_growth_percent_spotify_plays: "Crecimiento semanal Spotify",
  weekly_growth_percent_tiktok_posts: "Crecimiento semanal TikTok",
  weekly_growth_percent_youtube_views: "Crecimiento semanal YouTube",
  weekly_growth_percent_shazam_count: "Crecimiento semanal Shazam",
};

function renderSignalOrder(payload) {
  const summary = payload.summary || {};
  const state = payload.publication_state || "WAITING_FOR_AUTHORIZED_DATA";
  const stateNode = $("#signal-order-state");
  stateNode.textContent = signalStateLabels[state] || state;
  stateNode.className = `neutral-pill signal-state-${state.toLowerCase()}`;

  const stages = $("#signal-order-stages");
  stages.replaceChildren();
  for (const stage of payload.stages || []) {
    const card = element("article", `signal-stage stage-${String(stage.state || "waiting").toLowerCase()}`);
    const header = element("div", "signal-stage-head");
    header.append(element("span", "signal-stage-number", String(stage.number).padStart(2, "0")));
    header.append(element("span", "signal-stage-state", stageStateLabels[stage.state] || stage.state));
    card.append(header);
    card.append(element("strong", "", stage.name));
    card.append(element("p", "", stage.detail));
    stages.append(card);
  }

  const eligible = Number(summary.eligible_for_review || 0);
  const found = Number(summary.latest_signals_found || 0);
  const blocked = Number(summary.blocked_by_provider || 0) + Number(summary.blocked_by_rights || 0) + Number(summary.blocked_by_measurement || 0);
  $("#signal-order-count").textContent = fmtNumber(eligible);
  $("#signal-order-summary-copy").textContent = eligible ? "señales ordenadas para revisión" : "señales aptas para revisión";
  $("#signal-order-spotify-state").textContent = found
    ? `${fmtNumber(blocked)} bloqueadas · ${fmtNumber(summary.spotify_catalog_match_pending || 0)} sin identidad canónica · Spotify no recibe cambios.`
    : "Spotify: publicación detenida; se requieren proveedores autorizados y datos válidos.";

  const cohortsRoot = $("#signal-order-cohorts");
  cohortsRoot.replaceChildren();
  const cohorts = payload.cohorts || [];
  if (!cohorts.length) {
    const empty = element("div", "signal-order-empty");
    empty.textContent = found
      ? `Hay ${fmtNumber(found)} señales guardadas, pero no pasan los filtros actuales de conexión, derechos e integridad.`
      : "La cola se poblará cuando haya observaciones de proveedores autorizados.";
    cohortsRoot.append(empty);
  }
  for (const cohort of cohorts) {
    const article = element("article", "signal-cohort");
    const heading = element("div", "signal-cohort-heading");
    const title = metricSignalLabels[cohort.metric_code] || cohort.metric_code;
    heading.append(element("strong", "", `${cohort.provider === "Soundcharts" ? "Powered by Soundcharts · " : ""}${cohort.provider} · ${title}`));
    const context = [cohort.platform, cohort.market_code, cohort.unit].filter(Boolean).join(" · ");
    heading.append(element("small", "", `${context} · captura ${fmtDate(cohort.captured_at)}`));
    const direction = cohort.comparator === "lower_is_stronger"
      ? "menor puesto primero"
      : cohort.comparator === "higher_is_stronger"
        ? "mayor valor primero"
        : "sin comparador configurado";
    heading.append(element("span", "signal-comparator", direction));
    article.append(heading);

    const tableWrap = element("div", "signal-cohort-table-wrap");
    const table = document.createElement("table");
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const label of ["ORDEN", "PISTA / ARTISTA", "SEÑAL", "CAMBIO DE PUESTO", "OBSERVADA", "EVIDENCIA"]) headRow.append(element("th", "", label));
    thead.append(headRow);
    table.append(thead);
    const tbody = document.createElement("tbody");
    for (const signal of cohort.signals || []) {
      const tr = document.createElement("tr");
      tr.append(element("td", "signal-position", `#${signal.position_in_cohort}`));
      const track = element("td", "signal-track");
      track.append(element("strong", "", safeText(signal.title, "Título no informado")));
      track.append(element("small", "", safeText(signal.artist, "Artista no informado")));
      tr.append(track);
      const number = Number(signal.value).toLocaleString("es-CL", { maximumFractionDigits: 2 });
      tr.append(element("td", "", `${number} · ${signal.unit}`));
      let movement = "—";
      if (signal.movement_state === "IMPROVED") movement = `↑ ${fmtNumber(signal.movement_places)} puestos`;
      else if (signal.movement_state === "DECLINED") movement = `↓ ${fmtNumber(Math.abs(signal.movement_places))} puestos`;
      else if (signal.movement_state === "UNCHANGED") movement = "Sin cambio";
      else if (signal.movement_state === "HISTORY_PENDING") movement = "Esperando 2.ª captura";
      else if (signal.movement_state === "SAME_SOURCE_DATE") movement = "Misma fecha de chart";
      tr.append(element("td", "", movement));
      tr.append(element("td", "", fmtDate(signal.observed_at)));
      const evidence = signal.confidence === null && signal.coverage === null
        ? "No reportadas"
        : `Conf. ${signal.confidence === null ? "—" : `${Math.round(signal.confidence * 100)}%`} · Cob. ${signal.coverage === null ? "—" : `${Math.round(signal.coverage * 100)}%`}`;
      tr.append(element("td", "", evidence));
      tbody.append(tr);
    }
    table.append(tbody);
    tableWrap.append(table);
    article.append(tableWrap);
    cohortsRoot.append(article);
  }

  const method = payload.method || {};
  $("#signal-order-method").textContent = `${method.rank_direction || ""} ${method.growth_direction || ""} ${method.cross_source || ""} ${method.cohort_sampling || ""} ${method.movement || ""} ${payload.scan_truncated ? `Se alcanzó el límite de lectura (${fmtNumber(payload.scan_limit)} filas).` : ""}`.trim();
}

const providerConnectionLabels = {
  CONNECTED: "API conectada",
  DEGRADED: "Conexión degradada",
  PROVIDER_NOT_CONNECTED: "API sin conectar",
  LICENSE_SCOPE_REQUIRED: "Derechos por configurar",
  CONFIGURED_UNVERIFIED: "Configuración sin verificar",
  CONNECTED_UNVERIFIED_RIGHTS: "Conectada · derechos pendientes",
};

function safeProviderSourceLink(value, provider) {
  try {
    const url = new URL(value);
    const allowed = provider === "Soundcharts" ? ["soundcharts.com"] : ["chartmetric.com"];
    return url.protocol === "https:" && allowed.some((domain) => url.hostname === domain || url.hostname.endsWith(`.${domain}`)) ? url.href : null;
  } catch { return null; }
}

function renderProviderComparison(payload) {
  const summary = payload.summary || {};
  const state = $("#provider-comparison-state");
  const blocked = payload.state !== "REVIEW_AVAILABLE";
  state.textContent = blocked ? "ACCESO / DERECHOS PENDIENTES" : "REVISIÓN PARALELA ACTIVA";
  state.className = `neutral-pill ${blocked ? "provider-review-blocked" : "provider-review-ready"}`;
  $("#provider-comparison-paired").textContent = fmtNumber(summary.paired_track_market_groups || 0);
  const available = summary.available_signals_by_provider || {};
  const unmatched = summary.unmatched_groups_by_provider || {};
  $("#provider-comparison-summary-copy").textContent = `${fmtNumber(summary.fresh_paired_groups_within_72h || 0)} capturas por ambos proveedores ≤72 h · ${fmtNumber(summary.exact_text_matches_requiring_review || 0)} coincidencias textuales que requieren revisión · disponibles: Soundcharts ${fmtNumber(available.Soundcharts || 0)} / Chartmetric ${fmtNumber(available.Chartmetric || 0)} · sin pareja: ${fmtNumber(unmatched.Soundcharts || 0)} / ${fmtNumber(unmatched.Chartmetric || 0)}`;

  const providerRoot = $("#provider-comparison-providers");
  providerRoot.replaceChildren();
  for (const name of ["Soundcharts", "Chartmetric"]) {
    const info = payload.providers?.[name] || {};
    const article = element("article", `provider-review-card ${info.usable_for_comparison ? "provider-review-connected" : "provider-review-needs-action"}`);
    const head = element("div", "provider-review-card-head");
    head.append(element("strong", "", name));
    head.append(element("span", "provider-review-status", providerConnectionLabels[info.state] || safeText(info.state, "Estado desconocido")));
    article.append(head);
    const accessParts = [
      `derechos ${info.rights_configured ? "configurados" : "pendientes"}`,
      `modo gratuito ${info.free_access_active ? "activo" : "inactivo"}`,
      `${fmtNumber(available[name] || 0)} señales autorizadas`,
    ];
    article.append(element("p", "provider-review-details", accessParts.join(" · ")));
    if (info.setup_action) article.append(element("p", "provider-review-action", info.setup_action));
    if (info.freshness_note) article.append(element("p", "provider-review-details", info.freshness_note));
    const markets = (info.markets_reported || []).slice(0, 10).join(" · ");
    const metrics = (info.metrics_reported || []).slice(0, 3).join(" · ");
    if (markets || metrics) article.append(element("small", "provider-review-meta", `Cobertura declarada: ${markets || "no informada"} · métricas: ${metrics || "no informadas"}${info.last_refresh ? ` · última conexión ${fmtDate(info.last_refresh)}` : ""}`));
    providerRoot.append(article);
  }

  const root = $("#provider-comparison-groups");
  root.replaceChildren();
  const groups = payload.groups || [];
  if (!groups.length) {
    const reasons = Object.entries(payload.providers || {}).flatMap(([name, info]) =>
      (info.blockers || []).map((reason) => `${name}: ${reason.replaceAll("_", " ").toLowerCase()}`));
    root.append(element("div", "provider-comparison-empty", reasons.length
      ? `Todavía no hay revisión simultánea. ${reasons.join(" · ")}`
      : "No hay pistas con identidad y observaciones válidas para revisar."));
    return;
  }

  const stateLabels = {
    PARALLEL_REVIEW: "AMBAS FUENTES · REVISIÓN HUMANA",
    ONLY_SOUNDCHARTS: "SOLO SOUNDCHARTS · SIN PAREJA",
    ONLY_CHARTMETRIC: "SOLO CHARTMETRIC · SIN PAREJA",
  };
  for (const group of groups) {
    const card = element("article", "provider-comparison-track");
    const header = element("div", "provider-comparison-track-head");
    const identity = element("div", "provider-comparison-identity");
    identity.append(element("strong", "", safeText(group.title, "Título no informado")));
    identity.append(element("small", "", safeText(group.artist, "Artista no informado")));
    header.append(identity);
    const identityLabel = group.identity_type === "CANONICAL_ID" ? "ID canónico común" : "Título + artista exactos · confirmar identidad";
    const badges = element("div", "provider-comparison-badges");
    badges.append(element("span", "provider-comparison-identity-badge", identityLabel));
    badges.append(element("span", "provider-comparison-match-badge", stateLabels[group.review_state] || group.review_state));
    header.append(badges);
    card.append(header);
    card.append(element("small", "provider-comparison-context", `${safeText(group.platform)} · ${safeText(group.market_code)} · captura local más reciente ${fmtDate(group.captured_at)}`));

    const sides = element("div", "provider-comparison-sides");
    for (const name of ["Soundcharts", "Chartmetric"]) {
      const side = element("section", `provider-comparison-side ${group.providers?.[name]?.length ? "has-provider-signal" : "missing-provider-signal"}`);
      side.append(element("h4", "", name === "Soundcharts" ? "Powered by Soundcharts" : name));
      const signals = group.providers?.[name] || [];
      if (!signals.length) {
        side.append(element("p", "provider-comparison-no-signal", "Sin observación coincidente para este mercado y plataforma."));
      }
      for (const signal of signals.slice(0, 5)) {
        const metric = element("div", "provider-comparison-metric");
        const value = Number(signal.value).toLocaleString("es-CL", { maximumFractionDigits: 2 });
        metric.append(element("strong", "", `${signal.metric_label}: ${value} ${signal.unit}`));
        const age = signal.freshness_state === "CAPTURE_WITHIN_72H"
          ? `capturada hace ${fmtNumber(signal.capture_age_hours)} h`
          : signal.freshness_state === "CAPTURE_OLDER_THAN_72H" ? `captura antigua · ${fmtNumber(signal.capture_age_hours)} h` : "marca de tiempo futura · revisar";
        const evidence = signal.confidence === null && signal.coverage === null
          ? "confianza/cobertura no informadas"
          : `confianza ${signal.confidence === null ? "—" : `${Math.round(signal.confidence * 100)}%`} · cobertura ${signal.coverage === null ? "—" : `${Math.round(signal.coverage * 100)}%`}`;
        metric.append(element("small", "", `${age} · observada ${fmtDate(signal.observed_at)} · ${evidence}`));
        const href = safeProviderSourceLink(signal.source_url, name);
        if (href) {
          const link = element("a", "provider-comparison-source", "Fuente ↗");
          link.href = href;
          link.target = "_blank";
          link.rel = "noreferrer";
          metric.append(link);
        }
        side.append(metric);
      }
      if (signals.length > 5) side.append(element("small", "provider-review-meta", `${fmtNumber(signals.length - 5)} métricas más en el grupo.`));
      sides.append(side);
    }
    card.append(sides);
    card.append(element("p", "provider-comparison-caution", "Lectura paralela; los valores no son equivalentes por defecto y no se restan ni promedian."));
    root.append(card);
  }
}

const playlistDrafts = new Map();
const playlistTypeLabels = {
  ELITE: "ÉLITE", NOW: "AHORA", RISING: "EN ASCENSO", BREAKOUT: "RUPTURA",
  DISCOVERY: "DESCUBRIMIENTO", NEXT: "PRÓXIMOS", NEW_MUSIC: "NUEVA MÚSICA",
};
const profileVectorLabels = {
  "0_7_days": "0–7 días", "8_30_days": "8–30 días", "31_90_days": "31–90 días",
  energy_1: "Energía 1", energy_2: "Energía 2", energy_3: "Energía 3", energy_4: "Energía 4", energy_5: "Energía 5",
};
const playlistBlockerLabels = {
  no_authorized_metrics_provider_connected: "Conecta al menos un proveedor autorizado.",
  no_source_backed_playlist_scores: "Aún no hay scores de playlist respaldados por observaciones.",
  score_provider_not_connected_and_verified: "El proveedor de las señales no está conectado y verificado.",
  authorized_source_observation_not_found: "Falta observación con derechos configurados.",
  source_evidence_or_score_is_stale: "La evidencia o el score superó 72 horas.",
  source_evidence_or_score_has_future_timestamp: "La fecha de la evidencia requiere revisión.",
  score_not_ready_for_public_playlist_ranking: "El score todavía no está listo para selección pública.",
  score_confidence_or_coverage_below_engine_gate: "Confianza o cobertura bajo el mínimo del motor.",
  canonical_track_identity_not_resolved: "No se pudo resolver la identidad canónica de la pista.",
  spotify_catalog_identity_not_linked: "Falta una identidad exacta de pista de Spotify.",
  playlist_genre_fit_not_resolved: "Falta evidencia de género para medir el encaje editorial.",
  public_ranker_evidence_below_gate: "La evidencia combinada no supera el umbral del ranker.",
  canonical_primary_artist_not_resolved: "Falta identidad canónica del artista principal.",
  already_in_playlist: "La pista ya forma parte de la playlist.",
};
const rankComponentLabels = {
  playlist_fit: "encaje", trend: "tendencia", breakout: "ruptura", elite: "élite",
  next: "emergente", freshness: "frescura", market_affinity: "mercado", authenticity: "autenticidad",
};

function vectorSummary(vector, labels = {}) {
  const entries = Object.entries(vector || {}).sort((a, b) => b[1] - a[1]).slice(0, 4);
  return entries.length ? entries.map(([key, value]) => `${labels[key] || profileVectorLabels[key] || key.replace(/[-_]/g, " ")} ${Math.round(Number(value) * 100)}%`).join(" · ") : "Sin datos configurados";
}

function renderPlaylistDraft(card, draft) {
  const output = card.querySelector(".playlist-draft-output");
  output.replaceChildren();
  output.append(element("div", `playlist-draft-state playlist-state-${String(draft.status || "waiting").toLowerCase()}`, `${draft.status || "SIN ESTADO"} · ${fmtNumber(draft.candidate_count || 0)} candidatos en borrador · ${fmtNumber(draft.turnover_budget_max || 0)} posiciones como máximo · ranker ${draft.candidates?.[0]?.algorithm_version || "v2.0"}`));
  const composition = draft.portfolio_composition;
  if (composition) {
    const targets = Object.entries(composition.market_slot_targets || {}).map(([market, count]) => `${market} ${fmtNumber(count)}`).join(" · ");
    const selected = Object.entries(composition.selected_by_market || {}).map(([market, count]) => `${market} ${fmtNumber(count)}`).join(" · ");
    output.append(element("p", "playlist-draft-empty", `Mix objetivo por mercado: ${targets || "sin cuotas configuradas"}. Selección: ${selected || "sin candidatos"}. ${fmtNumber(composition.unfilled_slots || 0)} espacios sin cubrir; se conservan vacíos si la evidencia no alcanza.`));
  }
  const candidates = draft.candidates || [];
  if (candidates.length) {
    const list = element("ol", "playlist-candidate-list");
    for (const candidate of candidates.slice(0, 8)) {
      const item = element("li", "playlist-candidate");
      const identity = element("span", "playlist-candidate-identity");
      identity.append(element("strong", "", safeText(candidate.title, "Título no informado")));
      identity.append(element("small", "", safeText(candidate.artist, "Artista no informado")));
      item.append(identity);
      const scoreLabel = candidate.ranker_complete ? "score" : "score parcial";
      const soundchartsAttribution = (candidate.sources || []).includes("Soundcharts") ? " · Powered by Soundcharts" : "";
      item.append(element("span", "playlist-candidate-score", `${scoreLabel} ${Number(candidate.public_playlist_score).toLocaleString("es-CL", { maximumFractionDigits: 1 })} · confianza ${Math.round(Number(candidate.score_confidence) * 100)}%${soundchartsAttribution}`));
      const sourceNames = (candidate.sources || []).join(" + ") || "fuente sin etiqueta";
      const reasons = Object.entries(candidate.component_contributions || {})
        .sort((a, b) => Number(b[1].weighted_contribution) - Number(a[1].weighted_contribution))
        .slice(0, 3)
        .map(([key, contribution]) => `${rankComponentLabels[key] || key} +${Number(contribution.weighted_contribution).toLocaleString("es-CL", { maximumFractionDigits: 1 })}`)
        .join(" · ");
      const missing = (candidate.unavailable_dimensions || []).length ? ` · incompleto: ${candidate.unavailable_dimensions.map((key) => rankComponentLabels[key] || key).join(", ")}` : "";
      const allocation = candidate.portfolio_allocation === "MARKET_TARGET" ? "cuota de mercado" : "relleno por ranking";
      item.append(element("span", "playlist-candidate-note", `${allocation} · ${candidate.market_code} · ${sourceNames}${reasons ? ` · aportes: ${reasons}` : ""}${missing} · requiere aprobación`));
      list.append(item);
    }
    output.append(list);
  } else {
    const blockers = (draft.blockers || []).map((blocker) => {
      const label = playlistBlockerLabels[blocker.reason] || blocker.reason.replaceAll("_", " ");
      return `${label}${Number(blocker.count) > 1 ? ` (${fmtNumber(blocker.count)})` : ""}`;
    });
    output.append(element("p", "playlist-draft-empty", blockers.slice(0, 3).join(" ") || "Sin candidatos que superen todas las puertas actuales."));
  }
}

function safeExternalLink(value, hostname) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && (url.hostname === hostname || url.hostname === `www.${hostname}`) ? url.href : null;
  } catch { return null; }
}

function renderLastFmReview(card, payload) {
  const output = card.querySelector(".playlist-source-review-output");
  output.replaceChildren();
  const rows = payload.candidates || [];
  const captureRange = payload.oldest_captured_at && payload.captured_at && payload.oldest_captured_at !== payload.captured_at
    ? `${fmtDate(payload.oldest_captured_at)} a ${fmtDate(payload.captured_at)}`
    : fmtDate(payload.captured_at);
  output.append(element("div", "playlist-review-state", `${payload.status === "NO_RECENT_CHARTS" ? "SIN CAPTURAS RECIENTES" : "SEÑALES DE CHART · REVISIÓN MANUAL"} · ${fmtNumber(payload.review_ready_count || 0)} coincidencias exactas con género Last.fm · capturas ${captureRange}`));
  output.append(element("p", "playlist-review-method", payload.method || "Sin método disponible."));
  if (!rows.length) {
    output.append(element("p", "playlist-draft-empty", "No hay charts recientes en los mercados objetivo para esta revisión."));
    return;
  }
  const list = element("ol", "playlist-review-list");
  for (const row of rows) {
    const item = element("li", "playlist-review-item");
    const heading = element("div", "playlist-review-heading");
    const identity = element("span", "playlist-candidate-identity");
    identity.append(element("strong", "", safeText(row.title, "Título no informado")));
    identity.append(element("small", "", safeText(row.artist, "Artista no informado")));
    heading.append(identity);
    const stateLabels = {
      REVIEW_READY: "COINCIDENCIA EXACTA · REVISAR",
      GENRE_UNCONFIRMED: "GÉNERO SIN CONFIRMAR",
      SPOTIFY_MATCH_AMBIGUOUS: "VARIAS COINCIDENCIAS SPOTIFY",
      SPOTIFY_MATCH_NOT_EXACT: "COINCIDENCIA SPOTIFY NO VERIFICADA",
    };
    heading.append(element("span", "playlist-review-tag", stateLabels[row.spotify_match_state] || row.spotify_match_state));
    item.append(heading);
    const soundchartsPositions = Object.entries(row.soundcharts_markets || {}).map(([market, value]) => {
      const movement = value.movement_places === null || value.movement_places === undefined
        ? ""
        : value.movement_places > 0 ? ` ↑${fmtNumber(value.movement_places)}`
          : value.movement_places < 0 ? ` ↓${fmtNumber(Math.abs(value.movement_places))}` : " =";
      return `${market} #${fmtNumber(value.position)}${movement}`;
    }).join(" · ");
    const lastfmPositions = Object.entries(row.markets || {}).map(([market, value]) => `${market} #${fmtNumber(value.position)}`).join(" · ");
    const sourceLines = [];
    if (soundchartsPositions) sourceLines.push(`Soundcharts ${soundchartsPositions}`);
    if (lastfmPositions) sourceLines.push(`Last.fm ${lastfmPositions}`);
    item.append(element("p", "playlist-review-data", `${sourceLines.join(" · ")} · aparece en ${fmtNumber(row.market_coverage_count)} mercado(s)`));
    if (soundchartsPositions) item.append(element("p", "playlist-review-data", "Powered by Soundcharts · puesto del chart de Spotify, no vistas de playlist"));
    if (row.provider_join_basis === "normalized_title_and_artist") item.append(element("p", "playlist-review-data", "Las fuentes se cruzan por título y artista normalizados; la identidad de grabación puede seguir pendiente."));
    if (row.spotify_match) {
      const spotifyUrl = safeExternalLink(row.spotify_match.url, "open.spotify.com");
      const spotifyLine = element("p", "playlist-review-data");
      spotifyLine.append(document.createTextNode(`Spotify: ${row.spotify_match.name} · ${row.spotify_match.artist} `));
      if (spotifyUrl) {
        const link = element("a", "playlist-review-link", "Abrir en Spotify ↗");
        link.href = spotifyUrl;
        link.target = "_blank";
        link.rel = "noreferrer";
        spotifyLine.append(link);
      }
      item.append(spotifyLine);
    } else if (row.spotify_match_state === "SPOTIFY_MATCH_AMBIGUOUS") {
      item.append(element("p", "playlist-review-data", `${fmtNumber(row.ambiguous_spotify_match_count + 1)} resultados requieren elección manual en Spotify.`));
    } else {
      item.append(element("p", "playlist-review-data", "No se presenta como coincidencia confirmada. Verifica título y artista en Spotify."));
    }
    item.append(element("p", "playlist-review-data", `Etiquetas Last.fm: ${(row.lastfm_tags || []).slice(0, 5).join(" · ") || "sin etiquetas"}${row.matched_playlist_genres?.length ? ` · encaje por etiqueta: ${row.matched_playlist_genres.join(", ")}` : " · sin etiqueta que coincida con el género objetivo"}`));
    const lastfmUrl = safeExternalLink(row.lastfm_url, "last.fm");
    if (lastfmUrl) {
      const attribution = element("a", "playlist-review-link", "Abrir ficha Last.fm ↗");
      attribution.href = lastfmUrl;
      attribution.target = "_blank";
      attribution.rel = "noreferrer";
      item.append(attribution);
    }
    const soundchartsUrl = safeProviderSourceLink(row.soundcharts_url, "Soundcharts");
    if (soundchartsUrl && soundchartsPositions) {
      const attribution = element("a", "playlist-review-link", "Abrir fuente Soundcharts ↗");
      attribution.href = soundchartsUrl;
      attribution.target = "_blank";
      attribution.rel = "noreferrer";
      item.append(attribution);
    }
    list.append(item);
  }
  output.append(list);
}

function renderPublicPlaylists(payload) {
  const items = payload.items || [];
  const root = $("#public-playlist-cards");
  root.replaceChildren();
  $("#public-playlist-count").textContent = `${fmtNumber(payload.registered_count || 0)} / ${fmtNumber(payload.playlist_count || 0)}`;
  $("#public-playlist-data-state").textContent = payload.data_state === "DATA_NOT_AVAILABLE"
    ? "Proveedores de métricas sin conexión; las reglas editoriales están cargadas y el motor mantendrá las listas intactas."
    : "Perfiles listos · esperando scores de mercado que superen los controles de fuente y calidad.";

  for (const playlist of items) {
    const card = element("article", "public-playlist-card");
    card.dataset.playlistSlug = playlist.slug;
    const head = element("div", "public-playlist-card-head");
    const titleBlock = element("div", "public-playlist-title-block");
    titleBlock.append(element("h3", "", playlist.name));
    const subline = element("div", "public-playlist-subline");
    subline.append(element("span", "playlist-type-tag", playlistTypeLabels[playlist.playlist_type] || playlist.playlist_type));
    subline.append(element("span", "playlist-registration", playlist.registered ? "REGISTRADA EN SPOTIFY" : "SIN REGISTRO"));
    titleBlock.append(subline);
    head.append(titleBlock);
    const actions = element("div", "public-playlist-actions");
    const generate = element("button", "action-button action-button-small", "Generar borrador");
    generate.type = "button";
    generate.dataset.generatePlaylist = playlist.slug;
    actions.append(generate);
    const review = element("button", "action-button action-button-small action-button-review", "Revisar señales de tendencia");
    review.type = "button";
    review.dataset.reviewLastfmPlaylist = playlist.slug;
    actions.append(review);
    if (playlist.spotify_url) {
      const spotify = element("a", "playlist-spotify-link", "Abrir ↗");
      spotify.href = playlist.spotify_url;
      spotify.target = "_blank";
      spotify.rel = "noreferrer";
      actions.append(spotify);
    }
    head.append(actions);
    card.append(head);

    const dataGrid = element("div", "playlist-data-grid");
    const stageMix = Object.entries(playlist.playlist_dna?.artist_stage_profile || {}).map(([stage, share]) => `${stage} ${Math.round(share * 100)}%`).join(" · ") || "A la espera de clasificación autorizada";
    const turnover = playlist.playlist_dna
      ? `${Math.round(playlist.playlist_dna.turnover_min * 100)}–${Math.round(playlist.playlist_dna.turnover_max * 100)}% por ciclo`
      : "Sin configurar";
    const refresh = playlist.playlist_dna?.refresh_interval_hours_min
      ? `${playlist.playlist_dna.refresh_interval_hours_min}–${playlist.playlist_dna.refresh_interval_hours_max} h`
      : "Revisión por calendario y evidencia";
    const facts = [
      ["PISTAS", `${fmtNumber(playlist.current_track_count || playlist.configured_tracks)} actuales · meta ${fmtNumber(playlist.target_tracks)}`],
      ["MERCADOS", (playlist.markets || []).join(" · ") || "Global"],
      ["DNA DE MERCADO", vectorSummary(playlist.playlist_dna?.market_vector)],
      ["ROTACIÓN MÁXIMA", turnover],
      ["REVISIÓN", refresh],
      ["DNA DE GÉNERO", vectorSummary(playlist.playlist_dna?.genre_vector)],
      ["FRESCURA OBJETIVO", vectorSummary(playlist.playlist_dna?.freshness_profile)],
      ["BALANCE MAINSTREAM / DESCUBRIMIENTO", `${Math.round(Number(playlist.playlist_dna?.mainstream_discovery_balance ?? 0.5) * 100)}% descubrimiento`],
      ["ETAPA ARTÍSTICA", stageMix],
      ["MIX EDITORIAL", `Ancla ${playlist.priority_mix.anchor} · crecimiento ${playlist.priority_mix.growth} · descubrimiento ${playlist.priority_mix.discovery}`],
      ["RETRIEVERS", (playlist.retrievers || []).map((name) => name.replace("Retriever", "")).join(" · ") || "Pendiente de datos"],
      ["ENERGÍA", vectorSummary(playlist.playlist_dna?.energy_profile)],
      ["AUTORIDAD / SALUD", `${playlist.lifecycle_state || "PLANNED"} · ${playlist.lifecycle_signals_state || "DATA_NOT_AVAILABLE"}`],
    ];
    for (const [label, value] of facts) {
      const fact = element("div", "playlist-fact");
      fact.append(element("span", "", label), element("strong", "", value));
      dataGrid.append(fact);
    }
    card.append(dataGrid);
    const output = element("div", "playlist-draft-output");
    if (playlistDrafts.has(playlist.slug)) renderPlaylistDraft({ querySelector: () => output }, playlistDrafts.get(playlist.slug));
    else output.append(element("p", "playlist-draft-empty", "El contenido actual se conserva. Genera un borrador cuando existan señales autorizadas; no hay edición automática en Spotify."));
    card.append(output);
    const reviewOutput = element("div", "playlist-source-review-output");
    card.append(reviewOutput);
    root.append(card);
  }
}

const blueprintBlockerLabels = {
  authorized_trend_provider_not_connected: "Falta un proveedor autorizado de tendencias (Soundcharts o Chartmetric).",
  candidate_track_artist_catalog_empty: "El catálogo de candidatos y artistas está vacío.",
  playlist_quality_and_artist_diversity_not_evaluated: "Calidad y diversidad de artistas aún no evaluadas.",
  distinctiveness_against_existing_playlists_not_evaluated: "Falta comprobar que el concepto se diferencie de las listas existentes.",
  track_level_data_confidence_not_evaluated: "La confianza de los datos por pista aún no está evaluada.",
  minimum_tracks_not_met: "Se requieren al menos 30 pistas elegibles antes del lanzamiento.",
  minimum_artists_not_met: "Se requieren al menos 20 artistas distintos.",
  mean_source_confidence_below_gate: "La confianza media de las señales debe llegar a 0,55.",
  local_artist_origin_below_gate: "La selección necesita al menos 60% de artistas con origen local verificado.",
  insufficient_market_diversity: "La selección necesita presencia comprobada en al menos cuatro mercados objetivo.",
  sonic_sequence_review_required: "Falta una revisión humana de la secuencia sonora para estas pistas exactas.",
  not_distinct_from_existing_playlist: "La selección se solapa demasiado con una lista existente.",
  not_distinct_from_another_concept: "La selección se solapa demasiado con otra lista propuesta.",
  release_date_outside_30_day_window: "Un estreno propuesto está fuera de la ventana de 30 días.",
  already_registered: "Esta lista ya tiene una inscripción de Spotify.",
  name_collides_with_existing_config: "El nombre coincide con otra lista configurada.",
};

let playlistBlueprintPayload = null;
let playlistBlueprintPage = 0;
const PLAYLIST_BLUEPRINT_PAGE_SIZE = 20;

function renderPlaylistBlueprints(payload) {
  playlistBlueprintPayload = payload;
  const concepts = payload.items || [];
  const query = $("#playlist-blueprint-search").value.trim().toLocaleLowerCase("es");
  const filteredConcepts = query
    ? concepts.filter((blueprint) => JSON.stringify(blueprint).toLocaleLowerCase("es").includes(query))
    : concepts;
  const pageCount = Math.max(1, Math.ceil(filteredConcepts.length / PLAYLIST_BLUEPRINT_PAGE_SIZE));
  playlistBlueprintPage = Math.min(playlistBlueprintPage, pageCount - 1);
  const pageStart = playlistBlueprintPage * PLAYLIST_BLUEPRINT_PAGE_SIZE;
  const visibleConcepts = filteredConcepts.slice(pageStart, pageStart + PLAYLIST_BLUEPRINT_PAGE_SIZE);
  const root = $("#playlist-blueprint-cards");
  root.replaceChildren();
  $("#playlist-blueprint-count").textContent = fmtNumber(payload.concept_count || concepts.length);
  $("#playlist-blueprint-state").textContent = payload.activated_concept_count
    ? `${fmtNumber(payload.activated_concept_count)} CONCEPTOS ACTIVADOS`
    : `${fmtNumber(payload.ready_concept_count || 0)} / ${fmtNumber(payload.concept_count || concepts.length)} LISTOS PARA CREAR`;
  const connected = (payload.provider_basis || []).join(" · ") || "sin Soundcharts/Chartmetric autorizado";
  const catalog = payload.candidate_catalog || {};
  $("#playlist-blueprint-data-state").textContent = `${fmtNumber(payload.existing_playlist_count || 0)} listas actuales · ${fmtNumber(payload.activated_concept_count || 0)} de estas propuestas publicadas · fuentes: ${connected} · catálogo: ${fmtNumber(catalog.tracks || 0)} pistas / ${fmtNumber(catalog.artists || 0)} artistas`;
  const firstVisible = filteredConcepts.length ? pageStart + 1 : 0;
  const lastVisible = Math.min(pageStart + PLAYLIST_BLUEPRINT_PAGE_SIZE, filteredConcepts.length);
  $("#playlist-blueprint-page").textContent = `${filteredConcepts.length ? `Página ${playlistBlueprintPage + 1}/${pageCount} · ` : ""}${firstVisible}–${lastVisible} de ${fmtNumber(filteredConcepts.length)}`;
  $("#playlist-blueprint-prev").disabled = playlistBlueprintPage <= 0;
  $("#playlist-blueprint-next").disabled = playlistBlueprintPage >= pageCount - 1;

  if (!visibleConcepts.length) {
    root.append(element("p", "provider-loading", query ? "No hay propuestas que coincidan con esa búsqueda." : "No hay propuestas disponibles."));
    return;
  }

  for (const blueprint of visibleConcepts) {
    const card = element("article", "playlist-blueprint-card");
    const head = element("div", "playlist-blueprint-card-head");
    const title = element("div", "playlist-blueprint-title");
    title.append(element("h3", "", blueprint.name));
    const tags = element("div", "playlist-blueprint-tags");
    const phaseLabel = String(blueprint.portfolio_wave || "Orden editorial")
      .replace(/^Fase\s+\d+\s*·\s*/, "")
      .toLocaleUpperCase("es");
    tags.append(element("span", "playlist-blueprint-order", `#${String(blueprint.portfolio_order || "—").padStart(2, "0")} · ${phaseLabel}`));
    tags.append(element("span", "playlist-type-tag", playlistTypeLabels[blueprint.playlist_type] || blueprint.playlist_type));
    const launchLabel = blueprint.activation_status === "ACTIVE" ? "PUBLICADA" : blueprint.activation_status === "READY_TO_LAUNCH" ? "LISTA PARA CREAR" : "PENDIENTE DE EVIDENCIA";
    tags.append(element("span", "playlist-blueprint-status", launchLabel));
    title.append(tags);
    head.append(title);
    card.append(head);
    card.append(element("p", "playlist-blueprint-description", blueprint.description));
    if (blueprint.spotify_url && blueprint.activation_status === "ACTIVE") {
      const spotifyLink = element("a", "playlist-blueprint-spotify-link", "Abrir en Spotify ↗");
      spotifyLink.href = blueprint.spotify_url;
      spotifyLink.target = "_blank";
      spotifyLink.rel = "noopener noreferrer";
      card.append(spotifyLink);
    }

    const facts = element("div", "playlist-blueprint-facts");
    const factItems = [
      ["MERCADO", (blueprint.markets || []).join(" · ")],
      ["GÉNERO", vectorSummary(blueprint.genre_vector)],
      ["SUBGÉNERO", vectorSummary(blueprint.subgenre_vector)],
      ["HUB", blueprint.hub],
      ["ENERGÍA / IDIOMA", `${blueprint.energy} · ${blueprint.language}`],
      ["ÁNGULO", blueprint.listening_angle],
      ["REGLA DISTINTIVA", blueprint.differentiation_rule],
      ["PISTAS ELEGIBLES", fmtNumber(blueprint.candidate_count || 0)],
      ["ARTISTAS DISTINTOS", fmtNumber(blueprint.unique_artist_count || 0)],
      ["CONFIANZA MEDIA", Number(blueprint.mean_score_confidence || 0).toFixed(2)],
      ["MAYOR SOLAPAMIENTO", blueprint.highest_overlap?.playlist ? `${Math.round(100 * Number(blueprint.highest_overlap.fraction || 0))}% · ${blueprint.highest_overlap.playlist}` : "Sin comparación disponible"],
      ["OTRA PROPUESTA", blueprint.highest_concept_overlap?.playlist ? `${Math.round(100 * Number(blueprint.highest_concept_overlap.fraction || 0))}% · ${blueprint.highest_concept_overlap.playlist}` : "Sin solapamiento medido"],
    ];
    for (const [label, value] of factItems) {
      const fact = element("div", "playlist-blueprint-fact");
      fact.append(element("span", "", label), element("strong", "", safeText(value)));
      facts.append(fact);
    }
    card.append(facts);

    const brief = document.createElement("details");
    brief.className = "playlist-blueprint-brief";
    brief.append(element("summary", "", "Brief de campaña · objetivo, audiencia y medición"));
    const briefFacts = element("div", "playlist-blueprint-facts");
    for (const [label, value] of [
      ["OBJETIVO", blueprint.campaign_objective],
      ["AUDIENCIA", blueprint.target_audience],
      ["CICLO ORGÁNICO", blueprint.organic_growth_loop],
      ["CADENCIA", blueprint.review_cadence],
      ["MEDICIÓN", blueprint.measurement_plan],
    ]) {
      const fact = element("div", "playlist-blueprint-fact");
      fact.append(element("span", "", label), element("strong", "", safeText(value)));
      briefFacts.append(fact);
    }
    brief.append(briefFacts);
    card.append(brief);

    const gate = document.createElement("details");
    gate.className = "playlist-blueprint-gate";
    gate.append(element("summary", "", blueprint.activation_status === "ACTIVE" ? "Puerta de creación · superada" : blueprint.activation_status === "READY_TO_LAUNCH" ? "Puerta de creación · lista" : "Puerta de creación · evidencia pendiente"));
    const reasons = element("ul", "playlist-blueprint-blockers");
    for (const blocker of blueprint.blockers || []) {
      reasons.append(element("li", "", blueprintBlockerLabels[blocker] || blocker.replaceAll("_", " ")));
    }
    if (!reasons.children.length) reasons.append(element("li", "", "Selección y evidencia suficientes para esta fase."));
    gate.append(reasons);
    card.append(gate);
    root.append(card);
  }
}

const campaignStageLabels = {
  WAITING_FOR_AUTHORIZED_SIGNALS: "Esperando señales autorizadas",
  BLOCKED_NO_AUTHORIZED_PROVIDER: "Proveedor sin conexión",
  WAITING_FOR_QUALIFYING_EVIDENCE: "Esperando evidencia válida",
  REVISIÓN_EDITORIAL: "Revisión editorial",
  REQUIERE_EVIDENCIA_Y_APROBACIÓN: "Evidencia y aprobación",
  OBJETIVOS_PLANIFICADOS: "Objetivos planificados",
  COMPARTIR_MANUALMENTE: "Compartir manualmente",
  ENLACE_NO_REGISTRADO: "Falta enlace registrado",
  MÉTRICAS_REGISTRADAS: "Resultados registrados",
  SIN_MÉTRICAS_DE_AUDIENCIA: "Sin métricas de audiencia",
};

function localDateInputValue(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function campaignReportEditor(campaign) {
  const details = document.createElement("details");
  details.className = "campaign-report-editor";
  details.append(element("summary", "", "Registrar vistas, clics y seguidores del periodo"));
  details.append(element("p", "campaign-report-note", "Ingresa cifras observadas en las estadísticas del canal. Las vistas pertenecen a esa red; Spotify no entrega vistas de la playlist en esta app. Para el cambio de seguidores, copia el total al inicio y al cierre."));

  const form = document.createElement("form");
  form.className = "campaign-report-form";
  form.dataset.campaignReport = campaign.playlist_slug;
  form.noValidate = false;

  const field = (labelText, name, type = "number", options = {}) => {
    const label = element("label", "campaign-report-field");
    label.append(element("span", "", labelText));
    const input = document.createElement(type === "select" ? "select" : "input");
    input.name = name;
    if (type !== "select") input.type = type;
    if (options.min !== undefined) input.min = String(options.min);
    if (options.step !== undefined) input.step = String(options.step);
    if (options.max !== undefined) input.max = String(options.max);
    if (options.placeholder) input.placeholder = options.placeholder;
    if (options.value !== undefined) input.value = options.value;
    if (options.required) input.required = true;
    if (options.options) {
      for (const [value, text] of options.options) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        input.append(option);
      }
    }
    label.append(input);
    return { label, input };
  };

  const today = new Date();
  const start = new Date(today);
  start.setDate(start.getDate() - 6);
  const fields = [
    field("Canal principal", "channel", "select", { required: true, options: [
      ["Instagram Reels / Stories", "Instagram Reels / Stories"], ["TikTok", "TikTok"], ["YouTube Shorts", "YouTube Shorts"],
      ["Facebook", "Facebook"], ["X", "X"], ["Newsletter", "Newsletter"], ["Comunidad", "Comunidad"], ["Otro", "Otro"],
    ] }),
    field("Fuente vistas / clics", "source_name", "text", { required: true, value: "Analítica nativa del canal", placeholder: "Ej.: Instagram Insights" }),
    field("Inicio del periodo", "period_start", "date", { required: true, value: localDateInputValue(start) }),
    field("Cierre del periodo", "period_end", "date", { required: true, value: localDateInputValue(today) }),
    field("Vistas / impresiones del contenido", "views", "number", { min: 0, step: 1, placeholder: "Opcional" }),
    field("Alcance orgánico", "reach", "number", { min: 0, step: 1, placeholder: "Opcional" }),
    field("Clics al enlace Spotify", "link_clicks", "number", { min: 0, step: 1, placeholder: "Opcional" }),
    field("Seguidores al inicio", "followers_start", "number", { min: 0, step: 1, placeholder: "Opcional" }),
    field("Seguidores al cierre", "followers_end", "number", { min: 0, step: 1, placeholder: "Opcional" }),
    field("Fuente del conteo de seguidores", "followers_source", "text", { value: "Spotify · conteo visible", placeholder: "Ej.: ficha pública de Spotify" }),
    field("Notas / variante de contenido", "notes", "text", { placeholder: "Ej.: reel editorial, entrevista, recomendación" }),
  ];
  const grid = element("div", "campaign-report-grid");
  for (const item of fields) grid.append(item.label);
  form.append(grid);
  const actions = element("div", "campaign-report-actions");
  const submit = element("button", "action-button action-button-small", "Guardar lectura orgánica");
  submit.type = "submit";
  const status = element("span", "campaign-report-status", "Se guardará solo en esta app local.");
  status.setAttribute("role", "status");
  actions.append(submit, status);
  form.append(actions);
  details.append(form);
  return details;
}

function renderCampaignReportHistory(campaign) {
  const report = campaign.latest_report;
  const history = element("div", "campaign-report-history");
  if (!report) {
    history.append(element("p", "", "Aún no hay una línea base guardada para esta playlist."));
    return history;
  }
  const parts = [
    `${report.channel} · ${report.period_start}–${report.period_end}`,
    `${fmtNumber(report.views)} vistas/impresiones`,
    `${fmtNumber(report.link_clicks)} clics`,
  ];
  if (report.followers_delta !== null && report.followers_delta !== undefined) {
    parts.push(`${report.followers_delta >= 0 ? "+" : ""}${fmtNumber(report.followers_delta)} seguidores netos`);
  }
  history.append(element("p", "campaign-report-latest", `Última lectura · ${parts.join(" · ")} · Fuente: ${report.source_name}`));
  const reports = campaign.recent_reports || [];
  if (reports.length > 1) {
    const tableWrap = element("div", "campaign-report-table-wrap");
    const table = document.createElement("table");
    table.className = "campaign-report-table";
    const header = document.createElement("tr");
    for (const label of ["PERIODO / CANAL", "VISTAS", "ALCANCE", "CLICS", "CTR", "SEGUIDORES Δ"]) {
      header.append(element("th", "", label));
    }
    const thead = document.createElement("thead");
    thead.append(header);
    table.append(thead);
    const tbody = document.createElement("tbody");
    for (const row of reports.slice(0, 6)) {
      const tr = document.createElement("tr");
      const delta = row.followers_delta;
      const cells = [
        `${row.period_start}–${row.period_end} · ${row.channel}`,
        fmtNumber(row.views), fmtNumber(row.reach), fmtNumber(row.link_clicks),
        row.link_click_rate_percent === null ? "—" : `${row.link_click_rate_percent}%`,
        delta === null ? "—" : `${delta >= 0 ? "+" : ""}${fmtNumber(delta)}`,
      ];
      for (const value of cells) tr.append(element("td", "", value));
      tbody.append(tr);
    }
    table.append(tbody);
    tableWrap.append(table);
    history.append(tableWrap);
  }
  return history;
}

function renderCampaigns(payload) {
  const root = $("#campaign-cards");
  const campaigns = payload.items || [];
  root.replaceChildren();
  $("#campaign-count").textContent = fmtNumber(payload.campaign_count || campaigns.length);
  $("#campaign-plan-state").textContent = payload.plan_state === "PLANS_READY_EVIDENCE_REQUIRED" ? "PLANES · EVIDENCIA PENDIENTE" : safeText(payload.plan_state, "SIN ESTADO");
  $("#campaign-data-state").textContent = payload.provider_state === "DATA_NOT_AVAILABLE"
    ? "Las 10 campañas están estructuradas; la detección de tendencias y el feedback siguen bloqueados por falta de fuentes autorizadas."
    : "Planes revisables · valida las señales y la medición antes de activar cada ciclo.";

  for (const campaign of campaigns) {
    const card = element("article", "campaign-card");
    const head = element("div", "campaign-card-head");
    const title = element("div", "campaign-title");
    title.append(element("h3", "", campaign.campaign_name));
    title.append(element("span", "campaign-plan-tag", `${campaign.playlist_type || "PLAYLIST"} · ${campaign.status || "PLAN"}`));
    head.append(title);
    if (campaign.destination?.url) {
      const link = element("a", "playlist-spotify-link", "Abrir playlist ↗");
      link.href = campaign.destination.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      head.append(link);
    }
    card.append(head);
    card.append(element("p", "campaign-objective", campaign.objective));

    const markets = element("div", "campaign-market-row");
    markets.append(element("strong", "", "MERCADOS FOCO"));
    const marketNames = (campaign.market_targets || []).map((market) => `${market.market_code} · ${Math.round(Number(market.configured_weight) * 100)}%`);
    markets.append(element("span", "", marketNames.join("  /  ") || "Sin mercado configurado"));
    markets.append(element("small", "", campaign.market_target_basis));
    card.append(markets);

    const details = document.createElement("details");
    details.className = "campaign-details";
    details.append(element("summary", "", "Ver acciones, métricas y registro del ciclo"));
    const detailsBody = element("div", "campaign-details-body");

    const pipeline = element("div", "campaign-pipeline");
    for (const stage of campaign.pipeline || []) {
      const stageKey = String(stage.state || "waiting").toLowerCase().replace(/[^a-z0-9]+/g, "-");
      const stageCard = element("article", `campaign-stage campaign-stage-${stageKey}`);
      stageCard.append(element("span", "campaign-stage-name", stage.stage));
      stageCard.append(element("strong", "", campaignStageLabels[stage.state] || stage.state));
      stageCard.append(element("p", "", stage.detail));
      pipeline.append(stageCard);
    }
    detailsBody.append(pipeline);

    const schedule = element("ol", "campaign-schedule");
    for (const entry of campaign.seven_day_playbook || []) {
      const step = element("li", "campaign-schedule-step");
      step.append(element("strong", "", `DÍA ${entry.day} · ${entry.channel}`));
      step.append(element("span", "", entry.action));
      schedule.append(step);
    }
    detailsBody.append(schedule);

    const angles = element("ul", "campaign-content-angles");
    for (const angle of campaign.content_angles || []) angles.append(element("li", "", angle));
    if (angles.children.length) {
      const angleGroup = element("div", "campaign-angle-group");
      angleGroup.append(element("strong", "", "IDEAS DE CONTENIDO"), angles);
      detailsBody.append(angleGroup);
    }

    const share = element("div", "campaign-share-copy");
    share.append(element("span", "", "TEXTO SUGERIDO · REVISAR ANTES DE COMPARTIR"));
    share.append(element("p", "", campaign.share_copy));
    detailsBody.append(share);

    const metrics = element("div", "campaign-metrics");
    for (const metric of campaign.measurement || []) {
      const metricCard = element("div", "campaign-metric");
      metricCard.append(element("strong", "", metric.metric));
      metricCard.append(element("span", "", metric.value === null ? "Sin dato" : String(metric.value)));
      metricCard.append(element("small", "", metric.source_needed));
      metrics.append(metricCard);
    }
    detailsBody.append(metrics);
    detailsBody.append(renderCampaignReportHistory(campaign));
    detailsBody.append(campaignReportEditor(campaign));
    if ((campaign.blockers || []).length) {
      const blockers = element("ul", "campaign-blockers");
      for (const blocker of campaign.blockers) blockers.append(element("li", "", blocker));
      detailsBody.append(blockers);
    }
    details.append(detailsBody);
    card.append(details);
    root.append(card);
  }
}

function optionalCount(form, name) {
  const raw = form.elements[name].value.trim();
  return raw === "" ? null : Number(raw);
}

$("#campaign-cards").addEventListener("submit", async (event) => {
  const form = event.target.closest("form[data-campaign-report]");
  if (!form) return;
  event.preventDefault();
  const submit = form.querySelector("button[type='submit']");
  const status = form.querySelector(".campaign-report-status");
  submit.disabled = true;
  status.textContent = "Guardando…";
  const payload = {
    channel: form.elements.channel.value,
    source_name: form.elements.source_name.value.trim(),
    followers_source: form.elements.followers_source.value.trim() || null,
    period_start: form.elements.period_start.value,
    period_end: form.elements.period_end.value,
    views: optionalCount(form, "views"),
    reach: optionalCount(form, "reach"),
    link_clicks: optionalCount(form, "link_clicks"),
    followers_start: optionalCount(form, "followers_start"),
    followers_end: optionalCount(form, "followers_end"),
    notes: form.elements.notes.value.trim() || null,
  };
  try {
    await postJson(`/api/campaigns/${encodeURIComponent(form.dataset.campaignReport)}/report`, payload);
    const updated = await getJson("/api/campaigns");
    renderCampaigns(updated);
    const refreshedForm = document.querySelector(`[data-campaign-report="${CSS.escape(form.dataset.campaignReport)}"]`);
    if (refreshedForm) {
      refreshedForm.closest(".campaign-details").open = true;
      refreshedForm.closest(".campaign-report-editor").open = true;
      refreshedForm.querySelector(".campaign-report-status").textContent = "Informe guardado y métricas actualizadas.";
    }
  } catch (error) {
    status.textContent = `No se guardó: ${error.message}`;
    submit.disabled = false;
  }
});

async function generatePlaylistDraft(slug = null) {
  const button = slug
    ? document.querySelector(`[data-generate-playlist="${CSS.escape(slug)}"]`)
    : $("#generate-all-playlists");
  const priorLabel = button?.textContent;
  if (button) { button.disabled = true; button.textContent = "Calculando…"; }
  try {
    const path = slug ? `/api/public/playlists/${encodeURIComponent(slug)}/refresh` : "/api/public/playlists/generate";
    const result = await postJson(path);
    for (const draft of result.items || []) playlistDrafts.set(draft.slug, draft);
    const network = await getJson("/api/public/playlists");
    renderPublicPlaylists(network);
  } catch (error) {
    const state = $("#public-playlist-data-state");
    state.textContent = `No se pudo generar el borrador local: ${error.message}`;
  } finally {
    if (button) { button.disabled = false; button.textContent = priorLabel || "Generar borrador"; }
  }
}

async function reviewPlaylistWithLastFm(slug) {
  const button = document.querySelector(`[data-review-lastfm-playlist="${CSS.escape(slug)}"]`);
  const card = button?.closest(".public-playlist-card");
  const priorLabel = button?.textContent;
  if (button) { button.disabled = true; button.textContent = "Consultando…"; }
  try {
    const result = await postJson(`/api/public/playlists/${encodeURIComponent(slug)}/lastfm-review`);
    if (card) renderLastFmReview(card, result);
  } catch (error) {
    if (card) {
      const output = card.querySelector(".playlist-source-review-output");
      output.replaceChildren(element("p", "playlist-review-error", `No se pudo preparar la revisión: ${error.message}`));
    }
  } finally {
    if (button) { button.disabled = false; button.textContent = priorLabel || "Revisar señales de tendencia"; }
  }
}

async function loadMetrics() {
  const provider = $("#source-select").value || "Last.fm";
  const market = $("#market-select").value || "GLOBAL";
  if (provider === "Last.fm") {
    const payload = await getJson(`/api/charts/tracks?market_code=${encodeURIComponent(market)}&limit=100`);
    renderMetrics(payload);
  } else {
    const payload = await getJson(`/api/metrics/snapshots?provider=${encodeURIComponent(provider)}&market_code=${encodeURIComponent(market)}&limit=100`);
    renderProviderSnapshots(payload);
  }
}

async function refreshDashboard() {
  $("#refresh").classList.add("busy");
  try {
    const [overview, providerPayload, catalogs, signalOrder, providerComparison, publicPlaylists, playlistBlueprints, campaigns] = await Promise.all([
      getJson("/api/intelligence/overview"),
      getJson("/api/providers/status"),
      getJson("/api/markets"),
      getJson("/api/intelligence/signal-order?limit=100"),
      getJson("/api/intelligence/provider-comparison?limit=50"),
      getJson("/api/public/playlists"),
      getJson("/api/playlist-blueprints"),
      getJson("/api/campaigns"),
    ]);
    $("#metric-count").textContent = Number(overview.metric_observations || 0).toLocaleString("es-CL");
    const observationSummary = Object.entries(overview.observations_by_provider || {})
      .map(([name, count]) => `${name} ${fmtNumber(count)}`)
      .join(" · ");
    $("#observation-note").textContent = observationSummary || "esperando API, alcance y primera captura";
    const provider = renderProvider(providerPayload, overview);
    renderSignalOrder(signalOrder);
    renderProviderComparison(providerComparison);
    playlistDrafts.clear();
    renderPublicPlaylists(publicPlaylists);
    renderPlaylistBlueprints(playlistBlueprints);
    renderCampaigns(campaigns);
    if (($("#source-select").value || "Last.fm") === "Last.fm") {
      $("#metrics-empty .empty-checklist").style.display = "flex";
    }
    renderMarkets(overview, catalogs, provider);
    $("#updated-at").textContent = `Actualizado ${new Date().toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit" })}`;
    await loadMetrics();
  } catch (error) {
    $("#updated-at").textContent = "API local no disponible";
    $("#provider-hero-summary").textContent = "Inicia el servidor local para consultar señales";
    $("#provider-cards").replaceChildren(element("p", "provider-loading", "Inicia el servidor local para consultar el estado de las fuentes."));
    $("#metrics-footer").textContent = "No se pudo conectar con la API local";
    console.error("Dashboard refresh failed", error);
  } finally {
    $("#refresh").classList.remove("busy");
  }
}

$("#refresh").addEventListener("click", refreshDashboard);
$("#market-select").addEventListener("change", loadMetrics);
$("#source-select").addEventListener("change", loadMetrics);
$("#generate-all-playlists").addEventListener("click", () => generatePlaylistDraft());
$("#playlist-blueprint-search").addEventListener("input", () => {
  playlistBlueprintPage = 0;
  if (playlistBlueprintPayload) renderPlaylistBlueprints(playlistBlueprintPayload);
});
$("#playlist-blueprint-prev").addEventListener("click", () => {
  playlistBlueprintPage = Math.max(0, playlistBlueprintPage - 1);
  if (playlistBlueprintPayload) renderPlaylistBlueprints(playlistBlueprintPayload);
});
$("#playlist-blueprint-next").addEventListener("click", () => {
  playlistBlueprintPage += 1;
  if (playlistBlueprintPayload) renderPlaylistBlueprints(playlistBlueprintPayload);
});
$("#public-playlist-cards").addEventListener("click", (event) => {
  const review = event.target.closest("[data-review-lastfm-playlist]");
  if (review) { reviewPlaylistWithLastFm(review.dataset.reviewLastfmPlaylist); return; }
  const button = event.target.closest("[data-generate-playlist]");
  if (button) generatePlaylistDraft(button.dataset.generatePlaylist);
});
refreshDashboard();
