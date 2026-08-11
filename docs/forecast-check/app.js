/* Vanuatu cyclones — forecast trigger check.
   Plain JS + Leaflet. Data is pre-baked in data/ (see
   exploration/make_forecast_check_data.py). */

const SPEED = 64; // the trigger speed; 34/50 are carried as context only
const fmt = (n) => n.toLocaleString("en-US");
const $ = (s) => document.querySelector(s);

let CORE = null;
let selected = null;      // storm id (forecast tab)
let geomCache = {};
let map, layers = {}, playTimer = null;
let dMap, dLayers = {}, dSelected = null, obsGeomCache = {};

// the proposed trigger — locked on the forecast tab
const THRESH = 5000;
const peak = (s) => s.cycles.reduce((m, c) => Math.max(m, +c.exp[SPEED]), 0);
const peakLeg = (s, k) => s.cycles.reduce((m, c) => Math.max(m, +(c[k] || 0)), 0);
const thresh = () => THRESH;

/* ------------------------------------------------------------------ tabs */
function showTab(name, updateHash = true) {
  document.querySelectorAll(".tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === name)
  );
  $("#panel-design").hidden = name !== "design";
  $("#panel-forecast").hidden = name !== "forecast";
  if (updateHash) history.replaceState(null, "", "#" + name);
  // Leaflet can't size itself inside a hidden panel
  if (name === "forecast" && map) setTimeout(() => map.invalidateSize(), 0);
  if (name === "design" && dMap) setTimeout(() => dMap.invalidateSize(), 0);
}
document.querySelectorAll(".tab").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.tab))
);
if (location.hash === "#forecast") showTab("forecast", false);

/* ---------------------------------------------------------------- load */
let HIST = null;

Promise.all([
  fetch("data/core.json").then((r) => r.json()),
  fetch("data/hist.json").then((r) => r.json()),
  fetch("data/rp.json").then((r) => r.json()).catch(() => null),
])
  .then(([core, hist, rp]) => {
    CORE = core;
    HIST = hist;
    if (rp) drawRP(rp);
    $("#generated").textContent = "Generated " + core.generated + ".";
    if (core.aoi_pop) $("#aoiPop").textContent = fmt(core.aoi_pop);
    if (core.nat_pop) $("#natPop").textContent = fmt(core.nat_pop);
    $("#sort").addEventListener("change", render);
    $("#showZero").addEventListener("change", render);
    render();

    ["#dWind", "#dWt", "#dRt"].forEach((s) =>
      $(s).addEventListener("input", renderDesign)
    );
    document.querySelectorAll('input[name="dLogic"]').forEach((r) =>
      r.addEventListener("change", renderDesign)
    );
    renderDesign();
    drawCorr();
    drawOpt();
    // open the map on the biggest observed hit
    const top = [...HIST.storms].sort((a, b) => b.exp64 - a.exp64)[0];
    if (top) selectDesignStorm(top.sid);
  })
  .catch((e) => {
    $("#chart").innerHTML = $("#dScatter").innerHTML =
      '<p style="color:var(--critical)">Could not load data — ' +
      e.message + "</p>";
  });

/* =================== TAB 1: trigger design (observed) =================== */

const cap = (s) => s.charAt(0) + s.slice(1).toLowerCase();
const dParams = () => ({
  k: $("#dWind").value,
  wt: Math.max(0, +$("#dWt").value || 0),
  rt: Math.max(0, +$("#dRt").value || 0),
  logic: document.querySelector('input[name="dLogic"]:checked').value,
});
const dTriggered = (s, p) => {
  const w = s["exp" + p.k] >= p.wt;
  const r = s.rain >= p.rt;
  return p.logic === "AND" ? w && r : w || r;
};

function renderDesign() {
  const p = dParams();
  const storms = HIST.storms.map((s) => ({ ...s, trig: dTriggered(s, p) }));
  const trig = storms.filter((s) => s.trig);
  const n = trig.length;
  const target = HIST.target;
  const rp = n > 0 ? (HIST.n_seasons + 1) / n : null;
  const ta = trig.reduce((a, s) => a + s.affected, 0);
  const cerfHit = trig.filter((s) => s.cerf).length;
  const nCerf = storms.filter((s) => s.cerf).length;

  $("#dStats").innerHTML = [
    stat(target, "target activations (≤)", false),
    stat(n, "storms would have triggered" + (n > target ? " — too many" : ""),
         n > target),
    stat(rp ? rp.toFixed(1) : "∞", "seasons return period (Weibull)", false),
    stat(fmt(ta), "total people affected by triggered storms", false),
    stat(`${cerfHit}/${nCerf}`, "CERF-allocation storms captured",
         cerfHit < nCerf),
  ].join("");

  drawScatter(storms, p);
  drawDesignTable(storms, p);
}

function drawScatter(storms, p) {
  const W = 900, H = 620, padL = 74, padR = 18, padT = 18, padB = 52;
  const xmax = Math.max(p.wt * 1.15, ...storms.map((s) => s["exp" + p.k])) * 1.06 + 1;
  const ymax = Math.max(p.rt * 1.15, ...storms.map((s) => s.rain)) * 1.08 + 1;
  const x = (v) => padL + (v / xmax) * (W - padL - padR);
  const y = (v) => H - padB - (v / ymax) * (H - padT - padB);
  const maxTA = Math.max(...storms.map((s) => s.affected), 1);

  let sv = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMin meet">`;

  // trigger zone (gold): AND = upper-right rect; OR = L-shape as two strips
  const zx = x(Math.min(p.wt, xmax)), zy = y(Math.min(p.rt, ymax));
  const zone = `fill="var(--zone)" opacity="0.16"`;
  if (p.logic === "AND") {
    sv += `<rect x="${zx}" y="${padT}" width="${W - padR - zx}" height="${zy - padT}" ${zone}/>`;
  } else {
    sv += `<rect x="${zx}" y="${padT}" width="${W - padR - zx}" height="${H - padB - padT}" ${zone}/>`;
    sv += `<rect x="${padL}" y="${padT}" width="${zx - padL}" height="${zy - padT}" ${zone}/>`;
  }
  sv += `<text x="${W - padR - 8}" y="${padT + 16}" text-anchor="end"
          class="zone-label">Trigger zone</text>`;

  // axes + gridlines
  niceTicks(xmax, 5).forEach((v) => {
    sv += `<line class="gridline" x1="${x(v)}" y1="${padT}" x2="${x(v)}" y2="${H - padB}"/>`;
    sv += `<text class="axis-label" x="${x(v)}" y="${H - padB + 16}" text-anchor="middle">${abbr(v)}</text>`;
  });
  niceTicks(ymax, 5).forEach((v) => {
    sv += `<line class="gridline" x1="${padL}" y1="${y(v)}" x2="${W - padR}" y2="${y(v)}"/>`;
    sv += `<text class="axis-label" x="${padL - 8}" y="${y(v) + 3}" text-anchor="end">${Math.round(v)}</text>`;
  });
  sv += `<text class="axis-title" x="${(padL + W - padR) / 2}" y="${H - 8}" text-anchor="middle">
          population exposed to ${p.k} kt wind, AOI provinces [IBTrACS]</text>`;
  sv += `<text class="axis-title" x="14" y="${(padT + H - padB) / 2}" text-anchor="middle"
          transform="rotate(-90 14 ${(padT + H - padB) / 2})">2-day rainfall, country mean (mm) [IMERG]</text>`;

  // threshold lines
  sv += `<line x1="${zx}" y1="${padT}" x2="${zx}" y2="${H - padB}"
          stroke="var(--wind)" stroke-width="1.6" stroke-dasharray="5 4"/>`;
  sv += `<line x1="${padL}" y1="${zy}" x2="${W - padR}" y2="${zy}"
          stroke="var(--rain)" stroke-width="1.6" stroke-dasharray="5 4"/>`;

  // bubbles (impact-sized) + labels. Labelling every storm (as the marimo
  // did) stacks unreadably at the origin — label only storms that carry
  // information (triggered, CERF, impact, or meaningful exposure/rain);
  // the rest keep a dot and a hover tooltip.
  for (const s of storms) {
    const cx = x(s["exp" + p.k]), cy = y(s.rain);
    const r = 5 + Math.sqrt(s.affected / maxTA) * 42;
    const col = s.cerf ? "var(--critical)" : "var(--text-muted)";
    const sel = s.sid === dSelected;
    sv += `<circle class="d-pt" data-sid="${s.sid}" cx="${cx}" cy="${cy}" r="${r}"
            fill="${col}" opacity="0.3"${sel ? ' stroke="var(--text-primary)" stroke-width="1.5"' : ""}>
            <title>${esc(cap(s.name))} ${s.season} — exp${p.k} ${fmt(s["exp" + p.k])}, rain ${s.rain} mm, affected ${fmt(s.affected)}${s.cerf ? ", CERF" : ""}${s.trig ? " — TRIGGERED" : ""}. Click to map.</title></circle>`;
    const labelled =
      s.trig || s.cerf || s.affected > 0 ||
      s["exp" + p.k] > xmax * 0.05 || s.rain > ymax * 0.55;
    if (!labelled) {
      sv += `<circle cx="${cx}" cy="${cy}" r="2.5" fill="var(--text-muted)"/>`;
      continue;
    }
    sv += `<text x="${cx}" y="${cy - 1}" text-anchor="middle"
            class="pt-label${s.trig ? " trig" : ""}"
            fill="${s.cerf ? "var(--critical)" : "var(--text-primary)"}">${esc(cap(s.name))}</text>`;
    sv += `<text x="${cx}" y="${cy + 10}" text-anchor="middle"
            class="pt-label${s.trig ? " trig" : ""}"
            fill="${s.cerf ? "var(--critical)" : "var(--text-secondary)"}">${s.season}</text>`;
  }
  sv += `</svg>`;

  const legend =
    `<ul class="legend"><li><span class="dot" style="background:var(--critical)"></span>CERF allocation</li>
     <li><span class="dot" style="background:var(--text-muted)"></span>no CERF</li>
     <li><strong>bold</strong>&nbsp;= triggered</li>
     <li>bubble size = total affected (EM-DAT)</li>
     <li><span class="ln" style="border-color:var(--wind);border-top-style:dashed"></span>wind threshold</li>
     <li><span class="ln" style="border-color:var(--rain);border-top-style:dashed"></span>rain threshold</li></ul>`;
  $("#dScatter").innerHTML = sv + legend;
  $("#dScatter").querySelectorAll(".d-pt").forEach((c) =>
    c.addEventListener("click", () => selectDesignStorm(c.dataset.sid))
  );
}

function drawDesignTable(storms, p) {
  const maxTA = Math.max(...storms.map((s) => s.affected), 1);
  const maxW = Math.max(...storms.map((s) => s["exp" + p.k]), 1);
  const maxR = Math.max(...storms.map((s) => s.rain), 1);
  const rows = [...storms].sort(
    (a, b) => b.affected - a.affected ||
      b["exp" + p.k] - a["exp" + p.k] || b.rain - a.rain
  );
  const shade = (v, max, rgb) =>
    `background:color-mix(in srgb, ${rgb} ${Math.round((v / max) * 45)}%, transparent)`;
  let h = `<table><thead><tr><th>Cyclone</th>
    <th>Pop. exposed ${p.k} kt (AOI)</th><th>2-day rainfall (mm)</th>
    <th>Trigger?</th><th>CERF?</th><th>Total affected</th></tr></thead><tbody>`;
  for (const s of rows) {
    h += `<tr class="d-row ${s.trig ? "trig-row" : ""}${s.sid === dSelected ? " sel-row" : ""}" data-sid="${s.sid}">
      <td>${esc(cap(s.name))} ${s.season}</td>
      <td style="${shade(s["exp" + p.k], maxW, "var(--wind)")}">${fmt(s["exp" + p.k])}</td>
      <td style="${shade(s.rain, maxR, "var(--rain)")}">${Math.round(s.rain)}</td>
      <td>${s.trig ? '<span class="chip chip-trig">Yes</span>' : "No"}</td>
      <td>${s.cerf ? '<span class="chip chip-cerf">Yes</span>' : "No"}</td>
      <td><span class="impact-bar" style="--w:${Math.round((s.affected / maxTA) * 100)}%">${fmt(s.affected)}</span></td>
    </tr>`;
  }
  $("#dTable").innerHTML = h + "</tbody></table>";
  $("#dTable").querySelectorAll(".d-row").forEach((tr) =>
    tr.addEventListener("click", () => selectDesignStorm(tr.dataset.sid))
  );
}

/* ---------------- observed-swath map (design tab) ---------------- */

const RING_STYLE = {
  34: { color: "#e0a800", fillOpacity: 0.15 },
  50: { color: "#ee8434", fillOpacity: 0.22 },
  64: { color: "#c02526", fillOpacity: 0.32 },
};

function ensureDMap() {
  if (dMap) return;
  dMap = L.map("dMap", { scrollWheelZoom: false }).setView([-16.2, 167.7], 6);
  const dark =
    document.documentElement.dataset.theme === "dark" ||
    (document.documentElement.dataset.theme !== "light" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  L.tileLayer(
    `https://{s}.basemaps.cartocdn.com/${dark ? "dark_all" : "light_all"}/{z}/{x}/{y}{r}.png`,
    { attribution: "&copy; OpenStreetMap, &copy; CARTO", maxZoom: 11 }
  ).addTo(dMap);
}

function selectDesignStorm(sid) {
  dSelected = sid;
  const s = HIST.storms.find((x) => x.sid === sid);
  if (!s) return;
  $("#dMapTitle").textContent =
    `${cap(s.name)} (${s.season}) — observed swaths · ` +
    `64 kt: ${fmt(s.exp64)} · 50 kt: ${fmt(s.exp50)} · ` +
    `34 kt: ${fmt(s.exp34)} exposed · rain ${Math.round(s.rain)} mm`;
  ensureDMap();
  const p = obsGeomCache[sid]
    ? Promise.resolve(obsGeomCache[sid])
    : fetch(`data/obsgeom/${sid}.json`)
        .then((r) => r.json())
        .then((g) => (obsGeomCache[sid] = g));
  p.then((g) => {
    Object.keys(dLayers).forEach((k) => {
      dMap.removeLayer(dLayers[k]);
      delete dLayers[k];
    });
    if (CORE.aoi_geom) {
      dLayers.aoi = L.geoJSON(CORE.aoi_geom, {
        style: { color: "#1baf7a", weight: 2, fillOpacity: 0.25, fillColor: "#1baf7a" },
      }).addTo(dMap);
    }
    for (const speed of [34, 50, 64]) {
      const rings = g.rings[String(speed)];
      if (!rings) continue;
      const st = RING_STYLE[speed];
      dLayers["r" + speed] = L.polygon(ring2ll(rings), {
        color: st.color, weight: 1.5, fillColor: st.color,
        fillOpacity: st.fillOpacity,
      }).addTo(dMap);
    }
    if (g.track && g.track.length) {
      dLayers.track = L.polyline(tr2ll(g.track), {
        color: "#888", weight: 2, opacity: 0.85, dashArray: "1 6",
      }).addTo(dMap);
    }
    let b = null;
    ["aoi", "r34", "r50", "r64"].forEach((k) => {
      if (!dLayers[k]) return;
      const lb = dLayers[k].getBounds();
      b = b ? b.extend(lb) : L.latLngBounds(lb.getSouthWest(), lb.getNorthEast());
    });
    if (b && b.isValid()) dMap.fitBounds(b.pad(0.12));
    dMap.invalidateSize();
    renderDesign(); // refresh selection highlight
  });
}

/* correlations of each indicator with impact */
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

function drawCorr() {
  const inds = [
    ["exp34", "Exp 34 kt (AOI)"], ["exp50", "Exp 50 kt (AOI)"],
    ["exp64", "Exp 64 kt (AOI)"], ["rain", "Rainfall 2d"],
  ];
  const ta = HIST.storms.map((s) => s.affected);
  const cerf = HIST.storms.map((s) => (s.cerf ? 1 : 0));
  const panel = (title, ys) => {
    const W = 420, rowH = 28, padL = 120, padT = 24, H = padT + inds.length * rowH + 10;
    const x = (v) => padL + ((v + 1) / 2) * (W - padL - 12);
    let sv = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMin meet">`;
    sv += `<text class="axis-title" x="${padL}" y="14">${title}</text>`;
    sv += `<line class="gridline" x1="${x(0)}" y1="${padT}" x2="${x(0)}" y2="${H - 8}"/>`;
    [-1, -0.5, 0.5, 1].forEach((v) => {
      sv += `<text class="axis-label" x="${x(v)}" y="${H - 0}" text-anchor="middle">${v}</text>`;
    });
    inds.forEach(([key, label], i) => {
      const r = pearson(HIST.storms.map((s) => s[key]), ys);
      const yy = padT + i * rowH + 4;
      const x0 = Math.min(x(0), x(r)), w = Math.abs(x(r) - x(0));
      sv += `<text class="axis-label" x="${padL - 8}" y="${yy + 11}" text-anchor="end">${label}</text>`;
      sv += `<rect x="${x0}" y="${yy}" width="${Math.max(w, 1)}" height="15" rx="3"
              fill="${r >= 0 ? "var(--rain)" : "var(--critical)"}"/>`;
      sv += `<text class="axis-label" x="${x(r) + (r >= 0 ? 5 : -5)}" y="${yy + 11}"
              text-anchor="${r >= 0 ? "start" : "end"}">${r.toFixed(2)}</text>`;
    });
    return sv + "</svg>";
  };
  $("#dCorr").innerHTML =
    `<div>${panel("vs Total Affected", ta)}</div>` +
    `<div>${panel("vs CERF allocation", cerf)}</div>`;
}

/* grid-search: threshold combos activating exactly `target` storms,
   maximising total affected — a faithful port of the marimo optimiser */
function drawOpt() {
  const N = HIST.target;
  const S = HIST.storms;
  const ta = S.map((s) => s.affected);
  const label = (s) => `${cap(s.name)} ${s.season}`;
  const maxExp = Math.max(...[34, 50, 64].flatMap((k) => S.map((s) => s["exp" + k])));
  const maxRain = Math.max(...S.map((s) => s.rain)) + 5;

  const evalMask = (mask) => {
    let n = 0, t = 0;
    for (let i = 0; i < S.length; i++) if (mask[i]) { n++; t += ta[i]; }
    return [n, t];
  };
  const results = [];
  const consider = (best, scenario, wtLabel, rtLabel, mask, t) => {
    if (!best || t > best.t)
      return { scenario, wtLabel, rtLabel, t,
               storms: S.filter((_, i) => mask[i]).map(label).join(", ") };
    return best;
  };

  for (const k of [34, 50, 64]) {           // wind only
    let best = null;
    const exp = S.map((s) => s["exp" + k]);
    for (let wt = 0; wt <= maxExp + 1000; wt += 1000) {
      const mask = exp.map((v) => v >= wt);
      const [n, t] = evalMask(mask);
      if (n === N) best = consider(best, `Wind only (${k} kt)`, fmt(wt), "—", mask, t);
    }
    if (best) results.push(best);
  }
  {                                          // rain only
    let best = null;
    for (let rt = 0; rt <= maxRain; rt += 5) {
      const mask = S.map((s) => s.rain >= rt);
      const [n, t] = evalMask(mask);
      if (n === N) best = consider(best, "Rain only", "—", String(rt), mask, t);
    }
    if (best) results.push(best);
  }
  for (const logic of ["AND", "OR"]) {       // combinations
    for (const k of [34, 50, 64]) {
      let best = null;
      const exp = S.map((s) => s["exp" + k]);
      for (let wt = 0; wt <= maxExp + 1000; wt += 1000) {
        const wm = exp.map((v) => v >= wt);
        for (let rt = 0; rt <= maxRain; rt += 5) {
          const mask = S.map((s, i) =>
            logic === "AND" ? wm[i] && s.rain >= rt : wm[i] || s.rain >= rt
          );
          const [n, t] = evalMask(mask);
          if (n === N) best = consider(best, `${logic} (${k} kt)`, fmt(wt), String(rt), mask, t);
        }
      }
      if (best) results.push(best);
    }
  }

  const maxT = Math.max(...results.map((r) => r.t), 1);
  let h = `<table><thead><tr><th>Scenario</th><th>Wind thresh</th>
    <th>Rain thresh</th><th>Total affected</th><th>Storms</th></tr></thead><tbody>`;
  for (const r of results) {
    h += `<tr><td>${r.scenario}</td><td>${r.wtLabel}</td><td>${r.rtLabel}</td>
      <td><span class="impact-bar" style="--w:${Math.round((r.t / maxT) * 100)}%">${fmt(r.t)}</span></td>
      <td class="storm-list">${esc(r.storms)}</td></tr>`;
  }
  $("#dOpt").innerHTML = h + "</tbody></table>";
}

/* ------------------- return-period breakdown (forecast tab) ------------- */
function drawRP(rp) {
  const o = rp.observational, a = rp.action, c = rp.combined;
  const seasons = (xs) => xs.join(", ");
  const stormList = (xs) =>
    xs.map((h) => `${esc(h.name)} ${h.season}`).join(", ");
  const rows = [
    ["Observational (national, no leadtime)",
     `all ${o.scored_seasons}`,
     `${o.n_storms} storms / ${o.activated_seasons.length} seasons`,
     `${seasons(o.activated_seasons)}<br><span class="rp-storms">${stormList(o.storms)}</span>`,
     `<strong>${o.rp_seasons}</strong>`],
    ["Action leg (24–72 h forecast, AOI)",
     `${a.n_scored_seasons} (2005, 2012–25)`,
     `${a.n_storms} storms / ${a.activated_seasons.length} seasons` +
     ` — ${a.false_alarms.length} false alarms`,
     `${seasons(a.activated_seasons)}<br><span class="rp-storms">${stormList(a.storms)};` +
     ` FA: ${stormList(a.false_alarms)}</span>`,
     `<strong>${(rp.n_seasons + 1) / a.activated_seasons.length % 1 === 0
        ? ((rp.n_seasons + 1) / a.activated_seasons.length).toFixed(0)
        : ((rp.n_seasons + 1) / a.activated_seasons.length).toFixed(1)}</strong>` +
     `<br><span class="rp-storms">over all ${rp.n_seasons} seasons; forecast data covers ${a.n_scored_seasons}</span>`],
    ["Combined (either leg), scored data only",
     "—",
     `${c.scored_activated_seasons.length} activated seasons`,
     seasons(c.scored_activated_seasons),
     `<strong>${c.rp_scored_only}</strong>`],
    ["+ estimated activations in unscored seasons",
     `${c.unscored_seasons.length} seasons (${c.unscored_seasons[0]}–${c.unscored_seasons.at(-1)}) lack forecast decks`,
     `+${c.expected_extra} expected`,
     Object.entries(c.p_season)
       .map(([se, p]) => `${se}: P=${p}`).join(", ") +
       `<br><span class="rp-storms">${c.gap_storms.map((g) =>
         `${esc(g.name)} (obs swath ${g.dist_km === 0 ? "touched AOI" : g.dist_km + " km away"}, P=${g.p})`).join("; ")}</span>`,
     `<strong>${c.rp_median}</strong><br><span class="rp-storms">80% range ${c.rp_p90}–${c.rp_p10}</span>`],
  ];
  let h = `<table><thead><tr><th>Trigger</th><th>Seasons with data</th>
    <th>Activations</th><th>Activated seasons</th><th>RP (seasons)</th></tr></thead><tbody>`;
  for (const r of rows) {
    h += `<tr>${r.map((x, i) =>
      `<td class="${i >= 2 ? "rp-left" : ""}">${x}</td>`).join("")}</tr>`;
  }
  $("#rpTable").innerHTML = h + "</tbody></table>";
  $("#rpNote").innerHTML =
    `Return periods are Weibull, (${rp.n_seasons}+1) / activated seasons, over the ` +
    `${rp.first_season}–${rp.last_season} record at &ge;${fmt(rp.threshold)} people / 64&nbsp;kt. ` +
    `The framework's <strong>likely overall RP is ~1-in-${c.rp_p90}–${c.rp_p10} seasons</strong> ` +
    `(median ${c.rp_median}): the scored record alone gives ${c.rp_scored_only}, and the six ` +
    `2006–2011 seasons — where no JTWC forecast decks survive — very likely add activations ` +
    `(Gene 2008 and Atu 2011's observed 64&nbsp;kt swaths touched the AOI; the probabilities ` +
    `come from a logistic fit of action-leg outcome vs observed miss distance on the scored ` +
    `storms, and are floors — Kerry 2005, scored directly from recovered decks, fired from ` +
    `586&nbsp;km away, farther than the fit allows). The observational leg alone sits at ` +
    `${o.rp_seasons}; forecast false alarms are what pull the combined RP below it.`;
}

/* =================== TAB 2: forecast check ============================== */

/* ------------------------------------------------------------- summary */
function render() {
  const t = thresh();
  let rows = CORE.storms.map((s) => ({
    s,
    peak: peak(s),
    pA: peakLeg(s, "expA64"),
    obs: +(s.obs[SPEED] || 0),
  }));
  // the readiness leg (72-120 h) is deliberately not assessed: long-lead
  // 64 kt radii are too sparse in the historical record to score it fairly
  rows.forEach((r) => (r.fcst = r.pA >= t));

  const act = rows.filter((r) => r.pA >= t);
  const falseAlarm = rows.filter((r) => r.fcst && r.obs < t);
  const obsOnly = rows.filter((r) => !r.fcst && r.obs >= t);
  const cerf = rows.filter((r) => r.s.cerf);
  const cerfHit = cerf.filter((r) => r.fcst);

  $("#stats").innerHTML = [
    stat(act.length, "would have triggered the action leg (24–72 h, AOI)", act.length > 0),
    stat(falseAlarm.length, "of those stayed below threshold observed, anywhere in the country", falseAlarm.length > 0),
    stat(obsOnly.length, "caught only by the observational trigger (national, no leadtime)", obsOnly.length > 0),
    stat(`${cerfHit.length}/${cerf.length}`, "CERF-allocation storms captured by the action leg",
         cerfHit.length < cerf.length),
  ].join("");

  const cerfMiss = cerf.filter((r) => !r.fcst);
  $("#cerfNote").innerHTML = cerfMiss.length
    ? "CERF storm" + (cerfMiss.length > 1 ? "s" : "") + " below the action-leg threshold: " +
      cerfMiss.map((r) =>
        `<strong>${esc(r.s.name)}</strong> (action-window peak ${fmt(r.pA)} in the AOI; observed ${fmt(r.obs)} country-wide` +
        (r.obs >= t
          ? " — it would still have activated the framework via the observational trigger, whose scope is all of Vanuatu, but with no leadtime"
          : "") +
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
    : b.pA - a.pA || b.peak - a.peak || b.obs - a.obs
  );

  drawChart(rows, t);
  drawTable(rows, t);
}

function stat(v, k, alert) {
  return `<div class="stat${alert ? " alert" : ""}"><div class="v">${v}</div>
          <div class="k">${k}</div></div>`;
}

function drawChart(rows, t) {
  const W = 900, padL = 108, padR = 76, padT = 26, rowH = 22;
  const H = padT + rows.length * rowH + 30;
  const max = Math.max(t * 1.15,
    ...rows.map((r) => Math.max(r.pA, r.obs)), 1);
  const x = (v) => padL + (v / max) * (W - padL - padR);

  const ticks = niceTicks(max, 5);
  let sv = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMin meet">`;

  ticks.forEach((v) => {
    sv += `<line class="gridline" x1="${x(v)}" y1="${padT - 6}" x2="${x(v)}" y2="${H - 30}"/>`;
    sv += `<text class="axis-label" x="${x(v)}" y="${H - 16}" text-anchor="middle">${abbr(v)}</text>`;
  });
  sv += `<text class="axis-label" x="${padL}" y="${H - 3}" text-anchor="start">people exposed to ${SPEED} kt (forecast legs: AOI provinces &#183; observed: whole country)</text>`;

  rows.forEach((r, i) => {
    const y = padT + i * rowH;
    const over = r.fcst;
    sv += `<g class="bar-row${r.s.id === selected ? " sel" : ""}" data-id="${r.s.id}">`;
    sv += `<rect class="hit" x="0" y="${y - 2}" width="${W}" height="${rowH}" fill="transparent"/>`;
    // action-leg peak (upper), observed (lower) — 2px surface gap
    sv += bar(padL, y + 1, x(r.pA) - padL, 7, "var(--fcst)");
    sv += bar(padL, y + 10, x(r.obs) - padL, 7, "var(--obs)");
    const label = r.s.name + (r.s.cerf ? " ★" : "");
    sv += `<text class="storm-label${over ? " over" : ""}" x="${padL - 8}" y="${y + 13}" text-anchor="end">${esc(label)}</text>`;
    sv += `<text class="val-label" x="${Math.max(x(r.pA), x(r.obs)) + 6}" y="${y + 13}">${abbr(r.pA)} / ${abbr(r.obs)}</text>`;
    sv += `<title>${esc(r.s.name)} ${r.s.season} — action-window peak ${fmt(r.pA)} (AOI); observed ${fmt(r.obs)} (country)</title>`;
    sv += `</g>`;
  });

  sv += `<line class="threshline" x1="${x(t)}" y1="${padT - 10}" x2="${x(t)}" y2="${H - 30}"/>`;
  sv += `<text class="threshlabel" x="${x(t)}" y="${padT - 14}" text-anchor="middle">threshold ${abbr(t)}</text>`;
  sv += `</svg>`;

  const legend =
    `<ul class="legend"><li><span class="sw" style="background:var(--fcst)"></span>Action leg peak (24–72 h, JTWC, AOI)</li>
     <li><span class="sw" style="background:var(--obs)"></span>Observed (IBTrACS, whole country) — observational trigger</li>
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
    <th>Action 64kt (24–72h, AOI)</th>
    <th>Any-leadtime 64kt (AOI)</th><th>Observed 64kt (country)</th>
    <th>Observed 34kt (country)</th><th>Cycles</th></tr></thead><tbody>`;
  rows.forEach((r) => {
    h += `<tr><td>${esc(r.s.name)}${r.s.cerf ? " ★" : ""}</td><td>${r.s.season}</td>
      <td class="${r.pA >= t ? "over" : ""}">${fmt(r.pA)}</td>
      <td>${fmt(r.peak)}</td>
      <td class="${r.obs >= t ? "over" : ""}">${fmt(r.obs)}</td>
      <td>${fmt(+(r.s.obs["34"] || 0))}</td>
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
  const eA = +(c.expA64 || 0);
  const mark = (v) =>
    `<strong style="color:${v >= t ? "var(--critical)" : "inherit"}">${fmt(v)}</strong>${v >= t ? " — over threshold" : ""}`;
  $("#cycleInfo").innerHTML = `<dl>
    <dt>Forecast issued</dt><dd>${c.init}</dd>
    <dt>Peak forecast wind</dt><dd>${c.vmax} kt</dd>
    <dt>Action window 64 kt (24–72 h, AOI)</dt><dd>${mark(eA)}</dd>
    <dt>Any leadtime 64 kt (AOI)</dt><dd>${fmt(+c.exp["64"])}</dd>
    <dt>Exposed at 50 kt (AOI, any leadtime)</dt><dd>${fmt(+c.exp["50"])}</dd>
    <dt>Exposed at 34 kt (AOI, any leadtime)</dt><dd>${fmt(+c.exp["34"])}</dd>
    <dt>Observed at 64 kt (country)</dt><dd>${fmt(+(s.obs["64"] || 0))}</dd></dl>`;

  drawCycleChart(s, i);
}

function drawCycleChart(s, sel) {
  const W = 420, H = 170, padL = 52, padR = 12, padT = 14, padB = 30;
  const t = thresh();
  // the trigger reads the action window (24-72 h), so chart that
  const vals = s.cycles.map((c) => +(c.expA64 || 0));
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
