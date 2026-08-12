// What people actually do on the pages, counted without knowing who they are.
//
// Cloudflare Web Analytics already counts navigations, and it stays. What it
// cannot do is see anything that is not a navigation, and almost everything
// built here is not one: the what-if simulator, the fill, the copied link,
// the Lab board. A section could be the most used thing on the domain or
// completely dead and the pageview graph would look identical either way.
//
// So this is the second half of the measurement, and the whole design of it
// is the constraint that privacy.html states in the first person: no cookie,
// no local storage, no identifier, nothing that can be joined across two page
// loads or two days. That is not a limitation worked around. It is the shape:
//
//   * NOTHING FROM THE CLIENT IS TRUSTED AS A STRING. The event name and its
//     detail are matched against the table below and anything unrecognised is
//     dropped on the floor. There is no free-text field to smuggle a search
//     term, a display name or a scenario through, because there is no
//     free-text field at all.
//   * THE PAGE COMES FROM THE REFERER HEADER, not from the body. Same-origin
//     requests carry the full path there, so the client never has to send a
//     URL — and a Referer never includes the fragment, which is precisely
//     where the what-if keeps the scenario. The one measurement everybody
//     would want ("do shared links get opened?") is therefore answerable
//     without the server ever being in a position to learn what anybody
//     simulated. That is worth more than it costs.
//   * THE SESSION COOKIE IS NEVER READ HERE. The browser sends it — it is
//     same-origin and there is no way to ask it not to — and this file never
//     looks. Nothing written below is per-person, so there is nothing for a
//     user id to attach to even if one were in hand.
//
// Retention is Analytics Engine's own, ninety days, and there is no row to
// delete on request because there is no row that belongs to anybody.

/**
 * The vocabulary. An event is a name, an optional detail from a fixed list,
 * and an optional number.
 *
 * A closed list rather than an open one, and the reason is the failure mode
 * of the alternative: an ingest that accepts whatever it is sent grows a long
 * tail of typos, half-shipped experiments and one-off names, and a year later
 * nobody can say which of four spellings of the same thing to sum. Adding an
 * event here is one line, and the line is also the documentation.
 */
export const EVENTS = {
  // A page was read: how far down, and for how long it held attention.
  // The pageview is Cloudflare's job; this is the part that says whether the
  // view was a read or a bounce.
  read: { detail: ["25", "50", "75", "100"], value: "seconds" },

  // The what-if simulator, which is the most expensive thing on the site to
  // maintain and until now the least observed.
  whatif: { detail: ["pick", "fill", "clear", "model", "share"], value: "count" },

  // A scenario link was opened. `opened` is one that applied cleanly;
  // `stale` is one the page refused because the schedule moved under it —
  // the difference between a feature people use and a feature that quietly
  // breaks every time a game is rescheduled.
  scenario: { detail: ["opened", "stale"], value: null },

  // The top of the pools funnel, which the database cannot see: how many
  // people reached a page offering a sign-in and how many pressed it. Every
  // step after this one is a row in D1 and is counted there instead.
  pool: { detail: ["signin_shown", "signin_click"], value: null },

  // Collapsible cards. Cheap to record and it answers a real question about
  // page length: whether readers fold sections away or never touch them.
  card: { detail: ["collapse", "expand"], value: null },
};

/** No batch may be larger than this, and the client sends far smaller ones. */
export const MAX_BATCH = 16;

/**
 * Where the reader was, from the Referer of the beacon itself.
 *
 * Shape-gated rather than matched against a list of known pages. A list would
 * be a third copy of the site's routing table — build.py has one, assemble.sh
 * has another — and the failure mode of a stale copy here is that a new page
 * silently reports as "other" forever. What actually matters is that nothing
 * unbounded or personal gets stored, and a lowercase static path under 64
 * characters with the query string thrown away is bounded by construction.
 *
 * Fragments cannot appear: browsers do not put them in a Referer. That is
 * load-bearing rather than incidental — see the note at the top of this file.
 */
export function pageOf(referer, origin) {
  if (!referer) return { section: "unknown", page: "unknown" };
  let u;
  try { u = new URL(referer); } catch { return { section: "unknown", page: "unknown" }; }
  // A Referer from somewhere else means the beacon did not come from one of
  // our pages, which means it is not something to count.
  if (u.origin !== origin) return { section: "offsite", page: "offsite" };

  let path = u.pathname.replace(/index\.html$/, "");
  if (path.length > 1) path = path.replace(/\/+$/, "");
  if (!path) path = "/";
  if (path.length > 64 || !/^\/[a-z0-9/._-]*$/.test(path)) {
    return { section: "other", page: "other" };
  }
  const section = path === "/" ? "home" : path.split("/")[1];
  return { section, page: path };
}

/**
 * A per-isolate, per-minute cap, and it is worth being honest about what it
 * is: a speed bump, not a rate limit. Cloudflare spreads requests across
 * isolates, so a determined flood gets one bucket per isolate rather than
 * one bucket. The real defense against that is the WAF rule on /api/*, the
 * same one ratelimit.js names as the first line.
 *
 * What this stops is the accidental case, which is the likely one: a page
 * with a loop in it, a tab left open for a week, a bot walking the site with
 * JavaScript on. Those all arrive from one address through one isolate, and
 * this holds them to a number.
 *
 * D1 deliberately not used, unlike ratelimit.js. That limiter writes a row
 * per attempt because a sign-in attempt is rare and a row is affordable; a
 * beacon is neither, and paying a database write to decide whether to record
 * a free counter would cost more than the thing it protects.
 */
const RATE = { max: 120, minute: -1, seen: new Map() };

export function burst(ip, now = Date.now()) {
  const minute = Math.floor(now / 60000);
  // Drop the whole map on the turn of the minute rather than expiring keys
  // one at a time. It also bounds memory to a single minute of distinct
  // addresses, which is the only reason an in-memory map is safe here.
  if (minute !== RATE.minute) { RATE.minute = minute; RATE.seen.clear(); }
  const key = ip || "anon";
  const n = (RATE.seen.get(key) || 0) + 1;
  RATE.seen.set(key, n);
  return n <= RATE.max;
}

/**
 * Record one batch. Always 204, and the silence is deliberate.
 *
 * A measurement endpoint that reports its own errors teaches the client to
 * retry, and a client retrying a beacon is a client sending the same event
 * twice. Nothing here is worth a second attempt: a dropped event is one
 * missing tally in a number that only means anything in aggregate, and every
 * refusal below is either abuse or a bug on our own side that a 400 would not
 * fix in the field. So malformed, over-limit, unknown-name and rate-limited
 * all look the same from outside — nothing to probe and nothing to tune
 * against.
 */
export function record(env, req, body) {
  const ae = env.EVENTS;
  const rows = body && Array.isArray(body.e) ? body.e.slice(0, MAX_BATCH) : [];
  if (!rows.length) return 0;

  const { section, page } = pageOf(req.headers.get("Referer"), env.SITE_ORIGIN);
  // A beacon whose Referer is not one of our own pages is not measuring one
  // of our own pages, whatever the body claims.
  if (section === "offsite") return 0;

  let written = 0;
  for (const row of rows) {
    if (!Array.isArray(row)) continue;
    const [name, detail, value] = row;
    const spec = Object.prototype.hasOwnProperty.call(EVENTS, name)
      ? EVENTS[name] : null;
    if (!spec) continue;
    if (spec.detail && !spec.detail.includes(detail)) continue;

    // Clamped rather than rejected. A number out of range is a client bug and
    // the event around it is still true — an engaged time of four hours means
    // a tab was left open, not that the read did not happen.
    let n = 0;
    if (spec.value) {
      n = Number(value);
      if (!Number.isFinite(n)) n = 0;
      n = Math.min(Math.max(Math.round(n), 0), 3600);
    }

    written++;
    if (!ae) continue;  // local dev and the tests, which assert on the shape

    ae.writeDataPoint({
      // One index, which is all Analytics Engine allows, and it is the key
      // sampling groups by. The event name and nothing finer: an index with
      // a page or a detail in it multiplies the cardinality by the size of
      // the site for no gain, and a high-cardinality index is how sampling
      // starts throwing away the rare events that are the interesting ones.
      indexes: [String(name)],
      blobs: [String(name), section, page, detail == null ? "" : String(detail)],
      doubles: [n],
    });
  }
  return written;
}
