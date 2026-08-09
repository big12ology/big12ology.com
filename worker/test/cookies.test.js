// The signed state cookie, and the redirect allowlist.
//
// Both are small and both are the kind of thing that is "obviously fine" right
// up until it is a vulnerability, so they are tested as adversarially as they
// are cheap to test.

import test from "node:test";
import assert from "node:assert/strict";
import {
  clear, HOME, parseCookies, safeReturn, serialize, sign, unsign,
} from "../src/cookies.js";

const KEY = "test-signing-key-not-the-real-one";

test("a signed cookie round-trips", async () => {
  const t = await sign(KEY, { state: "abc", provider: "google" }, 600);
  const back = await unsign(KEY, t);
  assert.equal(back.state, "abc");
  assert.equal(back.provider, "google");
});

test("a tampered payload does not verify", async () => {
  const t = await sign(KEY, { state: "abc" }, 600);
  const [b, sig] = t.split(".");
  // Flip the payload, keep the signature.
  const evil = btoa(JSON.stringify({ state: "xyz", exp: 2 ** 31 }))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  assert.equal(await unsign(KEY, `${evil}.${sig}`), null);
});

test("a different key does not verify", async () => {
  const t = await sign(KEY, { state: "abc" }, 600);
  assert.equal(await unsign("some-other-key", t), null);
});

test("expiry is enforced from inside the signature", async () => {
  // Max-Age is a request to the browser. A replayed cookie has to be refused
  // by us, which is why exp is inside the signed payload.
  const t = await sign(KEY, { state: "abc" }, -1);
  assert.equal(await unsign(KEY, t), null);
});

test("garbage is null, not an exception", async () => {
  for (const junk of ["", "no-dot", "a.b", "...", "!!!.???", null, undefined]) {
    assert.equal(await unsign(KEY, junk), null, `threw or passed on ${junk}`);
  }
});

test("cookies parse, including values with = in them", () => {
  const c = parseCookies("__Host-b12s=abc%3D%3D; other=1; flag");
  assert.equal(c["__Host-b12s"], "abc==");
  assert.equal(c.other, "1");
  assert.equal(Object.keys(c).length, 2, "a valueless flag became a cookie");
});

test("__Host- attributes are all present, and Domain is not", () => {
  const s = serialize("__Host-b12s", "v", { maxAge: 60 });
  for (const bit of ["Path=/", "Secure", "SameSite=Lax", "HttpOnly", "Max-Age=60"]) {
    assert.match(s, new RegExp(bit.replace("/", "\\/")), `missing ${bit}`);
  }
  // A Domain attribute makes the browser reject a __Host- cookie outright,
  // and would also let a subdomain overwrite it.
  assert.doesNotMatch(s, /Domain=/);
  assert.doesNotMatch(clear("__Host-b12s"), /Domain=/);
});

test("SameSite is Lax and not Strict", () => {
  // Strict is withheld on the top-level cross-site GET back from Google, so
  // the state cookie would not arrive and no sign-in could ever complete.
  assert.match(serialize("x", "y"), /SameSite=Lax/);
});

test("return_to only ever goes somewhere in this section", () => {
  // The allowlist is the roof, /pools/, not one game under it — both games
  // and the account they share have to be reachable after a sign-in.
  assert.equal(safeReturn("/pools/pickem/card.html"), "/pools/pickem/card.html");
  assert.equal(safeReturn("/pools/survivor/"), "/pools/survivor/");
  assert.equal(safeReturn("/pools/account.html"), "/pools/account.html");
  assert.equal(safeReturn("/pools/"), "/pools/");
  // The old flat URL is a redirect now, and not somewhere to be sent.
  assert.equal(safeReturn("/pickem/"), HOME);
  // The one that gets through a naive startsWith("/"): a protocol-relative
  // URL is a valid absolute redirect to another host.
  assert.equal(safeReturn("//evil.example"), HOME);
  assert.equal(safeReturn("/\\evil.example"), HOME);
  assert.equal(safeReturn("https://evil.example"), HOME);
  assert.equal(safeReturn("/tiebreaker/"), HOME);
  assert.equal(safeReturn(""), HOME);
  assert.equal(safeReturn(null), HOME);
  assert.equal(safeReturn({ toString: () => "/pools/x" }), HOME);
});
