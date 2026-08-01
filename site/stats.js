// Attendance math for the tracker — the single source of truth.
// Replaces the spreadsheet's COUNTIF/SUMIF(">200") trick with explicit fields:
// a game counts if it exists in the season data, regardless of attendance value,
// so a 200-person or zero-attendance game can never silently drop from totals
// (a fragility the original sheet had).

export function teamSeason(team, games) {
  const played = games
    .filter((g) => g.team === team.team && g.attendance != null)
    .sort((a, b) => a.week - b.week);
  const total = played.reduce((s, g) => s + g.attendance, 0);
  const capacity = team.capacity;
  return {
    team: team.team,
    stadium: team.stadium,
    capacity,
    games: played.length,
    weeks: played.map((g) => ({
      week: g.week,
      attendance: g.attendance,
      pct: g.attendance / capacity,
    })),
    total,
    // Season % assumes constant stadium capacity for the year (as the sheet did).
    pct: played.length ? total / (played.length * capacity) : 0,
  };
}

export function weeklyTotals(teams, games, numWeeks) {
  const byCap = Object.fromEntries(teams.map((t) => [t.team, t.capacity]));
  const weeks = [];
  for (let w = 0; w < numWeeks; w++) {
    const wk = games.filter((g) => g.week === w && g.attendance != null);
    const attendance = wk.reduce((s, g) => s + g.attendance, 0);
    const capacity = wk.reduce((s, g) => s + byCap[g.team], 0);
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
