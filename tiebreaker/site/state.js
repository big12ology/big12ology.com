// Where the site keeps what you left behind.
//
// Three features now persist something, and before this each one invented its
// own key, its own JSON handling and its own try/catch. A fourth would have
// been a fourth copy. This owns the boring parts — namespacing, a version
// stamp, and the fact that localStorage throws in private mode and when a
// quota is full — so a feature declares what it keeps rather than reinventing
// how to keep it.
//
// ONE KEY PER NAMESPACE, not one blob for the site. Two reasons, and the
// second is the real one: a blob means every write rewrites every feature's
// state, so a bug in one can eat another; and the privacy page names the keys
// it stores, which is only a promise worth making if the names mean something.
//
// THEME IS NOT IN HERE, on purpose. `b12-theme` is a bare string read by an
// inline script in every page's <head>, before any file loads, because a
// theme applied after first paint is a visible flash of the wrong one. Wrap
// it in a versioned envelope and that inline script has to parse JSON, and
// everyone's existing choice is orphaned. It stays a scalar; this comment is
// the documentation that it was considered rather than missed.
(function () {
  "use strict";

  var PREFIX = "b12-";
  var VERSION = 1;

  function keyFor(name) { return PREFIX + name; }

  /**
   * Read a namespace. Anything written by a future version is DISCARDED
   * rather than guessed at — a shape we do not understand is not data we can
   * use, and silently half-reading it is how a preference turns into a bug
   * nobody can reproduce.
   */
  function get(name, fallback) {
    try {
      var raw = localStorage.getItem(keyFor(name));
      if (!raw) return fallback;
      var box = JSON.parse(raw);
      if (!box || box.v !== VERSION) return fallback;
      return box.d === undefined ? fallback : box.d;
    } catch (e) {
      return fallback;
    }
  }

  /**
   * Write a namespace, or remove it when the value is the default. Storing
   * emptiness leaves a row behind for every page a reader ever visited.
   */
  function set(name, value) {
    try {
      if (value === undefined || value === null ||
          (Array.isArray(value) && !value.length) ||
          (typeof value === "object" && !Array.isArray(value) &&
           !Object.keys(value).length)) {
        localStorage.removeItem(keyFor(name));
        return true;
      }
      localStorage.setItem(keyFor(name),
        JSON.stringify({ v: VERSION, d: value }));
      return true;
    } catch (e) {
      // Private mode, a full quota, storage disabled by policy. A preference
      // that cannot be saved is not a reason to break the page it belongs to.
      return false;
    }
  }

  /** Per-page state, for things that mean something only on one URL. */
  function getPage(name, fallback) {
    var all = get(name, null);
    if (!all || typeof all !== "object") return fallback;
    var mine = all[location.pathname];
    return mine === undefined ? fallback : mine;
  }

  function setPage(name, value) {
    var all = get(name, null);
    if (!all || typeof all !== "object") all = {};
    var empty = value === undefined || value === null ||
      (Array.isArray(value) && !value.length);
    if (empty) delete all[location.pathname];
    else all[location.pathname] = value;
    return set(name, all);
  }

  // ---------------------------------------------------------------- the URL
  //
  // Anything worth sending to somebody else belongs here rather than in
  // storage. A what-if season is the case that matters: "here is how BYU gets
  // in" is the most shareable thing on this site, and a copy in localStorage
  // can be neither linked nor sent. The hash, not the query, because it never
  // reaches the server and never turns into a second URL for the same page.

  function hashRead(name) {
    var m = new RegExp("(?:^|&)" + name + "=([^&]*)")
      .exec(location.hash.replace(/^#/, ""));
    return m ? decodeURIComponent(m[1]) : null;
  }

  /**
   * Write one key into the hash, preserving the others. replaceState, not
   * pushState: a pick is not a navigation, and 120 of them would bury the
   * page the reader arrived from under 120 back-button steps.
   */
  function hashWrite(name, value) {
    var parts = location.hash.replace(/^#/, "").split("&").filter(Boolean)
      .filter(function (p) { return p.indexOf(name + "=") !== 0; });
    if (value) parts.push(name + "=" + encodeURIComponent(value));
    var hash = parts.length ? "#" + parts.join("&") : "";
    try {
      history.replaceState(null, "",
        location.pathname + location.search + hash);
    } catch (e) {
      location.hash = hash;
    }
  }

  window.B12State = {
    get: get, set: set,
    getPage: getPage, setPage: setPage,
    hashRead: hashRead, hashWrite: hashWrite,
    VERSION: VERSION,
  };
})();
