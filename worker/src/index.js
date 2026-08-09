// The pick'em API. The only server-side code on big12ology.com.
//
// Everything not under /api/ is somebody else's problem — the route in
// wrangler.toml sees to that, and if it ever does not, this file answers 404
// rather than guessing. There is no CORS here and its absence is a control,
// not an oversight: the API is same-origin only, and adding a permissive
// Access-Control-Allow-Origin would hand every cookie-authenticated endpoint
// to any page on the internet.

import * as api from "./api.js";
import * as oauth from "./oauth.js";
import * as ratelimit from "./ratelimit.js";
import * as session from "./session.js";
import {
  SESSION_COOKIE, STATE_COOKIE, clear, parseCookies, safeReturn, serialize,
} from "./cookies.js";
import { importWeek, currentWeek } from "./slate.js";
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
    // job instead, and only then honour return_to.
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
    return new Response(null,
      { status: 204, headers: { "Set-Cookie": clear(SESSION_COOKIE) } });
  }

  // ------------------------------------------------------ public, no session

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
      return api.deleteMe(env, user);
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
    return api.unlink(env, user, unlink[1]);
  }

  return fail("not_found", 404);
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
    try {
      const wk = await currentWeek(env, season);
      // The current week and the next, so a slate goes in as soon as it
      // exists rather than waiting for the week to turn over — plus any
      // earlier week the database has never seen.
      //
      // That backfill matters more than it looks. Without it a Worker
      // deployed in October knows only October: every earlier week is a
      // published file nobody ever read, the season history is a single
      // point, and the week-by-week chart has nothing to draw. It costs
      // nothing in the ordinary case, because weeks already present are
      // skipped from a query rather than re-fetched.
      const { results: have } = await env.DB.prepare(
        `SELECT week FROM weeks WHERE season = ?`).bind(season).all();
      const known = new Set((have || []).map((r) => r.week));
      const want = new Set([wk, (wk || 0) + 1].filter((n) => n && n > 0));
      for (let w = 1; w <= (wk || 0); w++) if (!known.has(w)) want.add(w);

      for (const w of [...want].sort((a, b) => a - b)) {
        const r = await importWeek(env, season, w);
        if (!r.ok) console.log(`import ${season} w${w}: ${r.reason}`);
        else if (!r.unchanged) console.log(`import ${season} w${w}: ${r.games} games`);
      }
      const report = await scoreAll(env, season);
      for (const r of report) {
        if (r.changed) console.log(`score w${r.week}: ${JSON.stringify(r)}`);
      }
    } catch (e) {
      console.error("cron", e && (e.stack || e.message || e));
    }
  },
};
