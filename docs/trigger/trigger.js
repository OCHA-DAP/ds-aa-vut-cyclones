/* How-the-trigger-works explainer page.
   Reads the pre-baked data from ../forecast-check/data/. */

const T = 15000;
const fmt = (n) => n.toLocaleString("en-US");
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let CORE = null, RP = null;
let map, layers = {}, geomCache = {}, playTimer = null, curStorm = null;

const peakA = (s) => s.cycles.reduce((m, c) => Math.max(m, +(c.expA64 || 0)), 0);

const STORY = {
  "Jasmine 2012": "Forecasts placed hurricane-force winds over the trigger provinces, but the storm curved away — a false alarm. Minor crop damage only.",
  "Pam 2015": "Category 5, among the worst disasters in Vanuatu's history. Caught by the observational trigger; its forecasts kept the worst winds just under the threshold until late.",
  "Harold 2020": "Category 5 across Sanma and Pentecost. Both forecast and observational triggers fire — with days of leadtime.",
  "Judy 2023": "First of two cyclones in one week. Forecast trigger fires ~2 days ahead; observational confirms.",
  "Kevin 2023": "Struck days after Judy. Forecast trigger fires again — back-to-back activations in the same season count once.",
  "Lola 2024": "An out-of-season Category 5 that hit Torba and Penama — outside the forecast provinces, caught nationally by the observational trigger.",
};

Promise.all([
  fetch("../forecast-check/data/core.json").then((r) => r.json()),
  fetch("../forecast-check/data/rp.json").then((r) => r.json()),
  fetch("../forecast-check/data/hist.json").then((r) => r.json()),
]).then(([core, rp, hist]) => {
  CORE = core; RP = rp;
  $("#generated").textContent = " · data generated " + core.generated;
  drawTMS(rp);
  drawActivations(rp, hist);
  drawBars(rp);
  drawCorr(hist);
  initExplorer();
});

/* ---------------- Trigger Mechanism Statistics panel ---------------- */
function drawTMS(rp) {
  const N = rp.n_seasons, EXTRA = rp.combined.extra_default ?? 1;
  const rpv = (k) => (N + 1) / k;
  const yrs = (v) => (Math.round(v * 10) / 10).toFixed(1).replace(/\.0$/, "") + " years";
  const pct = (v) => Math.round(100 / v) + "%";
  const act = [...new Set(rp.scored_storms.filter((s) => s.peakA >= T).map((s) => s.season))].sort();
  const obs = [...new Set(rp.scored_storms.filter((s) => s.obs >= T).map((s) => s.season))].sort();
  const all = [...new Set([...act, ...obs])].sort();
  const rpAct = rpv(act.length + EXTRA), rpObs = rpv(obs.length), rpAll = rpv(all.length + EXTRA);
  $("#tms").innerHTML = `
  <div class="tms">
    <p class="tms-title">Trigger Mechanism Statistics</p>
    <div class="secbar"><span class="sec">Stats by trigger</span>
      <span class="yrs">Analysis years: ${rp.first_season}-${rp.last_season}</span></div>
    <table>
      <thead><tr><th>Trigger</th><th>Return period</th>
        <th>Activation<br>probability</th><th>Years activated</th></tr></thead>
      <tbody>
        <tr><td>Action</td><td>${yrs(rpAct)}</td><td>${pct(rpAct)}</td>
            <td>${act.join(", ")}, +1 assumed*</td></tr>
        <tr><td>Observational</td><td>${yrs(rpObs)}</td><td>${pct(rpObs)}</td>
            <td>${obs.join(", ")}</td></tr>
      </tbody>
    </table>
    <div class="overall">
      <span class="sec">Overall stats</span>
      <table><tbody>
        <tr><td class="lbl">Overall return period</td><td class="val">&le; ${yrs(rpAll)}</td></tr>
        <tr><td class="lbl">Overall probability of activation</td><td class="val">&ge; ${pct(rpAll)}</td></tr>
        <tr><td class="lbl">Average total spending per year</td><td class="val">&ge; $${(1.5 / rpAll).toFixed(1)}M</td></tr>
      </tbody></table>
    </div>
  </div>`;
  $("#tmsFoot").innerHTML =
    `*One assumed action activation in 2008 (Gene) or 2011 (Atu): both storms' hurricane-force ` +
    `winds reached or nearly reached the trigger provinces, but no forecast records survive for ` +
    `2006&ndash;11. The readiness window is not assessed (too little archived long-range data), ` +
    `so the overall return period is reported as &le; the computed value. Pre-arranged ` +
    `financing: $1.5M, disbursed in full on either an action or an observational activation.`;
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
        ${s.peakA >= T ? '<span class="badge action">ACTION — forecast</span>' : ""}
        ${s.obs >= T ? '<span class="badge obsv">OBSERVATIONAL</span>' : ""}
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
    reached the trigger provinces, and both caused documented damage — but no forecast archives
    survive from 2006&ndash;11, so one of these two seasons is carried as an assumed activation
    in the statistics above.</p>
  </div>`;
  $("#actCards").innerHTML = h;
}

/* ---------------- close-calls bar chart ---------------- */
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
  sv += `<text class="threshlabel" x="${x(T)}" y="${padT - 13}" text-anchor="middle">trigger: ${fmt(T)} people</text>`;
  sv += `</svg>`;
  sv += `<ul class="legend">
    <li><span class="sw" style="background:var(--fcst)"></span>forecast (1–3 days ahead)</li>
    <li><span class="sw" style="background:var(--obs)"></span>observed</li></ul>`;
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
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  L.tileLayer(
    `https://{s}.basemaps.cartocdn.com/${dark ? "dark_all" : "light_all"}/{z}/{x}/{y}{r}.png`,
    { attribution: "&copy; OpenStreetMap, &copy; CARTO", maxZoom: 11 }
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
  $("#xCycleLabel").textContent = `forecast ${i + 1} of ${curStorm.cycles.length} — issued ${nice}`;
  $("#xVerdict").innerHTML = eA >= T
    ? `<strong class="yes">TRIGGERS</strong> — ${fmt(eA)} people in the predicted hurricane-force area`
    : `does not trigger — ${fmt(eA)} people predicted (needs ${fmt(T)})`;
  $("#xInfo").innerHTML = `<dl>
    <dt>Predicted exposure (1–3 days ahead)</dt><dd>${fmt(eA)}</dd>
    <dt>What actually happened (whole country)</dt><dd>${fmt(+(curStorm.obs["64"] || 0))}</dd>
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
