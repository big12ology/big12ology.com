// The event ingest.
//
// Two things are being tested and only one of them is the happy path. The
// other is the promise: privacy.html states in plain English that nothing
// here identifies anybody and that no free text reaches the store, and a
// promise in prose is worth exactly as much as the test that holds it. So the
// adversarial cases below are not thoroughness — they are the enforcement.

import test from "node:test";
import assert from "node:assert/strict";

import { EVENTS, MAX_BATCH, burst, pageOf, record } from "../src/events.js";
import { makeEnv } from "./helpers/env.js";
import worker from "../src/index.js";

const ORIGIN = "https://big12ology.com";

/** An env whose Analytics Engine binding remembers instead of writing. */
function withAE(extra = {}) {
  const written = [];
  const env = makeEnv({
    EVENTS: { writeDataPoint: (p) => written.push(p) },
    ...extra,
  });
  return { env, written };
}

/** Just enough of a Request for record(): it only ever reads the Referer. */
function beacon({ referer = `${ORIGIN}/tiebreaker/lab.html` } = {}) {
  return { headers: new Headers({ Referer: referer }) };
}

// ------------------------------------------------------------------- pageOf

test("the page comes from the Referer, with the query thrown away", () => {
  const p = pageOf(`${ORIGIN}/tiebreaker/lab.html?utm_source=x`, ORIGIN);
  assert.equal(p.page, "/tiebreaker/lab.html");
  assert.equal(p.section, "tiebreaker");
});

test("a fragment cannot arrive, which is what protects the scenario", () => {
  // Browsers strip it, so this is belt and braces — but the whole argument
  // for measuring the sharing feature at all is that the thing being shared
  // never reaches us. If a fragment ever did turn up it must not be stored.
  const p = pageOf(`${ORIGIN}/tiebreaker/lab.html#lab=2026.abc.blend.QUJD`,
                   ORIGIN);
  assert.equal(p.page, "/tiebreaker/lab.html");
  assert.ok(!p.page.includes("lab="));
});

test("index.html and trailing slashes collapse to one page", () => {
  for (const u of ["/pools/", "/pools", "/pools/index.html"]) {
    assert.equal(pageOf(ORIGIN + u, ORIGIN).page, "/pools", u);
  }
  assert.equal(pageOf(`${ORIGIN}/`, ORIGIN).page, "/");
  assert.equal(pageOf(`${ORIGIN}/`, ORIGIN).section, "home");
});

test("anything unusual in a path becomes 'other' rather than being stored", () => {
  const nasty = [
    `${ORIGIN}/search/Chris%20Walsh`,        // a name, however it got there
    `${ORIGIN}/${"a".repeat(80)}`,           // unbounded
    `${ORIGIN}/UPPER/Case`,                  // not a path this site emits
  ];
  for (const u of nasty) assert.equal(pageOf(u, ORIGIN).page, "other", u);
});

test("a Referer from somewhere else is not one of our pages", () => {
  assert.equal(pageOf("https://example.com/x", ORIGIN).section, "offsite");
  assert.equal(pageOf("", ORIGIN).section, "unknown");
  assert.equal(pageOf("not a url", ORIGIN).section, "unknown");
});

// ------------------------------------------------------------------- record

test("a known event is written, with the name as the only index", () => {
  const { env, written } = withAE();
  assert.equal(record(env, beacon(), { e: [["whatif", "pick", 12]] }), 1);
  assert.equal(written.length, 1);
  assert.deepEqual(written[0].indexes, ["whatif"]);
  assert.deepEqual(written[0].blobs,
                   ["whatif", "tiebreaker", "/tiebreaker/lab.html", "pick"]);
  assert.deepEqual(written[0].doubles, [12]);
});

test("an unknown name is dropped, not stored", () => {
  const { env, written } = withAE();
  const n = record(env, beacon(), {
    e: [["whatif", "pick", 1], ["exfiltrate", "chris@example.com", 1]],
  });
  assert.equal(n, 1);
  assert.equal(written.length, 1);
});

test("a detail outside the list is dropped even under a known name", () => {
  // The one that matters. A name is easy to guess; the detail is where a
  // string would be smuggled if anything were allowed through by shape.
  const { env, written } = withAE();
  const n = record(env, beacon(), {
    e: [["whatif", "Kansas State", 1], ["card", "<script>", 1]],
  });
  assert.equal(n, 0);
  assert.equal(written.length, 0);
});

test("every detail the client can send is one this file already lists", () => {
  // A guard against the vocabulary drifting: if a new detail is added to
  // EVENTS it is deliberate, and if one is removed the call site has to go
  // with it. Both spellings live in one table by design.
  for (const [name, spec] of Object.entries(EVENTS)) {
    assert.ok(Array.isArray(spec.detail) && spec.detail.length,
              `${name} has no detail list`);
    for (const d of spec.detail) {
      assert.equal(typeof d, "string");
      assert.ok(/^[a-z0-9_]+$/.test(d), `${name}/${d} is not a plain token`);
    }
  }
});

test("a value is clamped rather than trusted, and never a string", () => {
  const { env, written } = withAE();
  record(env, beacon(), {
    e: [["read", "50", 99999], ["read", "50", -3], ["read", "50", "12"],
        ["read", "50", "nonsense"]],
  });
  assert.deepEqual(written.map((w) => w.doubles[0]), [3600, 0, 12, 0]);
});

test("an event with no value slot stores zero, whatever was sent", () => {
  const { env, written } = withAE();
  record(env, beacon(), { e: [["scenario", "opened", 500]] });
  assert.deepEqual(written[0].doubles, [0]);
});

test("a batch is capped and the rest is discarded", () => {
  const { env, written } = withAE();
  const many = Array.from({ length: 60 }, () => ["card", "collapse", 0]);
  assert.equal(record(env, beacon(), { e: many }), MAX_BATCH);
  assert.equal(written.length, MAX_BATCH);
});

test("a beacon from off-site records nothing at all", () => {
  const { env, written } = withAE();
  const n = record(env, beacon({ referer: "https://example.com/" }),
                   { e: [["whatif", "pick", 1]] });
  assert.equal(n, 0);
  assert.equal(written.length, 0);
});

test("junk in the body is survived rather than thrown on", () => {
  const { env, written } = withAE();
  for (const body of [null, {}, { e: null }, { e: "nope" }, { e: [null, 7] },
                      { e: [[]] }]) {
    assert.doesNotThrow(() => record(env, beacon(), body),
                        JSON.stringify(body));
  }
  assert.equal(written.length, 0);
});

test("a missing binding is not an error", () => {
  // Local dev and CI have no Analytics Engine. The endpoint must still
  // behave; a measurement that breaks the API it lives in is a bad trade.
  const env = makeEnv();
  assert.equal(record(env, beacon(), { e: [["card", "expand", 0]] }), 1);
});

// -------------------------------------------------------------------- burst

test("the per-minute cap lets a normal visit through and stops a loop", () => {
  const t = 60_000 * 1000;
  let allowed = 0;
  for (let i = 0; i < 500; i++) if (burst("1.2.3.4", t)) allowed++;
  assert.equal(allowed, 120);
  // A different address is unaffected by the first one's flood.
  assert.equal(burst("5.6.7.8", t), true);
  // And the turn of the minute resets everybody.
  assert.equal(burst("1.2.3.4", t + 60_000), true);
});

// ------------------------------------------------------------------- routing

const post = (body, headers = {}) => new Request(`${ORIGIN}/api/e`, {
  method: "POST",
  headers: { Origin: ORIGIN, "Content-Type": "application/json", ...headers },
  body: JSON.stringify(body),
});

test("the route answers 204 with no body, whatever happens", async () => {
  const { env, written } = withAE();
  const ok = await worker.fetch(post({ e: [["card", "expand", 0]] }), env, {});
  assert.equal(ok.status, 204);
  assert.equal(await ok.text(), "");
  assert.equal(written.length, 1);

  // Malformed, unknown and refused all look identical from outside. Nothing
  // to probe, and nothing that would teach a client to retry.
  for (const bad of [
    post({ e: [["nope", "x", 1]] }),
    post("not an object"),
    post({ e: [["card", "expand", 0]] }, { Origin: "https://evil.example" }),
    post({ e: [["card", "expand", 0]] }, { "Content-Type": "text/plain" }),
  ]) {
    const r = await worker.fetch(bad, env, {});
    assert.equal(r.status, 204);
    assert.equal(await r.text(), "");
  }
  assert.equal(written.length, 1, "only the good one was recorded");
});

test("a cross-origin beacon cannot write into our numbers", async () => {
  const { env, written } = withAE();
  const r = await worker.fetch(
    post({ e: [["whatif", "pick", 999]] }, { Origin: "https://evil.example" }),
    env, {});
  assert.equal(r.status, 204);
  assert.equal(written.length, 0);
});

test("the ingest never touches the session, signed in or not", async () => {
  // The browser sends the session cookie with the beacon because it is
  // same-origin and cannot be told otherwise. The guarantee is that nothing
  // looks at it — so a request carrying one must behave identically to one
  // that does not, and must not so much as read the KV cache.
  const { env, written } = withAE();
  let kvReads = 0;
  const kv = env.SESSIONS;
  env.SESSIONS = { get: (k) => { kvReads++; return kv.get(k); },
                   put: (...a) => kv.put(...a),
                   delete: (...a) => kv.delete(...a) };

  const r = await worker.fetch(
    post({ e: [["read", "100", 30]] }, { Cookie: "__Host-b12s=whatever" }),
    env, {});
  assert.equal(r.status, 204);
  assert.equal(kvReads, 0, "the ingest read a session");
  assert.equal(written.length, 1);
  // And nothing user-shaped reached the store.
  assert.ok(!JSON.stringify(written[0]).includes("whatever"));
});

test("GET is not the ingest", async () => {
  const { env } = withAE();
  const r = await worker.fetch(
    new Request(`${ORIGIN}/api/e`, { headers: { Origin: ORIGIN } }), env, {});
  assert.equal(r.status, 404);
});
