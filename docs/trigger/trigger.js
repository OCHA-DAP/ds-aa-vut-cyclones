/* The trigger page: the framework's historical activations first, then the
   analysis and the forecast explorer. Reads the pre-baked data from
   ../forecast-check/data/; the statistics panel and the Annex 2 table come
   from ../assets/historical.js. */

const { T, fmt, esc } = VutRecord;
const $ = (s) => document.querySelector(s);

let CORE = null;
let map, layers = {}, geomCache = {}, playTimer = null, curStorm = null;

const peakA = (s) => s.cycles.reduce((m, c) => Math.max(m, +(c.expA64 || 0)), 0);

const STORY = {
  "Jasmine 2012": "Forecasts put hurricane-force winds over the trigger provinces, but the storm curved away: a false alarm. Minor crop damage only.",
  "Pam 2015": "Category 5, among the worst disasters in Vanuatu's history. Activated by the observational trigger: the forecasts of 1 to 3 days ahead put no more than 2,478 people in the trigger provinces inside the hurricane-force wind area.",
  "Harold 2020": "Category 5 across Sanma and Pentecost. Both the action trigger, from the forecasts, and the observational trigger activated.",
  "Judy 2023": "First of two cyclones in one week. The action trigger activated from the forecasts 1 to 3 days ahead; the observational trigger confirmed it.",
  "Kevin 2023": "Struck days after Judy. The action trigger activated again; two activations in one season count once in the statistics.",
  "Lola 2024": "An out-of-season Category 5 (October 2023) that hit Torba and Penama, outside the trigger provinces: activated nationally by the observational trigger.",
};

Promise.all([
  fetch("../forecast-check/data/core.json").then((r) => r.json()),
  fetch("../forecast-check/data/rp.json").then((r) => r.json()),
  fetch("../forecast-check/data/hist.json").then((r) => r.json()),
]).then(([core, rp, hist]) => {
  CORE = core;
  $("#generated").textContent = " · data generated " + core.generated;
  $("#annex").innerHTML = VutRecord.annexTable(rp, hist);
  $("#tms").innerHTML = VutRecord.tmsPanel(rp);
  drawFacts(rp);
  drawActivations(rp, hist);
  drawBars(rp);
  drawCorr(hist);
  initExplorer();
  // the page fills in after load, so a link to a section lands short of it
  const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (target) target.scrollIntoView();
});

/* ---------------- performance in numbers ---------------- */
function drawFacts(rp) {
  const s = VutRecord.stats(rp);
  const cerf = rp.scored_storms.filter((r) => r.cerf);
  const caught = cerf.filter((r) => r.peakA >= T || r.obs >= T);
  const ahead = cerf.filter((r) => r.peakA >= T);
  const falseAlarms = rp.scored_storms.filter((r) => r.peakA >= T && r.obs < T);
  const fact = (v, k) => `<div class="fact"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  $("#facts").innerHTML = [
    fact(`${caught.length} of ${cerf.length}`,
      "cyclones with a CERF allocation activated the framework"),
    fact(`${ahead.length} of ${cerf.length}`,
      "of them through the action trigger, 1 to 3 days ahead; the others through the observational trigger"),
    fact(falseAlarms.length,
      `false alarm${falseAlarms.length === 1 ? "" : "s"}: ` +
      (falseAlarms.map((r) => `${esc(r.name)} ${r.season}`).join(", ") || "none") +
      `, in ${rp.action.n_scored_seasons} seasons with forecasts`),
    fact(`1 in ${(Math.round(s.rpAll * 10) / 10).toFixed(1).replace(/\.0$/, "")} years`,
      "or more often: how often the framework would have activated, against a target of about 1 in 4"),
  ].join("");
}

/* ---------------- activation cards ---------------- */
function drawActivations(rp, hist) {
  const affected = new Map(hist.storms.map((h) => [h.name.toUpperCase() + h.season, h.affected]));
  const storms = rp.scored_storms
    .filter((s) => s.peakA >= T || s.obs >= T)
    .sort((a, b) => a.season - b.season);
  let h = "";
  for (const s of storms) {
    const key = `${s.name} ${s.season}`;
    const aff = affected.get(s.name.toUpperCase() + s.season) || 0;
    h += `<div class="acard">
      <h3><span>${esc(key)}</span></h3>
      <div class="badges">
        ${s.peakA >= T ? '<span class="badge action">ACTION</span>' : ""}
        ${s.obs >= T ? '<span class="badge obsv">OBSERVATIONAL</span>' : ""}
        ${s.peakA >= T && s.obs < T ? '<span class="badge false">FALSE ALARM</span>' : ""}
        ${s.cerf ? '<span class="badge cerf">CERF ALLOCATION</span>' : ""}
      </div>
      <dl>
        <dt>Forecast exposure</dt><dd>${fmt(s.peakA)}</dd>
        <dt>Observed exposure</dt><dd>${fmt(s.obs)}</dd>
        <dt>People affected (EM-DAT)</dt><dd>${fmt(aff)}</dd>
      </dl>
      <p class="note" style="margin-top:.5rem">${STORY[key] || ""}</p>
    </div>`;
  }
  h += `<div class="acard assumed-card">
    <h3><span>Gene 2008 / Atu 2011</span></h3>
    <div class="badges"><span class="badge assumed">1 ASSUMED ACTIVATION</span></div>
    <p class="note" style="margin-top:.4rem">Both storms' hurricane-force winds reached or nearly
    reached the trigger provinces, and both caused documented damage, but no forecast records
    survive from 2006&ndash;11, so one of these two seasons is carried as an assumed activation
    in the statistics.</p>
  </div>`;
  $("#actCards").innerHTML = h;
}

/* ---------------- forecast vs observed bars ---------------- */
function drawBars(rp) {
  const rows = rp.scored_storms
    .filter((s) => s.peakA > 0 || s.obs > 0)
    .sort((a, b) => Math.max(b.peakA, b.obs) - Math.max(a.peakA, a.obs));
  const W = 900, padL = 110, padR = 60, padT = 26, rowH = 26;
  const H = padT + rows.length * rowH + 34;
  const max = Math.max(T * 1.2, ...rows.map((r) => Math.max(r.peakA, r.obs)));
  const x = (v) => padL + (v / max) * (W - padL - padR);
  let sv = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMin meet" role="img">`;
  for (const v of [0, 50000, 100000, 150000]) {
    if (v > max) continue;
    sv += `<line class="gridline" x1="${x(v)}" y1="${padT - 6}" x2="${x(v)}" y2="${H - 28}"/>`;
    sv += `<text class="axis-label" x="${x(v)}" y="${H - 14}" text-anchor="middle">${v / 1000}k</text>`;
  }
  rows.forEach((r, i) => {
    const y = padT + i * rowH;
    const on = r.peakA >= T || r.obs >= T;
    sv += `<text class="storm-label${on ? " over" : ""}" x="${padL - 8}" y="${y + 14}" text-anchor="end">${esc(r.name)} ${r.season}</text>`;
    const bar = (v, yy, col) => v > 0
      ? `<rect x="${padL}" y="${yy}" width="${Math.max(x(v) - padL, 2)}" height="8" rx="3" fill="${col}"/>` : "";
    sv += bar(r.peakA, y + 2, "var(--fcst)");
    sv += bar(r.obs, y + 12, "var(--obs)");
    sv += `<title>${esc(r.name)} ${r.season}: forecast ${fmt(r.peakA)}, observed ${fmt(r.obs)}</title>`;
  });
  sv += `<line class="threshline" x1="${x(T)}" y1="${padT - 10}" x2="${x(T)}" y2="${H - 28}"/>`;
  sv += `<text class="threshlabel" x="${x(T)}" y="${padT - 13}" text-anchor="middle">threshold: ${fmt(T)} people</text>`;
  sv += `</svg>`;
  sv += `<ul class="legend">
    <li><span class="sw" style="background:var(--fcst)"></span>forecast, 1 to 3 days ahead (Shefa, Sanma, Tafea)</li>
    <li><span class="sw" style="background:var(--obs)"></span>observed (all of Vanuatu)</li></ul>`;
  $("#xBars").innerHTML = sv;
}

/* ---------------- correlation evidence (rainfall section) ---------------- */
function pearson(xs, ys) {
  const n = xs.length;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let sxy = 0, sxx = 0, syy = 0;
  for (let i = 0; i < n; i++) {
    sxy += (xs[i] - mx) * (ys[i] - my);
    sxx += (xs[i] - mx) ** 2;
    syy += (ys[i] - my) ** 2;
  }
  return sxx && syy ? sxy / Math.sqrt(sxx * syy) : 0;
}

function drawCorr(hist) {
  const inds = [
    ["exp64", "Exp 64 kt (hurricane force)"],
    ["exp50", "Exp 50 kt (storm force)"],
    ["exp34", "Exp 34 kt (gale force)"],
    ["rain", "2-day rainfall"],
  ];
  const ta = hist.storms.map((s) => s.affected);
  const cerf = hist.storms.map((s) => (s.cerf ? 1 : 0));
  const panel = (title, ys) => {
    const W = 420, rowH = 30, padL = 190, padT = 24, H = padT + inds.length * rowH + 12;
    const x = (v) => padL + Math.max(v, 0) * (W - padL - 44);
    let sv = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMin meet">`;
    sv += `<text class="axis-title" x="${padL}" y="14">${title}</text>`;
    inds.forEach(([key, label], i) => {
      const r = pearson(hist.storms.map((s) => s[key]), ys);
      const yy = padT + i * rowH + 4;
      sv += `<text class="axis-label" x="${padL - 8}" y="${yy + 12}" text-anchor="end">${label}</text>`;
      sv += `<rect x="${x(0)}" y="${yy}" width="${Math.max(x(r) - x(0), 1)}" height="16" rx="3"
              fill="${key === "rain" ? "var(--rain)" : "var(--wind)"}"/>`;
      sv += `<text class="axis-label" x="${x(Math.max(r, 0)) + 5}" y="${yy + 12}">${r.toFixed(2)}</text>`;
    });
    return sv + "</svg>";
  };
  $("#corr").innerHTML =
    `<div>${panel("How well it tracks people affected", ta)}</div>` +
    `<div>${panel("How well it tracks CERF allocations", cerf)}</div>`;
}

/* ---------------- interactive forecast explorer ---------------- */
function initExplorer() {
  const storms = CORE.storms
    .filter((s) => s.cycles.some((c) => (c.expA64 || 0) > 0 || +c.exp["64"] > 0))
    .sort((a, b) => peakA(b) - peakA(a));
  $("#xStorm").innerHTML = storms.map((s) =>
    `<option value="${s.id}">${esc(s.name)} (${s.season})</option>`).join("");
  $("#xStorm").addEventListener("change", () => selectStorm($("#xStorm").value));
  $("#xCycle").addEventListener("input", () => showCycle(+$("#xCycle").value));
  $("#xPlay").addEventListener("click", togglePlay);
  selectStorm(storms[0].id);
}

function ensureMap() {
  if (map) return;
  map = L.map("xMap", { scrollWheelZoom: false }).setView([-16.2, 167.7], 6);
  // Esri's key-free canvas: CARTO's basemaps now need an API key
  const dark =
    document.documentElement.dataset.theme === "dark" ||
    (document.documentElement.dataset.theme !== "light" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/" +
      `${dark ? "World_Dark_Gray_Base" : "World_Light_Gray_Base"}/MapServer/tile/{z}/{y}/{x}`,
    { attribution: "Tiles &copy; Esri &mdash; Esri, HERE, Garmin, OpenStreetMap contributors",
      maxZoom: 11 }
  ).addTo(map);
}

const ring2ll = (rings) => rings.map((r) => r.map(([x, y]) => [y, x]));

function selectStorm(id) {
  curStorm = CORE.storms.find((s) => s.id === id);
  ensureMap();
  const p = geomCache[id]
    ? Promise.resolve(geomCache[id])
    : fetch(`../forecast-check/data/geom/${id}.json`).then((r) => r.json())
        .then((g) => (geomCache[id] = g));
  p.then((g) => {
    Object.values(layers).forEach((l) => map.removeLayer(l));
    layers = {};
    if (CORE.aoi_geom) {
      layers.aoi = L.geoJSON(CORE.aoi_geom, {
        style: { color: "#1baf7a", weight: 2, fillOpacity: 0.18, fillColor: "#1baf7a" },
      }).addTo(map);
    }
    if (g.obs_buf64) {
      layers.obs = L.polygon(ring2ll(g.obs_buf64), {
        color: "#eb6834", weight: 2, fillOpacity: 0.2, fillColor: "#eb6834",
      }).addTo(map);
    }
    // open on the peak action-window cycle
    let idx = 0;
    curStorm.cycles.forEach((c, i) => {
      if ((c.expA64 || 0) > (curStorm.cycles[idx].expA64 || 0)) idx = i;
    });
    $("#xCycle").max = curStorm.cycles.length - 1;
    $("#xCycle").value = idx;
    showCycle(idx);
    let b = layers.obs ? layers.obs.getBounds() : layers.aoi.getBounds();
    if (layers.aoi) b = b.extend(layers.aoi.getBounds());
    map.fitBounds(b.pad(0.2));
    map.invalidateSize();
  });
}

function showCycle(i) {
  const c = curStorm.cycles[i];
  const g = geomCache[curStorm.id];
  const key = c.init.replace(/[-:TZ]/g, "").slice(0, 10);
  const cg = g.cycles[key];
  if (layers.fcst) { map.removeLayer(layers.fcst); delete layers.fcst; }
  if (layers.trk) { map.removeLayer(layers.trk); delete layers.trk; }
  if (cg && cg.buf64) {
    layers.fcst = L.polygon(ring2ll(cg.buf64), {
      color: "#2a78d6", weight: 2, fillOpacity: 0.3, fillColor: "#2a78d6",
    }).addTo(map);
  }
  if (cg && cg.track) {
    layers.trk = L.polyline(cg.track.map((p) => [p[0], p[1]]), {
      color: "#2a78d6", weight: 2.5,
    }).addTo(map);
  }
  const eA = +(c.expA64 || 0);
  const d = new Date(c.init);
  const nice = d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) +
    ", " + String(d.getUTCHours()).padStart(2, "0") + ":00 UTC";
  $("#xCycleLabel").textContent = `forecast ${i + 1} of ${curStorm.cycles.length}, issued ${nice}`;
  $("#xVerdict").innerHTML = eA >= T
    ? `<strong class="yes">Action trigger threshold met</strong>: ${fmt(eA)} people in the forecast 64-knot area`
    : `Below the action trigger threshold: ${fmt(eA)} people forecast (${fmt(T)} needed)`;
  $("#xInfo").innerHTML = `<dl>
    <dt>Forecast exposure (1 to 3 days ahead)</dt><dd>${fmt(eA)}</dd>
    <dt>Observed exposure (all of Vanuatu)</dt><dd>${fmt(+(curStorm.obs["64"] || 0))}</dd>
    <dt>Peak forecast wind this cycle</dt><dd>${c.vmax} kt</dd></dl>`;
}

function togglePlay() {
  if (playTimer) {
    clearInterval(playTimer); playTimer = null; $("#xPlay").innerHTML = "&#9654;";
    return;
  }
  $("#xPlay").innerHTML = "&#10074;&#10074;";
  playTimer = setInterval(() => {
    const sl = $("#xCycle");
    const v = (+sl.value + 1) % (curStorm.cycles.length);
    sl.value = v;
    showCycle(v);
    if (v === curStorm.cycles.length - 1) togglePlay();
  }, 800);
}
