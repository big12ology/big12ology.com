import { seasonSummary, teamsForSeason } from "./stats.js?v=39";
import { renderSeasonCharts, renderAllTimeCharts, renderTeamCharts } from "./charts.js?v=39";
import { gameTooltipHTML } from "./gametip.js?v=39";

const $ = (sel) => document.querySelector(sel);
const num = (n) => n.toLocaleString("en-US");
// Data-sourced strings land in attributes; keep quotes and angle brackets out.
const esc = (t) => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;")
  .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const pct = (p) => (p * 100).toFixed(1) + "%";

// Current view state: computed summary plus sort order. Default sort matches
// the original sheet: season percent-full, best first.
const view = {
  season: null,
  summary: null,
  sort: { key: "pct", dir: "desc", metric: "pct" },
};

// The data are fetched, not linked, so assemble.sh's cache-bust check never
// sees them — it reads href/src in the HTML. Today's code against yesterday's
// JSON is the failure that hides: capacities and schedules change between
// builds, and the page looks merely wrong rather than stale. Take the version
// off this module's own URL so the two can never drift; the loader is the
// only place data enters the page.
const DATA_V = new URL(import.meta.url).search;

async function loadJSON(path) {
  const resp = await fetch(path + DATA_V);
  if (!resp.ok) throw new Error(`${path}: ${resp.status}`);
  return resp.json();
}

// Continuous fill scale, kept in sync with charts.js pctFill. Lightness
// comes from --pctl so light and dark themes each get a legible shade.
function pctHSL(p) {
  // Two regimes with a hard break at sold out. Below 100% the hue tops out
  // at 110 (yellow-green) — a not-quite-full house never reads truly green.
  // At 100% the hue jumps to green and over-capacity ramps toward teal, so
  // 100 vs 105 vs 110 are unmistakably different.
  if (p >= 1) {
    const u = Math.min((p - 1) / 0.10, 1); // 100%..110%+
    return [Math.round(145 + u * 35), Math.round(65 + u * 15)];
  }
  const A = [[0.55, 0], [0.70, 20], [0.80, 45], [0.90, 75],
             [0.95, 92], [1.00, 110]];
  let h = A[A.length - 1][1];
  if (p <= A[0][0]) h = A[0][1];
  else {
    for (let i = 1; i < A.length; i++) {
      if (p <= A[i][0]) {
        const t = (p - A[i - 1][0]) / (A[i][0] - A[i - 1][0]);
        h = A[i - 1][1] + t * (A[i][1] - A[i - 1][1]);
        break;
      }
    }
  }
  const s = h < 45 ? 100 - (h / 45) * 35 : 65;
  return [Math.round(h), Math.round(s)];
}
function pctColor(p) {
  const [h, s] = pctHSL(p);
  return `hsl(${h} ${s}% var(--pctl))`;
}
function pctSpan(p) {
  return `<span class="pct" style="color:${pctColor(p)}">${pct(p)}</span>`;
}

// Sort-value extractor per column key. Week and Season columns carry two
// metrics (raw attendance and percent full); view.sort.metric picks one.
// Missing values sort last regardless of direction.
function dualMetric(key) {
  return key.startsWith("week:") || key === "pct";
}

function sortValue(row, key, metric) {
  if (key === "team") return row.team.toLowerCase();
  if (key.startsWith("week:")) {
    const w = Number(key.slice(5));
    const wk = row.weeks.find((x) => x.week === w);
    if (!wk) return null;
    return (metric === "pct" ? wk.pct : wk.attendance) ?? null;
  }
  if (key === "pct") return (metric === "pct" ? row.pct : row.total) ?? null;
  return row[key] ?? null;
}

function sortedRows() {
  const { key, dir, metric } = view.sort;
  const sign = dir === "asc" ? 1 : -1;
  return [...view.summary.rows].sort((a, b) => {
    const va = sortValue(a, key, metric);
    const vb = sortValue(b, key, metric);
    if (va == null && vb == null) return a.team.localeCompare(b.team);
    if (va == null) return 1;
    if (vb == null) return -1;
    const cmp = typeof va === "string" ? va.localeCompare(vb) : va - vb;
    return cmp ? sign * cmp : a.team.localeCompare(b.team);
  });
}

function setSort(key) {
  if (view.sort.key === key) {
    if (dualMetric(key)) {
      // Cycle: raw desc -> raw asc -> % desc -> % asc -> raw desc ...
      const { dir, metric } = view.sort;
      if (dir === "desc") view.sort.dir = "asc";
      else {
        view.sort.dir = "desc";
        view.sort.metric = metric === "raw" ? "pct" : "raw";
      }
    } else {
      view.sort.dir = view.sort.dir === "desc" ? "asc" : "desc";
    }
  } else {
    // Fresh column: text starts ascending, numbers start descending.
    // Dual-metric columns start on the raw number.
    view.sort = { key, dir: key === "team" ? "asc" : "desc",
                  metric: key === "pct" ? "pct" : "raw" };
  }
  renderTable();
}

function fmtDateShort(iso) {
  if (!iso) return "";
  const [, m, d] = iso.split("-").map(Number);
  return `${MONTHS[m - 1]} ${d}`;
}

function renderTable() {
  const { summary, season } = view;
  const big12Era = season.big12Era !== false;
  const confOf = season.conferences ?? {};
  // A week is shown if it has any game — played, scheduled, or road/neutral.
  const scheduled = new Map(
    season.games
      .filter((g) => !g.role && g.attendance == null)
      .map((g) => [`${g.team}|${g.week}`, g])
  );
  const roadGames = new Map(
    season.games.filter((g) => g.role).map((g) => [`${g.team}|${g.week}`, g])
  );
  const homeInfo = new Map(
    season.games.filter((g) => !g.role).map((g) => [`${g.team}|${g.week}`, g])
  );
  // Per-team first/last week of any game — weeks between with no game are byes.
  const range = {};
  for (const g of season.games) {
    const r = (range[g.team] ??= { min: g.week, max: g.week });
    r.min = Math.min(r.min, g.week);
    r.max = Math.max(r.max, g.week);
  }
  const weekSet = new Set(season.games.map((g) => g.week));
  const activeWeeks = summary.weeks
    .filter((w) => w.games > 0 || weekSet.has(w.week))
    .map((w) => w.week);

  const arrow = (key) => {
    if (view.sort.key !== key) return "";
    const a = view.sort.dir === "desc" ? "▾" : "▴";
    if (!dualMetric(key)) return ` ${a}`;
    return ` <span class="sortmode">${view.sort.metric === "pct" ? "%" : "#"}${a}</span>`;
  };
  const th = (key, label, cls = "") =>
    `<th class="sortable ${cls}" data-sort="${key}"${
      view.sort.key === key
        ? ` aria-sort="${view.sort.dir === "desc" ? "descending" : "ascending"}"`
        : ""
    }${dualMetric(key) ? ' title="Click cycles: attendance ▾▴, then percent full ▾▴"' : ""}>${label}${arrow(key)}</th>`;

  const head = `<thead><tr>
      ${th("team", "Team", "team")}${th("games", "G")}${th("capacity", "Capacity")}
      ${activeWeeks.map((w) => th(`week:${w}`, season.weekLabels[w])).join("")}
      ${th("pct", "Season")}</tr></thead>`;

  const body = sortedRows()
    .map((row) => {
      const byWeek = Object.fromEntries(row.weeks.map((w) => [w.week, w]));
      const cells = activeWeeks
        .map((w) => {
          const g = byWeek[w];
          if (g) {
            const info = homeInfo.get(`${row.team}|${w}`);
            let sub = "";
            if (info?.opponent) {
              const result =
                info.pointsFor != null
                  ? ` · <span class="${info.pointsFor > info.pointsAgainst ? "win" : "loss"}">${info.pointsFor > info.pointsAgainst ? "W" : "L"} ${info.pointsFor}–${info.pointsAgainst}</span>`
                  : "";
              sub = `<span class="sub" title="${esc(info.opponent)}">` +
                `${info.opponent}${result}</span>`;
            }
            return `<td class="game" data-team="${row.team}" data-week="${w}">${num(g.attendance)}${pctSpan(g.pct)}${sub}</td>`;
          }
          const s = scheduled.get(`${row.team}|${w}`);
          if (s)
            return `<td class="game sched" data-team="${row.team}" data-week="${w}"><span class="opp" title="${esc(s.opponent ?? "TBD")}">${s.opponent ?? "TBD"}</span><span class="pct">${fmtDateShort(s.date)}</span></td>`;
          const r = roadGames.get(`${row.team}|${w}`);
          if (r) {
            const at = r.role === "neutral" ? "vs" : "@";
            const sub =
              r.pointsFor != null
                ? `${r.pointsFor > r.pointsAgainst ? "W" : "L"} ${r.pointsFor}–${r.pointsAgainst}`
                : fmtDateShort(r.date);
            return `<td class="game away" data-team="${row.team}" data-week="${w}"><span class="opp" title="${esc(at + " " + (r.opponent ?? "TBD"))}">${at} ${r.opponent ?? "TBD"}</span><span class="pct">${sub}</span></td>`;
          }
          const tr = range[row.team];
          if (tr && w > tr.min && w < tr.max)
            return `<td class="bye">BYE</td>`;
          return "<td></td>";
        })
        .join("");
      const stadiumLabel = row.multiVenue
        ? [...new Set(row.weeks.map((w) => w.venue).filter(Boolean))].join(" / ")
        : row.stadium;
      const logo = row.logo ? `<img class="team-logo" src="${row.logo}" alt="" loading="lazy" />` : "";
      const confTag = big12Era ? "" :
        `<span class="conf">${confOf[row.team] ?? "—"}</span>`;
      return `<tr>
        <td class="team" style="--tc:${row.color ?? "transparent"}">${logo}<span class="team-name">${row.team}${confTag}<span class="stadium">${stadiumLabel}</span></span></td>
        <td>${row.games}</td><td>${row.capacity != null ? num(row.capacity) : "varies"}${row.capacityEstimate ? ` <abbr class=estmark title="${esc(row.capacityEstimate)}">est</abbr>` : ""}${row.capacityDisputed ? ` <abbr class=capmark title="${esc(row.capacityDisputed)}">*</abbr>` : ""}</td>${cells}
        <td class="season-total">${num(row.total)}${pctSpan(row.pct)}</td>
      </tr>`;
    })
    .join("");

  // Before 2024 these sixteen programs were spread across as many as five
  // conferences, so league-wide totals are meaningless; the table shows each
  // team's conference of the day instead and drops the aggregate rows.
  const wk = (w) => summary.weeks[w];
  const foot = !big12Era ? "" : `<tfoot>
      <tr><td class="team">Big 12 total</td><td>${summary.totals.games}</td>
        <td>${num(summary.rows.reduce((s, r) => s + r.capacity, 0))}</td>
        ${activeWeeks.map((w) => (wk(w).games ? `<td>${num(wk(w).attendance)}${pctSpan(wk(w).pct)}</td>` : "<td>—</td>")).join("")}
        <td class="season-total">${num(summary.totals.attendance)}${pctSpan(summary.totals.pct)}</td></tr>
      <tr class="sub"><td class="team">Capacity in play / games</td><td></td><td></td>
        ${activeWeeks.map((w) => (wk(w).games ? `<td>${num(wk(w).capacity)} · ${wk(w).games}g</td>` : "<td>—</td>")).join("")}
        <td>${num(summary.totals.capacity)} · ${summary.totals.games}g</td></tr>
    </tfoot>`;

  $("#attendance-table").innerHTML = head + `<tbody>${body}</tbody>` + foot;
  tooltipEl().hidden = true;
  tipFor = null;
}

// ---- game-detail tooltip ----------------------------------------------

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function fmtDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  return `${DAYS[dt.getDay()]}, ${MONTHS[m - 1]} ${d}, ${y}`;
}

function fmtTime(hhmm) {
  if (!hhmm) return "";
  let [h, m] = hhmm.split(":").map(Number);
  const ap = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  return `${h}:${String(m).padStart(2, "0")} ${ap}`;
}

function tooltipHTML(team, week) {
  const game = view.season.games.find((g) => g.team === team && g.week === week);
  if (!game) return null;
  const row = view.summary.rows.find((r) => r.team === team);
  const wk = row ? row.weeks.find((w) => w.week === week) : null;
  return gameTooltipHTML({
    game,
    weekLabel: view.season.weekLabels[week],
    wk,
    cap: game.capacity ?? (row ? row.capacity : null),
  });
}

let hideTimer = null;
let tipFor = null;

function tooltipEl() {
  let el = $("#tooltip");
  if (!el) {
    el = document.createElement("div");
    el.id = "tooltip";
    el.hidden = true;
    document.body.appendChild(el);
    el.addEventListener("mouseenter", () => clearTimeout(hideTimer));
    el.addEventListener("mouseleave", hideTooltip);
  }
  return el;
}

// mouseover fires again for every child span inside a cell; re-rendering and
// re-placing the card mid-hover makes it twitch under the pointer.
function showTooltip(td) {
  if (td === tipFor && !tooltipEl().hidden) {
    clearTimeout(hideTimer);
    return;
  }
  const html = tooltipHTML(td.dataset.team, Number(td.dataset.week));
  if (!html) return;
  clearTimeout(hideTimer);
  tipFor = td;
  const el = tooltipEl();
  el.innerHTML = html;
  // Anchored under a cell, not trailing the cursor: styles.css bridges the
  // 6px below so the pointer can reach the link without crossing the row
  // underneath. Charts clear this when they borrow the same element.
  el.dataset.anchor = "cell";
  el.hidden = false;
  const r = td.getBoundingClientRect();
  el.style.left = "0px";
  el.style.top = "0px";
  const w = el.offsetWidth;
  let x = window.scrollX + r.left + r.width / 2 - w / 2;
  x = Math.max(8, Math.min(x, window.scrollX + document.documentElement.clientWidth - w - 8));
  el.style.left = `${x}px`;
  el.style.top = `${window.scrollY + r.bottom + 6}px`;
}

function hideTooltip() {
  hideTimer = setTimeout(() => {
    tooltipEl().hidden = true;
    tipFor = null;
  }, 150);
}

function render(teamsData, season) {
  const numWeeks = season.weekLabels.length;
  const teams = teamsForSeason(teamsData, season.season);
  view.season = season;
  view.summary = seasonSummary(teams, season.games, numWeeks);
  const summary = view.summary;
  const activeWeeks = summary.weeks.filter((w) => w.games > 0).map((w) => w.week);

  // If the sorted column is a week that doesn't exist in this season, fall
  // back to the default sort.
  if (view.sort.key.startsWith("week:") && !activeWeeks.includes(Number(view.sort.key.slice(5)))) {
    view.sort = { key: "pct", dir: "desc", metric: "pct" };
  }

  const scheduledCount = season.games.filter((g) => !g.role && g.attendance == null).length;
  if (season.big12Era === false) {
    const leagues = [...new Set(Object.values(season.conferences ?? {}))].sort();
    $("#summary").innerHTML =
      `<div class="card era-note">` +
      `<div class="label">Before the current Big 12 composition</div>` +
      `<div class="era-text">The Big 12 existed in ${season.season}, with a ` +
      `different membership. These sixteen programs played in ` +
      `${leagues.length} different conferences (${leagues.join(", ")}). ` +
      `Per-team figures below are accurate; conference totals and league-wide ` +
      `rankings don't apply to this season, so they aren't shown.</div></div>`;
  } else {
  $("#summary").innerHTML = [
    ["Total attendance", num(summary.totals.attendance)],
    ["Percent full", pct(summary.totals.pct)],
    [
      "Games",
      scheduledCount
        ? `${num(summary.totals.games)} <span class="of">of ${num(summary.totals.games + scheduledCount)}</span>`
        : num(summary.totals.games),
    ],
    ["Weeks played", num(summary.weeks.filter((w) => w.games > 0).length)],
  ]
    .map(
      ([label, value]) =>
        `<div class="card"><div class="label">${label}</div><div class="value">${value}</div></div>`
    )
    .join("");
  }

  renderTable();

  const empty = $("#empty-note");
  const hasPlayed = summary.totals.games > 0;
  const hasSchedule = season.games.length > 0;
  empty.hidden = hasPlayed;
  if (!hasSchedule) {
    empty.textContent = `No schedule loaded yet for the ${season.season} season.`;
  } else if (!hasPlayed) {
    empty.textContent = `Schedule loaded — attendance replaces each matchup as games are played.`;
  }
  $("#source-note").textContent = `Source: ${season.source}. Percent full is attendance ÷ capacity, per game; season percent divides by the sum of per-game capacities. Capacities are season-specific (current year from athletic departments, past years from stadium records). Click a column header to sort; game and season columns cycle raw attendance and percent full, descending then ascending. Team and conference marks via Wikimedia Commons (provenance in the repo); trademarks belong to their owners. An asterisk marks a published capacity this tracker does not trust as a denominator. Kansas State is the only one carrying it: the athletic department lists an official capacity of 50,000 that has not moved through seven stadium projects since 2013, while the same page claims a largest crowd of 53,811 — and K-State clears 100 percent in 72 of its 89 home games here. That gap is a stale published number, not proof of what any crowd actually was; several schools also sell standing room, so figures above 100 percent are expected league-wide.`;
}

async function main() {
  const [index, teamsData] = await Promise.all([
    loadJSON("data/seasons/index.json"),
    loadJSON("data/teams.json"),
  ]);
  const seasonsData = Object.fromEntries(
    await Promise.all(
      index.seasons.map(async (y) => [y, await loadJSON(`data/seasons/${y}.json`)])
    )
  );

  const select = $("#season");
  // Newest first, for two reasons. It is the order a reader wants, and the
  // native popup positions itself so the selected option sits under the
  // pointer — with the current season last, fourteen options opened upward
  // off the top of the window.
  select.innerHTML = [...index.seasons]
    .sort((a2, b2) => b2 - a2)
    .map((y) => `<option value="${y}" ${y === index.default ? "selected" : ""}>${y}</option>`)
    .join("");

  // --- era scrubber -------------------------------------------------------
  // The archive spans fourteen seasons; these let a reader narrow the
  // cumulative charts to a stretch that interests them (a coach's tenure,
  // the pre-realignment years) without touching the per-season views.
  const allYears = index.seasons.map(Number).sort((a2, b2) => a2 - b2);
  const era = { from: allYears[0], to: allYears[allYears.length - 1] };
  const inEra = () => Object.fromEntries(
    Object.entries(seasonsData)
      .filter(([y]) => Number(y) >= era.from && Number(y) <= era.to));

  const EXCLUDED = { 2020: "COVID capacity caps and missing announcements" };

  // Years missing from the archive that fall inside the chosen span — the
  // charts say so rather than letting a gap pass as continuity.
  const eraGaps = () => Object.keys(EXCLUDED).map(Number)
    .filter((y) => y > era.from && y < era.to);

  const pctOf = (i) => (allYears.length > 1
    ? (i / (allYears.length - 1)) * 100 : 0);

  // Repaint a bar's own chrome from the current era. Deliberately does NOT
  // touch innerHTML: rebuilding the input mid-drag is what limited dragging
  // to one step per press.
  function paintEraBar(el, opts) {
    const from = el.querySelector(".era-from");
    const to = el.querySelector(".era-to");
    const a2 = allYears.indexOf(era.from);
    const b2 = allYears.indexOf(era.to);
    if (opts && opts.syncInputs) {
      from.value = a2;
      to.value = b2;
    }
    el.querySelector(".era-label").innerHTML =
      `Seasons <b>${era.from}</b>–<b>${era.to}</b>`;
    const fill = el.querySelector(".era-fill");
    fill.style.left = `${pctOf(a2)}%`;
    fill.style.right = `${100 - pctOf(b2)}%`;
    const skip = el.querySelector(".era-skip");
    const gaps = eraGaps();
    skip.textContent = gaps.length ? `${gaps.join(", ")} excluded` : "";
  }

  function buildEraBar(el) {
    const n = allYears.length - 1;
    const iFrom = allYears.indexOf(era.from);
    const iTo = allYears.indexOf(era.to);
    el.innerHTML =
      `<span class="era-label"></span>` +
      `<span class="era-slider">` +
        `<span class="era-track"></span>` +
        `<span class="era-fill"></span>` +
        `<input type="range" class="era-from" min="0" max="${n}" step="1" ` +
          `value="${iFrom}" aria-label="First season">` +
        `<input type="range" class="era-to" min="0" max="${n}" step="1" ` +
          `value="${iTo}" aria-label="Last season">` +
      `</span>` +
      `<span class="era-quick">` +
        `<button class="era-last" data-n="5">Last 5</button>` +
        `<button class="era-last" data-n="10">Last 10</button>` +
        `<button class="era-reset">All</button>` +
      `</span>` +
      `<span class="era-skip"></span>`;
    const from = el.querySelector(".era-from");
    const to = el.querySelector(".era-to");

    // While dragging: update the label and fill only, so the pointer keeps
    // its grip and the year follows the thumb continuously.
    const drag = () => {
      const lo = Math.min(Number(from.value), Number(to.value));
      const hi = Math.max(Number(from.value), Number(to.value));
      era.from = allYears[lo];
      era.to = allYears[hi];
      document.querySelectorAll(".erabar").forEach((bar) =>
        paintEraBar(bar, { syncInputs: bar !== el }));
    };
    // On release: redraw the charts once, not on every step.
    const commit = () => redrawEraCharts();
    from.addEventListener("input", drag);
    to.addEventListener("input", drag);
    from.addEventListener("change", commit);
    to.addEventListener("change", commit);
    const setSpan = (fromYear, toYear) => {
      era.from = fromYear;
      era.to = toYear;
      document.querySelectorAll(".erabar").forEach((bar) =>
        paintEraBar(bar, { syncInputs: true }));
      redrawEraCharts();
    };
    el.querySelector(".era-reset").onclick = () =>
      setSpan(allYears[0], allYears[allYears.length - 1]);
    el.querySelectorAll(".era-last").forEach((btn) => {
      btn.onclick = () => {
        const n2 = Math.min(Number(btn.dataset.n), allYears.length);
        setSpan(allYears[allYears.length - n2],
                allYears[allYears.length - 1]);
      };
    });

    // Grab the filled segment to slide the whole window, keeping its length
    // — pick a five-season span, shift it two years earlier, still five.
    const fill = el.querySelector(".era-fill");
    const slider = el.querySelector(".era-slider");
    fill.addEventListener("pointerdown", (e) => {
      const n3 = allYears.length - 1;
      if (!n3) return;
      const startX = e.clientX;
      const startFrom = allYears.indexOf(era.from);
      const startTo = allYears.indexOf(era.to);
      const span = startTo - startFrom;
      const stepPx = slider.getBoundingClientRect().width / n3;
      fill.setPointerCapture(e.pointerId);
      fill.classList.add("dragging");
      const move = (ev) => {
        let delta = Math.round((ev.clientX - startX) / stepPx);
        delta = Math.max(-startFrom, Math.min(delta, n3 - startTo));
        era.from = allYears[startFrom + delta];
        era.to = allYears[startFrom + delta + span];
        document.querySelectorAll(".erabar").forEach((bar) =>
          paintEraBar(bar, { syncInputs: true }));
      };
      const up = () => {
        fill.classList.remove("dragging");
        fill.removeEventListener("pointermove", move);
        fill.removeEventListener("pointerup", up);
        redrawEraCharts();
      };
      fill.addEventListener("pointermove", move);
      fill.addEventListener("pointerup", up);
      e.preventDefault();
    });

    paintEraBar(el, { syncInputs: true });
  }

  function redrawEraCharts() {
    const scoped = inEra();
    renderAllTimeCharts($("#charts-alltime"), teamsData, scoped, eraGaps());
    renderTeamCharts($("#charts-teams"), teamsData, seasonsData,
                     select.value, selectedTeams, scoped, eraGaps());
  }

  const selectedTeams = new Set();
  const drawTeamCharts = () =>
    renderTeamCharts($("#charts-teams"), teamsData, seasonsData,
                     select.value, selectedTeams, inEra(), eraGaps());
  const renderChips = () => {
    const box = $("#team-chips");
    const teams = teamsForSeason(teamsData, Number(select.value));
    box.innerHTML = teams.map((t) =>
      `<button data-team="${t.team}" class="${selectedTeams.has(t.team) ? "on" : ""}"
        style="${selectedTeams.has(t.team) ? `background:${t.color};border-color:${t.color}` : ""}">
        ${t.logo ? `<img src="${t.logo}" alt="">` : ""}${t.team}</button>`).join("");
    box.querySelectorAll("button").forEach((b) => {
      b.onclick = () => {
        const team = b.dataset.team;
        selectedTeams.has(team) ? selectedTeams.delete(team)
                                : selectedTeams.add(team);
        renderChips();
        drawTeamCharts();
      };
    });
  };

  const show = (year) => {
    render(teamsData, seasonsData[year]);
    renderSeasonCharts($("#charts-season"), teamsData, seasonsData, year);
    renderAllTimeCharts($("#charts-alltime"), teamsData, inEra(), eraGaps());
    document.querySelectorAll(".erabar").forEach(buildEraBar);
    renderChips();
    drawTeamCharts();
  };

  // module tabs (hash-routed)
  const TABS = ["season", "charts", "alltime", "teams"];
  const setTab = (tab) => {
    if (!TABS.includes(tab)) tab = "season";
    for (const t of TABS) {
      document.querySelector(`#view-${t}`).hidden = t !== tab;
    }
    document.querySelectorAll("#tabs a").forEach((a) =>
      a.classList.toggle("on", a.dataset.tab === tab));
    document.body.classList.toggle("tab-alltime", tab === "alltime");
  };
  window.addEventListener("hashchange", () =>
    setTab(location.hash.slice(1)));
  setTab(location.hash.slice(1));
  select.addEventListener("change", () => show(select.value));
  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => show(select.value));

  // Delegated listeners survive table re-renders.
  const table = $("#attendance-table");
  table.addEventListener("click", (e) => {
    const header = e.target.closest("th[data-sort]");
    if (header) setSort(header.dataset.sort);
  });
  table.addEventListener("mouseover", (e) => {
    const td = e.target.closest("td.game");
    if (td) showTooltip(td);
  });
  table.addEventListener("mouseout", (e) => {
    if (e.target.closest("td.game")) hideTooltip();
  });

  show(index.default);
}

main().catch((err) => {
  $("#empty-note").hidden = false;
  $("#empty-note").textContent = `Failed to load data: ${err.message}`;
});


// The week grid scrolls sideways and nothing said so — people read Games and
// Capacity as the whole table. Fade the live edge, shadow the pinned team
// column, and drop both cues once there is nothing more to see.
(function scrollCues() {
  const wrap = $("#table-scroll");
  const hint = $("#scroll-hint");
  if (!wrap) return;
  const update = () => {
    const max = wrap.scrollWidth - wrap.clientWidth;
    const scrollable = max > 2;
    wrap.classList.toggle("at-end", !scrollable || wrap.scrollLeft >= max - 2);
    wrap.classList.toggle("at-start", wrap.scrollLeft <= 2);
    // Fades out once you have reached the end; it has done its job.
    if (hint) {
      hint.hidden = !scrollable;
      hint.style.opacity = wrap.classList.contains("at-end") ? "0" : "1";
      hint.style.transition = "opacity .2s ease";
    }
  };
  wrap.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update);
  new ResizeObserver(update).observe(wrap);
  new MutationObserver(update).observe(wrap, { childList: true, subtree: true });
  update();
})();
