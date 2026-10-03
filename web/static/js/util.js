// DOM + data helpers shared by every screen.

// h("div.card.big", {onclick, style:{...}, dataset:{...}}, child|[children]|"text")
export function h(sel, attrs, ...kids) {
  const [tag, ...classes] = sel.split(".");
  const el = document.createElement(tag || "div");
  if (classes.length) el.className = classes.join(" ");
  if (attrs !== undefined && (attrs === null || typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    kids.unshift(attrs); attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "style" && typeof v === "object") {
      for (const [sk, sv] of Object.entries(v)) sk.startsWith("--") ? el.style.setProperty(sk, sv) : (el.style[sk] = sv);
    }
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className += " " + v;
    else if (k in el && k !== "list" && k !== "form") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

export const icon = (name, cls = "") => h(`i.bi.bi-${name}${cls ? "." + cls : ""}`);

export async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.body && typeof opts.body !== "string" && !(opts.body instanceof Blob)) {
    init.body = JSON.stringify(opts.body);
    init.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  }
  const r = await fetch("/api/" + path, init);
  const ct = r.headers.get("Content-Type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.error) || `${r.status} ${r.statusText}`);
  return data;
}

export const slug = (name) => String(name).replace(/[^\p{L}\p{N}_]+/gu, "_").replace(/^_+|_+$/g, "").toLowerCase();
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export const SEAT_COLORS = { P1: "#6fa0d2", P2: "#d4a95c", P3: "#cf7468", P4: "#7fb68c" };
export const seatColor = (h) => SEAT_COLORS[h] || "#9aa0a8";

// persisted per-viewer settings
const SETTINGS_KEY = "mtgsim.settings";
const DEFAULTS = { thoughts: true, hands: true, sound: 0.6, voice: false, speed: 1, feed: true,
                   feedFilter: "all", readSpeed: 1, motion: 1, showHidden: false };
let settings = { ...DEFAULTS };
try { settings = { ...DEFAULTS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") }; } catch {}
const listeners = new Set();
export const S = new Proxy(settings, {
  set(t, k, v) {
    t[k] = v;
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(t)); } catch {}
    listeners.forEach((f) => f(k, v));
    return true;
  },
});
export const onSetting = (f) => { listeners.add(f); return () => listeners.delete(f); };

export function store(key, value) {
  try {
    if (value === undefined) return JSON.parse(localStorage.getItem("mtgsim." + key) || "null");
    localStorage.setItem("mtgsim." + key, JSON.stringify(value));
  } catch { return null; }
}

export function timeAgo(dateStr) {
  const d = new Date(String(dateStr).replace(" ", "T"));
  const s = (Date.now() - d) / 1000;
  if (isNaN(s)) return "";
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)}d ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

// minimal markdown → DOM for postmortems and memos (headings, lists, bold, code, paragraphs)
export function markdown(src) {
  const root = h("div.md");
  const inline = (t) => {
    const frag = document.createDocumentFragment();
    const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g;
    let last = 0, m;
    while ((m = re.exec(t))) {
      frag.append(t.slice(last, m.index));
      const s = m[0];
      frag.append(s.startsWith("**") ? h("strong", s.slice(2, -2)) : s.startsWith("`") ? h("code", s.slice(1, -1)) : h("em", s.slice(1, -1)));
      last = m.index + s.length;
    }
    frag.append(t.slice(last));
    return frag;
  };
  let list = null, para = [], code = null;
  const flush = () => { if (para.length) root.append(h("p", inline(para.join(" ")))); para = []; list = null; };
  for (const line of src.split("\n")) {
    if (line.startsWith("```")) {
      if (code) { root.append(h("pre", code.join("\n"))); code = null; } else { flush(); code = []; }
      continue;
    }
    if (code) { code.push(line); continue; }
    const hd = line.match(/^(#{1,4})\s+(.*)/);
    if (hd) { flush(); root.append(h(`h${Math.min(6, hd[1].length + 1)}`, inline(hd[2]))); continue; }
    const li = line.match(/^\s*[-*]\s+(.*)/) || line.match(/^\s*\d+\.\s+(.*)/);
    if (li) {
      if (para.length) { root.append(h("p", inline(para.join(" ")))); para = []; }
      if (!list) { list = h("ul"); root.append(list); }
      list.append(h("li", inline(li[1])));
      continue;
    }
    if (!line.trim()) { flush(); continue; }
    list = null;
    para.push(line.trim());
  }
  flush();
  return root;
}

// modal / drawer host: -> {close}
export function modal(content, { side = false } = {}) {
  const back = h("div.modal-back" + (side ? ".side" : ""));
  const close = () => { back.classList.add("out"); setTimeout(() => back.remove(), 180); document.removeEventListener("keydown", esc); };
  const esc = (e) => { if (e.key === "Escape") close(); };
  back.addEventListener("pointerdown", (e) => { if (e.target === back) close(); });
  document.addEventListener("keydown", esc);
  back.append(content);
  document.body.append(back);
  return { close };
}

// dropdown: a button showing the current value; its menu opens anchored under it.
// options: [{value, label}] -> Element (el.value reads the current value)
export function dropdown(options, value, onChange) {
  let cur = value;
  const label = () => (options.find((o) => o.value === cur) || options[0]).label;
  const btn = h("button.dd", { type: "button" }, h("span.dd-v", label()), icon("chevron-down"));
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (document.querySelector(".dd-menu")) return closeMenus();
    const r = btn.getBoundingClientRect();
    const menu = h("div.dd-menu", { style: { left: r.left + "px", top: r.bottom + 4 + "px", minWidth: r.width + "px" } },
      options.map((o) => h("button.dd-opt" + (o.value === cur ? ".on" : ""), { type: "button", onclick: () => {
        cur = o.value; btn.querySelector(".dd-v").textContent = label(); closeMenus(); onChange(cur);
      } }, o.label)));
    document.body.append(menu);
    const close = (ev) => { if (!menu.contains(ev.target)) closeMenus(); };
    const esc = (ev) => { if (ev.key === "Escape") closeMenus(); };
    setTimeout(() => { document.addEventListener("pointerdown", close); document.addEventListener("keydown", esc); });
    menu._off = () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", esc); };
  });
  return btn;
}
const closeMenus = () => document.querySelectorAll(".dd-menu").forEach((m) => { m._off?.(); m.remove(); });
