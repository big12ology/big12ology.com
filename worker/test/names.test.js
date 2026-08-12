import test from "node:test";
import assert from "node:assert/strict";
import { normalize, validate, MAX_LEN } from "../src/names.js";

const ok = (n) => {
  const r = validate(n);
  assert.equal(r.ok, true, `${JSON.stringify(n)} was rejected: ${r.error}`);
  return r;
};
const no = (n, err) => {
  const r = validate(n);
  assert.equal(r.ok, false, `${JSON.stringify(n)} was accepted`);
  if (err) assert.equal(r.error, err);
};

test("ordinary names are left alone", () => {
  for (const n of ["Chris", "Cyclone Fan 92", "O'Brien", "jim.bob",
                   "Bob-Smith", "ISU4Life", "Ñandú", "北京球迷"]) ok(n);
  assert.equal(ok("Chris").display, "Chris");   // capitalisation is kept
});

test("uniqueness is decided on the folded form, not the shown one", () => {
  assert.equal(normalize("Chris"), normalize("CHRIS"));
  assert.equal(normalize("Chris"), normalize("  chris  "));
  assert.equal(normalize("Chris"), normalize("Ｃｈｒｉｓ"));   // fullwidth
});

test("a Cyrillic lookalike cannot sit beside the Latin name", () => {
  // "сhris" with a Cyrillic с. Renders identically in most faces; this is the
  // whole reason display_norm exists.
  assert.equal(normalize("сhris"), normalize("chris"));
  assert.equal(normalize("Сhrіs"), normalize("chris"));      // с and і
  assert.equal(normalize("οmaha"), normalize("omaha"));      // Greek ο
});

test("invisible characters cannot make a second copy of a taken name", () => {
  assert.equal(normalize("chr\u200Bis"), "chris");   // zero-width space
  assert.equal(normalize("\u200E" + "chris"), "chris"); // LTR mark
  assert.equal(normalize("chris\uFEFF"), "chris");   // BOM
  // And they are stripped from the shown form too, not just the folded one.
  assert.equal(ok("chr\u200Bis").display, "chris");
});

test("bidi overrides are removed", () => {
  // These reorder rendering without changing the bytes.
  assert.equal(normalize("\u202Echris"), "chris");
  assert.equal(normalize("a\u2066b\u2069c"), "abc");
});

test("whitespace is collapsed, never leading or trailing", () => {
  assert.equal(ok("Cyclone   Fan").display, "Cyclone Fan");
  assert.equal(ok("  Cyclone Fan  ").display, "Cyclone Fan");
  assert.equal(normalize("Cyclone\tFan"), "cyclone fan");
});

test("length is counted in characters, not code units", () => {
  no("ab", "name_too_short");
  ok("abc");
  // A realistic name of exactly MAX_LEN. Not "a".repeat(20) — that trips the
  // repetition rule, and would have been testing two things at once.
  const twenty = "Cyclone Fan Of Ames";
  assert.equal([...twenty].length, MAX_LEN - 1);
  ok(twenty);
  no(twenty + " of Iowa", "name_too_long");
  // Accented characters are one character each, not two.
  assert.equal([...("Ñandú")].length, 5);
  ok("Ñandú");
});

test("a fan can shout", () => {
  // The repetition rule is anti-padding, not anti-enthusiasm.
  ok("Goooooo Cyclones");
  ok("Wooo Hooo");
  no("a".repeat(MAX_LEN), "name_repetitive");
});

test("nobody can claim to be the site", () => {
  for (const n of ["admin", "Admin", "ADMIN", "moderator", "official",
                   "big12ology", "Big12ology", "staff", "support"]) {
    no(n, "name_reserved");
  }
});

test("reserved words cannot be smuggled in with decoration", () => {
  for (const n of ["admin1", "the-admin", "big12ology official",
                   "xxadminxx", "Admin.", "big12ology2"]) {
    const r = validate(n);
    assert.equal(r.ok, false, `${JSON.stringify(n)} was accepted`);
  }
});

test("only real lookalikes are folded, and that is the right line", () => {
  // Cyrillic а (U+0430) and і (U+0456) are indistinguishable from Latin in
  // ordinary text, so "аdmіn" must not be available.
  no("аdmіn", "name_reserved");
  // Cyrillic д is NOT a Latin "d" to look at, so "Адmin" is a
  // different name, not an impersonation, and folding it would be
  // over-reach — it would deny a Russian speaker an ordinary word.
  ok("Адmin");
});

test("a reserved word cannot be reached through a lookalike", () => {
  // "аdmin" with a Cyrillic а — folds to "admin" before the check runs.
  no("аdmin", "name_reserved");
  no("оfficial", "name_reserved");
});

test("names that are not names are rejected", () => {
  no("");
  no("   ");
  no("...", "name_charset");
  no("-.-", "name_charset");
  no("a.-'b", "name_charset");        // mostly punctuation
  no("aaaaaaa", "name_repetitive");   // five or more of the same run
  // A control character is not whitespace and does not trim away.
  no("chris\u0000", "name_charset");
  no("chris\u0007", "name_charset");
  no(null, "name_required");
  no(undefined, "name_required");
  no(42, "name_required");
});

test("emoji are out — they render differently on every platform", () => {
  no("chris👍", "name_charset");
  no("👍👍👍", "name_charset");
});

test("validate is idempotent: its own output always revalidates", () => {
  for (const n of ["  Cyclone   Fan  ", "chr\u200Bis", "Ｃｈｒｉｓ", "O'Brien"]) {
    const first = validate(n);
    if (!first.ok) continue;
    const second = validate(first.display);
    assert.equal(second.ok, true,
      `${JSON.stringify(n)} normalized to ${JSON.stringify(first.display)}, ` +
      `which then failed: ${second.error}`);
    assert.equal(second.display, first.display);
    assert.equal(second.norm, first.norm);
  }
});
