// Display names for a public leaderboard.
//
// The board is the product. A name on it is the only thing a player publishes,
// and the only thing anyone else sees, so two properties matter more than they
// would anywhere else on this site:
//
//   * Two names that LOOK the same must not both exist. Otherwise the board
//     can be made unreadable — or worse, someone can appear to be someone
//     else — with nothing more than a Cyrillic а.
//   * Nobody can claim to be the site. "admin", "big12ology", "official".
//
// Uniqueness is enforced on display_norm, not on display_name: players keep
// the capitalisation they chose, and the comparison happens on the folded
// form. Everything here is pure, so it is cheap to test exhaustively.

export const MIN_LEN = 3;
export const MAX_LEN = 20;

// Invisible characters, and the bidi overrides that let a name render in an
// order its bytes do not have. Neither has any business in a display name and
// both are how a lookalike gets built.
//
// Written as escapes, deliberately. The first version of this used the literal
// characters and was both unreadable and broken: one of them is a line
// separator, which ended the regex early and would not parse.
const INVISIBLE = new RegExp(
  "[" +
  "\\u00AD" +           // soft hyphen
  "\\u180E" +           // Mongolian vowel separator
  "\\u200B-\\u200F" +   // zero-width space/non-joiner/joiner, LRM, RLM
  "\\u202A-\\u202E" +   // bidi embeddings and overrides
  "\\u2060-\\u2064" +   // word joiner, invisible operators
  "\\u2066-\\u2069" +   // bidi isolates
  "\\uFEFF" +           // zero-width no-break space / BOM
  "]", "gu");

// Characters from other scripts that render as Latin ones in most faces. Not a
// complete confusables table — that is enormous, and Unicode publishes it —
// but these are the ones reachable from a standard keyboard layout and they
// cover the realistic attempt.
const CONFUSABLE = new Map(Object.entries({
  // Cyrillic
  "а": "a", "в": "b", "с": "c", "е": "e", "ѕ": "s", "і": "i", "ј": "j",
  "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "т": "t", "у": "y",
  "х": "x", "ԁ": "d", "ɡ": "g",
  // Greek
  "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ο": "o", "ρ": "p",
  "τ": "t", "υ": "u", "ν": "v", "χ": "x", "γ": "y", "ϲ": "c",
  // Latin lookalikes that NFKC leaves alone
  "ł": "l", "ø": "o", "đ": "d", "ı": "i",
}));

// Names nobody may take, in normalised form. Impersonation only. Profanity is
// a different problem with a different answer — it is subjective, it is
// language-specific, and a hardcoded list of slurs in a public repository is
// its own kind of bad. That is what the admin rename endpoint and a reported-
// name queue are for.
const RESERVED = new Set([
  "admin", "administrator", "mod", "moderator", "staff", "official",
  "big12ology", "big12", "big 12", "system", "root", "support", "help",
  "null", "undefined", "anonymous", "deleted", "the chalk", "chalk",
]);

/**
 * The comparison form of a name. Never shown to anyone.
 *
 * Order matters: NFKC first so fullwidth and ligature forms become their plain
 * equivalents, then case, then the confusable fold — folding before NFKC would
 * miss the fullwidth Cyrillic forms.
 */
export function normalize(name) {
  let s = String(name).normalize("NFKC").toLowerCase();
  s = s.replace(INVISIBLE, "");
  s = [...s].map((ch) => CONFUSABLE.get(ch) ?? ch).join("");
  // Any run of any whitespace becomes one plain space.
  s = s.replace(/\s+/g, " ").trim();
  return s;
}

/**
 * Is this a name someone may have?
 *
 * Returns {ok: true, display, norm} or {ok: false, error} with a slug the API
 * turns into a message. Length is measured in code points: "👍👍👍" is three
 * characters to a person and six to String.length, and the person is right.
 */
export function validate(name) {
  if (typeof name !== "string") return { ok: false, error: "name_required" };

  // Trim the display form too — a leading space is invisible and would sort
  // the name to the top of the board.
  const display = name.replace(INVISIBLE, "").replace(/\s+/g, " ").trim();
  const norm = normalize(display);

  const len = [...display].length;
  if (len < MIN_LEN) return { ok: false, error: "name_too_short" };
  if (len > MAX_LEN) return { ok: false, error: "name_too_long" };

  // Letters and numbers from any script, plus a few separators. Emoji are out:
  // they render differently on every platform, which defeats the point of a
  // name being recognisable on a shared board.
  if (!/^[\p{L}\p{N}][\p{L}\p{N} ._'-]*[\p{L}\p{N}.]$/u.test(display)) {
    return { ok: false, error: "name_charset" };
  }
  // Must contain something pronounceable. "..." passes the pattern above only
  // if it starts and ends with a letter or digit, but "a.-'b" would sneak
  // through as mostly punctuation.
  if ([...display].filter((c) => /[\p{L}\p{N}]/u.test(c)).length < MIN_LEN) {
    return { ok: false, error: "name_charset" };
  }
  // Seven in a row, not five. "Goooooo Cyclones" is a name a real person picks
  // on a college football site, and a filter that rejects it is wrong more
  // often than the padding it was meant to catch.
  if (/(.)\1{6,}/u.test(display)) return { ok: false, error: "name_repetitive" };
  if (RESERVED.has(norm)) return { ok: false, error: "name_reserved" };
  // "admin1", "big12ology-official": reserved words with decoration.
  const stripped = norm.replace(/[^a-z]/g, "");
  for (const r of RESERVED) {
    const rs = r.replace(/[^a-z]/g, "");
    if (rs.length >= 4 && stripped.includes(rs)) {
      return { ok: false, error: "name_reserved" };
    }
  }
  if (!norm) return { ok: false, error: "name_charset" };

  return { ok: true, display, norm };
}
