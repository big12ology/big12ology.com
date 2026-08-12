// The pick'em API. The only server-side code on big12ology.com.
//
// Everything not under /api/ is somebody else's problem — the route in
// wrangler.toml sees to that, and if it ever does not, this file answers 404
// rather than guessing. There is no CORS here and its absence is a control,
// not an oversight: the API is same-origin only, and adding a permissive
// Access-Control-Allow-Origin would hand every cookie-authenticated endpoint
// to any page on the internet.

import * as api from "./api.js";
import * as events from "./events.js";
import * as ingest from "./ingest.js";
import * as oauth from "./oauth.js";
import * as ratelimit from "./ratelimit.js";
import * as session from "./session.js";
import {
  SESSION_COOKIE, STATE_COOKIE, clear, parseCookies, safeReturn, serialize,
} from "./cookies.js";
import { importWeek } from "./slate.js";
import { scoreAll } from "./scoring.js";

const { json, fail } = api;

/**
 * CSRF, by three checks that must all pass on any state-changing request.
 *
 * No token, no hidden field, no second cookie. A cross-site form post cannot
 * set Content-Type: application/json without preflight, and a preflight we
 * never answer stops it; Origin is sent on every cross-origin request a
 * browser makes; and the session cookie is SameSite=Lax, which excludes
 * cross-site POST outright. Any one of these would mostly do. Together they
 * are cheap and there is nothing to keep in sync.
 */
function csrfOk(req, env) {
  const origin = req.headers.get("Origin");
  if (origin !== env.SITE_ORIGIN) return false;
  const ct = (req.headers.get("Content-Type") || "").split(";")[0].trim();
  return ct === "application/json";
}

async function body(req) {
  try { return await req.json(); } catch { return null; }
}

/**
 * Tell the outside world this run finished.
 *
 * A dead man's switch, which is the only honest way to monitor something that
 * runs unattended: everything else infers liveness from a symptom, and a cron
 * that has silently stopped produces no symptom at all until the boards are
 * visibly stale. Here the absence of a call IS the alarm, so it survives the
 * Worker being broken, the account being suspended, or the schedule being
 * deleted — none of which could send an error report.
 *
 * Fire and forget, and swallowed whole. Scoring must never fail because a
 * monitoring host is down; that would be the tail wagging the dog.
 */
async function heartbeat(env, msg) {
  if (!env.HEARTBEAT_URL) return;
  try {
    const u = new URL(env.HEARTBEAT_URL);
    // Named for the job rather than the service. This points at
    // Healthchecks.io because the Uptime Kuma instance that watches
    // everything else is bound to 127.0.0.1 on its own box and is not
    // reachable from Cloudflare's edge — a switch has to be pingable by the
    // thing it is watching, and that ruled Kuma out for this one job.
    //
    // Both spellings are harmless: Healthchecks.io ignores the query string,
    // Kuma reads it, so the same call works if this ever moves.
    u.searchParams.set("status", "up");
    u.searchParams.set("msg", msg);
    await fetch(u.toString(), { method: "GET", cf: { cacheTtl: 0 } });
  } catch (e) {
    console.log(`heartbeat failed: ${e && (e.message || e)}`);
  }
}

function clientIp(req) {
  return req.headers.get("CF-Connecting-IP") || null;
}

function redirect(to, headers = {}) {
  return new Response(null, { status: 302, headers: { Location: to, ...headers } });
}

/**
 * Sign-in failures come back to a page, not to a JSON blob — the reader is
 * mid-navigation in a browser tab. The reason rides in the query string so
 * the account page can say something specific.
 */
function authFail(env, reason, returnTo = "/pools/account.html") {
  return redirect(`${returnTo}?auth_error=${encodeURIComponent(reason)}`,
                  { "Set-Cookie": clear(STATE_COOKIE) });
}

async function handle(req, env, ctx) {
  const url = new URL(req.url);
  const path = url.pathname.replace(/\/+$/, "") || "/api";

  // Bare /api has its own route in wrangler.toml, because it does not match
  // /api/* and would otherwise fall through to Pages and 404 as HTML.
  if (path === "/api") return fail("not_found", 404);
  if (!path.startsWith("/api/")) return fail("not_found", 404);

  const cookies = parseCookies(req.headers.get("Cookie"));
  const rawSession = cookies[SESSION_COOKIE];

  // ------------------------------------------------------------ auth flow

  const login = path.match(/^\/api\/auth\/login\/(\w+)$/);
  if (login && req.method === "GET") {
    const provider = login[1];
    if (!oauth.PROVIDERS.includes(provider)) return fail("unknown_provider", 404);
    const rl = await ratelimit.take(env, "login", clientIp(req) || "anon");
    if (!rl.ok) return fail("rate_limited", 429, { retry_after: rl.retryAfter });

    const { url: dest, cookie } = await oauth.begin(
      env, provider, url.searchParams.get("return_to"));
    return redirect(dest, {
      "Set-Cookie": serialize(STATE_COOKIE, cookie, { maxAge: oauth.STATE_TTL }),
    });
  }

  // Linking a second provider to an account that already exists. Same dance,
  // different landing: `mode` rides in the signed state cookie so the callback
  // knows which one it is finishing.
  const link = path.match(/^\/api\/auth\/link\/(\w+)$/);
  if (link && req.method === "GET") {
    const user = await session.read(env, rawSession);
    if (!user) return authFail(env, "unauthenticated");
    if (!oauth.PROVIDERS.includes(link[1])) return fail("unknown_provider", 404);
    const { url: dest, cookie } = await oauth.begin(
      env, link[1], "/pools/account.html", "link");
    return redirect(dest, {
      "Set-Cookie": serialize(STATE_COOKIE, cookie, { maxAge: oauth.STATE_TTL }),
    });
  }

  const cb = path.match(/^\/api\/auth\/callback\/(\w+)$/);
  if (cb && req.method === "GET") {
    const provider = cb[1];
    if (!oauth.PROVIDERS.includes(provider)) return fail("unknown_provider", 404);

    // The provider's own refusal — the reader pressed cancel, usually.
    if (url.searchParams.get("error")) {
      return authFail(env, url.searchParams.get("error"));
    }
    const state = await oauth.readState(env, cookies[STATE_COOKIE], provider,
                                        url.searchParams.get("state"));
    if (!state) return authFail(env, "bad_state");
    const code = url.searchParams.get("code");
    if (!code) return authFail(env, "no_code");

    let subject;
    try {
      subject = await oauth.finish(env, provider, code, state);
    } catch (e) {
      return authFail(env, "exchange_failed");
    }

    const linking = state.mode === "link";
    let linkTo = null;
    if (linking) {
      const me = await session.read(env, rawSession);
      if (!me) return authFail(env, "unauthenticated");
      linkTo = me.userId;
    }

    const res = await api.resolveIdentity(env, provider, subject,
                                          { linkTo, ip: clientIp(req) });
    if (res.error) return authFail(env, res.error);
    if (linking) {
      return redirect(safeReturn(state.return_to),
                      { "Set-Cookie": clear(STATE_COOKIE) });
    }

    if (res.created) {
      const rl = await ratelimit.take(env, "signup", clientIp(req) || "anon");
      // Never an automatic block: campus and dorm NAT make a shared address
      // the normal case on a college football site, so this is flagged for a
      // person to look at and the signup proceeds.
      if (!rl.ok) {
        await env.DB.prepare(
          `INSERT INTO audit_log (at, actor, action, subject, detail)
           VALUES (?, ?, 'signup_burst', ?, ?)`)
          .bind(Math.floor(Date.now() / 1000), res.userId, res.userId,
                provider).run();
      }
    }

    const s = await session.create(env, res.userId, {
      ua: null, ip: clientIp(req),
    });

    // Where to land. An account with no display name cannot pick and cannot
    // appear on the board, so returning it to the slate drops somebody who
    // just signed up onto a page that looks identical to the one they left —
    // which is exactly what it did. Send them to the page that finishes the
    // job instead, and only then honor return_to.
    const named = await env.DB.prepare(
      `SELECT display_name FROM users WHERE id = ?`).bind(res.userId).first();
    const dest = (named && named.display_name)
      ? safeReturn(state.return_to)
      : `/pools/account.html?welcome=1&next=${
          encodeURIComponent(safeReturn(state.return_to))}`;

    const headers = new Headers();
    headers.append("Set-Cookie", serialize(SESSION_COOKIE, s.raw,
                                           { maxAge: session.TTL }));
    headers.append("Set-Cookie", clear(STATE_COOKIE));
    headers.set("Location", dest);
    return new Response(null, { status: 302, headers });
  }

  if (path === "/api/auth/logout" && req.method === "POST") {
    if (!csrfOk(req, env)) return fail("bad_origin", 403);
    // Strict: a sign-out must not be satisfied by a KV entry that a previous
    // revocation has not finished propagating away from.
    const user = await session.read(env, rawSession, { strict: true });
    if (user) await session.revoke(env, user.hash);
    // And drop the cache entry for the token presented even when the strict
    // read found nothing. Signing out twice inside the propagation window
    // used to be a no-op that left this edge's copy behind, so the second
    // press of the button reported success and changed nothing here.
    else if (rawSession) await session.forget(env, rawSession);
    return new Response(null,
      { status: 204, headers: { "Set-Cookie": clear(SESSION_COOKIE) } });
  }

  // ------------------------------------------------------ public, no session

  // Counted, not identified. This route sits above every line that reads a
  // session on purpose: the browser sends the session cookie with the beacon
  // because it is same-origin and cannot be told otherwise, and the answer to
  // that is that nothing here ever looks at it. See worker/src/events.js.
  //
  // The same CSRF check as every other write, for a different reason than the
  // others. There is nothing to forge into — the endpoint has no side effect
  // worth causing — but the Origin check is what keeps somebody else's page
  // from posting counts into our numbers, and a measurement anybody can write
  // to is not a measurement.
  if (path === "/api/e" && req.method === "POST") {
    if (!csrfOk(req, env)) return new Response(null, { status: 204 });
    if (events.burst(clientIp(req))) events.record(env, req, await body(req));
    return new Response(null, { status: 204 });
  }

  // The publisher's channel, and the only route on this Worker that is not
  // driven by a browser.
  //
  // NO csrfOk HERE, and its absence is deliberate rather than forgotten. That
  // check requires an Origin header equal to SITE_ORIGIN, which is exactly
  // what a browser sends and exactly what a GitHub Actions runner does not.
  // The protection is the signature in ingest.js, which is strictly stronger:
  // it authenticates the bytes rather than the tab they came from.
  //
  // Grades in the same request, from the body it just verified. That is the
  // point of the whole exercise — a final used to wait for the next :30 sweep
  // to be noticed, and now the board moves while the publisher is still
  // watching its own workflow log.
  if (path === "/api/ingest/scores" && req.method === "POST") {
    const got = await ingest.receiveScores(req, env);
    if (!got.ok) return fail(got.error, got.status);
    // Never let grading failure lose the scores. They are in KV by now, so the
    // next cron will grade them; reporting a 500 to the publisher when the
    // publish itself succeeded would send somebody looking in the wrong place.
    let report = [], graded = null;
    try {
      report = await scoreAll(env, got.season, undefined, { scores: got.scores });
    } catch (e) {
      graded = `failed: ${e && (e.message || e)}`;
      console.error("ingest grade", e && (e.stack || e.message || e));
    }
    const moved = report.filter((r) => r.changed).length;
    if (moved) console.log(`ingest: ${got.games} games, ${moved} weeks moved`);
    return json({
      ok: true, season: got.season, games: got.games,
      weeks_changed: moved, graded: graded || "ok",
    });
  }

  if (path === "/api/health") return api.getHealth(env);
  if (path === "/api/season/current") return api.getSeasonCurrent(env);
  if (path === "/api/history" && req.method === "GET") {
    // Reads a session when there is one — the "you" line — but never needs
    // one, so it sits with the public routes and resolves the user itself.
    return api.getHistory(env, await session.read(env, rawSession));
  }
  if (path === "/api/slate" && req.method === "GET") return api.getSlate(env, url);
  if (path === "/api/leaderboard" && req.method === "GET") {
    return api.getBoard(env, url);
  }
  if (path === "/api/consensus" && req.method === "GET") {
    return api.getConsensus(env, url);
  }
  if (path === "/api/survivor/board" && req.method === "GET") {
    return api.getSurvivorBoard(env);
  }
  const upicks = path.match(/^\/api\/users\/([A-Za-z0-9]+)\/picks$/);
  if (upicks && req.method === "GET") {
    return api.getUserPicks(env, url, upicks[1]);
  }

  // ------------------------------------------------------------ needs a user

  const user = await session.read(env, rawSession);

  // Re-issue only when the session is close to expiring. A sliding expiry that
  // wrote on every request would spend the KV free tier's daily budget in an
  // afternoon.
  let refresh = null;
  if (user && user.expiresAt && session.stale(user.expiresAt)) {
    await session.extend(env, user.hash);
    refresh = serialize(SESSION_COOKIE, rawSession, { maxAge: session.TTL });
  }

  const withRefresh = (res) => {
    if (refresh) res.headers.append("Set-Cookie", refresh);
    return res;
  };

  /**
   * The session again, asked of D1 rather than the cache.
   *
   * KV answers the ordinary request because every authenticated request makes
   * one and D1 would be a query on each; that is the whole reason the cache
   * is there. But it is eventually consistent, so a session revoked at one
   * edge is still served at another for up to about a minute, and session.js
   * says in as many words that the cached answer is not to be trusted for
   * anything destructive.
   *
   * It was, though. Deleting an account and unlinking an identity both ran on
   * the cached read, so signing out on a shared machine left a window in
   * which that same cookie could still end the account — and with no email on
   * file there is no way back from that.
   */
  const confirmed = async () => session.read(env, rawSession, { strict: true });

  if (path === "/api/me") {
    if (req.method === "GET") return withRefresh(await api.getMe(env, user));
    if (req.method === "PATCH") {
      if (!csrfOk(req, env)) return fail("bad_origin", 403);
      const b = await body(req);
      if (!b) return fail("bad_json", 400);
      return withRefresh(await api.patchMe(env, user, b));
    }
    if (req.method === "DELETE") {
      if (!csrfOk(req, env)) return fail("bad_origin", 403);
      return api.deleteMe(env, await confirmed());
    }
    return fail("method_not_allowed", 405);
  }

  if (path === "/api/picks") {
    if (req.method === "GET") return withRefresh(await api.getPicks(env, user, url));
    if (req.method === "PUT") {
      if (!csrfOk(req, env)) return fail("bad_origin", 403);
      const b = await body(req);
      if (!b) return fail("bad_json", 400);
      return withRefresh(await api.putPicks(env, user, b));
    }
    return fail("method_not_allowed", 405);
  }

  if (path === "/api/survivor") {
    if (req.method === "GET") {
      return withRefresh(await api.getSurvivor(env, user, url));
    }
    return fail("method_not_allowed", 405);
  }
  if (path === "/api/survivor/pick") {
    if (req.method === "PUT") {
      if (!csrfOk(req, env)) return fail("bad_origin", 403);
      const b = await body(req);
      if (!b) return fail("bad_json", 400);
      return withRefresh(await api.putSurvivorPick(env, user, b));
    }
    return fail("method_not_allowed", 405);
  }

  const unlink = path.match(/^\/api\/auth\/identities\/(\w+)$/);
  if (unlink && req.method === "DELETE") {
    if (!csrfOk(req, env)) return fail("bad_origin", 403);
    return api.unlink(env, await confirmed(), unlink[1]);
  }

  return fail("not_found", 404);
}

/**
 * Ask GitHub to build and deploy the site.
 *
 * THE CLOCK PROBLEM, AND WHY THIS EXISTS. GitHub's own scheduled workflows
 * are best-effort, and measured on this repo they are worse than that phrase
 * suggests: a median 19 minutes late, 130 at the 90th percentile, 210 at
 * worst, and one slot in ten never fires at all. Cloudflare fires this
 * Worker on a real schedule, so the punctual thing triggers the unpunctual
 * one and GitHub stays what it is good at — building.
 *
 * BELT AND BRACES. pages.yml keeps its own crons. If this token expires, or
 * the Worker breaks, or Cloudflare has a bad morning, the site still updates
 * on GitHub's sloppy schedule instead of not at all. A duplicate build costs
 * nothing now that build.py asks the provider nothing unless a game has
 * finished, which is what makes redundant triggers affordable.
 *
 * Fire and forget, and never allowed to break scoring: a deploy that did not
 * get triggered is a stale page, and throwing here would also skip the
 * grading below it, which is worse.
 */
async function triggerDeploy(env) {
  if (!env.GITHUB_DISPATCH_TOKEN || !env.GITHUB_REPO) return "not configured";
  try {
    const r = await fetch(
      `https://api.github.com/repos/${env.GITHUB_REPO}` +
      `/actions/workflows/pages.yml/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          // GitHub rejects an API call with no User-Agent.
          "User-Agent": "big12ology-worker",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: "main", inputs: { deploy: "true" } }),
      });
    return r.status === 204 ? "dispatched" : `github ${r.status}`;
  } catch (e) {
    return `failed: ${e && (e.message || e)}`;
  }
}


export default {
  async fetch(req, env, ctx) {
    try {
      return await handle(req, env, ctx);
    } catch (e) {
      // Never leak a stack to the client. The message goes to the tail log,
      // which is what observability in wrangler.toml is switched on for.
      console.error("unhandled", e && (e.stack || e.message || e));
      return fail("server_error", 500);
    }
  },

  /**
   * The cron. Two jobs on one schedule, in order.
   *
   * Importing before scoring matters: a week that has just been published
   * needs its rows before anything can be graded against them, and the Tuesday
   * trigger exists precisely to run an hour after pages.yml publishes.
   */
  async scheduled(event, env, ctx) {
    const season = Number(env.SEASON || new Date().getUTCFullYear());

    // WHAT TIME CLOUDFLARE ACTUALLY THINKS IT IS. event.scheduledTime is the
    // slot this run was meant to occupy; Date.now() is when it started. The
    // difference is this platform's punctuality, and writing it down is the
    // only way to know whether moving the clock here was worth doing —
    // GitHub's drift was measurable from its run history, and this has no
    // equivalent until something records it.
    const drift = Math.round((Date.now() - event.scheduledTime) / 1000);
    console.log(`cron ${event.cron} fired ${drift}s after its slot`);

    // The deploy trigger runs on its own schedule and does not wait for the
    // scoring below it: publishing scores and grading them are two jobs, and
    // the grading sweep deliberately runs half an hour behind the publish.
    if (env.DEPLOY_CRONS && env.DEPLOY_CRONS.split("|").includes(event.cron)) {
      console.log(`deploy trigger: ${await triggerDeploy(env)}`);
    }

    try {
      // Walk forward from week one until the publisher runs out.
      //
      // NOT from currentWeek(). That reads the weeks table, which on a cold
      // start is empty — so the first run imported week one and only week
      // one, and each hourly run after it advanced the frontier by exactly
      // one. A Worker deployed in week ten took ten hours to learn the
      // season, and served week one's slate the whole time. The local dev
      // script has been running this cron twice in a row for weeks with a
      // comment about "the first run has no weeks yet", which was the bug
      // wearing a workaround.
      //
      // The publisher only ever writes the current week and, once its lines
      // land, the next — so this walks 1..current+1 and stops. Cheap, because
      // importWeek hashes the file and returns `unchanged` without writing;
      // the cost of a quiet Saturday is one conditional fetch per week.
      //
      // One gap is tolerated before giving up: a week nobody published (a bye,
      // a season that skips a number) must not hide everything after it.
      //
      // FROM ZERO, not one. College football has a week 0 — 2026 opens with a
      // single game on August 29, nine days before Labor Day — and the
      // publisher numbers weeks from the same Tuesday-to-Monday rule the
      // attendance section uses, so it writes week-00.json for it. A walk
      // starting at one would never fetch that file, and the season's first
      // pick'em week would simply not exist: no slate, no picks, nothing to
      // say anything was missing.
      const MAX_WEEK = 25;
      let misses = 0, imported = 0;
      for (let w = 0; w <= MAX_WEEK && misses < 2; w++) {
        const r = await importWeek(env, season, w);
        if (!r.ok) {
          misses++;
          // A 404 past the frontier is the ordinary end of the walk and not
          // worth a line every hour. Anything else is.
          if (!/^fetch_404$/.test(r.reason)) {
            console.log(`import ${season} w${w}: ${r.reason}`);
          }
          continue;
        }
        misses = 0;
        imported++;
        if (!r.unchanged) console.log(`import ${season} w${w}: ${r.games} games`);
      }
      if (!imported) console.log(`import ${season}: nothing published yet`);
      const report = await scoreAll(env, season);
      for (const r of report) {
        if (r.changed) console.log(`score w${r.week}: ${JSON.stringify(r)}`);
      }
      const moved = report.filter((r) => r.changed).length;
      await heartbeat(env, `${imported} weeks, ${moved} changed`);
    } catch (e) {
      console.error("cron", e && (e.stack || e.message || e));
      // Deliberately no heartbeat here. Silence is the signal: a monitor that
      // is told about the failure and one that simply stops being told are
      // the same alert, and the second needs nothing to be working.
    }
  },
};
