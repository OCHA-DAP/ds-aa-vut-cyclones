/* The trigger's historical record as the framework document shows it: the
   Trigger Mechanism Statistics panel (section III.2) and the storm table of
   Annex 2 ("Evaluation of trigger performance"). Shared by /trigger/ and
   /slides/ so both draw the published figures with the same code, from
   ../forecast-check/data/{rp,hist}.json. tests/test_pages_framework.py checks
   that those files still give the framework's numbers. */
(function () {
  // The endorsed trigger: at least 15,000 people exposed to 64 kt (10-minute)
  // winds, for all three triggers.
  const T = 15000;
  // No forecast record survives for 2006-11; these two storms' hurricane-force
  // winds reached or nearly reached the trigger provinces (one assumed activation).
  const ASSUMED = new Set(["GENE2008", "ATU2011"]);
  const ROWS = 20;

  const fmt = (n) => n.toLocaleString("en-US");
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /* Return periods by season (Weibull, (N + 1) / activated seasons), with the
     one assumed action activation added to the action and overall counts. The
     action trigger is scored where forecasts survive (scored_storms); the
     observational trigger over every season. */
  function stats(rp) {
    const N = rp.n_seasons, extra = rp.combined.extra_default ?? 1;
    const act = [...new Set(rp.scored_storms.filter((s) => s.peakA >= T).map((s) => s.season))].sort();
    const obs = [...rp.observational.activated_seasons].sort();
    const all = [...new Set([...act, ...obs])].sort();
    return {
      first: rp.first_season, last: rp.last_season, act, obs,
      rpAct: (N + 1) / (act.length + extra),
      rpObs: (N + 1) / obs.length,
      rpAll: (N + 1) / (all.length + extra),
    };
  }

  const years = (v) => (Math.round(v * 10) / 10).toFixed(1).replace(/\.0$/, "") + " years";
  const pct = (v) => Math.round(100 / v) + "%";

  function tmsPanel(rp) {
    const s = stats(rp);
    return `
    <div class="tms">
      <p class="tms-title">Trigger Mechanism Statistics</p>
      <div class="secbar"><span class="sec">Stats by trigger</span>
        <span class="yrs">Analysis years: ${s.first}-${s.last}</span></div>
      <table>
        <thead><tr><th>Trigger</th><th>Return period</th>
          <th>Activation<br>probability</th><th>Years activated</th></tr></thead>
        <tbody>
          <tr><td>Action</td><td>${years(s.rpAct)}</td><td>${pct(s.rpAct)}</td>
              <td>${s.act.join(", ")}, +1 assumed*</td></tr>
          <tr><td>Observational</td><td>${years(s.rpObs)}</td><td>${pct(s.rpObs)}</td>
              <td>${s.obs.join(", ")}</td></tr>
        </tbody>
      </table>
      <div class="overall">
        <span class="sec">Overall stats</span>
        <table><tbody>
          <tr><td class="lbl">Overall return period</td><td class="val">&le; ${years(s.rpAll)}</td></tr>
          <tr><td class="lbl">Overall probability of activation</td><td class="val">&ge; ${pct(s.rpAll)}</td></tr>
          <tr><td class="lbl">Average total spending per year</td><td class="val">&ge; $${(1.5 / s.rpAll).toFixed(1)}M</td></tr>
        </tbody></table>
      </div>
    </div>
    <p class="tms-foot">*One assumed action activation in 2008 (Gene) or 2011 (Atu): both
    storms' hurricane-force winds reached or nearly reached the trigger provinces, but no
    forecast records survive for 2006&ndash;11. The readiness window is not assessed (too
    little archived long-range data), so the overall return period is reported as &le; the
    computed value. Pre-arranged financing: $1.5M, disbursed in full on either an action or
    an observational activation.</p>`;
  }

  /* The Annex 2 rows: every storm with forecast or observed 64 kt exposure,
     impact, a CERF allocation or an assumed activation, then the closest
     remaining passes (most people exposed to gale-force winds) up to 20 rows;
     sorted by people affected. */
  function annexRows(rp, hist) {
    const scored = new Map(rp.scored_storms.map((s) => [s.name.toUpperCase() + s.season, s]));
    const all = hist.storms.map((h) => {
      const key = h.name.toUpperCase() + h.season;
      const s = scored.get(key);
      return {
        name: h.name.charAt(0) + h.name.slice(1).toLowerCase(),
        season: h.season,
        peakA: s ? s.peakA : null,
        obs: s ? s.obs : 0,
        cerf: h.cerf, affected: h.affected,
        exp34: h.exp34 || 0,
        assumed: ASSUMED.has(key),
      };
    });
    const shown = all.filter((r) =>
      (r.peakA || 0) > 0 || r.obs > 0 || r.cerf || r.affected > 0 || r.assumed);
    const rest = all.filter((r) => !shown.includes(r)).sort((x, y) => y.exp34 - x.exp34);
    return shown
      .concat(rest.slice(0, Math.max(0, ROWS - shown.length)))
      .sort((x, y) => y.affected - x.affected || y.obs - x.obs ||
        (y.peakA || 0) - (x.peakA || 0) || y.exp34 - x.exp34);
  }

  function annexTable(rp, hist) {
    const rows = annexRows(rp, hist);
    const maxTA = Math.max(...rows.map((r) => r.affected), 1);
    const body = rows.map((r) => {
      const f = r.assumed
        ? `<td class="num assumed">no record*</td>`
        : r.peakA === null
          ? `<td class="num muted">no record</td>`
          : `<td class="num${r.peakA >= T ? " met" : ""}">${fmt(r.peakA)}</td>`;
      return `<tr>
        <td>${esc(r.name)} ${r.season}</td>
        ${f}
        <td class="num${r.obs >= T ? " met" : ""}">${fmt(r.obs)}</td>
        <td class="center${r.cerf ? " cerf" : ""}">${r.cerf ? "&#10003;" : "&mdash;"}</td>
        <td><span class="impact-bar" style="--w:${Math.round((r.affected / maxTA) * 100)}%">${fmt(r.affected)}</span></td>
      </tr>`;
    }).join("");
    return `
    <table class="annex">
      <thead><tr>
        <th>Storm</th>
        <th class="num">64&nbsp;kt forecast exposure<br><span class="muted">action window 24&ndash;72&nbsp;h, AOI provinces</span></th>
        <th class="num">64&nbsp;kt observed exposure<br><span class="muted">whole country</span></th>
        <th class="center">CERF allocation</th>
        <th>EM-DAT total affected</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table>`;
  }

  window.VutRecord = { T, fmt, esc, stats, tmsPanel, annexRows, annexTable };
})();
