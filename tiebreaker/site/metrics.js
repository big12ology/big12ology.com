// Counting what happens on a page, without learning anything about who is on
// it.
//
// The rule this file exists under, and the reason it is short: NOTHING IS
// STORED ON YOUR DEVICE AND NOTHING IDENTIFIES YOU. No cookie, no
// localStorage, no id generated and kept, not even for the length of a visit.
// state.js is the site's storage and this deliberately does not use it —
// there is no b12- key here, which is why the privacy page's list of stored
// keys is unchanged by any of this and why assemble.sh's disclosure gate has
// nothing new to find.
//
// The consequence is real and worth stating rather than hiding: two page
// loads cannot be told apart from one person or two, and nothing can be
// followed from one day to the next. Retention and funnels across sessions
// are therefore not answerable here and were not traded away for something
// else — they were declined. The pools have real accounts and their numbers
// come from the database instead, where a person already exists because they
// asked to.
//
// Loaded blocking rather than deferred, for the same reason state.js is: the
// tiebreaker's app.js is a blocking script at the foot of the page and would
// run before any deferred file, so a deferred B12Metrics would be undefined
// at exactly the call sites that matter. It is two kilobytes and makes no
// requests until something has happened. Every caller still guards on
// `window.B12Metrics` — a page that does not ship this file must keep working.
(function () {
  "use strict";

  var ENDPOINT = "/api/e";

  // The same ceiling the Worker enforces. Matching it here means the overflow
  // is dropped in the place that can see which events they were, rather than
  // silently truncated at the far end.
  var MAX = 16;

  /**
   * Do not count me. Two signals, both of which mean the same thing.
   *
   * Global Privacy Control is the one that is actually implemented and, in
   * some places, actually law. Do Not Track is dead everywhere and honored by
   * almost nobody, which is precisely why it is honored here: the handful of
   * people still sending it are sending it on purpose, and the cost of
   * agreeing with them is one comparison.
   *
   * Nothing degrades. A reader who opts out gets every feature; the site
   * simply does not learn that they used it.
   */
  function optedOut() {
    try {
      if (navigator.globalPrivacyControl === true) return true;
      if (navigator.doNotTrack === "1" || window.doNotTrack === "1") return true;
    } catch (e) {}
    return false;
  }

  if (optedOut()) {
    // A no-op with the same shape, so no caller needs to know.
    window.B12Metrics = {
      send: function () {}, atEnd: function () {}, flush: function () {},
      off: true,
    };
    return;
  }

  var queue = [];
  var sentRead = false;

  // Engaged time, meaning time this page was actually in front of somebody.
  // Not time since load: a tab opened and left for an hour is not an hour of
  // reading, and counting it that way is how "average time on page" becomes a
  // number nobody can act on.
  var visibleSince = document.visibilityState === "visible" ? Date.now() : 0;
  var engaged = 0;
  var deepest = 0;

  function tick() {
    if (visibleSince) {
      engaged += Date.now() - visibleSince;
      visibleSince = document.visibilityState === "visible" ? Date.now() : 0;
    } else if (document.visibilityState === "visible") {
      visibleSince = Date.now();
    }
  }

  /** How far down the page has been seen, as a percentage of what there is. */
  function depth() {
    var doc = document.documentElement;
    var total = Math.max(doc.scrollHeight, document.body ? document.body.scrollHeight : 0);
    if (!total) return 0;
    var seen = (window.pageYOffset || doc.scrollTop || 0) + window.innerHeight;
    return Math.min(100, Math.round((seen / total) * 100));
  }

  var pending = false;
  function onScroll() {
    if (pending) return;
    pending = true;
    // One measurement per frame. A scroll handler that reads scrollHeight on
    // every event forces a layout on every event, which is a jank bug shipped
    // in the name of measuring whether anybody scrolled.
    requestAnimationFrame(function () {
      pending = false;
      var d = depth();
      if (d > deepest) deepest = d;
    });
  }

  function bucket(pct) {
    if (pct >= 100) return "100";
    if (pct >= 75) return "75";
    if (pct >= 50) return "50";
    return "25";
  }

  /**
   * Queue one event. `detail` and `value` are optional and both are checked
   * against a fixed table at the other end, so a name this file gets wrong is
   * dropped there rather than quietly polluting a total.
   */
  function send(name, detail, value) {
    if (queue.length >= MAX) return;
    queue.push([name, detail === undefined ? null : detail,
                value === undefined ? 0 : value]);
    // Only when the batch is full. Everything else waits for the page to go
    // away, which is one request per visit instead of one per click.
    if (queue.length >= MAX) flush();
  }

  /**
   * Hand the batch to the browser and forget it.
   *
   * sendBeacon, because the moment worth sending at is the moment the page is
   * being torn down, and an ordinary fetch is cancelled when that happens —
   * which loses precisely the events of the readers who left, who are the
   * ones the numbers are about. The queue is cleared whether or not the send
   * succeeds: a retry would be a second copy of an event whose only purpose
   * is to be summed.
   */
  function flush() {
    if (!queue.length) return;
    var body = JSON.stringify({ e: queue });
    queue = [];
    try {
      var blob = new Blob([body], { type: "application/json" });
      // The Content-Type on the blob is not decoration. The Worker requires
      // application/json, which is one of the three things standing in for a
      // CSRF token there, and a beacon sent as text/plain is refused.
      if (navigator.sendBeacon && navigator.sendBeacon(ENDPOINT, blob)) return;
    } catch (e) {}
    try {
      fetch(ENDPOINT, {
        method: "POST", body: body, keepalive: true,
        headers: { "Content-Type": "application/json" },
      }).catch(function () {});
    } catch (e) {}
  }

  /**
   * Something to add to the batch at the very end, rather than as it happens.
   *
   * The case this exists for is the what-if simulator, where a reader may
   * click forty games in a row. Forty events is forty times the volume for a
   * number nobody would read individually — "how many picks did this visit
   * make" is the question, and it is one event with a count on it. A caller
   * therefore keeps its own tally and hands it over here.
   *
   * It has to be a hook rather than the caller listening for pagehide itself,
   * and the reason is listener order: this file is loaded in the head and
   * registers its own pagehide handler first, so anything a later script
   * queued from its own handler would arrive after the batch had already
   * gone. Registering the contribution instead of the listener puts it on the
   * right side of that race.
   */
  var enders = [];
  function atEnd(fn) { enders.push(fn); }

  /**
   * Close the page out: collect the end-of-visit contributions, then the read
   * itself. Runs once and once only.
   *
   * Once, rather than on every hide: a reader who switches tabs four times
   * would otherwise contribute four reads of the same page and pull the
   * average down by three. The cost is that time spent after the first hide
   * is not counted, so the number is a floor rather than an estimate. A floor
   * everybody understands beats a total nobody can define.
   */
  function finish() {
    if (sentRead) return;
    sentRead = true;
    tick();
    for (var i = 0; i < enders.length; i++) {
      try { enders[i](); } catch (e) {}
    }
    enders = [];
    var d = depth();
    if (d > deepest) deepest = d;
    var secs = Math.round(engaged / 1000);
    // Nothing to report from a page that was never actually looked at — a
    // prerender, a background tab closed unread. Anything the contributions
    // above queued still goes; it is only the read that is withheld, because
    // a read of zero seconds at zero depth is not a read.
    if (!secs && !deepest) return;
    // Front of the queue, and something else gives way if the batch is
    // already full. The read is the one event every page produces and the
    // only one the aggregates are divided by, so it is the last thing that
    // should be lost to a cap — and the far end truncates at the same number,
    // which would otherwise drop whichever event happened to be last.
    if (queue.length >= MAX) queue.pop();
    queue.unshift(["read", bucket(deepest), secs]);
  }

  document.addEventListener("visibilitychange", function () {
    tick();
    if (document.visibilityState === "hidden") { finish(); flush(); }
  });

  // pagehide as well as visibilitychange, and both are needed. visibilitychange
  // is the one that fires when a phone is locked or an app is switched, which
  // is how most mobile visits actually end; pagehide is the one that fires on
  // a desktop tab being closed and on a back-forward-cache navigation, where
  // the tab may go straight from visible to gone.
  window.addEventListener("pagehide", function () { tick(); finish(); flush(); });

  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  window.B12Metrics = { send: send, atEnd: atEnd, flush: flush, off: false };
})();
