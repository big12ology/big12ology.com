// Mint a session and two players, without a sign-in.
//
// WHY NOT DRIVE THE REAL OAUTH FLOW. It ends at Google and GitHub, with
// somebody's actual credentials, and no test should be typing those. The
// consent screen is also not automatable in any way that stays working.
//
// What makes this sound rather than a shortcut: a session IS a random token
// whose SHA-256 lives in sessions.sid_hash. The Worker never saw the raw value
// when it minted one either — it hashes whatever arrives in the cookie and
// looks the hash up — so a row written here is indistinguishable from one
// written by a real sign-in. session.read() tries KV first and falls through to
// D1 on a miss, which is why seeding D1 alone is enough.
//
// WHAT THIS THEREFORE DOES NOT COVER, stated so nobody reads a green run as
// more than it is: the OAuth callback, identity linking and unlinking, and
// logout. Those are worker/test/oauth.e2e.test.js's job and stay there.
//
// Writes two files into the run directory: seed.sql for wrangler to apply, and
// cookie.txt for the checks to send.
import { webcrypto as crypto } from "node:crypto";
import { writeFileSync } from "node:fs";
import { join } from "node:path";

const dir = process.argv[2];
if (!dir) {
  console.error("usage: seed.mjs <run-dir>");
  process.exit(2);
}

const b64url = (buf) => Buffer.from(buf).toString("base64")
  .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

const raw = b64url(crypto.getRandomValues(new Uint8Array(32)));
const hash = b64url(await crypto.subtle.digest(
  "SHA-256", new TextEncoder().encode(raw)));

// Crockford base32, the alphabet the Worker's ULIDs use.
const A = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const ulid = () => Array.from(crypto.getRandomValues(new Uint8Array(26)))
  .map((b) => A[b % 32]).join("");

const now = Math.floor(Date.now() / 1000);
// Two players, because one cannot show that the board ranks anybody, and
// 'active' rather than 'provisional' because a provisional account is kept off
// the leaderboard by design and the board is what the last checks read.
const me = { id: ulid(), name: "E2E Player" };
const other = { id: ulid(), name: "E2E Rival" };

const user = (u) =>
  `INSERT INTO users (id, display_name, display_norm, status, created_at)
   VALUES ('${u.id}', '${u.name}', '${u.name.toLowerCase().replace(/[^a-z0-9]/g, "")}',
           'active', ${now});`;

writeFileSync(join(dir, "seed.sql"), [
  user(me),
  user(other),
  `INSERT INTO sessions (sid_hash, user_id, created_at, expires_at)
   VALUES ('${hash}', '${me.id}', ${now}, ${now + 30 * 86400});`,
].join("\n") + "\n");

writeFileSync(join(dir, "cookie.txt"), raw);
writeFileSync(join(dir, "users.json"),
              JSON.stringify({ me, other }, null, 1));

console.log(`seeded ${me.name} (${me.id}) and ${other.name}`);
