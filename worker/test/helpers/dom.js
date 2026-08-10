// A DOM small enough to read and big enough to run the pick'em client.
//
// Modelled on attendance/tests/render.test.mjs, which does the same thing for
// the chart module, but this one has to be real enough to answer questions
// rather than merely absorb calls: the tests ask what text ended up on the
// page, whether an input is disabled, which classes a label carries. A stub
// whose setters throw everything away would make every assertion pass.
//
// It is deliberately not jsdom. The client touches a dozen DOM features and
// pulling in a browser implementation to exercise them would add a dependency
// to a repo whose whole build is Python and rsync.

class ClassList {
  constructor(el) { this.el = el; }
  get _set() {
    return new Set(String(this.el.className || "").split(/\s+/).filter(Boolean));
  }
  _write(s) { this.el.className = [...s].join(" "); }
  add(...cs) { const s = this._set; cs.forEach((c) => s.add(c)); this._write(s); }
  remove(...cs) {
    const s = this._set; cs.forEach((c) => s.delete(c)); this._write(s);
  }
  contains(c) { return this._set.has(c); }
  toggle(c, on) {
    const s = this._set;
    const want = on === undefined ? !s.has(c) : !!on;
    want ? s.add(c) : s.delete(c);
    this._write(s);
    return want;
  }
}

class Node {
  constructor(tag) {
    this.tagName = String(tag || "").toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.dataset = {};
    this.style = {
      _p: {},
      setProperty(k, v) { this._p[k] = v; },
      getPropertyValue(k) { return this._p[k]; },
    };
    this.classList = new ClassList(this);
    this.className = "";
    this._text = "";
    this.disabled = false;
    this.checked = false;
    this.hidden = false;
    this.value = "";
  }

  appendChild(c) {
    if (c == null) throw new TypeError("appendChild(null)");
    c.parentNode = this;
    this.children.push(c);
    return c;
  }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  removeChild(c) {
    this.children = this.children.filter((x) => x !== c);
    return c;
  }
  setAttribute(k, v) { this.attributes[String(k)] = String(v); }
  getAttribute(k) {
    return Object.prototype.hasOwnProperty.call(this.attributes, k)
      ? this.attributes[k] : null;
  }
  removeAttribute(k) { delete this.attributes[k]; }
  hasAttribute(k) {
    return Object.prototype.hasOwnProperty.call(this.attributes, k);
  }
  addEventListener() {}
  removeEventListener() {}
  getBoundingClientRect() {
    return { left: 0, top: 0, right: 600, bottom: 300, width: 600, height: 300 };
  }
  closest() { return null; }

  /**
   * Disabled as a browser sees it: a disabled FIELDSET disables everything
   * inside it.
   *
   * The pick'em relies on this deliberately — per-input disabling is the one
   * form that does not survive forced-colors: active — so a stub that only
   * looked at the input's own flag would report every closed game as live.
   */
  get effectiveDisabled() {
    for (let n = this; n; n = n.parentNode) {
      if (n.disabled && (n === this || n.tagName === "FIELDSET")) return true;
    }
    return false;
  }
  focus() {}

  set textContent(v) {
    this._text = v == null ? "" : String(v);
    this.children = [];
  }
  get textContent() {
    if (this.children.length) {
      return this.children.map((c) => c.textContent).join("");
    }
    return this._text;
  }
  set innerHTML(v) { this._html = String(v); this.children = []; }
  get innerHTML() { return this._html || ""; }

  /** Everything under here, this node included. */
  walk(out = []) {
    out.push(this);
    for (const c of this.children) if (c.walk) c.walk(out);
    return out;
  }
  querySelectorAll(sel) {
    // Only the forms the client actually uses: ".class" and "tag".
    const all = this.walk().slice(1);
    if (sel.startsWith(".")) {
      return all.filter((n) => n.classList && n.classList.contains(sel.slice(1)));
    }
    return all.filter((n) => n.tagName === sel.toUpperCase());
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

class TextNode {
  constructor(t) { this._t = t == null ? "" : String(t); this.children = []; }
  get textContent() { return this._t; }
  walk(out = []) { out.push(this); return out; }
}

/** Install a fresh document/window on the global. Returns the document. */
export function installDOM() {
  const byId = new Map();
  const document = {
    createElement: (t) => new Node(t),
    createElementNS: (_ns, t) => new Node(t),
    createTextNode: (t) => new TextNode(t),
    getElementById: (id) => byId.get(id) || null,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    documentElement: new Node("html"),
    body: new Node("body"),
    hidden: false,
    _register(id, node) { byId.set(id, node); return node; },
    _reset() { byId.clear(); },
  };
  globalThis.document = document;
  globalThis.window = {
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    addEventListener() {},
    scrollX: 0, scrollY: 0, innerWidth: 1200, innerHeight: 800,
    location: { pathname: "/pools/pickem/", search: "", href:
                "https://big12ology.com/pools/pickem/" },
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
  };
  globalThis.location = globalThis.window.location;
  // Node 22 ships a read-only navigator, so it is defined rather than
  // assigned. The client reads only the language off it.
  if (!globalThis.navigator || !globalThis.navigator.language) {
    Object.defineProperty(globalThis, "navigator", {
      value: { language: "en-US" }, configurable: true, writable: true,
    });
  }
  globalThis.fetch = async () => {
    throw new Error("the fuzzer must not reach the network");
  };
  globalThis.Node = Node;
  return document;
}

export { Node, TextNode };
