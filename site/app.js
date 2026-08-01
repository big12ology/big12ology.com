import { seasonSummary } from "./stats.js";

const $ = (sel) => document.querySelector(sel);
const num = (n) => n.toLocaleString("en-US");
const pct = (p) => (p * 100).toFixed(1) + "%";

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

function render(teamsData, season) {
  const numWeeks = season.weekLabels.length;
  const summary = seasonSummary(teamsData.teams, season.games, numWeeks);
  // Only render week columns that have at least one game (2026 preseason
  // renders no week columns until data arrives).
  const activeWeeks = summary.weeks.filter((w) => w.games > 0).map((w) => w.week);

  $("#summary").innerHTML = [
    ["Total attendance", num(summary.totals.attendance)],
    ["Percent full", pct(summary.totals.pct)],
    ["Games", num(summary.totals.games)],
    ["Weeks played", num(activeWeeks.length)],
  ]
    .map(
      ([label, value]) =>
        `<div class="card"><div class="label">${label}</div><div class="value">${value}</div></div>`
    )
    .join("");

  const head = `<thead><tr>
      <th class="team">Team</th><th>G</th><th>Capacity</th>
      ${activeWeeks.map((w) => `<th>${season.weekLabels[w]}</th>`).join("")}
      <th>Season</th></tr></thead>`;

  const body = summary.rows
    .map((row) => {
      const byWeek = Object.fromEntries(row.weeks.map((w) => [w.week, w]));
      const cells = activeWeeks
        .map((w) => {
          const g = byWeek[w];
          return g
            ? `<td>${num(g.attendance)}<span class="${pctClass(g.pct)}">${pct(g.pct)}</span></td>`
            : "<td></td>";
        })
        .join("");
      return `<tr>
        <td class="team">${row.team}<span class="stadium">${row.stadium}</span></td>
        <td>${row.games}</td><td>${num(row.capacity)}</td>${cells}
        <td class="season-total">${num(row.total)}<span class="${pctClass(row.pct)}">${pct(row.pct)}</span></td>
      </tr>`;
    })
    .join("");

  const wk = (w) => summary.weeks[w];
  const foot = `<tfoot>
      <tr><td class="team">Big 12 total</td><td>${summary.totals.games}</td>
        <td>${num(summary.rows.reduce((s, r) => s + r.capacity, 0))}</td>
        ${activeWeeks.map((w) => `<td>${num(wk(w).attendance)}<span class="${pctClass(wk(w).pct)}">${pct(wk(w).pct)}</span></td>`).join("")}
        <td class="season-total">${num(summary.totals.attendance)}<span class="${pctClass(summary.totals.pct)}">${pct(summary.totals.pct)}</span></td></tr>
      <tr class="sub"><td class="team">Capacity in play / games</td><td></td><td></td>
        ${activeWeeks.map((w) => `<td>${num(wk(w).capacity)} · ${wk(w).games}g</td>`).join("")}
        <td>${num(summary.totals.capacity)} · ${summary.totals.games}g</td></tr>
    </tfoot>`;

  $("#attendance-table").innerHTML = head + `<tbody>${body}</tbody>` + foot;
  const empty = $("#empty-note");
  empty.hidden = activeWeeks.length > 0;
  empty.textContent = `No attendance reported yet for the ${season.season} season — check back after the first week of games.`;
  $("#source-note").textContent = `Source: ${season.source}. Percent full is attendance ÷ stadium capacity; season percent assumes constant capacity (${teamsData.capacitySource}).`;
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
  await show(index.default);
}

main().catch((err) => {
  $("#empty-note").hidden = false;
  $("#empty-note").textContent = `Failed to load data: ${err.message}`;
});
