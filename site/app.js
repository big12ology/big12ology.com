import { seasonSummary, teamsForSeason } from "./stats.js";

const $ = (sel) => document.querySelector(sel);
const num = (n) => n.toLocaleString("en-US");
const pct = (p) => (p * 100).toFixed(1) + "%";

// Current view state: computed summary plus sort order. Default sort matches
// the original sheet: season percent-full, best first.
const view = {
  season: null,
  summary: null,
  sort: { key: "pct", dir: "desc" },
};

async function loadJSON(path) {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(`${path}: ${resp.status}`);
  return resp.json();
}

function pctClass(p) {
  if (p >= 1) return "pct full";
  if (p < 0.85) return "pct low";
  return "pct";
}

// Sort-value extractor per column key. Week columns use that week's
// attendance. Missing values sort last regardless of direction.
function sortValue(row, key) {
  if (key === "team") return row.team.toLowerCase();
  if (key.startsWith("week:")) {
    const w = Number(key.slice(5));
    return row.weeks.find((x) => x.week === w)?.attendance ?? null;
  }
  return row[key] ?? null;
}

function sortedRows() {
  const { key, dir } = view.sort;
  const sign = dir === "asc" ? 1 : -1;
  return [...view.summary.rows].sort((a, b) => {
    const va = sortValue(a, key);
    const vb = sortValue(b, key);
    if (va == null && vb == null) return a.team.localeCompare(b.team);
    if (va == null) return 1;
    if (vb == null) return -1;
    const cmp = typeof va === "string" ? va.localeCompare(vb) : va - vb;
    return cmp ? sign * cmp : a.team.localeCompare(b.team);
  });
}

function setSort(key) {
  if (view.sort.key === key) {
    view.sort.dir = view.sort.dir === "desc" ? "asc" : "desc";
  } else {
    // Fresh column: text starts ascending, numbers start descending.
    view.sort = { key, dir: key === "team" ? "asc" : "desc" };
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
  // A week is shown if it has any game — played, scheduled, or road/neutral.
  const scheduled = new Map(
    season.games
      .filter((g) => !g.role && g.attendance == null)
      .map((g) => [`${g.team}|${g.week}`, g])
  );
  const roadGames = new Map(
    season.games.filter((g) => g.role).map((g) => [`${g.team}|${g.week}`, g])
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

  const arrow = (key) =>
    view.sort.key === key ? (view.sort.dir === "desc" ? " ▾" : " ▴") : "";
  const th = (key, label, cls = "") =>
    `<th class="sortable ${cls}" data-sort="${key}"${
      view.sort.key === key
        ? ` aria-sort="${view.sort.dir === "desc" ? "descending" : "ascending"}"`
        : ""
    }>${label}${arrow(key)}</th>`;

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
          if (g)
            return `<td class="game" data-team="${row.team}" data-week="${w}">${num(g.attendance)}<span class="${pctClass(g.pct)}">${pct(g.pct)}</span></td>`;
          const s = scheduled.get(`${row.team}|${w}`);
          if (s)
            return `<td class="game sched" data-team="${row.team}" data-week="${w}"><span class="opp">${s.opponent ?? "TBD"}</span><span class="pct">${fmtDateShort(s.date)}</span></td>`;
          const r = roadGames.get(`${row.team}|${w}`);
          if (r) {
            const at = r.role === "neutral" ? "vs" : "@";
            const sub =
              r.pointsFor != null
                ? `${r.pointsFor > r.pointsAgainst ? "W" : "L"} ${r.pointsFor}–${r.pointsAgainst}`
                : fmtDateShort(r.date);
            return `<td class="game away" data-team="${row.team}" data-week="${w}"><span class="opp">${at} ${r.opponent ?? "TBD"}</span><span class="pct">${sub}</span></td>`;
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
      return `<tr>
        <td class="team">${row.team}<span class="stadium">${stadiumLabel}</span></td>
        <td>${row.games}</td><td>${row.capacity != null ? num(row.capacity) : "varies"}</td>${cells}
        <td class="season-total">${num(row.total)}<span class="${pctClass(row.pct)}">${pct(row.pct)}</span></td>
      </tr>`;
    })
    .join("");

  const wk = (w) => summary.weeks[w];
  const foot = `<tfoot>
      <tr><td class="team">Big 12 total</td><td>${summary.totals.games}</td>
        <td>${num(summary.rows.reduce((s, r) => s + r.capacity, 0))}</td>
        ${activeWeeks.map((w) => (wk(w).games ? `<td>${num(wk(w).attendance)}<span class="${pctClass(wk(w).pct)}">${pct(wk(w).pct)}</span></td>` : "<td>—</td>")).join("")}
        <td class="season-total">${num(summary.totals.attendance)}<span class="${pctClass(summary.totals.pct)}">${pct(summary.totals.pct)}</span></td></tr>
      <tr class="sub"><td class="team">Capacity in play / games</td><td></td><td></td>
        ${activeWeeks.map((w) => (wk(w).games ? `<td>${num(wk(w).capacity)} · ${wk(w).games}g</td>` : "<td>—</td>")).join("")}
        <td>${num(summary.totals.capacity)} · ${summary.totals.games}g</td></tr>
    </tfoot>`;

  $("#attendance-table").innerHTML = head + `<tbody>${body}</tbody>` + foot;
  tooltipEl().hidden = true;
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
  const wk = row.weeks.find((w) => w.week === week);
  const cap = game.capacity ?? row.capacity;

  const lines = [];
  const when = [fmtDate(game.date), fmtTime(game.time)].filter(Boolean).join(" · ");
  lines.push(`<div class="tip-head">${view.season.weekLabels[week]}${when ? " · " + when : ""}</div>`);
  if (game.opponent) {
    let result = "";
    if (game.pointsFor != null) {
      const won = game.pointsFor > game.pointsAgainst;
      result = ` — <strong class="${won ? "win" : "loss"}">${won ? "W" : "L"} ${game.pointsFor}–${game.pointsAgainst}</strong>`;
    }
    const prep = game.role === "away" ? "at" : "vs";
    lines.push(`<div class="tip-opp">${prep} ${game.opponent}${result}</div>`);
  }
  if (game.role) {
    const where = [game.venue, game.city ? `${game.city}, ${game.state ?? ""}`.replace(/, $/, "") : null]
      .filter(Boolean)
      .join(" · ");
    if (where) lines.push(`<div>${where}${game.role === "neutral" ? " (neutral site)" : ""}</div>`);
    if (game.attendance != null) lines.push(`<div class="tip-wx">Attendance ${num(game.attendance)}</div>`);
  } else if (wk) {
    lines.push(
      `<div>${num(wk.attendance)} · ${pct(wk.pct)}${cap ? ` of ${num(cap)}` : ""}${game.venue ? ` · ${game.venue}` : ""}</div>`
    );
  }
  if (game.weather) {
    const w = game.weather;
    const parts = [`${w.tempF}°F`];
    if (w.windMph != null) parts.push(`wind ${w.windMph} mph`);
    if (w.precipIn > 0) parts.push(`${w.precipIn}" precip`);
    lines.push(`<div class="tip-wx">${parts.join(" · ")}</div>`);
  }
  if (game.attendanceSource) {
    lines.push(`<div class="tip-src">Attendance: ${game.attendanceSource}</div>`);
  }
  if (game.espnId) {
    const played = game.attendance != null || game.pointsFor != null;
    const link = played
      ? `https://www.espn.com/college-football/boxscore/_/gameId/${game.espnId}`
      : `https://www.espn.com/college-football/game/_/gameId/${game.espnId}`;
    lines.push(
      `<a href="${link}" target="_blank" rel="noopener">${played ? "Box score" : "Game preview"} ↗</a>`
    );
  }
  return lines.join("");
}

let hideTimer = null;

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

function showTooltip(td) {
  const html = tooltipHTML(td.dataset.team, Number(td.dataset.week));
  if (!html) return;
  clearTimeout(hideTimer);
  const el = tooltipEl();
  el.innerHTML = html;
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
    view.sort = { key: "pct", dir: "desc" };
  }

  const scheduledCount = season.games.filter((g) => !g.role && g.attendance == null).length;
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
  $("#source-note").textContent = `Source: ${season.source}. Percent full is attendance ÷ capacity, per game; season percent divides by the sum of per-game capacities. Capacities are season-specific (current year from athletic departments, past years from stadium records). Click a column header to sort.`;
}

async function main() {
  const [index, teamsData] = await Promise.all([
    loadJSON("data/seasons/index.json"),
    loadJSON("data/teams.json"),
  ]);

  const select = $("#season");
  select.innerHTML = index.seasons
    .map((y) => `<option value="${y}" ${y === index.default ? "selected" : ""}>${y}</option>`)
    .join("");

  const show = async (year) =>
    render(teamsData, await loadJSON(`data/seasons/${year}.json`));
  select.addEventListener("change", () => show(select.value));

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

  await show(index.default);
}

main().catch((err) => {
  $("#empty-note").hidden = false;
  $("#empty-note").textContent = `Failed to load data: ${err.message}`;
});
