/* Big12ology theme control — light / dark / system.
 *
 * The choice is applied by an inline snippet in <head> (see b12ThemeBoot in
 * the page templates) so the root carries data-theme before the first paint;
 * this file only builds the switch and keeps it in sync. Loading it with
 * defer is fine — a flash of the wrong theme would come from the boot
 * snippet being missing, not from this.
 *
 * Stored value is one of "light" | "dark" | "system". "system" is the
 * default and is stored as an explicit absence, so a reader who has never
 * touched the switch follows their OS forever without us pinning anything.
 */
(function () {
  var KEY = "b12-theme";
  var root = document.documentElement;

  var ICON = {
    light: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" aria-hidden="true">' +
      '<circle cx="12" cy="12" r="4.2"/><path d="M12 2v2M12 20v2M2 12h2M20 ' +
      '12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 ' +
      '1.4"/></svg>',
    dark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
      'aria-hidden="true"><path d="M20 13.5A8.2 8.2 0 0 1 10.5 4a8.2 8.2 0 1 ' +
      '0 9.5 9.5z"/></svg>',
    system: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
      'aria-hidden="true"><rect x="2.5" y="4" width="19" height="12.5" ' +
      'rx="1.8"/><path d="M8.5 20.5h7"/></svg>'
  };
  var LABEL = { light: "Light", dark: "Dark", system: "System" };

  function stored() {
    try {
      var v = localStorage.getItem(KEY);
      return v === "light" || v === "dark" ? v : "system";
    } catch (e) {
      return "system";
    }
  }

  function apply(mode) {
    if (mode === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", mode);
    try {
      if (mode === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, mode);
    } catch (e) { /* private mode: the choice lasts the session */ }
    // Let the browser paint form controls and scrollbars to match.
    root.style.colorScheme =
      mode === "system" ? "light dark" : mode;
  }

  function build() {
    var host = document.querySelector(".b12-theme");
    if (!host) return;
    var current = stored();
    host.setAttribute("role", "group");
    host.setAttribute("aria-label", "Color theme");
    host.innerHTML = ["light", "system", "dark"].map(function (m) {
      return '<button type="button" data-mode="' + m + '" title="' +
        LABEL[m] + ' theme" aria-label="' + LABEL[m] + ' theme" ' +
        'aria-pressed="' + (m === current) + '">' + ICON[m] + "</button>";
    }).join("");
    host.querySelectorAll("button").forEach(function (b) {
      b.onclick = function () {
        var mode = b.dataset.mode;
        apply(mode);
        host.querySelectorAll("button").forEach(function (o) {
          o.setAttribute("aria-pressed", String(o.dataset.mode === mode));
        });
      };
    });
  }

  apply(stored());
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
