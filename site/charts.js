// SVG charts for the tracker. No dependencies; theme-aware; every chart has a
// hover layer, and the season table doubles as the accessible table view.
import { seasonSummary, teamsForSeason } from "./stats.js?v=9";
import { gameTooltipHTML } from "./gametip.js?v=9";

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

function showTipHTML(clientX, clientY, html) {
  clearTimeout(tipTimer);
  const el = tipEl();
  el.innerHTML = html;
  el.hidden = false;
  placeTip(el, clientX, clientY);
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
  placeTip(el, clientX, clientY);
}

function placeTip(el, clientX, clientY) {
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

// Same fill semantics as the table's percent colors (see app.js — keep in
// sync). Hue carries the meaning; the anchor curve is log-like so the top
// of the scale (where most games land) gets most of the resolution.
function pctHSLC(p) {
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
function pctFill(p) {
  const [h, s] = pctHSLC(p);
  return `hsl(${h} ${s}% ${isDark() ? 60 : 38}%)`;
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
  const homeInfo = new Map(
    season.games.filter((g) => !g.role).map((g) => [`${g.team}|${g.week}`, g]));
  svg.addEventListener("pointermove", (e) => {
    const el = e.target.closest(".hit");
    if (!el) return hideTip();
    const r = rows[Number(el.dataset.i)];
    const w = r.weeks.find((x) => x.week === Number(el.dataset.w));
    const info = homeInfo.get(`${r.team}|${w.week}`) ?? {};
    showTipHTML(e.clientX, e.clientY, gameTooltipHTML({
      game: info, weekLabel: season.weekLabels[w.week], wk: w,
      cap: w.attendance / (w.pct || 1), prefix: r.team,
    }));
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


// ---- panel 6: weather vs fill --------------------------------------------

function weatherScatter(cardEl, seasonsData, teamsData) {
  const t = theme();
  const pts = [];
  for (const [year, season] of Object.entries(seasonsData)) {
    const teams = teamsForSeason(teamsData, Number(year));
    const capOf = new Map(teams.map((x) => [x.team, x]));
    for (const g of season.games) {
      if (g.role || g.attendance == null || !g.weather ||
          g.weather.tempF == null) continue;
      const team = capOf.get(g.team);
      if (!team) continue;
      const cap = g.capacity ?? team.capacity;
      if (!cap) continue;
      pts.push({ team: g.team, year, temp: g.weather.tempF,
                 rain: (g.weather.precipIn ?? 0) > 0.05,
                 pct: g.attendance / cap, g, cap,
                 color: team.color });
    }
  }
  if (pts.length < 8) return emptyNote(cardEl, "Not enough weather data yet.");
  const m = { t: 14, r: 12, b: 34, l: 46 };
  const W = 640, H = 300, iw = W - m.l - m.r, ih = H - m.t - m.b;
  const tmin = Math.min(...pts.map((p) => p.temp)) - 4;
  const tmax = Math.max(...pts.map((p) => p.temp)) + 4;
  const pmin = Math.min(0.6, ...pts.map((p) => p.pct)) - 0.03;
  const pmax = Math.max(1.05, ...pts.map((p) => p.pct)) + 0.03;
  const x = (v) => m.l + ((v - tmin) / (tmax - tmin)) * iw;
  const y = (v) => m.t + (1 - (v - pmin) / (pmax - pmin)) * ih;
  let marks = "";
  for (const gp of [0.7, 0.85, 1.0]) {
    marks += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(gp)}" y2="${y(gp)}" class="grid"/>` +
      `<text x="${m.l - 6}" y="${y(gp) + 4}" text-anchor="end" class="tick">${Math.round(gp * 100)}%</text>`;
  }
  for (const gt of [30, 50, 70, 90]) {
    if (gt < tmin || gt > tmax) continue;
    marks += `<text x="${x(gt)}" y="${H - 12}" text-anchor="middle" class="tick">${gt}°F</text>`;
  }
  // least-squares trend of fill on temperature
  const n = pts.length;
  const mx = pts.reduce((s, p) => s + p.temp, 0) / n;
  const my = pts.reduce((s, p) => s + p.pct, 0) / n;
  let sxx = 0, sxy = 0, syy = 0;
  for (const p of pts) {
    sxx += (p.temp - mx) ** 2;
    sxy += (p.temp - mx) * (p.pct - my);
    syy += (p.pct - my) ** 2;
  }
  const slope = sxy / sxx;
  const icept = my - slope * mx;
  const r2 = sxx && syy ? (sxy * sxy) / (sxx * syy) : 0;
  const tl = tmin + 3, tr2 = tmax - 3;
  const clampY = (v) => Math.max(pmin, Math.min(pmax, v));
  marks += `<line x1="${x(tl)}" y1="${y(clampY(icept + slope * tl))}"
    x2="${x(tr2)}" y2="${y(clampY(icept + slope * tr2))}"
    stroke="${isDark() ? "#9aa0aa" : "#6b7280"}" stroke-width="1.5"
    stroke-dasharray="6 4" opacity="0.8"/>`;
  const per10 = slope * 10 * 100;
  marks += `<text x="${W - m.r - 4}" y="${m.t + 12}" text-anchor="end"
    class="tick">trend: ${per10 >= 0 ? "+" : ""}${per10.toFixed(1)} pts of fill per +10°F (R² ${r2.toFixed(2)})</text>`;
  pts.forEach((p, i) => {
    marks += `<circle cx="${x(p.temp)}" cy="${y(p.pct)}" r="${p.rain ? 5 : 3.5}"
      fill="${p.color ?? t.series[0]}" fill-opacity="${p.rain ? 0.9 : 0.55}"
      ${p.rain ? 'stroke="' + (isDark() ? "#7aa2ff" : "#003087") + '" stroke-width="1.5"' : ""}
      data-i="${i}" class="hit"/>`;
  });
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = marks;
  svg.addEventListener("pointermove", (e) => {
    const el = e.target.closest(".hit");
    if (!el) return hideTip();
    const p = pts[Number(el.dataset.i)];
    showTipHTML(e.clientX, e.clientY, gameTooltipHTML({
      game: p.g, weekLabel: `Week ${p.g.week} · ${p.year}`,
      wk: { attendance: p.g.attendance, pct: p.pct }, cap: p.cap,
      prefix: p.team,
    }));
  });
  svg.addEventListener("pointerleave", hideTip);
  cardEl.appendChild(svg);
}

// ---- panel 7: records watch ------------------------------------------------

function recordsWatch(cardEl, seasonsData, teamsData) {
  const best = new Map();   // team -> {crowd, pct, game}
  const streaks = new Map(); // team -> current consecutive sellouts
  const ended = new Map();  // team -> how the last streak died
  const years = Object.keys(seasonsData).sort();
  for (const year of years) {
    const season = seasonsData[year];
    const teams = teamsForSeason(teamsData, Number(year));
    const capOf = new Map(teams.map((x) => [x.team, x]));
    const home = season.games
      .filter((g) => !g.role && g.attendance != null)
      .sort((a, b) => (a.date || "").localeCompare(b.date || ""));
    for (const g of home) {
      const team = capOf.get(g.team);
      const cap = g.capacity ?? team?.capacity;
      if (!cap) continue;
      const p = g.attendance / cap;
      const b = best.get(g.team) ?? { crowd: 0, pct: 0 };
      if (g.attendance > b.crowd) { b.crowd = g.attendance; b.crowdGame = `${g.opponent} ${year}`; }
      if (p > b.pct) { b.pct = p; b.pctGame = `${g.opponent} ${year}`; }
      best.set(g.team, b);
      const cur = streaks.get(g.team) ?? 0;
      if (p >= 1) {
        streaks.set(g.team, cur + 1);
      } else {
        if (cur > 0) {
          ended.set(g.team, { count: cur, opp: g.opponent, year,
                              pct: p, short: cap - g.attendance });
        }
        streaks.set(g.team, 0);
      }
    }
  }
  const first = years[0];
  const rows = [...best.entries()]
    .sort((a, b) => b[1].pct - a[1].pct)
    .map(([team, b]) => {
      const s = streaks.get(team) ?? 0;
      let cell;
      if (s > 0) {
        cell = `<span class="rw-good">${s} game${s > 1 ? "s" : ""} active</span>`;
      } else if (ended.has(team)) {
        const e = ended.get(team);
        cell = `<span class="rw-bad">ended</span><span class="rw-sub">` +
          `after ${e.count} · ${e.opp} ${e.year}, ${num(e.short)} short</span>`;
      } else {
        cell = `<span class="rw-dim">none</span>` +
          `<span class="rw-sub">no sellout since ${first}</span>`;
      }
      return `<tr><td>${team}</td>` +
        `<td>${num(b.crowd)}<span class="rw-sub">vs ${b.crowdGame}</span></td>` +
        `<td>${pct(b.pct)}<span class="rw-sub">vs ${b.pctGame}</span></td>` +
        `<td>${cell}</td></tr>`;
    }).join("");
  cardEl.classList.add("span-all");
  const wrap = document.createElement("div");
  wrap.className = "records-table";
  wrap.innerHTML = `<table><thead><tr><th>Team</th><th>Biggest crowd</th>` +
    `<th>Best fill</th><th>Sellout streak</th></tr></thead><tbody>${rows}</tbody></table>`;
  cardEl.appendChild(wrap);
}




// ---- road draw: who fills someone else's stadium ---------------------------

function roadDraw(cardEl, seasonsData, teamsData) {
  const t = theme();
  // For every home game, compare the crowd to that host's own season
  // average fill. A visitor's road draw is the average of those gaps —
  // it isolates the visitor from the host's baseline popularity.
  const byVisitor = new Map();
  const colorOf = new Map();
  for (const [year, season] of Object.entries(seasonsData)) {
    const teams = teamsForSeason(teamsData, Number(year));
    teams.forEach((x) => colorOf.set(x.team, x.color));
    const summary = seasonSummary(teams, season.games,
                                  season.weekLabels.length);
    const hostPct = new Map(summary.rows.filter((r) => r.games > 0)
                                        .map((r) => [r.team, r.pct]));
    const capOf = new Map(teams.map((x) => [x.team, x]));
    for (const g of season.games) {
      if (g.role || g.attendance == null || !g.opponent) continue;
      const base = hostPct.get(g.team);
      const cap = g.capacity ?? capOf.get(g.team)?.capacity;
      if (!base || !cap) continue;
      const gap = g.attendance / cap - base;
      const cur = byVisitor.get(g.opponent) ?? { n: 0, sum: 0, hosts: [] };
      cur.n += 1;
      cur.sum += gap;
      cur.hosts.push({ host: g.team, year, gap, att: g.attendance });
      byVisitor.set(g.opponent, cur);
    }
  }
  // Conference members only — outside visitors appear once or twice and
  // their averages are noise.
  const rows = [...byVisitor.entries()]
    .filter(([team, v]) => colorOf.has(team) && v.n >= 2)
    .map(([team, v]) => ({ team, avg: v.sum / v.n, n: v.n, hosts: v.hosts }))
    .sort((a, b) => b.avg - a.avg);
  if (rows.length < 4) {
    return emptyNote(cardEl, "Not enough conference road games yet to compare.");
  }
  const show = rows;

  const rowH = 22, gap = 4;
  const m = { t: 10, r: 60, b: 26, l: 132 };
  const W = 640;
  const H = m.t + show.length * (rowH + gap) + m.b;
  const iw = W - m.l - m.r;
  const maxAbs = Math.max(...show.map((r) => Math.abs(r.avg)), 0.05);
  const x0 = m.l + iw / 2;
  const x = (v) => x0 + (v / maxAbs) * (iw / 2);
  let marks = `<line x1="${x0}" x2="${x0}" y1="${m.t}" y2="${H - m.b}" class="grid"/>`;
  show.forEach((r, i) => {
    const y = m.t + i * (rowH + gap);
    const fill = r.avg >= 0
      ? (isDark() ? "#4ade80" : "#0d7a3f")
      : (isDark() ? "#f87171" : "#c0392b");
    const w = Math.abs(x(r.avg) - x0);
    marks += `<rect x="${r.avg >= 0 ? x0 : x0 - w}" y="${y}" width="${w}"
      height="${rowH}" rx="3" fill="${fill}" fill-opacity="0.95"
      data-i="${i}" class="hit"/>`;
    marks += `<text x="${m.l - 8}" y="${y + rowH / 2 + 4}" text-anchor="end"
      class="lbl">${r.team.length > 15 ? r.team.slice(0, 14) + "…" : r.team}` +
      `<tspan class="tick"> ${r.n}</tspan></text>`;
    marks += `<text x="${r.avg >= 0 ? x0 + w + 6 : x0 - w - 6}"
      y="${y + rowH / 2 + 4}" text-anchor="${r.avg >= 0 ? "start" : "end"}"
      class="val">${r.avg >= 0 ? "+" : ""}${(r.avg * 100).toFixed(1)}</text>`;
  });
  marks += `<text x="${x0}" y="${H - 8}" text-anchor="middle" class="tick">
    points of fill vs the host's season average · small number = road trips</text>`;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = marks;
  svg.addEventListener("pointermove", (e) => {
    const el = e.target.closest(".hit");
    if (!el) return hideTip();
    const r = show[Number(el.dataset.i)];
    const best = [...r.hosts].sort((a, b) => b.gap - a.gap)[0];
    showTip(e.clientX, e.clientY, [
      { head: `${r.team} on the road` },
      { value: `${r.avg >= 0 ? "+" : ""}${(r.avg * 100).toFixed(1)} pts`,
        label: `vs host average · ${r.n} trips` },
      { value: `${best.host} ${best.year}`,
        label: `best draw, ${num(best.att)} · ${best.gap >= 0 ? "+" : ""}${(best.gap * 100).toFixed(1)} pts` },
    ]);
  });
  svg.addEventListener("pointerleave", hideTip);
  cardEl.appendChild(svg);
}

// ---- team comparison ------------------------------------------------------

function multiLine(cardEl, seriesList, xLabels, yFmt, yMinPad, yMaxPad) {
  const t = theme();
  if (!seriesList.some((s) => s.pts.length)) {
    return emptyNote(cardEl, "No data yet for the selected teams.");
  }
  const m = { t: 16, r: 14, b: 30, l: 50 };
  const W = 640, H = 300, iw = W - m.l - m.r, ih = H - m.t - m.b;
  const xs = xLabels.map((_, i) => i);
  const allY = seriesList.flatMap((s) => s.pts.map((p) => p.y));
  const ymin = Math.min(...allY) - yMinPad;
  const ymax = Math.max(...allY) + yMaxPad;
  const x = (i) => m.l + (xs.length > 1 ? (i / (xs.length - 1)) * iw : iw / 2);
  const y = (v) => m.t + (1 - (v - ymin) / Math.max(ymax - ymin, 1e-9)) * ih;
  let marks = "";
  const step = Math.max(1, Math.ceil(xLabels.length / 12));
  xLabels.forEach((lb, i) => {
    if (i % step) return;
    marks += `<text x="${x(i)}" y="${H - 10}" text-anchor="middle" class="tick">${lb}</text>`;
  });
  for (const gv of [ymin + (ymax - ymin) * 0.25, ymin + (ymax - ymin) * 0.6,
                    ymax - (ymax - ymin) * 0.05]) {
    marks += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(gv)}" y2="${y(gv)}" class="grid"/>` +
      `<text x="${m.l - 6}" y="${y(gv) + 4}" text-anchor="end" class="tick">${yFmt(gv)}</text>`;
  }
  const hits = [];
  seriesList.forEach((s) => {
    if (!s.pts.length) return;
    const d = s.pts.map((p, j) => `${j ? "L" : "M"}${x(p.i)},${y(p.y)}`).join(" ");
    marks += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2.5" opacity="0.9"/>`;
    s.pts.forEach((p) => {
      hits.push({ s, p });
      marks += `<circle cx="${x(p.i)}" cy="${y(p.y)}" r="4" fill="${s.color}"
        data-i="${hits.length - 1}" class="hit"/>`;
    });
  });
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = marks;
  svg.addEventListener("pointermove", (e) => {
    const el = e.target.closest(".hit");
    if (!el) return hideTip();
    const { s, p } = hits[Number(el.dataset.i)];
    if (p.html) return showTipHTML(e.clientX, e.clientY, p.html);
    showTip(e.clientX, e.clientY, [{ head: `${s.name} · ${p.head}` },
                                   ...p.rows]);
  });
  svg.addEventListener("pointerleave", hideTip);
  cardEl.appendChild(svg);
}


function teamWeather(cardEl, seasonsData, teamsData, sel) {
  const pts = [];
  const colorOf = new Map();
  for (const [year, season] of Object.entries(seasonsData)) {
    const teams = teamsForSeason(teamsData, Number(year));
    teams.forEach((x) => colorOf.set(x.team, x.color));
    const capOf = new Map(teams.map((x) => [x.team, x]));
    for (const g of season.games) {
      if (g.role || g.attendance == null || !g.weather ||
          g.weather.tempF == null || !sel.has(g.team)) continue;
      const cap = g.capacity ?? capOf.get(g.team)?.capacity;
      if (!cap) continue;
      pts.push({ team: g.team, year, temp: g.weather.tempF,
                 pct: g.attendance / cap, g, cap,
                 rain: (g.weather.precipIn ?? 0) > 0.05 });
    }
  }
  if (pts.length < 3) {
    return emptyNote(cardEl, "Not enough games with weather for these teams.");
  }
  const m = { t: 30, r: 14, b: 34, l: 46 };
  const W = 640, H = 300, iw = W - m.l - m.r, ih = H - m.t - m.b;
  const tmin = Math.min(...pts.map((p) => p.temp)) - 4;
  const tmax = Math.max(...pts.map((p) => p.temp)) + 4;
  const pmin = Math.min(0.6, ...pts.map((p) => p.pct)) - 0.03;
  const pmax = Math.max(1.05, ...pts.map((p) => p.pct)) + 0.03;
  const x = (v) => m.l + ((v - tmin) / (tmax - tmin)) * iw;
  const y = (v) => m.t + (1 - (v - pmin) / (pmax - pmin)) * ih;
  let marks = "";
  for (const gp of [0.7, 0.85, 1.0]) {
    marks += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(gp)}" y2="${y(gp)}" class="grid"/>` +
      `<text x="${m.l - 6}" y="${y(gp) + 4}" text-anchor="end" class="tick">${Math.round(gp * 100)}%</text>`;
  }
  for (const gt of [30, 50, 70, 90]) {
    if (gt < tmin || gt > tmax) continue;
    marks += `<text x="${x(gt)}" y="${H - 12}" text-anchor="middle" class="tick">${gt}°F</text>`;
  }
  let legendX = m.l;
  [...sel].sort().forEach((team) => {
    const mine = pts.filter((p) => p.team === team);
    const color = colorOf.get(team) ?? "#888";
    if (mine.length >= 3) {
      const n = mine.length;
      const mx = mine.reduce((s, p) => s + p.temp, 0) / n;
      const my = mine.reduce((s, p) => s + p.pct, 0) / n;
      let sxx = 0, sxy = 0, syy = 0;
      for (const p of mine) {
        sxx += (p.temp - mx) ** 2;
        sxy += (p.temp - mx) * (p.pct - my);
        syy += (p.pct - my) ** 2;
      }
      if (sxx > 0) {
        const slope = sxy / sxx, icept = my - slope * mx;
        const r2 = syy ? (sxy * sxy) / (sxx * syy) : 0;
        const cl = (v) => Math.max(pmin, Math.min(pmax, v));
        marks += `<line x1="${x(tmin + 3)}" y1="${y(cl(icept + slope * (tmin + 3)))}"
          x2="${x(tmax - 3)}" y2="${y(cl(icept + slope * (tmax - 3)))}"
          stroke="${color}" stroke-width="2" stroke-dasharray="6 4" opacity="0.85"/>`;
        const per10 = slope * 10 * 100;
        const hot = mine.filter((p) => p.temp >= 85);
        const cold = mine.filter((p) => p.temp <= 50);
        const avg = (a) => a.reduce((s, p) => s + p.pct, 0) / a.length;
        let extra = "";
        if (hot.length) {
          extra += ` · ${(avg(hot) * 100).toFixed(0)}% when 85°F+`;
        }
        if (cold.length) {
          extra += ` · ${(avg(cold) * 100).toFixed(0)}% when ≤50°F`;
        }
        marks += `<text x="${legendX}" y="${m.t - 12}" class="tick"
          fill="${color}">${team}: ${per10 >= 0 ? "+" : ""}${per10.toFixed(1)}/10°F${extra}</text>`;
        legendX += 250;
      }
    }
    mine.forEach((p) => {
      const i = pts.indexOf(p);
      marks += `<circle cx="${x(p.temp)}" cy="${y(p.pct)}" r="${p.rain ? 5.5 : 4}"
        fill="${color}" fill-opacity="${p.rain ? 0.95 : 0.6}"
        ${p.rain ? `stroke="${color}" stroke-width="1.5"` : ""}
        data-i="${i}" class="hit"/>`;
    });
  });
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = marks;
  svg.addEventListener("pointermove", (e) => {
    const el = e.target.closest(".hit");
    if (!el) return hideTip();
    const p = pts[Number(el.dataset.i)];
    showTipHTML(e.clientX, e.clientY, gameTooltipHTML({
      game: p.g, weekLabel: `Week ${p.g.week} · ${p.year}`,
      wk: { attendance: p.g.attendance, pct: p.pct }, cap: p.cap,
      prefix: p.team,
    }));
  });
  svg.addEventListener("pointerleave", hideTip);
  cardEl.appendChild(svg);
}

export function renderTeamCharts(root, teamsData, seasonsData, currentYear, selected) {
  root.textContent = "";
  if (!selected.size) {
    const note = document.createElement("p");
    note.className = "note";
    note.style.padding = "1rem 1.5rem";
    note.textContent = "Pick one or more teams above to chart them — " +
      "add a second team to compare.";
    root.appendChild(note);
    return;
  }
  const season = seasonsData[currentYear];
  const teams = teamsForSeason(teamsData, Number(currentYear));
  const summary = seasonSummary(teams, season.games, season.weekLabels.length);
  const colorOf = new Map(teams.map((x) => [x.team, x.color]));
  const sel = [...selected].sort();

  // 1. weekly fill, selected season
  const weeks = summary.weeks.filter((w) => w.games > 0).map((w) => w.week);
  const c1 = card(`Percent full by week — ${currentYear}`,
    "Home games only; hover any point for the crowd");
  const homeInfo = new Map(
    season.games.filter((g) => !g.role).map((g) => [`${g.team}|${g.week}`, g]));
  const gamePoint = (team, w, yval) => {
    const info = homeInfo.get(`${team}|${w.week}`) ?? {};
    return { i: weeks.indexOf(w.week), y: yval,
             html: gameTooltipHTML({
               game: info, weekLabel: season.weekLabels[w.week], wk: w,
               cap: w.attendance / (w.pct || 1), prefix: team }) };
  };
  if (!weeks.length) {
    emptyNote(c1, "No home games played yet this season.");
  } else {
    const series = sel.map((team) => {
      const row = summary.rows.find((r) => r.team === team);
      const pts = (row ? row.weeks : [])
        .filter((w) => weeks.includes(w.week))
        .map((w) => gamePoint(team, w, w.pct));
      return { name: team, color: colorOf.get(team) ?? "#888", pts };
    });
    multiLine(c1, series, weeks.map((w) => "wk " + w), (v) => pct(v), 0.04, 0.03);
  }

  // 2. season fill, year over year
  const years = Object.keys(seasonsData).sort();
  const c2 = card(`Season percent full, year over year (${years[0]}–${years[years.length - 1]})`,
    "One point per season per team");
  const series2 = sel.map((team) => {
    const pts = [];
    years.forEach((yr, i) => {
      const ts = teamsForSeason(teamsData, Number(yr));
      const sm = seasonSummary(ts, seasonsData[yr].games,
                               seasonsData[yr].weekLabels.length);
      const row = sm.rows.find((r) => r.team === team);
      if (row && row.games > 0) {
        pts.push({ i, y: row.pct, head: yr,
                   rows: [{ value: pct(row.pct), label: "season" },
                          { value: num(row.total), label: "total attendance" }] });
      }
    });
    return { name: team, color: colorOf.get(team) ?? "#888", pts };
  });
  multiLine(c2, series2, years, (v) => pct(v), 0.04, 0.03);

  // 3. weekly attendance (raw), selected season
  const c3 = card(`Attendance by week — ${currentYear}`,
    "Raw crowds; capacity differences show here");
  if (!weeks.length) {
    emptyNote(c3, "No home games played yet this season.");
  } else {
    const series3 = sel.map((team) => {
      const row = summary.rows.find((r) => r.team === team);
      const pts = (row ? row.weeks : [])
        .filter((w) => weeks.includes(w.week))
        .map((w) => gamePoint(team, w, w.attendance));
      return { name: team, color: colorOf.get(team) ?? "#888", pts };
    });
    multiLine(c3, series3, weeks.map((w) => "wk " + w),
      (v) => Math.round(v / 1000) + "k", 3000, 2000);
  }

  const c4 = card("Weather sensitivity",
    "Every home game with weather, per selected team; the dashed line is " +
    "that team's own trend. A downward slope means crowds thin as it heats " +
    "up (September in Tempe, Tucson, Lubbock); upward means the cold is " +
    "what keeps them home");
  teamWeather(c4, seasonsData, teamsData, selected);

  root.append(c1, c2, c3, c4);
}

export function renderSeasonCharts(root, teamsData, seasonsData, currentYear) {
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

  root.append(c1, c2, c3);
}


export function renderAllTimeCharts(root, teamsData, seasonsData) {
  root.textContent = "";
  const years = Object.keys(seasonsData).sort();
  const span = years.length > 1
    ? `${years[0]}–${years[years.length - 1]}` : years[0];

  const c1 = card(`Records watch (${span})`,
    `High-water marks across every tracked season, and active sellout streaks`);
  recordsWatch(c1, seasonsData, teamsData);

  const c2 = card(`Kickoff weather vs fill, all seasons (${span})`,
    `Cumulative: every tracked home game ${span}; ringed dots = rain; dashed line = least-squares trend`);
  weatherScatter(c2, seasonsData, teamsData);

  const cRoad = card(`Road draw (${span})`,
    "How much each Big 12 visitor lifts — or dents — the host's usual crowd; " +
    "the small number is road trips in the sample");
  roadDraw(cRoad, seasonsData, teamsData);

  const c3 = card(`Weekly percent full, year over year (${span})`, "Each line is a season; conference-wide");
  yoyLines(c3, seasonsData, teamsData);

  const c4 = card(`Season percent full by team, year over year (${span})`, "One dot per season; the connector spans a team's range");
  yoyTeams(c4, seasonsData, teamsData);

  root.append(c1, c2, cRoad, c3, c4);
}
