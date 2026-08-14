# The pick'em Worker

The only server-side code on big12ology.com. Everything else is static files on
GitHub Pages; this answers `/api/*` and nothing else.

`wrangler.toml` points here twice for where the secrets live, so that is what
this is mostly about.

## Secrets — never in wrangler.toml

`wrangler.toml` is committed. It carries **public identifiers only**: OAuth
client *IDs*, the site origins, the survivor entry cutoff, the deploy crons.
Everything below is a secret and lives in `wrangler secret put`, which stores it
encrypted at Cloudflare and never writes it to disk here.

```bash
cd worker
npx wrangler secret put IDENTITY_PEPPER      # then paste the value at the prompt
```

| Secret | What it is | Consequence of losing it |
|---|---|---|
| `IDENTITY_PEPPER` | Hashes the provider account id before it is stored, so the database never holds a raw Google/GitHub subject | **Rotating it orphans every account.** The hash is the only link between a person and their picks. There is no migration; it is not a key you cycle. |
| `STATE_SIGNING_KEY` | Signs the OAuth `state` cookie | Rotating it invalidates sign-ins in flight. Harmless, they retry. |
| `SCORES_INGEST_KEY` | Shared with `tools/publish-scores.sh`, authenticates `POST /api/ingest/scores` | Change it in both places in the same breath, or scores stop arriving and the boards silently stop moving. Also a repo secret: `SCORES_INGEST_KEY` in Actions. |
| `GOOGLE_CLIENT_SECRET` | Google OAuth | Sign-in with Google 500s. |
| `GITHUB_CLIENT_SECRET` | GitHub OAuth | Sign-in with GitHub 500s. |
| `MICROSOFT_CLIENT_SECRET` | Microsoft OAuth — **wired and dark** | Provider stays hidden. See below. |
| `AMAZON_CLIENT_SECRET` | Amazon OAuth — **wired and dark** | Same. |
| `GITHUB_DISPATCH_TOKEN` | Fine-grained PAT with Actions: write, used by the cron to trigger `pages.yml` | The Worker's deploy trigger stops firing; `pages.yml`'s own crons still deploy. |
| `HEARTBEAT_URL` | Optional. Pinged by the daily cron | Nothing breaks; the external monitor stops hearing from us. |

Locally, the same values go in `worker/.dev.vars`, which is gitignored and
blocked by `tools/hooks/pre-commit`. It is plaintext — treat it like the
secrets it holds.

**Two providers are wired and dark.** `src/oauth.js` knows how to talk to
Microsoft and Amazon; `enabled(env)` offers a provider only when its credentials
are actually present, so an unconfigured one is a 404 at
`/api/auth/login/<name>` and its button is removed from the account page by
`/api/auth/providers`. Neither is a placeholder waiting to be filled in — both
code paths are tested. To turn one on, add its `*_CLIENT_ID` to `wrangler.toml`
and put the secret in. No deploy is required for the button to appear.

## The route is the blast radius

Read the comment at the top of `wrangler.toml` before touching `routes`. The
short version: the apex is A records pointing at GitHub Pages, orange-clouded,
and this Worker is evaluated at the edge before the origin fetch. Two ways to
take the whole domain down from that file — a route of `big12ology.com/*`, or a
Workers Custom Domain on the apex, which *replaces* the A records. The deploy
workflow asserts the exact route strings before it runs.

## Storage

| Binding | Kind | Holds | Backed up |
|---|---|---|---|
| `DB` | D1 `b12_pickem` | accounts, identities, sessions, picks, results, scores, leaderboards, survivor | **Yes** — `backup.yml`, weekly, encrypted |
| `SESSIONS` | KV | session cache, its own namespace on purpose | No: rebuildable, and expiring them only signs people out |
| `SCORES` | KV | one key per season, written by `/api/ingest/scores` | No: derived from the site's own data |
| `EVENTS` | Analytics Engine | write-only, sampled, 90-day expiry, no joinable key | No |

Only `DB` holds anything a person would miss, and only two tables of it are
irreplaceable: who signed up and what they picked. Everything else replays.

## Working on it

```bash
cd worker
npm ci
npm test                    # node --test, 287 tests, no network
npx wrangler dev            # local, against a local D1
npm run migrate:local       # apply migrations to the local D1
```

Deploying is `worker.yml` on a push to `worker/**`, not a manual `wrangler
deploy`. It runs the tests, asserts the route, refuses to ship if any migration
on disk is unapplied to the remote D1, deploys, smoke-tests `/api/health`, and
rolls back automatically if the smoke test fails.

Migrations are `migrate.yml`, manual only, and require typing the database name
to confirm. A bad script is a minute of downtime because the deploy rolls back;
a bad migration is forward-only.
