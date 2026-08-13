// One escape, in one place, because there were three files building HTML by
// hand and only one of them had a copy.
//
// EVERY STRING RENDERED BY THIS SECTION IS A STRANGER'S. The season files are
// written by scripts/fetch_attendance.py straight from the CFBD API — opponent
// names, venue names, cities, conference labels — and committed by a scheduled
// workflow. Nothing in that pipeline escapes or allowlists anything, and
// nothing should: the fix belongs at the point of rendering, where it is
// obvious what the bytes are about to become.
//
// The failure this closes was not theoretical and it was not one call site.
// app.js already had this function and used it on four `title=` attributes
// while interpolating THE SAME VALUE unescaped into the cell text three lines
// away; charts.js had sixteen innerHTML assignments and no escape at all. A
// per-file helper is how that happens, so there is now one and it is imported.
//
// Quotes and angle brackets both, and the quotes matter as much as the
// brackets: most of these values land in an attribute, where a bare `"` is
// enough to open `onerror=` without ever writing a `<`.
export const esc = (t) =>
  String(t ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

/**
 * A URL that is safe to put in `href` or `src`.
 *
 * Escaping alone does not make a URL safe — `javascript:alert(1)` contains no
 * character this file rewrites. So the scheme is checked first and anything
 * that is not an ordinary web reference becomes empty rather than being
 * cleaned up into something that still runs.
 *
 * Relative paths (`assets/logos/ksu.svg`, `/schedule/game/x.html`) are the
 * common case here and are allowed through unchanged; only an explicit scheme
 * has to justify itself.
 */
export const escUrl = (u) => {
  const s = String(u ?? "").trim();
  if (/^[a-z][a-z0-9+.-]*:/i.test(s) && !/^https?:/i.test(s)) return "";
  return esc(s);
};
