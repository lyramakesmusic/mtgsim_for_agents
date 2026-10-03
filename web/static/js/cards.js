// Card data, faces, mana symbols, and the shared hover preview.
import { h, icon, slug, clamp } from "./util.js";

export const DB = {};            // name -> {cost, type, text, pt, mv, art}
export const ART = new Set();    // slugs with a cached scan in data/art

export function addCards(cards) {
  Object.assign(DB, cards);
  for (const [n, d] of Object.entries(cards)) if (d.art) ART.add(slug(n));   // art fetched since boot
}
export function setArt(slugs) { slugs.forEach((s) => ART.add(s)); }

// "Sol Ring#4" -> "Sol Ring"; front face of a DFC keeps its full name for lookup
export const baseName = (id) => String(id).replace(/#\w+$/, "");
export function info(name) {
  const n = baseName(name);
  return DB[n] || DB[Object.keys(DB).find((k) => k.split(" // ")[0] === n) || ""] || null;
}
export function artUrl(name) {
  const n = baseName(name);
  if (ART.has(slug(n))) return `/art/${slug(n)}.jpg`;
  const full = Object.keys(DB).find((k) => k.split(" // ")[0] === n);
  if (full && ART.has(slug(full))) return `/art/${slug(full)}.jpg`;
  return null;
}

const COLOR_ORDER = "WUBRG";
export function colorsOf(name) {
  const d = info(name);
  if (!d) return "";
  const s = new Set();
  for (const ch of d.cost || "") if (COLOR_ORDER.includes(ch)) s.add(ch);
  if (!s.size && /Land/.test(d.type)) for (const m of (d.text || "").matchAll(/\{([WUBRG])\}/g)) s.add(m[1]);
  return [...COLOR_ORDER].filter((c) => s.has(c)).join("");
}

// a permanent's P/T as the state gives it, or null. A 0/0 with no +1/+1 counters can't be on the
// battlefield as a creature, so that's a noncreature token (Treasure, Clue, Food) created with a P/T.
const NONCREATURE_TOKENS = new Set(["treasure", "food", "clue", "gold", "blood", "map", "powerstone", "incubator", "junk", "shard", "lander"]);

export function ptOf(perm) {
  if (!perm) return null;
  // older games gave artifact tokens the 1/1 default; they aren't creatures
  if (perm.token && NONCREATURE_TOKENS.has(String(perm.name).split(" ")[0].toLowerCase()) && !(perm.mods || []).length) return null;
  const mods = perm.mods || [];
  let base = perm.pt;
  for (const m of mods) if (m.pt) base = m.pt;             // a temporary "becomes a 1/1"
  if (!base) return null;
  const c = perm.counters || {};
  if (base[0] === 0 && base[1] === 0 && !(c["+1/+1"] > 0) && !mods.some((m) => m.pt_mod)) return null;
  return base;
}

// current [power, toughness]: base (or temporary override) + counters + temporary pt_mods; null for noncreatures
export function ptNow(perm) {
  const base = ptOf(perm);
  if (!base) return null;
  const c = perm.counters || {};
  const plus = (c["+1/+1"] || 0) - (c["-1/-1"] || 0);
  let dp = 0, dt = 0;
  for (const m of perm.mods || []) if (m.pt_mod) { dp += m.pt_mod[0]; dt += m.pt_mod[1]; }
  return [base[0] + plus + dp, base[1] + plus + dt];
}

// "+3/+3 until end of turn" per temporary change
export const modLines = (perm) => (perm && perm.mods || []).map((m) =>
  (m.pt_mod ? `${m.pt_mod[0] >= 0 ? "+" : ""}${m.pt_mod[0]}/${m.pt_mod[1] >= 0 ? "+" : ""}${m.pt_mod[1]}` : `${m.pt[0]}/${m.pt[1]}`)
  + (m.until === "eot" ? " until end of turn" : ` until ${m.seat}'s next turn`));

export const typeClass = (name, perm) => {
  const t = (info(name) || {}).type || "";
  if (ptOf(perm) || /Creature/.test(t)) return "creature";
  if (/Land/.test(t)) return "land";
  if (/Planeswalker/.test(t)) return "walker";
  return "other";
};

// compact cost "2UU(W/B)X" -> DOM pips
export function manaCost(cost, cls = "") {
  const wrap = h("span.cost" + (cls ? "." + cls : ""));
  const toks = String(cost || "").match(/\([^)]*\)|\d+|[A-Za-z]/g) || [];
  for (const t of toks) {
    const sym = t.replace(/[()]/g, "").toUpperCase();
    const parts = sym.split("/");
    const pip = h("span.pip");
    if (parts.length > 1) {
      pip.classList.add("hybrid");
      pip.style.setProperty("--a", `var(--mana-${parts[0]}, var(--mana-C))`);
      pip.style.setProperty("--b", `var(--mana-${parts[1] === "P" ? parts[0] : parts[1]}, var(--mana-C))`);
      pip.textContent = parts[1] === "P" ? parts[0] : "";
    } else {
      pip.dataset.m = /^\d+$/.test(sym) || sym === "X" ? "C" : sym;
      pip.textContent = /^\d+$/.test(sym) || sym === "X" || sym === "C" ? sym : "";
    }
    wrap.append(pip);
  }
  return wrap;
}

// a card face element (front). size is controlled by the parent via width/--cw.
export function cardFace(name, { token = false } = {}) {
  if (name === "__library__") return h("div.face.front.libface");
  const url = artUrl(name);
  if (url) {
    const img = h("img.scan", { src: url, alt: baseName(name), draggable: false, decoding: "async" });
    return h("div.face.front", img);
  }
  if (token || !info(name)) {
    const face = h("div.face.front");
    const img = h("img.scan", { src: tokenArtUrl(name), alt: baseName(name), draggable: false, decoding: "async",
      onerror: () => { const g = generatedFace(name, token); face.replaceWith(g); if (face._owner) face._owner.front = g; } });
    face.append(img);
    return face;
  }
  return generatedFace(name, token);
}

export const tokenArtUrl = (name) => "/tokenart/" + encodeURIComponent(baseName(name));

function generatedFace(name, token) {
  const d = info(name);
  const cols = colorsOf(name);
  const el = h("div.face.front.gen" + (token ? ".token" : ""), { dataset: { colors: cols || "C" } },
    h("div.gen-name", baseName(name)),
    h("div.gen-art", token ? h("span.gen-token", "token") : null),
    h("div.gen-type", d ? d.type : token ? "Token" : ""),
    d && d.text ? h("div.gen-text", d.text) : null);
  if (cols.length) el.style.setProperty("--gc", `var(--mana-${cols[0]})`);
  if (cols.length > 1) el.style.setProperty("--gc2", `var(--mana-${cols[1]})`);
  return el;
}

export const cardBack = () => h("div.face.back");

// commander-art crest background: crops the art box out of a full card scan
export function artCropStyle(name) {
  const url = artUrl(name);
  return url ? { backgroundImage: `url("${url}")` } : {};
}

// ------------------------------------------------------------------ hover preview
let preview, previewTimer, pinned = null;
export function initPreview() {
  preview = h("div.preview", { hidden: true });
  document.body.append(preview);
  document.addEventListener("pointerover", (e) => {
    const t = e.target.closest("[data-card]");
    if (!t || pinned) return;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(() => showPreview(t), t.classList.contains("sprite") ? 140 : 60);
  });
  document.addEventListener("pointerout", (e) => {
    const t = e.target.closest("[data-card]");
    if (!t || (e.relatedTarget && t.contains(e.relatedTarget))) return;
    clearTimeout(previewTimer);
    if (!pinned) hidePreview();
  });
  document.addEventListener("pointerdown", () => { clearTimeout(previewTimer); if (!pinned) hidePreview(); }, true);
}

export function hidePreview() { if (preview) preview.hidden = true; }

function showPreview(target) {
  const name = target.dataset.card;
  const perm = target._perm || null;
  const d = info(name);
  preview.replaceChildren();
  const url = artUrl(name);
  preview.append(h("div.preview-card", url ? h("img", { src: url, alt: "" }) : cardFace(name, { token: perm && perm.token })));
  const rows = [];
  if (perm) {
    const c = perm.counters || {};
    const chips = Object.entries(c).map(([k, v]) => h("span.chip", `${k} ${v}`));
    const now = ptNow(perm);
    if (now) {
      rows.push(h("div.preview-pt", `${now[0]}/${now[1]}`,
        perm.damage ? h("span.dmg", `${perm.damage} damage`) : null));
      for (const l of modLines(perm)) rows.push(h("div.preview-mod", icon("hourglass-split"), " " + l));
    }
    if (chips.length) rows.push(h("div.preview-chips", chips));
    const flags = [target._attachedTo && `attached to ${baseName(target._attachedTo)}`, perm.tapped && "tapped", perm.sick && "summoning sick", perm.token && "token",
      perm.controller && perm.owner && perm.controller !== perm.owner && `owned by ${perm.owner}`].filter(Boolean);
    if (flags.length) rows.push(h("div.preview-flags", flags.join(" · ")));
    rows.push(h("div.preview-id", perm.id));
  }
  if (!url && d && d.text && !rows.length) rows.push(h("div.preview-text", d.text));
  if (url && d && /\/\//.test(name + (d.type || ""))) rows.push(h("div.preview-text", d.text));
  if (rows.length) preview.append(h("div.preview-info", rows));
  preview.hidden = false;
  const r = target.getBoundingClientRect();
  const pw = 300, ph = preview.offsetHeight || 440;
  const right = r.right + 14 + pw < innerWidth;
  const x = right ? r.right + 14 : r.left - 14 - pw;
  const y = clamp(r.top + r.height / 2 - ph / 2, 8, innerHeight - ph - 8);
  preview.style.transform = `translate(${Math.round(clamp(x, 8, innerWidth - pw - 8))}px, ${Math.round(y)}px)`;
}

// a clickable-free inline card reference (hover previews it)
export const cardChip = (name, cls = "") => h("span.cardref" + (cls ? "." + cls : ""), { dataset: { card: baseName(name) } }, baseName(name));
