// A local front door: static files plus a real API, on ONE origin.
//
// WHY THIS EXISTS. `wrangler dev --assets` serves the built site and the Worker
// together, which is the obvious way to run this locally — but its asset layer
// does not pass the Origin header through to the Worker. csrfOk compares Origin
// to SITE_ORIGIN and refuses when they differ, so every write (a pick, a team, a
// display name) came back 403 bad_origin while every read worked. That is an
// artifact of the dev server, not of the site: the same PUT against
// `wrangler dev` WITHOUT --assets returns {"saved":true}.
//
// So the asset serving happens here and the Worker runs bare on its own port,
// where its headers arrive intact. Both halves answer on one origin, which is
// the only arrangement in which the browser sends an Origin the Worker accepts
// and a cookie the Worker can read.
//
// IT IS NEVER PUBLISHED. assemble.sh copies the built site out of tiebreaker/
// and attendance/; nothing under worker/ is an input to it, and
// tools/pickem-e2e.sh asserts that this file cannot reach dist. It is a test
// fixture that happens to be a server.
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, normalize, resolve as resolvePath } from "node:path";

const PORT = Number(process.env.PORT || 8799);
const API = process.env.API || "http://127.0.0.1:8798";
const ROOT = resolvePath(process.env.ROOT || "dist");

const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
  ".woff2": "font/woff2", ".xml": "application/xml", ".txt": "text/plain",
  ".csv": "text/csv",
};

/** The file a URL path means, or null. Tries exact, then .html, then index. */
async function resolveFile(pathname) {
  // normalize() collapses ".." before it is joined, so a request cannot climb
  // out of ROOT. Worth doing even in a fixture that only serves a build.
  const rel = normalize(decodeURIComponent(pathname))
    .replace(/^(\.\.[/\\])+/, "");
  for (const p of [rel, rel + ".html", join(rel, "index.html")]) {
    const full = join(ROOT, p);
    if (!full.startsWith(ROOT)) continue;
    try {
      const s = await stat(full);
      if (s.isFile()) return full;
    } catch { /* try the next shape */ }
  }
  return null;
}

createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);

  if (url.pathname === "/api" || url.pathname.startsWith("/api/")) {
    // Every header forwarded verbatim: Origin and Cookie are the whole point.
    const headers = { ...req.headers, host: new URL(API).host };
    const chunks = [];
    for await (const c of req) chunks.push(c);
    try {
      const upstream = await fetch(API + req.url, {
        method: req.method,
        headers,
        body: ["GET", "HEAD"].includes(req.method)
          ? undefined : Buffer.concat(chunks),
        redirect: "manual",
      });
      // getSetCookie() keeps multiple Set-Cookie headers separate. Folding them
      // into one comma-joined string is how a session cookie and a cleared
      // state cookie arrive as a single unparseable header.
      //
      // content-encoding and content-length are dropped, and that is not
      // tidying. fetch() has already decompressed the body, so forwarding
      // `content-encoding: gzip` alongside plain bytes makes the browser try to
      // gunzip JSON and fail, which surfaced as "Could not reach the pick'em"
      // while curl, which does not ask for gzip, worked perfectly. The length is
      // wrong for the same reason.
      const DROP = new Set(["set-cookie", "content-encoding", "content-length"]);
      const out = {};
      upstream.headers.forEach((v, k) => {
        if (!DROP.has(k.toLowerCase())) out[k] = v;
      });
      const cookies = upstream.headers.getSetCookie?.() ?? [];
      res.writeHead(upstream.status,
                    cookies.length ? { ...out, "set-cookie": cookies } : out);
      res.end(Buffer.from(await upstream.arrayBuffer()));
    } catch (e) {
      res.writeHead(502, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "dev_proxy_upstream", detail: String(e) }));
    }
    return;
  }

  const file = await resolveFile(url.pathname);
  if (!file) {
    res.writeHead(404, { "content-type": "text/plain" });
    res.end("not found");
    return;
  }
  res.writeHead(200, {
    "content-type": TYPES[extname(file)] || "application/octet-stream",
    // no-store, because the harness rewrites a slate between runs and a
    // conditional request served from cache is a test that passes on stale
    // bytes.
    "cache-control": "no-store",
  });
  res.end(await readFile(file));
}).listen(PORT, () => {
  console.log(`e2e proxy on http://localhost:${PORT}`);
  console.log(`  static  <- ${ROOT}`);
  console.log(`  /api/*  -> ${API}`);
});
