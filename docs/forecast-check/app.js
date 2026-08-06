/* Vanuatu cyclones — forecast trigger check.
   Plain JS + Leaflet. Data is pre-baked in data/ (see
   exploration/make_forecast_check_data.py). */

const SPEED = 64; // the trigger speed; 34/50 are carried as context only
const fmt = (n) => n.toLocaleString("en-US");
const $ = (s) => document.querySelector(s);

let CORE = null;
let selected = null;      // storm id
let geomCache = {};
let map, layers = {}, playTimer = null;

const peak = (s) => s.cycles.reduce((m, c) => Math.max(m, +c.exp[SPEED]), 0);
const thresh = () => Math.max(0, +$("#threshold").value || 0);

/* ---------------------------------------------------------------- load */
fetch("data/core.json")
  .then((r) => r.json())
  .then((d) => {
    CORE = d;
    $("#generated").textContent = "Generated " + d.generated + ".";
    if (d.aoi_pop) $("#aoiPop").textContent = fmt(d.aoi_pop);
    $("#threshold").addEventListener("input", render);
    $("#sort").addEventListener("change", render);
    $("#showZero").addEventListener("change", render);
    render();
  })
  .catch((e) => {
    $("#chart").innerHTML =
      '<p style="color:var(--critical)">Could not load data/core.json — ' +
      e.message + "</p>";
  });

/* ------------------------------------------------------------- summary */
function render() {
  const t = thresh();
  let rows = CORE.storms.map((s) => ({
    s,
    peak: peak(s),
    obs: +(s.obs[SPEED] || 0),
  }));

  const trig = rows.filter((r) => r.peak >= t);
  const falseAlarm = rows.filter((r) => r.peak >= t && r.obs < t);
  const missed = rows.filter((r) => r.peak < t && r.obs >= t);
  const cerf = rows.filter((r) => r.s.cerf);
  const cerfHit = cerf.filter((r) => r.peak >= t);

  $("#stats").innerHTML = [
    stat(trig.length, "storms would have triggered on forecast", trig.length > 0),
    stat(falseAlarm.length, "of those were below threshold in observation", falseAlarm.length > 0),
    stat(missed.length, "over threshold observed but never forecast to be", missed.length > 0),
    stat(`${cerfHit.length}/${cerf.length}`, "CERF-allocation storms would have triggered",
         cerfHit.length < cerf.length),
  ].join("");

  const cerfMiss = cerf.filter((r) => r.peak < t);
  $("#cerfNote").innerHTML = cerfMiss.length
    ? "CERF storm" + (cerfMiss.length > 1 ? "s" : "") + " below the forecast threshold: " +
      cerfMiss.map((r) =>
        `<strong>${esc(r.s.name)}</strong> (forecast peak ${fmt(r.peak)}, observed ${fmt(r.obs)}` +
        (r.obs < t ? " — also below threshold in observation, so this is an AOI-scope issue rather than a forecast one" : "") +
        ")").join("; ") + "."
    : "";

  const hidden = rows.filter((r) => r.peak === 0 && r.obs === 0).length;
  if (!$("#showZero").checked) {
    rows = rows.filter((r) => r.peak > 0 || r.obs > 0);
  }
  $("#hiddenNote").textContent = $("#showZero").checked || !hidden
    ? ""
    : `${hidden} further storms had zero forecast and zero observed exposure and are hidden.`;
  const sort = $("#sort").value;
  rows.sort((a, b) =>
    sort === "season" ? a.s.season - b.s.season || a.s.name.localeCompare(b.s.name)
    : sort === "name" ? a.s.name.localeCompare(b.s.name)
    : b.peak - a.peak || b.obs - a.obs
  );

  drawChart(rows, t);
  drawTable(rows, t);
}

function stat(v, k, alert) {
  return `<div class="stat${alert ? " alert" : ""}"><div class="v">${v}</div>
          <div class="k">${k}</div></div>`;
}

function drawChart(rows, t) {
  const W = 900, padL = 108, padR = 56, padT = 26, rowH = 22;
  const H = padT + rows.length * rowH + 30;
  const max = Math.max(t * 1.15, ...rows.map((r) => Math.max(r.peak, r.obs)), 1);
  const x = (v) => padL + (v / max) * (W - padL - padR);

  const ticks = niceTicks(max, 5);
  let sv = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMin meet">`;

  ticks.forEach((v) => {
    sv += `<line class="gridline" x1="${x(v)}" y1="${padT - 6}" x2="${x(v)}" y2="${H - 30}"/>`;
    sv += `<text class="axis-label" x="${x(v)}" y="${H - 16}" text-anchor="middle">${abbr(v)}</text>`;
  });
  sv += `<text class="axis-label" x="${padL}" y="${H - 3}" text-anchor="start">people exposed to ${SPEED} kt in AOI provinces</text>`;

  rows.forEach((r, i) => {
    const y = padT + i * rowH;
    const over = r.peak >= t;
    sv += `<g class="bar-row${r.s.id === selected ? " sel" : ""}" data-id="${r.s.id}">`;
    sv += `<rect class="hit" x="0" y="${y - 2}" width="${W}" height="${rowH}" fill="transparent"/>`;
    // forecast bar (upper), observed bar (lower) — 2px surface gap between
    sv += bar(padL, y + 1, x(r.peak) - padL, 7, "var(--fcst)");
    sv += bar(padL, y + 10, x(r.obs) - padL, 7, "var(--obs)");
    const label = r.s.name + (r.s.cerf ? " ★" : "");
    sv += `<text class="storm-label${over ? " over" : ""}" x="${padL - 8}" y="${y + 13}" text-anchor="end">${esc(label)}</text>`;
    sv += `<text class="val-label" x="${Math.max(x(r.peak), x(r.obs)) + 6}" y="${y + 13}">${abbr(r.peak)} / ${abbr(r.obs)}</text>`;
    sv += `<title>${esc(r.s.name)} ${r.s.season} — forecast peak ${fmt(r.peak)}, observed ${fmt(r.obs)}</title>`;
    sv += `</g>`;
  });

  sv += `<line class="threshline" x1="${x(t)}" y1="${padT - 10}" x2="${x(t)}" y2="${H - 30}"/>`;
  sv += `<text class="threshlabel" x="${x(t)}" y="${padT - 14}" text-anchor="middle">threshold ${abbr(t)}</text>`;
  sv += `</svg>`;

  const legend =
    `<ul class="legend"><li><span class="sw" style="background:var(--fcst)"></span>Peak forecast (JTWC)</li>
     <li><span class="sw" style="background:var(--obs)"></span>Observed (IBTrACS)</li>
     <li>★ = CERF-allocation storm</li></ul>`;
  $("#chart").innerHTML = sv + legend;

  $("#chart").querySelectorAll(".bar-row").forEach((g) =>
    g.addEventListener("click", () => selectStorm(g.dataset.id))
  );
}

/* 4px rounded data-end anchored to the baseline (left edge here) */
function bar(x0, y, w, h, fill) {
  if (w <= 0.5) return "";
  const r = Math.min(4, w / 2);
  return `<path d="M${x0},${y} H${x0 + w - r} a${r},${r} 0 0 1 ${r},${r}
           V${y + h - r} a${r},${r} 0 0 1 -${r},${r} H${x0} Z" fill="${fill}"/>`;
}

function drawTable(rows, t) {
  let h = `<table><thead><tr><th>Storm</th><th>Season</th>
    <th>Peak forecast 64kt</th><th>Observed 64kt</th>
    <th>Observed 50kt</th><th>Observed 34kt</th><th>Cycles</th></tr></thead><tbody>`;
  rows.forEach((r) => {
    h += `<tr><td>${esc(r.s.name)}${r.s.cerf ? " ★" : ""}</td><td>${r.s.season}</td>
      <td class="${r.peak >= t ? "over" : ""}">${fmt(r.peak)}</td>
      <td class="${r.obs >= t ? "over" : ""}">${fmt(r.obs)}</td>
      <td>${fmt(+(r.s.obs["50"] || 0))}</td><td>${fmt(+(r.s.obs["34"] || 0))}</td>
      <td>${r.s.cycles.length}</td></tr>`;
  });
  $("#table").innerHTML = h + "</tbody></table>";
}

/* -------------------------------------------------------------- detail */
function selectStorm(id) {
  selected = id;
  const s = CORE.storms.find((x) => x.id === id);
  $("#detail").hidden = false;
  $("#detailTitle").textContent =
    `${s.name} (${s.season}) — ${s.cycles.length} forecast cycles` +
    (s.n_vmgd ? `, ${s.n_vmgd} VMGD forecast maps` : "");

  // open on the cycle that peaks — that's the one the trigger turns on
  let peakIdx = 0;
  s.cycles.forEach((c, i) => {
    if (+c.exp[SPEED] > +s.cycles[peakIdx].exp[SPEED]) peakIdx = i;
  });

  const sl = $("#cycle");
  sl.max = s.cycles.length - 1;
  sl.value = peakIdx;
  sl.oninput = () => showCycle(s, +sl.value);
  $("#playBtn").onclick = () => togglePlay(s);

  ensureMap();
  fetchGeom(id).then((g) => {
    drawStatic(s, g);
    showCycle(s, +sl.value);
    fitView();
    map.invalidateSize();
  });
  render();
  $("#detail").scrollIntoView({ behavior: "smooth", block: "start" });
}

function fetchGeom(id) {
  if (geomCache[id]) return Promise.resolve(geomCache[id]);
  return fetch(`data/geom/${id}.json`)
    .then((r) => r.json())
    .then((g) => (geomCache[id] = g));
}

function ensureMap() {
  if (map) return;
  map = L.map("map", { scrollWheelZoom: false }).setView([-16.2, 167.7], 5);
  const dark =
    document.documentElement.dataset.theme === "dark" ||
    (document.documentElement.dataset.theme !== "light" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  L.tileLayer(
    `https://{s}.basemaps.cartocdn.com/${dark ? "dark_all" : "light_all"}/{z}/{x}/{y}{r}.png`,
    { attribution: "&copy; OpenStreetMap, &copy; CARTO", maxZoom: 11 }
  ).addTo(map);
}

const clear = (k) => { if (layers[k]) { map.removeLayer(layers[k]); delete layers[k]; } };
// rings come as [lon,lat]; Leaflet wants [lat,lon]
const ring2ll = (rings) => rings.map((r) => r.map(([x, y]) => [y, x]));
const tr2ll = (t) => t.map((p) => [p[0], p[1]]);

function drawStatic(s, g) {
  ["aoi", "obsBuf", "obsTrk", "vmgd"].forEach(clear);

  if (CORE.aoi_geom) {
    layers.aoi = L.geoJSON(CORE.aoi_geom, {
      style: { color: "#1baf7a", weight: 2.5, fillOpacity: 0.35, fillColor: "#1baf7a" },
    }).addTo(map);
  }
  if (g.obs_buf64) {
    layers.obsBuf = L.polygon(ring2ll(g.obs_buf64), {
      color: "#eb6834", weight: 2, fillOpacity: 0.18, fillColor: "#eb6834",
    }).addTo(map);
  }
  if (g.obs_track && g.obs_track.length) {
    layers.obsTrk = L.polyline(tr2ll(g.obs_track), {
      color: "#eb6834", weight: 2.5, opacity: 0.9,
    }).addTo(map);
  }
  if (g.vmgd && g.vmgd.length) {
    layers.vmgd = L.layerGroup(
      g.vmgd.map((v) =>
        L.polyline(tr2ll(v.track), {
          color: "#767570", weight: 1.5, opacity: 0.5, dashArray: "4 3",
        }).bindTooltip(`VMGD map #${v.number ?? "?"} — issued ${v.issue}`)
      )
    ).addTo(map);
  }

}

/* Frame the AOI together with whatever swaths are on screen, so the storm is
   always shown relative to the provinces the trigger is counted over. */
function fitView() {
  let b = null;
  ["aoi", "obsBuf", "fcstBuf", "fcstTrk"].forEach((k) => {
    if (!layers[k]) return;
    const lb = layers[k].getBounds();
    b = b ? b.extend(lb) : L.latLngBounds(lb.getSouthWest(), lb.getNorthEast());
  });
  if (b && b.isValid()) map.fitBounds(b.pad(0.15));
}

function showCycle(s, i) {
  const c = s.cycles[i];
  const g = geomCache[s.id];
  const key = c.init.replace(/[-:TZ]/g, "").slice(0, 10);
  const cg = g.cycles[key];
  $("#cycleLabel").textContent = `${c.init}  (${i + 1}/${s.cycles.length})`;

  ["fcstBuf", "fcstTrk"].forEach(clear);
  if (cg && cg.buf64) {
    layers.fcstBuf = L.polygon(ring2ll(cg.buf64), {
      color: "#2a78d6", weight: 2, fillOpacity: 0.3, fillColor: "#2a78d6",
    }).addTo(map);
  }
  if (cg && cg.track) {
    layers.fcstTrk = L.polyline(tr2ll(cg.track), {
      color: "#2a78d6", weight: 3,
    }).addTo(map);
  }

  const t = thresh();
  const e = +c.exp[SPEED];
  $("#cycleInfo").innerHTML = `<dl>
    <dt>Forecast issued</dt><dd>${c.init}</dd>
    <dt>Peak forecast wind</dt><dd>${c.vmax} kt</dd>
    <dt>Exposed at 64 kt</dt><dd><strong style="color:${e >= t ? "var(--critical)" : "inherit"}">${fmt(e)}</strong>${e >= t ? " — over threshold" : ""}</dd>
    <dt>Exposed at 50 kt</dt><dd>${fmt(+c.exp["50"])}</dd>
    <dt>Exposed at 34 kt</dt><dd>${fmt(+c.exp["34"])}</dd>
    <dt>Observed at 64 kt</dt><dd>${fmt(+(s.obs["64"] || 0))}</dd></dl>`;

  drawCycleChart(s, i);
}

function drawCycleChart(s, sel) {
  const W = 420, H = 170, padL = 52, padR = 12, padT = 14, padB = 30;
  const t = thresh();
  const vals = s.cycles.map((c) => +c.exp[SPEED]);
  const max = Math.max(t * 1.2, ...vals, 1);
  const x = (i) => padL + (s.cycles.length < 2 ? 0 : (i / (s.cycles.length - 1)) * (W - padL - padR));
  const y = (v) => H - padB - (v / max) * (H - padT - padB);

  let sv = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMin meet">`;
  niceTicks(max, 4).forEach((v) => {
    sv += `<line class="gridline" x1="${padL}" y1="${y(v)}" x2="${W - padR}" y2="${y(v)}"/>`;
    sv += `<text class="axis-label" x="${padL - 6}" y="${y(v) + 3}" text-anchor="end">${abbr(v)}</text>`;
  });
  sv += `<line class="threshline" x1="${padL}" y1="${y(t)}" x2="${W - padR}" y2="${y(t)}"/>`;

  const pts = vals.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  sv += `<polyline points="${pts}" fill="none" stroke="var(--fcst)" stroke-width="2"/>`;
  vals.forEach((v, i) => {
    const on = i === sel;
    sv += `<circle cx="${x(i)}" cy="${y(v)}" r="${on ? 5 : 3}"
           fill="var(--fcst)" stroke="var(--surface-1)" stroke-width="2"><title>${s.cycles[i].init}: ${fmt(v)}</title></circle>`;
  });
  sv += `<text class="axis-label" x="${padL}" y="${H - 8}">first cycle</text>`;
  sv += `<text class="axis-label" x="${W - padR}" y="${H - 8}" text-anchor="end">last cycle</text>`;
  sv += `</svg>`;
  $("#cycleChart").innerHTML = sv;
}

function togglePlay(s) {
  const sl = $("#cycle");
  if (playTimer) {
    clearInterval(playTimer); playTimer = null; $("#playBtn").textContent = "▶";
    return;
  }
  $("#playBtn").textContent = "❚❚";
  playTimer = setInterval(() => {
    let v = (+sl.value + 1) % s.cycles.length;
    sl.value = v;
    showCycle(s, v);
    if (v === s.cycles.length - 1) { clearInterval(playTimer); playTimer = null; $("#playBtn").textContent = "▶"; }
  }, 700);
}

/* --------------------------------------------------------------- utils */
function niceTicks(max, n) {
  const raw = max / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || mag * 10;
  const out = [];
  for (let v = 0; v <= max; v += step) out.push(v);
  return out;
}
function abbr(v) {
  if (v >= 1000000) return (v / 1000000).toFixed(1).replace(/\.0$/, "") + "M";
  if (v >= 1000) return (v / 1000).toFixed(v >= 10000 ? 0 : 1).replace(/\.0$/, "") + "k";
  return String(Math.round(v));
}
function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
