#!/usr/bin/env node
// Two shareable cards: last week's home crowds by percent full, and the
// season to date by the same measure. Written as self-contained SVG so they
// can be dropped into someone else's page with an <img> tag and nothing else.
//
// FROM THE SNAPSHOT, not from the season file. build_snapshots.mjs already
// reduced the games through the site's own stats module, so the percentages
// here are the ones the page shows by construction rather than by a second
// implementation agreeing with the first. Same reasoning as that script's.
//
// SELF-CONTAINED IS THE WHOLE POINT. An SVG loaded through <img> gets no
// external anything: no stylesheet, no webfont, no logo files, no script. So
// every color is inline, the type is a generic stack that resolves to
// something reasonable on any machine, and the background is painted rather
// than left transparent, because a transparent card on somebody's dark page
// is white text on white. Team color is carried by a swatch rather than a
// mark for the same reason: inlining sixteen logos would quadruple the file
// for something the name already says.
//
// EVERY WEEK KEEPS ITS OWN URL. cards/<year>/week-NN.svg is written once and
// then never changes, because a finished week's crowds do not. cards/week.svg
// is a copy of the newest one, for anybody who wants "whatever is current"
// without editing their page every Sunday. Hotlink either; the versioned name
// is the one that will still show week 3 in November.
//
// INCREMENTAL, because the PNGs are rendered by launching Chrome once per
// file and this runs on every attendance update. A card whose SVG comes out
// byte-identical to the one on disk is skipped, so a steady-state run spends
// nothing and only the current week is ever re-rendered.
//
// Usage: node scripts/build_cards.mjs 2026 [outDir] [--png]

import { readFileSync, writeFileSync, mkdirSync, readdirSync,
         existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2).filter((a) => a !== "--png");
const wantPng = process.argv.includes("--png");
const year = args[0] ?? "2026";
const outDir = args[1] ?? join(ROOT, "cards");

// The page's own palette, hard-coded because a card cannot read tokens.css.
const INK = "#16181A";
const DIM = "#5b6169";
const LINE = "#e2ddd3";
const PAGE = "#f6f4ef";
const CARD = "#ffffff";
const TEAL = "#0B6E77";
const FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
           + "Helvetica, Arial, sans-serif";

const esc = (s) => String(s).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const pct1 = (p) => `${(p * 100).toFixed(1)}%`;
const commas = (n) => n.toLocaleString("en-US");

/** The fill ramp, matching the site: green at capacity, warm below it. */
function fill(p) {
  if (p >= 1) return "#1f8a4c";
  if (p >= 0.9) return "#4a9a3f";
  if (p >= 0.75) return "#9a9a2f";
  if (p >= 0.6) return "#b8802e";
  return "#b5502e";
}

/**
 * One card. `rows` is [{team, pct, attendance, capacity, note}], already
 * sorted, and the bars are scaled against the largest percentage rather than
 * against 100%, so a week where nobody sold out still fills the card.
 */
function card({ title, subtitle, rows, footnote }) {
  const W = 900;
  const PAD = 32;
  const HEAD = 104;
  const ROW = 34;
  const FOOT = 46;
  const H = HEAD + rows.length * ROW + FOOT + PAD;

  const nameW = 168;
  const barX = PAD + nameW + 10;
  const barW = W - barX - PAD - 132;
  const top = Math.max(1, ...rows.map((r) => r.pct));

  const parts = [];
  parts.push(
    `<rect width="${W}" height="${H}" fill="${PAGE}"/>`,
    `<rect x="12" y="12" width="${W - 24}" height="${H - 24}" rx="14" ` +
      `fill="${CARD}" stroke="${LINE}"/>`,
    `<text x="${PAD}" y="52" font-family="${FONT}" font-size="24" ` +
      `font-weight="700" fill="${INK}">${esc(title)}</text>`,
    `<text x="${PAD}" y="76" font-family="${FONT}" font-size="14" ` +
      `fill="${DIM}">${esc(subtitle)}</text>`,
  );

  rows.forEach((r, i) => {
    const y = HEAD + i * ROW;
    const mid = y + ROW / 2;
    const w = Math.max(2, Math.round((r.pct / top) * barW));
    parts.push(
      `<rect x="${PAD}" y="${mid - 7}" width="5" height="14" rx="2" ` +
        `fill="${esc(r.color || TEAL)}"/>`,
      `<text x="${PAD + 14}" y="${mid + 5}" font-family="${FONT}" ` +
        `font-size="14" fill="${INK}">${esc(r.team)}</text>`,
      `<rect x="${barX}" y="${mid - 9}" width="${barW}" height="18" rx="4" ` +
        `fill="${PAGE}"/>`,
      `<rect x="${barX}" y="${mid - 9}" width="${w}" height="18" rx="4" ` +
        `fill="${fill(r.pct)}"/>`,
      `<text x="${barX + barW + 12}" y="${mid + 5}" font-family="${FONT}" ` +
        `font-size="14" font-weight="700" fill="${INK}">${pct1(r.pct)}</text>`,
      `<text x="${W - PAD}" y="${mid + 5}" font-family="${FONT}" ` +
        `font-size="12" fill="${DIM}" text-anchor="end">` +
        `${commas(r.attendance)}${r.note ? " " + esc(r.note) : ""}</text>`,
    );
  });

  parts.push(
    `<text x="${PAD}" y="${H - PAD + 4}" font-family="${FONT}" ` +
      `font-size="12" fill="${DIM}">${esc(footnote)}</text>`,
    `<text x="${W - PAD}" y="${H - PAD + 4}" font-family="${FONT}" ` +
      `font-size="12" fill="${TEAL}" text-anchor="end" ` +
      `font-weight="600">big12ology.com/attendance</text>`,
  );

  // width/height as well as viewBox: an <img> with no CSS sizing needs
  // intrinsic dimensions or it renders at whatever the host decides.
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" `
       + `viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}. `
       + `${esc(subtitle)}">\n` + parts.join("\n") + "\n</svg>\n";
}

function snapshots(y) {
  const dir = join(ROOT, "data", "snapshots", String(y));
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((f) => /^week-\d+\.json$/.test(f)).sort()
    .map((f) => JSON.parse(readFileSync(join(dir, f))));
}

/** The week's home games, sorted, or [] if that week reported nothing. */
function weekRowsOf(snap) {
  const wk = snap.throughWeek;
  return snap.rows
    .map((r) => ({ row: r, w: (r.weeks || []).find((x) => x.week === wk) }))
    .filter((x) => x.w && x.w.attendance != null)
    .map(({ row, w }) => ({
      team: row.team,
      pct: w.pct,
      attendance: w.attendance,
      color: row.color,
      // An estimated capacity makes the percentage an estimate too, and the
      // card travels away from the page that would otherwise say so.
      note: row.capacityEstimate ? "est" : "",
    }))
    .sort((a, b) => b.pct - a.pct);
}

function seasonRowsOf(snap) {
  return snap.rows
    .filter((r) => r.games > 0 && r.pct != null)
    .map((r) => ({
      team: r.team,
      pct: r.pct,
      attendance: r.total,
      color: r.color,
      note: `${r.games}g${r.capacityEstimate ? " est" : ""}`,
    }))
    .sort((a, b) => b.pct - a.pct);
}

function chrome() {
  const named = process.env.CHROME_BIN || process.env.CHROME_PATH;
  return [
    named,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
  ].filter(Boolean).find((p) => existsSync(p)) || null;
}

// Chrome, because it is the one renderer that is definitely there. The GitHub
// runner image ships Chrome, Chromium and ChromeDriver but no librsvg and no
// ImageMagick, and ImageMagick without an rsvg delegate renders SVG text badly
// enough to be worse than nothing.
//
// A PNG at all, when the SVG is smaller and sharper, because an SVG cannot go
// everywhere the card wants to: link previews on Slack, Discord and the rest
// will not unfurl one, and neither will most email clients.
let BIN = null;
function png(svgPath, outPath, w, h) {
  if (!wantPng) return false;
  if (BIN === null) {
    BIN = chrome();
    // Loud rather than optional. A missing PNG is a 404 on a URL somebody else
    // put in their page, which is worse than a failed build.
    if (!BIN) {
      console.error("no Chrome found for --png; set CHROME_BIN. Tried the "
                    + "usual macOS and Linux paths.");
      process.exit(1);
    }
  }
  execFileSync(BIN, [
    "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    // Twice the intrinsic size, so the card stays sharp when a site scales it
    // up or a reader is on a retina screen.
    "--force-device-scale-factor=2",
    `--window-size=${w},${h}`,
    "--default-background-color=FFFFFFFF",
    `--screenshot=${outPath}`,
    svgPath,
  ], { stdio: ["ignore", "ignore", "pipe"] });
  return true;
}

/** Write only on change, and re-render the PNG only when the SVG moved. */
function emit(dir, name, svg) {
  mkdirSync(dir, { recursive: true });
  const svgPath = join(dir, `${name}.svg`);
  const pngPath = join(dir, `${name}.png`);
  const had = existsSync(svgPath) ? readFileSync(svgPath, "utf8") : null;
  const moved = had !== svg;
  if (moved) writeFileSync(svgPath, svg);
  if (moved || (wantPng && !existsSync(pngPath))) {
    const m = svg.match(/width="(\d+)" height="(\d+)"/);
    if (png(svgPath, pngPath, Number(m[1]), Number(m[2]))) {
      console.log(`  ${name}.svg + .png`);
      return true;
    }
  }
  if (moved) console.log(`  ${name}.svg`);
  return moved;
}

const snaps = snapshots(year);
if (!snaps.length) {
  console.error(`no snapshots for ${year}; nothing to build`);
  process.exit(1);
}

const seasonDir = join(outDir, String(year));
let wrote = 0;
let newest = null;

for (const snap of snaps) {
  const wk = snap.throughWeek;
  const nn = String(wk).padStart(2, "0");
  const rows = weekRowsOf(snap);
  // A week nobody hosted in gets no card rather than an empty one.
  if (rows.length) {
    wrote += emit(seasonDir, `week-${nn}`, card({
      title: `Big 12 home crowds, week ${wk}`,
      subtitle: `${rows.length} home games, sorted by percent of capacity`,
      rows,
      footnote: `${snap.season} season, week ${wk}`,
    })) ? 1 : 0;
  }
  wrote += emit(seasonDir, `season-${nn}`, card({
    title: `Big 12 attendance, ${snap.season} season to date`,
    subtitle: `Through week ${wk}, sorted by percent of capacity`,
    rows: seasonRowsOf(snap),
    footnote: `${commas(snap.totals.attendance)} across `
            + `${snap.totals.games} games, ${pct1(snap.totals.pct)} full`,
  })) ? 1 : 0;
  newest = { nn, hadWeek: rows.length > 0 };
}

// The unversioned pair, copied rather than linked: Pages serves files, and an
// rsync of a symlink is a decision nobody wants to debug later.
for (const [alias, src] of [["week", `week-${newest.nn}`],
                            ["season", `season-${newest.nn}`]]) {
  if (alias === "week" && !newest.hadWeek) continue;
  for (const ext of wantPng ? ["svg", "png"] : ["svg"]) {
    const from = join(seasonDir, `${src}.${ext}`);
    const to = join(outDir, `${alias}.${ext}`);
    if (!existsSync(from)) continue;
    const next = readFileSync(from);
    const had = existsSync(to) ? readFileSync(to) : null;
    if (!had || !had.equals(next)) {
      writeFileSync(to, next);
      console.log(`  ${alias}.${ext} -> ${src}.${ext}`);
      wrote++;
    }
  }
}

console.log(`${year}: ${snaps.length} week(s) on file, ${wrote} file(s) written`);
