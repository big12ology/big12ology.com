// tools/publish-scores.sh against the real handler.
//
// ingest.test.js proves the Worker accepts a signature made by crypto.js. That
// is only half the seam: in production the signature is made by openssl in a
// shell script, and the two implementations have to agree about base64url,
// about where the timestamp goes, and — the one that actually bites — about
// the exact bytes of the file. `--data` instead of `--data-binary` strips a
// trailing newline, which changes the body after it was signed and produces a
// bad_signature that reads like a wrong key.
//
// So this runs the script itself, with curl and openssl, against a socket that
// hands the request to worker.fetch. No mock of the publisher and no mock of
// the Worker: the only thing standing in for production is the transport.

import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { execFile } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import worker from "../src/index.js";
import { scoresKey } from "../src/scoring.js";
import { forceLock, makeEnv, seedPick, seedUser, seedWeek, HOUR, NOW }
  from "./helpers/env.js";

const SEASON = 2026;
const KEY = "test-ingest-key-not-the-production-one";
const SCRIPT = resolve(fileURLToPath(new URL("../../tools/publish-scores.sh",
                                             import.meta.url)));

function kv() {
  const map = new Map();
  return {
    map,
    async get(k, type) {
      if (!map.has(k)) return null;
      const v = map.get(k);
      if (type !== "json") return v;
      try { return JSON.parse(v); } catch { return null; }
    },
    async put(k, v) { map.set(k, v); },
    async delete(k) { map.delete(k); },
  };
}

/** A socket that speaks HTTP on one side and worker.fetch on the other. */
async function serve(env) {
  const seen = [];
  const server = createServer((req, res) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", async () => {
      const body = Buffer.concat(chunks);
      seen.push(body);
      const r = await worker.fetch(new Request(
        `https://big12ology.com${req.url}`,
        { method: req.method, headers: req.headers, body },
      ), env, {});
      res.writeHead(r.status, { "Content-Type": "application/json" });
      res.end(await r.text());
    });
  });
  await new Promise((ok) => server.listen(0, "127.0.0.1", ok));
  return { server, seen, base: `http://127.0.0.1:${server.address().port}` };
}

function run(script, args, env) {
  return new Promise((ok) => {
    execFile("bash", [script, ...args], { env: { ...process.env, ...env } },
             (err, stdout, stderr) => ok({ code: err ? err.code || 1 : 0,
                                           stdout, stderr }));
  });
}

function scoresFile(doc) {
  const dir = mkdtempSync(join(tmpdir(), "b12-scores-"));
  const p = join(dir, "pickem-scores.json");
  // With a trailing newline, the way a file written by a build actually looks.
  // This is the byte that --data would eat.
  writeFileSync(p, JSON.stringify(doc) + "\n");
  return p;
}

test("the publish script signs what the Worker verifies, end to end", async (t) => {
  const env = makeEnv({ SCORES: kv(), SCORES_INGEST_KEY: KEY });
  seedWeek(env, { season: SEASON, week: 3 });
  seedUser(env, "U1", { name: "Reader" });
  seedPick(env, "U1", SEASON, 3, 401, "home", -13);
  forceLock(env, SEASON, 3, NOW() - HOUR);

  const { server, base } = await serve(env);
  t.after(() => server.close());

  const file = scoresFile({ season: SEASON, games: { 401: [21, 17, true] } });
  const r = await run(SCRIPT, [file],
                      { SCORES_INGEST_KEY: KEY, B12_API_BASE: base });

  assert.equal(r.code, 0, `script failed: ${r.stderr}`);
  assert.match(r.stdout, /publishing 1 games for 2026/);
  assert.match(r.stdout, /HTTP 200/);

  // Stored, and stored as the bytes that were signed — newline included.
  const stored = await env.SCORES.get(scoresKey(SEASON));
  assert.ok(stored.endsWith("\n"), "the trailing newline was not preserved");

  // And graded, which is the point of doing it in one request.
  const row = env.raw.prepare(
    `SELECT status, home_points FROM results
      WHERE season = ? AND week = ? AND game_id = 401`).get(SEASON, 3);
  assert.equal(row.status, "final");
  assert.equal(row.home_points, 21);
});

test("a key mismatch fails loudly rather than publishing nothing quietly",
     async (t) => {
  const env = makeEnv({ SCORES: kv(), SCORES_INGEST_KEY: KEY });
  const { server, base } = await serve(env);
  t.after(() => server.close());

  const file = scoresFile({ season: SEASON, games: {} });
  const r = await run(SCRIPT, [file],
                      { SCORES_INGEST_KEY: "the-wrong-key", B12_API_BASE: base });

  assert.equal(r.code, 1);
  assert.match(r.stdout, /HTTP 403/);
  assert.match(r.stderr, /signature refused/);
  assert.equal(env.SCORES.map.size, 0);
});

test("a malformed file is caught here, before anything is signed", async (t) => {
  const env = makeEnv({ SCORES: kv(), SCORES_INGEST_KEY: KEY });
  const { server, seen, base } = await serve(env);
  t.after(() => server.close());

  // `games` as a list. The Worker would refuse it too, but the point is that
  // this never becomes a request at all.
  const file = scoresFile({ season: SEASON, games: [] });
  const r = await run(SCRIPT, [file],
                      { SCORES_INGEST_KEY: KEY, B12_API_BASE: base });

  assert.equal(r.code, 1);
  assert.match(r.stderr, /no games object/);
  assert.equal(seen.length, 0, "it posted anyway");
});

test("no key set means nothing is sent", async () => {
  const file = scoresFile({ season: SEASON, games: {} });
  const r = await run(SCRIPT, [file], { SCORES_INGEST_KEY: "" });
  assert.equal(r.code, 1);
  assert.match(r.stderr, /SCORES_INGEST_KEY is not set/);
});
