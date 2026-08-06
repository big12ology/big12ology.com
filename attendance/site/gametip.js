// The one game tooltip. Every hover on this site — table cell, heatmap
// cell, weather scatter dot, team-comparison point — renders this exact
// card, so a game reads the same wherever you meet it.

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
                "Sep", "Oct", "Nov", "Dec"];
const num = (n) => n.toLocaleString("en-US");
const pct = (p) => (p * 100).toFixed(1) + "%";

// Prose dates carry no year — the season is already on screen (selector,
// chart title, or the weekLabel the caller passes for multi-season views).
export function fmtDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y, m - 1, d);
  return `${DAYS[dt.getDay()]}, ${MONTHS[m - 1]} ${d}`;
}

export function fmtTime(hhmm) {
  if (!hhmm) return "";
  let [h, m] = hhmm.split(":").map(Number);
  const ap = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  return `${h}:${String(m).padStart(2, "0")} ${ap}`;
}

/**
 * game      the season-file game object (opponent, points, weather, venue…)
 * weekLabel display label for the week ("Week 4")
 * wk        optional computed week stats {attendance, pct}
 * cap       optional capacity for the "of 50,000" tail
 * prefix    optional leading crumb (team name, when the chart mixes teams)
 */
export function gameTooltipHTML({ game, weekLabel, wk, cap, prefix }) {
  if (!game) return null;
  const lines = [];
  const when = [fmtDate(game.date), fmtTime(game.time)].filter(Boolean).join(" · ");
  const head = [prefix, weekLabel, when].filter(Boolean).join(" · ");
  lines.push(`<div class="tip-head">${head}</div>`);

  if (game.opponent) {
    let result = "";
    if (game.pointsFor != null && game.pointsAgainst != null) {
      const won = game.pointsFor > game.pointsAgainst;
      result = ` — <strong class="${won ? "win" : "loss"}">` +
        `${won ? "W" : "L"} ${game.pointsFor}–${game.pointsAgainst}</strong>`;
    }
    const prep = game.role === "away" ? "at" : "vs";
    lines.push(`<div class="tip-opp">${prep} ${game.opponent}${result}</div>`);
  }

  if (game.role) {
    const where = [game.venue,
                   game.city ? `${game.city}, ${game.state ?? ""}`.replace(/, $/, "") : null]
      .filter(Boolean).join(" · ");
    if (where) {
      lines.push(`<div>${where}${game.role === "neutral" ? " (neutral site)" : ""}</div>`);
    }
    if (game.attendance != null) {
      lines.push(`<div class="tip-wx">Attendance ${num(game.attendance)}</div>`);
    }
  } else if (wk && wk.attendance != null) {
    lines.push(
      `<div>${num(wk.attendance)} · ${pct(wk.pct)}` +
      `${cap ? ` of ${num(Math.round(cap))}` : ""}` +
      `${game.venue ? ` · ${game.venue}` : ""}</div>`);
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
    lines.push(`<a href="${link}" target="_blank" rel="noopener">` +
      `${played ? "Box score" : "Game preview"} ↗</a>`);
  }
  return lines.join("");
}
