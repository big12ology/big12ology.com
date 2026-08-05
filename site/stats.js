// Attendance math for the tracker — the single source of truth.
// Replaces the spreadsheet's COUNTIF/SUMIF(">200") trick with explicit fields:
// a game counts if it exists in the season data, regardless of attendance value,
// so a 200-person or zero-attendance game can never silently drop from totals
// (a fragility the original sheet had).
//
// Capacity is per-game: a game's own capacity (alternate venue, e.g. Kansas
// 2024 in Kansas City) wins over the team's stadium capacity for that year.
// Season percent divides by the sum of per-game capacities — identical to the
// sheet's games × capacity when capacity is constant, correct when it isn't.

// teams.json keys capacity and stadium by year; resolve to the most recent
// entry at or before the requested season.
function resolveByYear(byYear, year) {
  if (byYear == null || typeof byYear !== "object") return byYear;
  const years = Object.keys(byYear)
    .map(Number)
    .filter((y) => y <= year)
    .sort((a, b) => b - a);
  return years.length ? byYear[years[0]] : null;
}

export function teamsForSeason(teamsData, year) {
  return teamsData.teams.map((t) => ({
    team: t.team,
    stadium: resolveByYear(t.stadium, year),
    capacity: resolveByYear(t.capacity, year),
    // A capacity we carried forward because the school has not published
    // one. Exact-year only: an estimate for 2026 says nothing about 2027.
    capacityEstimate: (t.capacityEstimated || {})[String(year)] || null,
    color: t.color,
    altColor: t.altColor,
    logo: t.logo,
  }));
}

// Season files may contain non-summing perspective entries (a Big 12 team on
// the road or at a true neutral site), marked with a `role` field. Every
// computation here skips them — they exist purely for display.
export function teamSeason(team, games) {
  const played = games
    .filter((g) => g.team === team.team && !g.role && g.attendance != null)
    .sort((a, b) => a.week - b.week);
  const capOf = (g) => g.capacity ?? team.capacity;
  const total = played.reduce((s, g) => s + g.attendance, 0);
  const capTotal = played.reduce((s, g) => s + capOf(g), 0);
  return {
    team: team.team,
    stadium: team.stadium,
    capacity: team.capacity,
    capacityEstimate: team.capacityEstimate,
    color: team.color,
    logo: team.logo,
    multiVenue: played.some((g) => g.capacity != null),
    games: played.length,
    weeks: played.map((g) => ({
      week: g.week,
      attendance: g.attendance,
      venue: g.venue,
      pct: g.attendance / capOf(g),
    })),
    total,
    pct: capTotal ? total / capTotal : 0,
  };
}

export function weeklyTotals(teams, games, numWeeks) {
  const byCap = Object.fromEntries(teams.map((t) => [t.team, t.capacity]));
  const weeks = [];
  for (let w = 0; w < numWeeks; w++) {
    const wk = games.filter((g) => g.week === w && !g.role && g.attendance != null);
    const attendance = wk.reduce((s, g) => s + g.attendance, 0);
    const capacity = wk.reduce((s, g) => s + (g.capacity ?? byCap[g.team]), 0);
    weeks.push({
      week: w,
      games: wk.length,
      attendance,
      capacity,
      pct: capacity ? attendance / capacity : 0,
    });
  }
  return weeks;
}

export function seasonSummary(teams, games, numWeeks = 15) {
  const rows = teams
    .map((t) => teamSeason(t, games))
    .sort((a, b) => b.pct - a.pct || a.team.localeCompare(b.team));
  const weeks = weeklyTotals(teams, games, numWeeks);
  const attendance = weeks.reduce((s, w) => s + w.attendance, 0);
  const capacity = weeks.reduce((s, w) => s + w.capacity, 0);
  return {
    rows,
    weeks,
    totals: {
      games: weeks.reduce((s, w) => s + w.games, 0),
      attendance,
      capacity,
      pct: capacity ? attendance / capacity : 0,
    },
  };
}
