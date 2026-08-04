// SVG charts for the tracker. No dependencies; theme-aware; every chart has a
// hover layer, and the season table doubles as the accessible table view.
import { seasonSummary, teamsForSeason } from "./stats.js";

const num = (n) => n.toLocaleString("en-US");
const pct = (p) => (p * 100).toFixed(1) + "%";

// Validated palette (dataviz reference instance; 3 categorical slots pass all
// checks on this site's light/dark surfaces). Color follows the season entity:
// 2024 → slot 1, 2025 → slot 2, 2026 → slot 3.
const THEME = {
  light: {
    series: ["#2a78d6", "#eb6834", "#1baf7a"],
    seq: ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"],
    grid: "#e1e0d9",
    baseline: "#c3c2b7",
    ink: "#52514e",
    muted: "#898781",
    surface: "#ffffff",
  },
  dark: {
    series: ["#3987e5", "#d95926", "#199e70"],
    // On a dark surface, magnitude reads light-er = louder.
    seq: ["#0d366b", "#104281", "#184f95", "#1c5cab", "#256abf", "#2a78d6", "#3987e5", "#5598e7", "#6da7ec", "#86b6ef", "#9ec5f4"],
    grid: "#2e323a",
    baseline: "#383835",
    ink: "#c3c2b7",
    muted: "#898781",
    surface: "#1e2127",
  },
};
const isDark = () => window.matchMedia("(prefers-color-scheme: dark)").matches;
const theme = () => (isDark() ? THEME.dark : THEME.light);

const SEASON_SLOT = {}; // year -> series slot index, assigned once in order
function seasonColor(year) {
  if (!(year in SEASON_SLOT)) SEASON_SLOT[year] = Object.keys(SEASON_SLOT).length % 3;
  return theme().series[SEASON_SLOT[year]];
}

// ---- shared tooltip (DOM-built; values lead, labels follow) ---------------

let tipTimer = null;
function tipEl() {
  let el = document.querySelector("#tooltip");
  if (!el) {
    el = document.createElement("div");
    el.id = "tooltip";
    el.hidden = true;
    document.body.appendChild(el);
  }
  return el;
}

function showTip(clientX, clientY, rows) {
  clearTimeout(tipTimer);
  const el = tipEl();
  el.textContent = "";
  for (const r of rows) {
    const div = document.createElement("div");
    if (r.head) {
      div.className = "tip-head";
      div.textContent = r.head;
    } else {
      if (r.color) {
        const key = document.createElement("span");
        key.className = "tip-key";
        key.style.background = r.color;
        div.appendChild(key);
      }
      const val = document.createElement("strong");
      val.textContent = r.value;
      div.appendChild(val);
      if (r.label) div.appendChild(document.createTextNode(` ${r.label}`));
    }
    el.appendChild(div);
  }
  el.hidden = false;
  const w = el.offsetWidth;
  let x = window.scrollX + clientX - w / 2;
  x = Math.max(8, Math.min(x, window.scrollX + document.documentElement.clientWidth - w - 8));
  el.style.left = `${x}px`;
  el.style.top = `${window.scrollY + clientY + 16}px`;
}

function hideTip() {
  tipTimer = setTimeout(() => (tipEl().hidden = true), 100);
}

// ---- svg helpers -----------------------------------------------------------

function roundedTopBar(x, y, w, h, r = 4) {
  if (h <= r) return `M${x},${y + h}h${w}v-${h}h-${w}z`;
  return `M${x},${y + h}v-${h - r}q0,-${r} ${r},-${r}h${w - 2 * r}q${r},0 ${r},${r}v${h - r}z`;
}

function roundedRightBar(x, y, w, h, r = 4) {
  if (w <= r) return `M${x},${y}h${w}v${h}h-${w}z`;
  return `M${x},${y}h${w - r}q${r},0 ${r},${r}v${h - 2 * r}q0,${r} -${r},${r}h-${w - r}z`;
}

function card(title, subtitle) {
  const div = document.createElement("div");
  div.className = "chart-card";
  const h = document.createElement("h2");
  h.textContent = title;
  div.appendChild(h);
  if (subtitle) {
    const p = document.createElement("p");
    p.className = "chart-sub";
    p.textContent = subtitle;
    div.appendChild(p);
  }
  return div;
}

function emptyNote(cardEl, text) {
  const p = document.createElement("p");
  p.className = "chart-empty";
  p.textContent = text;
  cardEl.appendChild(p);
}

// ---- panel 1: weekly percent full (columns) --------------------------------

function weeklyBars(cardEl, summary, season) {
  const t = theme();
  const weeks = summary.weeks.filter((w) => w.games > 0);
  if (!weeks.length) return emptyNote(cardEl, "No attendance yet this season.");
  const W = 720, H = 240, m = { t: 16, r: 16, b: 28, l: 48 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const maxPct = Math.max(1.1, ...weeks.map((w) => w.pct)) * 1.05;
  const band = iw / weeks.length;
  const bw = Math.min(24, band * 0.6);
  const y = (v) => m.t + ih - (v / maxPct) * ih;

  let marks = "";
  const ticks = [0, 0.25, 0.5, 0.75, 1.0];
  for (const v of ticks) {
    const yy = y(v);
    const isFull = v === 1.0;
    marks += `<line x1="${m.l}" x2="${W - m.r}" y1="${yy}" y2="${yy}" stroke="${isFull ? t.baseline : t.grid}" stroke-width="1"/>` +
      `<text x="${m.l - 6}" y="${yy + 3}" text-anchor="end" class="tick">${v * 100}%</text>`;
  }
  weeks.forEach((w, i) => {
    const x = m.l + i * band + (band - bw) / 2;
    const yy = y(w.pct);
    marks += `<path d="${roundedTopBar(x, yy, bw, m.t + ih - yy)}" fill="${t.series[0]}" data-i="${i}" class="hit"/>` +
      `<text x="${m.l + i * band + band / 2}" y="${H - 8}" text-anchor="middle" class="tick">${w.week}</text>`;
  });

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = `<text x="${m.l - 34}" y="${m.t - 4}" class="tick"></text>${marks}`;
  svg.addEventListener("pointermove", (e) => {
    const el = e.target.closest(".hit");
    if (!el) return hideTip();
    const w = weeks[Number(el.dataset.i)];
    showTip(e.clientX, e.clientY, [
      { head: `${season.weekLabels[w.week]}` },
      { value: pct(w.pct), label: "full" },
      { value: num(w.attendance), label: `of ${num(w.capacity)} capacity` },
      { value: String(w.games), label: w.games === 1 ? "game" : "games" },
    ]);
  });
  svg.addEventListener("pointerleave", hideTip);
  cardEl.appendChild(svg);
}

// ---- panel 2: team ranking (horizontal bars) -------------------------------

function teamBars(cardEl, summary) {
  const t = theme();
  const rows = summary.rows.filter((r) => r.games > 0);
  if (!rows.length) return emptyNote(cardEl, "No attendance yet this season.");
  const rowH = 26, W = 720, m = { t: 8, r: 56, b: 8, l: 152 };
  const H = m.t + m.b + rows.length * rowH;
  const iw = W - m.l - m.r;
  const maxPct = Math.max(1.05, ...rows.map((r) => r.pct));
  let marks = `<line x1="${m.l + (1 / maxPct) * iw}" x2="${m.l + (1 / maxPct) * iw}" y1="${m.t}" y2="${H - m.b}" stroke="${t.baseline}" stroke-width="1"/>`;
  rows.forEach((r, i) => {
    const yy = m.t + i * rowH + (rowH - 16) / 2;
    const w = (r.pct / maxPct) * iw;
    // Brand color is decoration here — identity rides the name label and logo.
    const fill = r.color ?? t.series[0];
    const logo = r.logo
      ? `<image href="${r.logo}" x="${m.l - 24}" y="${yy - 1}" width="18" height="18"/>`
      : "";
    marks += `<text x="${m.l - 30}" y="${yy + 12}" text-anchor="end" class="lbl">${r.team}</text>` + logo +
      `<path d="${roundedRightBar(m.l, yy, w, 16)}" fill="${fill}" data-i="${i}" class="hit"/>` +
      `<text x="${m.l + w + 6}" y="${yy + 12}" class="val">${pct(r.pct)}</text>`;
  });
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = marks;
  svg.addEventListener("pointermove", (e) => {
    const el = e.target.closest(".hit");
    if (!el) return hideTip();
    const r = rows[Number(el.dataset.i)];
    showTip(e.clientX, e.clientY, [
      { head: r.team },
      { value: pct(r.pct), label: "season" },
      { value: num(r.total), label: `total · ${r.games} games` },
    ]);
  });
  svg.addEventListener("pointerleave", hideTip);
  cardEl.appendChild(svg);
}

// ---- panel 3: team × week heatmap -----------------------------------------

// Same fill semantics as the table's percent colors (see app.js pctColor —
// keep in sync): green at 100%+, yellowing toward 80%, red saturating by
// 50%. Hue carries the meaning, so light and dark themes read identically
// and "more red = emptier" needs no legend footnote.
function pctFill(p) {
  let h, s, u;
  if (p >= 1) {
    h = 140; s = 62;
  } else if (p >= 0.8) {
    u = (p - 0.8) / 0.2; h = 45 + u * 95; s = 65;
  } else {
    u = Math.max(0, (p - 0.5) / 0.3); h = u * 45; s = 100 - u * 30;
  }
  return `hsl(${Math.round(h)} ${Math.round(s)}% ${isDark() ? 60 : 38}%)`;
}

function heatmap(cardEl, summary, season) {
  const t = theme();
  const rows = summary.rows.filter((r) => r.games > 0);
  const weeks = summary.weeks.filter((w) => w.games > 0).map((w) => w.week);
  if (!rows.length) return emptyNote(cardEl, "No attendance yet this season.");
  const cells = [];
  let lo = Infinity, hi = -Infinity;
  for (const r of rows)
    for (const w of r.weeks) {
      lo = Math.min(lo, w.pct);
      hi = Math.max(hi, w.pct);
      cells.push({ team: r.team, ...w });
    }
  const color = pctFill;

  const cw = 34, ch = 22, gap = 2, m = { t: 22, r: 8, b: 8, l: 128 };
  const W = m.l + weeks.length * (cw + gap) + m.r;
  const H = m.t + rows.length * (ch + gap) + m.b;
  let marks = "";
  weeks.forEach((w, j) => {
    marks += `<text x="${m.l + j * (cw + gap) + cw / 2}" y="${m.t - 8}" text-anchor="middle" class="tick">${w}</text>`;
  });
  rows.forEach((r, i) => {
    const yy = m.t + i * (ch + gap);
    marks += `<text x="${m.l - 8}" y="${yy + ch / 2 + 4}" text-anchor="end" class="lbl">${r.team}</text>`;
    const byWeek = Object.fromEntries(r.weeks.map((w) => [w.week, w]));
    weeks.forEach((w, j) => {
      const g = byWeek[w];
      if (!g) return;
      marks += `<rect x="${m.l + j * (cw + gap)}" y="${yy}" width="${cw}" height="${ch}" rx="3" fill="${color(g.pct)}" data-i="${i}" data-w="${w}" class="hit"/>`;
    });
  });
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = marks;
  svg.addEventListener("pointermove", (e) => {
    const el = e.target.closest(".hit");
    if (!el) return hideTip();
    const r = rows[Number(el.dataset.i)];
    const g = r.weeks.find((x) => x.week === Number(el.dataset.w));
    showTip(e.clientX, e.clientY, [
      { head: `${r.team} · ${season.weekLabels[g.week]}` },
      { value: pct(g.pct), label: "full" },
      { value: num(g.attendance), label: "attendance" },
    ]);
  });
  svg.addEventListener("pointerleave", hideTip);
  cardEl.appendChild(svg);

  // scale legend
  const legend = document.createElement("div");
  legend.className = "heat-legend";
  const lowLbl = document.createElement("span");
  lowLbl.textContent = pct(lo);
  const bar = document.createElement("span");
  bar.className = "heat-scale";
  const stops = Array.from({ length: 9 }, (_, i) =>
    pctFill(lo + ((hi - lo) * i) / 8));
  bar.style.background = `linear-gradient(to right, ${stops.join(", ")})`;
  const hiLbl = document.createElement("span");
  hiLbl.textContent = pct(hi);
  legend.append(lowLbl, bar, hiLbl);
  cardEl.appendChild(legend);
}

// ---- panel 4: year-over-year weekly lines ----------------------------------

function yoyLines(cardEl, seasonsData, teamsData) {
  const t = theme();
  const series = [];
  for (const [year, season] of Object.entries(seasonsData)) {
    const teams = teamsForSeason(teamsData, Number(year));
    const s = seasonSummary(teams, season.games, season.weekLabels.length);
    const pts = s.weeks.filter((w) => w.games > 0).map((w) => ({ week: w.week, ...w }));
    if (pts.length) series.push({ year: Number(year), pts, color: seasonColor(Number(year)) });
  }
  if (!series.length) return emptyNote(cardEl, "No data yet.");

  const allWeeks = [...new Set(series.flatMap((s) => s.pts.map((p) => p.week)))].sort((a, b) => a - b);
  const W = 720, H = 260, m = { t: 16, r: 56, b: 28, l: 48 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const lo = Math.min(0.8, ...series.flatMap((s) => s.pts.map((p) => p.pct))) - 0.02;
  const hi = Math.max(1.02, ...series.flatMap((s) => s.pts.map((p) => p.pct))) + 0.02;
  const x = (w) => m.l + ((w - allWeeks[0]) / Math.max(allWeeks[allWeeks.length - 1] - allWeeks[0], 1)) * iw;
  const y = (v) => m.t + ih - ((v - lo) / (hi - lo)) * ih;

  let marks = "";
  for (const v of [0.85, 0.9, 0.95, 1.0]) {
    if (v < lo || v > hi) continue;
    marks += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" stroke="${v === 1 ? t.baseline : t.grid}" stroke-width="1"/>` +
      `<text x="${m.l - 6}" y="${y(v) + 3}" text-anchor="end" class="tick">${Math.round(v * 100)}%</text>`;
  }
  for (const w of allWeeks)
    marks += `<text x="${x(w)}" y="${H - 8}" text-anchor="middle" class="tick">${w}</text>`;
  for (const s of series) {
    const d = s.pts.map((p, i) => `${i ? "L" : "M"}${x(p.week)},${y(p.pct)}`).join("");
    marks += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    const last = s.pts[s.pts.length - 1];
    marks += `<circle cx="${x(last.week)}" cy="${y(last.pct)}" r="4" fill="${s.color}" stroke="${t.surface}" stroke-width="2"/>` +
      `<text x="${x(last.week) + 8}" y="${y(last.pct) + 4}" class="lbl">${s.year}</text>`;
  }
  const cross = `<line id="cross" x1="0" x2="0" y1="${m.t}" y2="${H - m.b}" stroke="${t.baseline}" stroke-width="1" opacity="0"/>`;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = marks + cross + `<rect x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent"/>`;

  const crossEl = svg.querySelector("#cross");
  svg.addEventListener("pointermove", (e) => {
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    let nearest = allWeeks[0];
    for (const w of allWeeks) if (Math.abs(x(w) - px) < Math.abs(x(nearest) - px)) nearest = w;
    crossEl.setAttribute("x1", x(nearest));
    crossEl.setAttribute("x2", x(nearest));
    crossEl.setAttribute("opacity", "1");
    const rows = [{ head: `Week ${nearest}` }];
    for (const s of series) {
      const p = s.pts.find((q) => q.week === nearest);
      if (p) rows.push({ color: s.color, value: pct(p.pct), label: `${s.year} · ${num(p.attendance)}` });
    }
    showTip(e.clientX, e.clientY, rows);
  });
  svg.addEventListener("pointerleave", () => {
    crossEl.setAttribute("opacity", "0");
    hideTip();
  });
  cardEl.appendChild(legendRow(series.map((s) => ({ label: String(s.year), color: s.color })), "line"));
  cardEl.appendChild(svg);
}

// ---- panel 5: year-over-year by team (dot plot) ----------------------------

function yoyTeams(cardEl, seasonsData, teamsData) {
  const t = theme();
  const byTeam = {};
  const years = [];
  for (const [yearStr, season] of Object.entries(seasonsData)) {
    const year = Number(yearStr);
    const teams = teamsForSeason(teamsData, year);
    const s = seasonSummary(teams, season.games, season.weekLabels.length);
    let any = false;
    for (const r of s.rows) {
      if (!r.games) continue;
      (byTeam[r.team] ??= []).push({ year, pct: r.pct, total: r.total, games: r.games });
      any = true;
    }
    if (any) years.push(year);
  }
  const teams = Object.keys(byTeam);
  if (!teams.length) return emptyNote(cardEl, "No data yet.");
  teams.sort((a, b) => {
    const la = byTeam[a][byTeam[a].length - 1].pct;
    const lb = byTeam[b][byTeam[b].length - 1].pct;
    return lb - la;
  });

  const rowH = 26, W = 720, m = { t: 8, r: 24, b: 24, l: 128 };
  const H = m.t + m.b + teams.length * rowH;
  const iw = W - m.l - m.r;
  const all = Object.values(byTeam).flat();
  const lo = Math.min(...all.map((d) => d.pct)) - 0.03;
  const hi = Math.max(1.02, ...all.map((d) => d.pct)) + 0.03;
  const x = (v) => m.l + ((v - lo) / (hi - lo)) * iw;

  let marks = `<line x1="${x(1)}" x2="${x(1)}" y1="${m.t}" y2="${H - m.b}" stroke="${t.baseline}" stroke-width="1"/>` +
    `<text x="${x(1)}" y="${H - 8}" text-anchor="middle" class="tick">100%</text>`;
  teams.forEach((team, i) => {
    const cy = m.t + i * rowH + rowH / 2;
    marks += `<text x="${m.l - 8}" y="${cy + 4}" text-anchor="end" class="lbl">${team}</text>` +
      `<line x1="${m.l}" x2="${W - m.r}" y1="${cy}" y2="${cy}" stroke="${t.grid}" stroke-width="1"/>`;
    const pts = byTeam[team];
    if (pts.length > 1) {
      const xs = pts.map((d) => x(d.pct));
      marks += `<line x1="${Math.min(...xs)}" x2="${Math.max(...xs)}" y1="${cy}" y2="${cy}" stroke="${t.muted}" stroke-width="1.5"/>`;
    }
    for (const d of pts)
      marks += `<circle cx="${x(d.pct)}" cy="${cy}" r="5" fill="${seasonColor(d.year)}" stroke="${t.surface}" stroke-width="2" data-team="${team}" data-year="${d.year}" class="hit"/>`;
  });
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = marks;
  svg.addEventListener("pointermove", (e) => {
    const el = e.target.closest(".hit");
    if (!el) return hideTip();
    const d = byTeam[el.dataset.team].find((q) => q.year === Number(el.dataset.year));
    showTip(e.clientX, e.clientY, [
      { head: `${el.dataset.team} · ${el.dataset.year}` },
      { value: pct(d.pct), label: "season" },
      { value: num(d.total), label: `total · ${d.games} games` },
    ]);
  });
  svg.addEventListener("pointerleave", hideTip);
  cardEl.appendChild(legendRow(years.map((y) => ({ label: String(y), color: seasonColor(y) })), "dot"));
  cardEl.appendChild(svg);
}

function legendRow(items, kind) {
  const div = document.createElement("div");
  div.className = "chart-legend";
  for (const it of items) {
    const span = document.createElement("span");
    const key = document.createElement("span");
    key.className = kind === "line" ? "key-line" : "key-dot";
    key.style.background = it.color;
    span.append(key, document.createTextNode(it.label));
    div.appendChild(span);
  }
  return div;
}

// ---- entry -----------------------------------------------------------------

export function renderCharts(root, teamsData, seasonsData, currentYear) {
  root.textContent = "";
  const season = seasonsData[currentYear];
  const teams = teamsForSeason(teamsData, Number(currentYear));
  const summary = seasonSummary(teams, season.games, season.weekLabels.length);

  const c1 = card(`Weekly percent full — ${currentYear}`, "Conference-wide attendance ÷ capacity in play, by week");
  weeklyBars(c1, summary, season);

  const c2 = card(`Season percent full by team — ${currentYear}`, "Attendance ÷ per-game capacity, season to date");
  teamBars(c2, summary);

  const c3 = card(`Percent full, team × week — ${currentYear}`, "Same scale as the table: green is full, red is empty seats");
  heatmap(c3, summary, season);

  const c4 = card("Weekly percent full, year over year", "Each line is a season; conference-wide");
  yoyLines(c4, seasonsData, teamsData);

  const c5 = card("Season percent full by team, year over year", "One dot per season; the connector spans a team's range");
  yoyTeams(c5, seasonsData, teamsData);

  root.append(c1, c2, c3, c4, c5);
}
