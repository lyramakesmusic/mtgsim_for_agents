// The game table: seat mats + every card as a spring-driven sprite.
//
// render(state) lays out targets for every visible card and reconciles them with live
// sprites. Continuity comes from the diff: a sprite that leaves one zone and a card of
// the same name that appears in another are the same object, so the sprite is re-keyed
// and flies. play(event) adds intent on top: bubbles, arrows, banners, pulses.
import { h, icon, seatColor, clamp, S } from "./util.js";
import { cardFace, cardBack, info, baseName, typeClass, artCropStyle, ptOf, ptNow } from "./cards.js";
import * as fx from "./fx.js";
import { sfx } from "./sfx.js";

const W0 = 244, H0 = 340, AR = H0 / W0;       // sprite base size; cards scale from here
const fmtN = (n) => n < 1e4 ? String(n) : n < 1e6 ? (n / 1e3).toFixed(n < 1e5 ? 1 : 0).replace(/\.0$/, "") + "k" : (n / 1e6).toFixed(n < 1e7 ? 1 : 0).replace(/\.0$/, "") + "M";

// ------------------------------------------------------------------ springs
class Spring {
  constructor(v) { this.v = v; this.t = v; this.vel = 0; }
  snap(v) { this.v = this.t = v; this.vel = 0; }
  step(dt, k, c) {
    const a = k * (this.t - this.v) - c * this.vel;
    this.vel += a * dt;
    this.v += this.vel * dt;
    if (Math.abs(this.t - this.v) < 0.01 && Math.abs(this.vel) < 0.01) { this.v = this.t; this.vel = 0; return false; }
    return true;
  }
}

class Sprite {
  constructor(layer, key, name, opts) {
    this.key = key; this.name = name; this.zone = opts.zone; this.seat = opts.seat;
    this.el = h("div.sprite", { dataset: { card: baseName(name) } });
    this.el._sp = this;
    this.flip = h("div.flip");
    this.front = cardFace(name, { token: opts.token });
    this.front._owner = this;
    this.flip.append(this.front, cardBack());
    this.over = h("div.over");
    this.el.append(this.flip, this.over);
    layer.append(this.el);
    this.p = { x: new Spring(0), y: new Spring(0), r: new Spring(0), s: new Spring(0.3), o: new Spring(1),
               f: new Spring(0), lift: new Spring(0) };
    this.delay = 0;
    this.dying = false;
    this.overSig = "";
    this.z = 0;
  }
  setTarget(t, now, stagger = 0) {
    const far = Math.hypot(t.x - this.p.x.t, t.y - this.p.y.t) > 60;
    this.p.x.t = t.x; this.p.y.t = t.y; this.p.r.t = t.r; this.p.s.t = t.w / W0;
    this.p.f.t = t.face ? 0 : 1;
    this.p.o.t = t.o ?? 1;
    if (far) { this.p.lift.vel += 5.5; this.delay = now + stagger; }
    this.z = t.z;
    this.flying = far;
  }
  snap(t) {
    this.p.x.snap(t.x); this.p.y.snap(t.y); this.p.r.snap(t.r); this.p.s.snap(t.w / W0);
    this.p.f.snap(t.face ? 0 : 1); this.p.o.snap(t.o ?? 1); this.p.lift.snap(0);
    this.z = t.z; this.flying = false;
  }
  from(x, y, w, face, r = 0, o = 1) {
    this.p.x.snap(x); this.p.y.snap(y); this.p.s.snap(w / W0); this.p.f.snap(face ? 0 : 1);
    this.p.r.snap(r); this.p.o.snap(o);
  }
  draw() {
    const { x, y, r, s, o, f, lift } = this.p;
    const L = Math.max(0, lift.v);
    const sc = s.v * (1 + 0.14 * L);
    this.el.style.transform =
      `translate3d(${(x.v - W0 / 2).toFixed(1)}px,${(y.v - H0 / 2).toFixed(1)}px,0) rotate(${r.v.toFixed(2)}deg) scale(${sc.toFixed(4)})`;
    this.el.style.opacity = clamp(o.v, 0, 1).toFixed(3);
    const fl = clamp(f.v, 0, 1);
    this.flip.style.transform = fl > 0.001 && fl < 0.999 ? `rotateY(${(fl * 180).toFixed(1)}deg)` : fl >= 0.999 ? "rotateY(180deg)" : "";
    this.el.style.setProperty("--lift", L.toFixed(3));
    this.el.style.zIndex = String(this.flying || L > 0.05 ? 5000 + this.z : this.z);
  }
}

// ------------------------------------------------------------------ table
export class Table {
  constructor(root, seats) {
    this.seats = seats;                   // [{handle, deck, commanders, agent}]
    this.el = h("div.table", { dataset: { n: seats.length } });
    this.mats = h("div.mats");
    this.layer = h("div.cards");
    this.svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    this.svg.classList.add("arrows");
    this.fxl = h("div.fx");
    this.stackDim = h("div.stack-dim");
    this.layer.append(this.stackDim);
    this.el.append(this.mats, this.layer, this.svg, this.fxl);
    root.append(this.el);
    this.sprites = new Map();
    this.state = null;
    this.matEls = {};
    this.geo = null;
    this.arrows = [];                     // [{from(), to(), color, el, born}]
    this.combat = null;                   // {attacks:[{seat,target,ids}], blocks:{}, resolved}
    this.spotlight = new Set();           // sprite keys pulsing
    this.timeScale = 1;
    this.running = false;
    this.last = performance.now();
    for (const s of seats) this.buildMat(s);
    this.viewer = null;
    this.el.addEventListener("click", (e) => {
      const sp = e.target.closest(".sprite")?._sp;
      if (sp && this.onCardClick && this.onCardClick(sp)) return;
      const slot = e.target.closest(".pile-slot, .pile-label, .hand-label");
      const mat = e.target.closest(".mat");
      if (sp && ["graveyard", "exile", "command", "hand"].includes(sp.zone)) return this.openZone(sp.seat, sp.zone);
      if (slot && mat) return this.openZone(mat.dataset.seat, slot.dataset.zone || "hand");
      if (!e.target.closest(".zone-view")) this.closeZone();
    });
    this.onEsc = (e) => { if (e.key === "Escape") this.closeZone(); };
    document.addEventListener("keydown", this.onEsc);
    this.ro = new ResizeObserver(() => { this.layout(); if (this.state) this.render(this.state, { animate: false }); });
    this.ro.observe(this.el);
    this.layout();
  }

  destroy() { this.ro.disconnect(); this.running = false; document.removeEventListener("keydown", this.onEsc); this.el.remove(); }

  // ---------------------------------------------------------------- zone viewer
  openZone(seat, zone) {
    if (!seat || !zone || zone === "library") return;
    if (this.viewer && this.viewer.seat === seat && this.viewer.zone === zone) return this.closeZone();
    this.closeZone();
    const el = h("div.zone-view", { style: { "--seat": seatColor(seat) } });
    this.el.append(el);
    this.viewer = { seat, zone, el };
    this.fillZone();
  }
  closeZone() { if (this.viewer) { this.viewer.el.remove(); this.viewer = null; } }
  fillZone() {
    const v = this.viewer, pl = this.state && this.state.players.find((p) => p.handle === v.seat);
    if (!pl) return;
    const cards = v.zone === "command" ? (pl.commanders || []).map((c) => ({ name: c, dim: !(pl.command_zone || {})[c], tax: (pl.commander_tax || {})[c] }))
      : [...(pl[v.zone] || [])].map((name) => ({ name })).reverse();
    const s = this.seatOf(v.seat) || {};
    const m = this.geo.mats[v.seat];
    const cw = 112;
    const cols = Math.max(1, Math.min(cards.length, Math.floor((m.w - 40) / (cw + 8)), 8));
    v.el.replaceChildren(
      h("div.zv-head", h("span.zv-who", s.deck || v.seat), h("span.zv-zone", v.zone), h("span.zv-n", cards.length),
        h("button.zv-x", { onclick: () => this.closeZone() }, icon("x-lg"))),
      cards.length ? h("div.zv-grid", { style: { gridTemplateColumns: `repeat(${cols}, ${cw}px)` } },
        cards.map((c) => h("div.zv-card" + (c.dim ? ".dim" : ""), { dataset: { card: baseName(c.name) } }, cardFace(c.name),
          c.tax ? h("div.qty", `+${c.tax}`) : null)))
        : h("div.zv-empty", "—"));
    const W = this.geo.W, H = this.geo.H;
    const pw = cols * (cw + 8) + 24;
    const pile = m.piles[v.zone === "hand" ? "graveyard" : v.zone];
    const left = m.side === "left" ? m.x + m.crest.w + 20 : m.x + m.w - m.crest.w - 20 - pw;
    Object.assign(v.el.style, { left: clamp(left, 8, W - pw - 8) + "px", width: pw + "px", maxHeight: (H * 0.62) + "px",
      [m.orient === "top" ? "top" : "bottom"]: (m.orient === "top" ? m.y + 10 : H - m.y - m.h + 10) + "px" });
  }

  buildMat(seat) {
    const hdl = seat.handle;
    const color = seatColor(hdl);
    const cmdr = (seat.commanders || [])[0];
    const life = h("div.life", "40");
    const counters = h("div.pcounters");
    const crest = h("div.crest", { style: { "--seat": color } },
      h("div.crest-art", { style: artCropStyle(cmdr || "") }),
      h("div.crest-body",
        h("div.crest-top", h("span.handle", hdl), h("span.deck", seat.deck)),
        counters,
        h("div.crest-bottom", life)));
    const piles = {};
    for (const z of ["library", "graveyard", "exile", "command"]) {
      piles[z] = { slot: h("div.pile-slot", { dataset: { zone: z } }),
                   label: h("div.pile-label", { dataset: { zone: z } }, icon({ library: "stack", graveyard: "trash3", exile: "x-octagon", command: "star" }[z]), h("span.n", "")) };
    }
    const handLbl = h("div.hand-label", icon("hand-index"), h("span.n", "0"));
    const el = h("div.mat", { style: { "--seat": color }, dataset: { seat: hdl } }, crest,
      ...Object.values(piles).flatMap((p) => [p.slot, p.label]), handLbl,
      h("div.elim-stamp", h("div.elim-word", "eliminated"), h("div.elim-how")));
    this.mats.append(el);
    this.matEls[hdl] = { el, crest, life, counters, piles, handLbl, lifeShown: 40, lifeAnim: null };
  }

  // ---------------------------------------------------------------- geometry
  layout() {
    const W = this.el.clientWidth, H = this.el.clientHeight;
    if (!W || !H) return;
    const n = this.seats.length, g = 6;
    const cells = n === 2 ? [[0, 0, 1, 0, "top", "left"], [0, 1, 1, 0, "bottom", "left"]]
      : n === 3 ? [[0, 0, 0.5, 0, "top", "left"], [0.5, 0, 0.5, 0, "top", "right"], [0, 1, 1, 0, "bottom", "left"]]
      : [[0, 0, 0.5, 0, "top", "left"], [0.5, 0, 0.5, 0, "top", "right"], [0.5, 1, 0.5, 0, "bottom", "right"], [0, 1, 0.5, 0, "bottom", "left"]];
    // the talk band runs between the two rows of mats: speech lives there and covers nothing
    const bandH = clamp(H * 0.12, 78, 126);
    const rowH = (H - bandH) / 2;
    const ncols = n === 2 ? 1 : 2;
    this.geo = { W, H, mats: {}, band: { y: rowH, h: bandH }, slots: {} };
    this.layoutBand(ncols, W, rowH, bandH);
    this.seats.forEach((s, i) => {
      const [cx, row, cw, , orient, side] = cells[i];
      const x = cx * (W + g), w = cw * W - (cw < 1 ? g / 2 : 0), y = row * (rowH + bandH);
      this.geo.slots[s.handle] = { col: ncols === 1 || cx < 0.5 ? 0 : 1, pos: orient, side };
      const m = this.matGeo(x, y, w, rowH, orient, side);
      this.geo.mats[s.handle] = m;
      const me = this.matEls[s.handle];
      Object.assign(me.el.style, { left: x + "px", top: y + "px", width: w + "px", height: rowH + "px" });
      me.el.dataset.orient = orient; me.el.dataset.side = side;
      const place = (el, r) => Object.assign(el.style, { left: r.x - x + "px", top: r.y - y + "px", width: r.w + "px", height: r.h + "px" });
      place(me.crest, m.crest);
      for (const [z, p] of Object.entries(me.piles)) {
        const r = m.piles[z];
        place(p.slot, { x: r.cx - r.w / 2, y: r.cy - r.h / 2, w: r.w, h: r.h });
        place(p.label, { x: r.cx - r.w / 2 - 4, y: r.cy + r.h / 2 + 2, w: r.w + 8, h: 16 });
      }
      place(me.handLbl, { x: m.hand.x + m.hand.w - 60, y: orient === "top" ? m.hand.y + m.hand.h + 1 : m.hand.y - 17, w: 60, h: 16 });
    });
  }

  layoutBand(ncols, W, y, bh) {
    this.cols = this.cols || [];
    while (this.cols.length < ncols) { const c = h("div.talk-col"); this.fxl.append(c); this.cols.push(c); }
    this.cols.forEach((c, k) => {
      c.hidden = k >= ncols;
      const cw = W / ncols;
      Object.assign(c.style, { left: k * cw + "px", top: y + 3 + "px", width: cw + "px", height: bh - 6 + "px" });
      c.style.setProperty("--lines", Math.max(2, Math.floor((bh - 26) / 20)));
      c.style.setProperty("--lines-pair", Math.max(1, Math.floor((bh / 2 - 20) / 20)));
      c.style.setProperty("--maxw", Math.max(220, cw - (ncols === 2 ? 150 : 300)) + "px");
    });
  }

  matGeo(x, y, w, hgt, orient, side) {
    const pad = 10, gap = 8;
    const sc = clamp(w * 0.16, 104, 164);            // side column width
    const colX = side === "left" ? x + pad : x + w - pad - sc;
    const crestH = clamp(Math.min(sc * 0.92, hgt * 0.42), 80, 170);
    const top = orient === "top";
    const crest = { x: colX, y: top ? y + pad : y + hgt - pad - crestH, w: sc, h: crestH };
    // zone piles: 2x2 grid in the rest of the column
    const zH = hgt - 2 * pad - crestH - gap;
    const pw = Math.max(18, Math.min((sc - gap) / 2 - 6, (zH - 2 * 20 - gap) / 2 / AR));
    const ph = pw * AR;
    const gridY0 = top ? crest.y + crestH + gap : y + pad;
    const cellH = zH / 2;
    const px = (c) => colX + sc * (c ? 0.75 : 0.25);
    const py = (r) => gridY0 + cellH * r + (cellH - 18) / 2;
    const order = top ? [["library", 0, 0], ["graveyard", 1, 0], ["exile", 0, 1], ["command", 1, 1]]
      : [["command", 1, 0], ["exile", 0, 0], ["library", 0, 1], ["graveyard", 1, 1]];
    const piles = {};
    for (const [z, c, r] of order) piles[z] = { cx: px(c), cy: py(r), w: pw, h: ph };
    // battlefield + hand
    const bx = side === "left" ? colX + sc + gap + 4 : x + pad;
    const bw = w - 2 * pad - sc - gap - 4;
    const handH = clamp(hgt * 0.17, 48, 104);
    const hand = { x: bx, y: top ? y + pad : y + hgt - pad - handH, w: bw, h: handH };
    const field = { x: bx, y: top ? hand.y + handH + 14 : y + pad, w: bw, h: hgt - 2 * pad - handH - 14 };
    return { x, y, w, h: hgt, orient, side, crest, piles, hand, field, center: { x: x + w / 2, y: y + hgt / 2 } };
  }

  setWaiting(hdl) {
    for (const [k, me] of Object.entries(this.matEls)) me.crest.classList.toggle("waiting", k === hdl);
    const slot = hdl && this.geo && this.geo.slots[hdl];
    fx.waiting(this.fxl, slot ? this.cols[slot.col] : null, hdl, slot, seatColor(hdl));
  }

  crestCenter(hdl) {
    const m = this.geo && this.geo.mats[hdl];
    return m ? { x: m.crest.x + m.crest.w / 2, y: m.crest.y + m.crest.h / 2 } : { x: 0, y: 0 };
  }

  // ---------------------------------------------------------------- targets
  // -> Map key -> {key, name, seat, zone, x, y, w, r, z, face, perm, badge, token}
  targets(state) {
    const T = new Map();
    const G = this.geo;
    if (!G) return T;
    // attachments: {host id -> [{p, seat}]}; attached permanents render under their host, wherever it is
    const where = {};
    for (const pl of state.players) for (const p of pl.battlefield || []) where[p.id] = { p, seat: pl.handle };
    const byHost = {}, attached = new Set(), bySeat = {};
    for (const [a, hst] of Object.entries(state.attach || {})) {
      if (where[a] && G.mats[hst]) { (bySeat[hst] = bySeat[hst] || []).push(where[a]); attached.add(a); continue; }
      if (!where[a] || !where[hst] || a === hst) continue;
      (byHost[hst] = byHost[hst] || []).push(where[a]);
      attached.add(a);
    }
    this._att = { byHost, attached };
    state.players.forEach((pl, pi) => {
      const hdl = pl.handle, m = G.mats[hdl];
      if (!m) return;
      const zb = pi * 1000;
      // cards attached to a player (curses, gifts) sit on that player's field, marked with their owner
      const onMe = (bySeat[hdl] || []).map((a) => ({ ...a.p, owner: a.p.owner || a.seat, onSeat: true }));
      this.layoutField(T, onMe.length ? { ...pl, battlefield: [...(pl.battlefield || []), ...onMe] } : pl, m, zb);
      this.layoutHand(T, pl, m, zb);
      // piles: library (one back with count), graveyard/exile tops, commanders
      const lp = m.piles.library;
      if (pl.library > 0) T.set(`lib:${hdl}`, { key: `lib:${hdl}`, name: "__library__", seat: hdl, zone: "library",
        x: lp.cx, y: lp.cy, w: lp.w, r: 0, z: zb + 5, face: false, depth: pl.library });
      for (const [zone, list] of [["graveyard", pl.graveyard || []], ["exile", pl.exile || []]]) {
        const p = m.piles[zone];
        const from = Math.max(0, list.length - 3);
        for (let k = from; k < list.length; k++) {
          const key = `${zone[0]}:${hdl}:${k}:${list[k]}`;
          const d = k - from;
          T.set(key, { key, name: list[k], seat: hdl, zone, x: p.cx + d * 1.5, y: p.cy - d * 1.5, w: p.w, r: 0,
                       z: zb + 10 + d, face: true });
        }
      }
      const cp = m.piles.command;
      const inCz = (pl.commanders || []).filter((c) => pl.command_zone && pl.command_zone[c]);
      inCz.forEach((c, k) => {
        const key = `cz:${hdl}:${c}`;
        const off = inCz.length > 1 ? (k - 0.5) * cp.w * 0.34 : 0;
        T.set(key, { key, name: c, seat: hdl, zone: "command", x: cp.cx + off, y: cp.cy - off * 0.3, w: cp.w, r: 0,
                     z: zb + 20 + k, face: true, tax: (pl.commander_tax || {})[c] || 0 });
      });
    });
    this.layoutStack(T, state);
    return T;
  }

  layoutHand(T, pl, m, zb) {
    const hand = pl.hand || [];
    const hr = m.hand, n = hand.length;
    if (!n) return;
    const cw = Math.min(hr.h / AR, 118), ch = cw * AR;
    const step = n > 1 ? Math.min(cw + 5, (hr.w - cw - 64) / (n - 1)) : 0;
    const total = step * (n - 1) + cw;
    const x0 = hr.x + cw / 2;
    const seen = {};
    const face = !!S.hands && (!this.viewer || S.showHidden || pl.handle === this.viewer);
    hand.forEach((name, k) => {
      const occ = (seen[name] = (seen[name] ?? -1) + 1);
      const key = `h:${pl.handle}:${name}:${occ}`;
      const tilt = n > 1 ? (k / (n - 1) - 0.5) : 0;
      T.set(key, { key, name, seat: pl.handle, zone: "hand", x: x0 + k * step + Math.max(0, (hr.w - 64 - total) * 0), y: hr.y + hr.h / 2 + (m.orient === "top" ? -1 : 1) * Math.abs(tilt) * 4,
                   w: cw, r: tilt * (m.orient === "top" ? -3 : 3), z: zb + 300 + k, face });
    });
  }

  layoutField(T, pl, m, zb) {
    const F = m.field;
    const bf = pl.battlefield || [];
    if (!bf.length) return;
    const atk = this.attackingIds();
    // group identical permanents into piles: one pass, string signatures
    const groups = new Map();
    const bands = { creature: [], outer: [] };
    const { byHost, attached } = this._att || { byHost: {}, attached: new Set() };
    for (const p of bf) {
      if (attached.has(p.id) && !p.onSeat) continue;
      const c = p.counters, att = byHost[p.id];
      const sig = `${p.name}|${p.tapped ? 1 : 0}|${p.token ? 1 : 0}|${c ? JSON.stringify(c) : ""}|${p.pt ? p.pt[0] + "/" + p.pt[1] : ""}|${p.damage || 0}|${p.mods ? JSON.stringify(p.mods) : ""}|${p.owner !== pl.handle ? p.owner || "" : ""}|${atk.has(p.id) ? 1 : 0}|${att ? att.map((a) => a.p.id).join(",") : ""}`;
      let g = groups.get(sig);
      if (!g) {
        const kind = typeClass(p.name, p);
        g = { perms: [], tapped: !!p.tapped, attacking: atk.has(p.id), kind, att: att || [] };
        groups.set(sig, g);
        (kind === "creature" ? bands.creature : bands.outer).push(g);
      }
      g.perms.push(p);
    }
    // outer band: lands first (by name, untapped before tapped), then everything else
    bands.outer.sort((a, b) => (a.kind === "land" ? 0 : 1) - (b.kind === "land" ? 0 : 1) ||
      (a.kind === "land" ? a.tapped - b.tapped || a.perms[0].name.localeCompare(b.perms[0].name) : 0));
    // land piles fan: each covers the next one's right side, like a held hand
    bands.outer.forEach((g, i) => { const nx = bands.outer[i + 1]; g.fan = g.kind === "land" && nx && nx.kind === "land"; });
    const order = (m.orient === "top" ? ["outer", "creature"] : ["creature", "outer"]).filter((b) => bands[b].length);
    const gapY = 10;
    // crowded rows overlap before they shrink: lands/others show their left (name) side,
    // creatures tuck under their left neighbor so every P/T badge stays in view
    const minStep = { creature: 0.62, outer: 0.5 };
    const depth = (g) => Math.min(g.perms.length, 4) - 1;             // visible pile offsets
    const PEEK = 0.13 * AR;                                   // an attachment shows its name bar past its host
    // a tapped host turns its attachments with it: they fan out sideways, so the slot widens
    const slotW = (g, w) => (g.tapped ? w * AR : w) * (g.fan ? 0.56 : 1) + depth(g) * w * 0.07 + (g.tapped ? g.att.length * PEEK * w : 0);
    const gapX = (w, a, b) => w * (a.fan ? 0.02 : a.kind !== b.kind && (a.kind === "land" || b.kind === "land") ? 0.4 : 0.1);
    // -> lines of groups at card width w; compress packs each line as tight as minStep allows
    const wrap = (band, w, compress) => {
      const lines = [[]];
      let x = 0, prev = null;
      for (const g of bands[band]) {
        const full = slotW(g, w), gp = prev ? gapX(w, prev, g) : 0;
        if (prev && x + gp + full > F.w) { lines.push([]); x = 0; prev = null; }
        x += (prev ? gp : 0) + (compress ? Math.min(full, full * minStep[band] + w * 0.04) : full);
        lines[lines.length - 1].push(g);
        prev = g;
      }
      return lines;
    };
    const attMax = { creature: 0, outer: 0 };                 // untapped hosts' attachments need headroom
    for (const b of ["creature", "outer"]) for (const g of bands[b]) if (!g.tapped) attMax[b] = Math.max(attMax[b], g.att.length);
    const lineH = (w, b) => w * (AR + 0.1) + (b ? attMax[b] * PEEK * w : 0);
    const widthOf = (b, cw, boost) => cw * (b === "creature" ? boost : 0.8);
    const height = (cw, boost, compress) => order.reduce((s, b) => {
      const w = widthOf(b, cw, boost);
      return s + wrap(b, w, compress).length * lineH(w, b);
    }, 0) + gapY * (order.length - 1);
    const fits = (cw, boost, compress) => height(cw, boost, compress) <= F.h;
    const search = (lo, hi, ok) => {
      if (ok(hi)) return hi;
      for (let it = 0; it < 16; it++) { const mid = (lo + hi) / 2; ok(mid) ? (lo = mid) : (hi = mid); }
      return lo;
    };
    const cwMax = Math.min(150, F.h / AR);
    // natural spacing when the board fits it at a decent size, overlap when it doesn't
    let compress = false;
    let cw = search(18, cwMax, (c) => fits(c, 1, false));
    const cwTight = search(18, cwMax, (c) => fits(c, 1, true));
    if (cwTight > cw * 1.12) { compress = true; cw = cwTight; }
    // leftover height goes to the creatures
    const boost = bands.creature.length ? search(1, Math.min(1.8, 170 / cw), (b) => fits(cw, b, compress)) : 1;
    let plan = order.map((b) => ({ b, lines: wrap(b, widthOf(b, cw, boost), compress) }));
    // past even the tightest overlap: share the height and squeeze each line
    if (!fits(cw, boost, compress)) {
      plan = plan.map(({ b }) => {
        const n = Math.max(1, Math.floor((F.h / order.length) / lineH(widthOf(b, cw, boost), b))), gs = bands[b], per = Math.ceil(gs.length / n);
        return { b, lines: Array.from({ length: n }, (_, k) => gs.slice(k * per, (k + 1) * per)).filter((l) => l.length) };
      });
    }
    const alignRight = m.side === "right";
    let y = F.y, zi = zb + 100;
    for (const { b, lines } of plan) {
      const w = widthOf(b, cw, boost), lh = lineH(w, b), head = attMax[b] * PEEK * w;
      for (const line of lines) {
        const widths = line.map((g) => slotW(g, w));
        const gaps = line.map((g, i) => (i ? gapX(w, line[i - 1], g) : 0));
        const natural = widths.reduce((a, c) => a + c, 0) + gaps.reduce((a, c) => a + c, 0);
        const k = natural > F.w ? (F.w - widths[widths.length - 1]) / (natural - widths[widths.length - 1]) : 1;
        const tuck = b === "creature" && k < 1;       // each creature slides under its left neighbor
        let x = alignRight ? F.x + F.w - Math.min(natural, F.w) : F.x;
        const cy = y + head + (lh - head) / 2;
        line.forEach((g, gi) => {
          x += gaps[gi] * k;
          const sw = widths[gi];
          const n = g.perms.length, dmax = depth(g);
          const cx = x + (g.tapped ? w * AR : w) / 2;
          // attackers lunge over their neighbors, so they ride above them
          const zg = zi + (tuck ? line.length - gi : gi) * 5 + (g.attacking ? 600 : 0);
          let ax = 0, ay = 0;
          if (g.attacking) {
            const tgt = this.attackTargetOf(g.perms[0].id);
            const dst = tgt ? this.crestCenter(tgt) : m.center;
            const vx = dst.x - cx, vy = dst.y - cy, L = Math.hypot(vx, vy) || 1;
            ax = vx / L * w * 0.3; ay = vy / L * w * 0.3;
          }
          // only the top of a pile gets sprites; the rest are the count on the badge
          for (let pi = n - 1 - dmax, d = 0; pi < n; pi++, d++) {
            const p = g.perms[pi];
            T.set(p.id, { key: p.id, name: p.name, seat: pl.handle, zone: "battlefield", perm: p,
              x: cx + d * w * 0.07 + ax, y: cy - d * w * 0.07 + ay, w, r: p.tapped ? 90 : 0, z: zg + d,
              face: true, token: !!p.token, badge: pi === n - 1 && n > 1 ? n : 0, attacking: g.attacking,
              stolen: p.owner && p.owner !== pl.handle ? p.owner : null });
          }
          // attachments peek out above their host, name bars stacked; they travel with it
          const host = g.perms[n - 1];
          g.att.forEach((a, j) => {
            const off = (j + 1) * PEEK * w;
            const x = g.tapped ? cx + off : cx + (j + 1) * w * 0.035;
            const y = g.tapped ? cy - (j + 1) * w * 0.035 : cy - off;
            T.set(a.p.id, { key: a.p.id, name: a.p.name, seat: a.seat, zone: "battlefield", perm: a.p,
              x: x + ax, y: y + ay, w, r: g.tapped ? 90 : 0,
              z: zg - 1 - j, face: true, token: !!a.p.token, attacking: g.attacking, attachedTo: host.name,
              stolen: a.seat !== pl.handle ? a.seat : a.p.owner && a.p.owner !== a.seat ? a.p.owner : null });
          });
          x += sw * k;
        });
        zi += line.length * 5 + 5;
        y += lh;
      }
      y += gapY;
    }
  }

  layoutStack(T, state) {
    const st = state.stack || [];
    if (!st.length) return;
    const { W, H } = this.geo;
    const sw = clamp(Math.min(W * 0.15, H * 0.4 / AR), 110, 250);
    const n = st.length, shown = Math.min(n, 8);
    // fanned left to right, oldest to newest; the top of the stack is largest and bottoms line up
    const widths = st.map((_, k) => (k === n - 1 ? sw : sw * 0.72));
    const gap = 16;
    const vis = st.map((_, k) => k >= n - shown);
    const natural = widths.reduce((a, w, k) => a + (vis[k] ? w + (k < n - 1 ? gap : 0) : 0), 0);
    const maxW = W * 0.62;
    const f = natural > maxW ? (maxW - sw) / (natural - sw) : 1;          // squeeze into overlap past maxW
    let cursor = W / 2 - Math.min(natural, maxW) / 2;
    st.forEach((it, k) => {
      const w = widths[k];
      const x = cursor + w / 2;
      if (vis[k] && k < n - 1) cursor += (w + gap) * f;
      const y = H / 2 + (sw - w) * AR / 2;
      const name = it.ability ? (T.get(it.card)?.name || baseName(it.card)) : it.card;
      T.set(`s:${it.sid}`, { key: `s:${it.sid}`, name, seat: it.seat, zone: "stack", item: it, x, y, w,
        r: 0, z: 9000 + k, face: true, ability: it.ability, countered: it.countered, o: vis[k] ? 1 : 0 });
    });
  }

  attackingIds() {
    const s = new Set();
    if (this.combat) for (const a of this.combat.attacks) for (const id of a.ids) s.add(id);
    return s;
  }
  attackTargetOf(id) {
    if (!this.combat) return null;
    const a = this.combat.attacks.find((a) => a.ids.includes(id));
    return a ? a.target : null;
  }

  // ---------------------------------------------------------------- reconcile
  render(state, { animate = true } = {}) {
    const prev = this.state;
    this.state = state;
    if (!this.geo) this.layout();
    const T = this.targets(state);
    const now = performance.now();
    const removed = [];
    for (const [key, sp] of this.sprites) if (!T.has(key) && !sp.dying) removed.push(sp);
    const added = [...T.values()].filter((t) => !this.sprites.has(t.key));
    const pool = new Map();
    for (const sp of removed) {
      const n = baseName(sp.name);
      if (!pool.has(n)) pool.set(n, []);
      pool.get(n).push(sp);
    }
    const libDelta = {};
    if (prev) prev.players.forEach((p, i) => { const q = state.players[i]; if (q) libDelta[q.handle] = q.library - p.library; });
    const PREF = {
      battlefield: { stack: 6, hand: 4, command: 3, graveyard: 3, exile: 3, battlefield: 2 },
      stack: { hand: 6, command: 5, graveyard: 4, exile: 4, battlefield: 1 },
      graveyard: { stack: 6, battlefield: 5, hand: 4, exile: 2 },
      exile: { stack: 6, battlefield: 5, hand: 4, graveyard: 3 },
      hand: { battlefield: 5, stack: 5, graveyard: 4, exile: 4, command: 3 },
      command: { battlefield: 5, stack: 5, graveyard: 4, exile: 4, hand: 3 },
    };
    // arrivals in a stable order: stack first so casts claim hand cards before anything else
    const zoneOrder = { stack: 0, battlefield: 1, graveyard: 2, exile: 2, command: 3, hand: 4, library: 5 };
    added.sort((a, b) => zoneOrder[a.zone] - zoneOrder[b.zone]);
    let stagger = 0;
    for (const t of added) {
      let sp = null;
      const cands = t.zone === "library" || t.ability ? null : pool.get(baseName(t.name));
      if (animate && cands && cands.length) {
        let best = -1, bi = -1;
        cands.forEach((c, i) => {
          const sc = (PREF[t.zone]?.[c.zone] ?? 0) + (c.seat === t.seat ? 1.5 : 0);
          if (sc > best) { best = sc; bi = i; }
        });
        sp = cands.splice(bi, 1)[0];
        removed.splice(removed.indexOf(sp), 1);
        this.sprites.delete(sp.key);
        sp.key = t.key;
        if (baseName(sp.name) !== baseName(t.name)) this.refreshFace(sp, t);
        sp.name = t.name;
        this.sprites.set(t.key, sp);
        this.onMove(sp, t, now);
      } else {
        sp = new Sprite(this.layer, t.key, t.name, { zone: t.zone, seat: t.seat, token: t.token });
        this.sprites.set(t.key, sp);
        if (!animate) sp.snap(t);
        else this.spawn(sp, t, libDelta, now);
      }
      sp.zone = t.zone; sp.seat = t.seat;
      if (animate) { sp.setTarget(t, now, stagger); if (sp.flying) stagger += 22; } else sp.snap(t);
      this.decorate(sp, t);
    }
    for (const [key, sp] of this.sprites) {
      const t = T.get(key);
      if (!t || added.includes(t)) continue;
      if (sp.dying) continue;
      const moved = Math.hypot(t.x - sp.p.x.t, t.y - sp.p.y.t) > 60;
      sp.zone = t.zone; sp.seat = t.seat;
      if (animate) { sp.setTarget(t, now, moved ? stagger : 0); if (moved) stagger += 18; } else sp.snap(t);
      this.decorate(sp, t);
    }
    for (const sp of removed) this.depart(sp, libDelta, animate);
    this.updateMats(state, prev, animate);
    this.stackDim.classList.toggle("on", !!(state.stack && state.stack.length));
    if (this.viewer) this.fillZone();
    this.kick();
  }

  refreshFace(sp, t) {
    const f = cardFace(t.name, { token: t.token });
    f._owner = sp;
    sp.front.replaceWith(f); sp.front = f;
    sp.el.dataset.card = baseName(t.name);
  }

  onMove(sp, t, now) {
    if (t.zone === "stack") sfx("cast", t.seat);
    else if (t.zone === "battlefield" && sp.zone === "stack") sfx("resolve", t.seat);
    else if ((t.zone === "graveyard" || t.zone === "exile") && sp.zone === "battlefield") sfx("die", t.seat);
  }

  spawn(sp, t, libDelta, now) {
    const m = this.geo.mats[t.seat];
    if (t.zone === "library") { sp.snap(t); return; }
    if (t.zone === "stack" && t.ability) {
      const src = this.sprites.get(t.item.card) || [...this.sprites.values()].find((s) => s.zone === "battlefield" && baseName(s.name) === baseName(t.item.card));
      if (src) sp.from(src.p.x.v, src.p.y.v, src.p.s.v * W0 * 0.9, true, 0, 0.2);
      else { const c = this.crestCenter(t.seat); sp.from(c.x, c.y, 40, true, 0, 0); }
      return;
    }
    const lib = m && m.piles.library;
    const fromLib = lib && ((libDelta[t.seat] ?? 0) < 0) && t.zone !== "command" && !t.token;
    if (fromLib) {
      sp.from(lib.cx, lib.cy, lib.w, false);
      if (t.zone === "hand") sfx("draw", t.seat);
      return;
    }
    if (t.zone === "battlefield") sfx(t.token ? "token" : "resolve", t.seat);
    sp.from(t.x, t.y, t.w * 0.2, t.face, t.r, 0);
  }

  depart(sp, libDelta, animate) {
    if (!animate) { sp.el.remove(); this.sprites.delete(sp.key); return; }
    sp.dying = true;
    const up = Object.entries(libDelta).find(([, d]) => d > 0);
    if (up && sp.zone !== "graveyard" && sp.zone !== "exile" && sp.zone !== "library") {
      const lp = this.geo.mats[up[0]].piles.library;
      sp.p.x.t = lp.cx; sp.p.y.t = lp.cy; sp.p.s.t = lp.w / W0; sp.p.f.t = 1; sp.p.r.t = 0; sp.p.lift.vel += 4;
      sp.p.o.t = 0;
      sp.fadeAfter = 0.35;
    } else {
      sp.p.s.t = sp.p.s.v * (sp.zone === "battlefield" ? 0.6 : 0.9);
      sp.p.o.t = 0;
      if (sp.zone === "battlefield") sp.el.classList.add("vanish");
    }
    sp.flying = true;
    this.sprites.delete(sp.key);
    this.sprites.set(sp.key + "~" + Math.random().toString(36).slice(2, 7), sp);
  }

  // overlays: P/T, counters, pile count, damage, stolen marker, attack state
  decorate(sp, t) {
    const p = t.perm;
    const c = p ? p.counters || {} : {};
    const pt = ptNow(p);
    const temp = !!(p && p.mods && p.mods.length);
    const printed = info(t.name);
    const sig = JSON.stringify([pt, temp, c, t.badge, p && p.damage, t.stolen, t.attachedTo, t.zone === "library" ? t.depth : 0, t.tax,
      p && p.sick, t.countered, t.attacking, t.ability]);
    sp.el.classList.toggle("tapped", !!(p && p.tapped));
    if (sig === sp.overSig) return;
    sp.overSig = sig;
    sp.el._perm = p || null;
    sp.el._attachedTo = t.attachedTo || null;
    const kids = [];
    const ptStr = pt ? `${pt[0]}/${pt[1]}/${(p && p.damage) || 0}` : null;
    const bump = sp.lastPT != null && ptStr && ptStr !== sp.lastPT;
    sp.lastPT = ptStr;
    if (pt) {
      const buffed = printed && printed.pt ? Math.sign(pt[0] + pt[1] - printed.pt[0] - printed.pt[1]) : 0;
      kids.push(h("div.pt" + (buffed > 0 ? ".up" : buffed < 0 ? ".down" : "") + (bump ? ".bump" : "") + (temp ? ".temp" : ""),
        temp ? icon("hourglass-split") : null, `${pt[0]}/`, h("span.tough" + (p.damage ? ".hurt" : ""), pt[1] - (p.damage || 0))));
    }
    const ck = Object.entries(c).filter(([k]) => k !== "+1/+1" && k !== "-1/-1");
    if (ck.length) kids.push(h("div.ctrs", ck.slice(0, 3).map(([k, v]) => h("span", `${k.replace(/ counters?$/, "")} ${v}`))));
    if (t.badge) kids.push(h("div.count", `×${fmtN(t.badge)}`));
    if (t.tax) kids.push(h("div.count.tax", `+${t.tax}`));
    if (t.stolen) kids.push(h("div.stolen", { style: { "--owner": seatColor(t.stolen) } }));
    if (t.ability) kids.push(h("div.abil", "ability"));
    if (t.countered) kids.push(h("div.countered", icon("slash-circle")));
    sp.over.replaceChildren(...kids);
    sp.el.classList.toggle("sick", !!(p && p.sick));
    sp.el.classList.toggle("attacking", !!t.attacking);
    sp.el.classList.toggle("on-stack", t.zone === "stack");
    sp.el.classList.toggle("ability", !!t.ability);
    sp.el.classList.toggle("is-countered", !!t.countered);
    sp.el.classList.toggle("in-hand", t.zone === "hand");
    if (t.zone === "stack") sp.el.style.setProperty("--seat", seatColor(t.seat));
  }

  updateMats(state, prev, animate) {
    for (const pl of state.players) {
      const me = this.matEls[pl.handle];
      if (!me) continue;
      me.el.classList.toggle("active", state.active === pl.handle);
      me.el.classList.toggle("dead", !pl.alive);
      me.piles.library.label.querySelector(".n").textContent = pl.library;
      me.piles.graveyard.label.querySelector(".n").textContent = (pl.graveyard || []).length || "";
      me.piles.exile.label.querySelector(".n").textContent = (pl.exile || []).length || "";
      // casts from the command zone, read off the tax the engine keeps (+2 per cast)
      const tax = pl.commander_tax || {};
      const cz = (pl.commanders || []).map((c) => ({ c, n: Math.round((tax[c] || 0) / 2), t: tax[c] || 0 }));
      const czLbl = me.piles.command.label;
      czLbl.querySelector(".n").textContent = cz.some((x) => x.n) ? cz.map((x) => `cast ${x.n} · +${x.t}`).join("\n") : "";
      czLbl.title = cz.map((x) => `${x.c}: cast ${x.n} time${x.n === 1 ? "" : "s"} from the command zone, next cast costs {${x.t}} more`).join("\n");
      me.handLbl.querySelector(".n").textContent = (pl.hand || []).length;
      me.counters.replaceChildren(...Object.entries(pl.counters || {}).map(([k, v]) =>
        h("span.pc", { dataset: { kind: k } }, h("b", v), " ", k)));
      const old = prev && prev.players.find((p) => p.handle === pl.handle);
      if (old && old.life !== pl.life && animate) {
        const d = pl.life - old.life;
        const mg = this.geo.mats[pl.handle];
        const at = { x: mg.side === "left" ? mg.crest.x + mg.crest.w + 34 : mg.crest.x - 34, y: mg.crest.y + mg.crest.h - 30 };
        fx.floatText(this.fxl, at, (d > 0 ? "+" : "") + d, d > 0 ? "gain" : "loss", "bottom");
        me.crest.classList.remove("hit", "heal"); void me.crest.offsetWidth;
        me.crest.classList.add(d < 0 ? "hit" : "heal");
        sfx(d < 0 ? "damage" : "gain", pl.handle, Math.abs(d));
        this.tweenLife(me, pl.life);
      } else if (!animate || me.lifeShown !== pl.life && !me.lifeAnim) {
        me.lifeShown = pl.life; me.life.textContent = pl.life; cancelAnimationFrame(me.lifeAnim); me.lifeAnim = null;
      }
      me.life.classList.toggle("low", pl.life <= 10);
      if (!pl.alive && old && old.alive && animate) sfx("elim", pl.handle);
    }
  }

  tweenLife(me, to) {
    cancelAnimationFrame(me.lifeAnim);
    const from = me.lifeShown, t0 = performance.now(), dur = clamp(Math.abs(to - from) * 60, 250, 900) / this.timeScale;
    const step = (t) => {
      const k = clamp((t - t0) / dur, 0, 1);
      const v = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3)));
      me.life.textContent = v;
      me.lifeShown = v;
      me.lifeAnim = k < 1 ? requestAnimationFrame(step) : null;
    };
    me.lifeAnim = requestAnimationFrame(step);
  }

  // ---------------------------------------------------------------- frame loop
  kick() {
    if (this.running) return;
    this.running = true;
    this.last = performance.now();
    requestAnimationFrame((t) => this.frame(t));
  }

  frame(now) {
    if (!this.running) return;
    const dt = Math.min(0.034, (now - this.last) / 1000) * clamp(this.timeScale, 0.5, 3.5);
    this.last = now;
    let busy = false;
    const k = 190 * (S.motion || 1), c = 2 * Math.sqrt(k) * 0.82;
    for (const [key, sp] of this.sprites) {
      if (sp.delay > now) { busy = true; continue; }
      let moving = false;
      for (const [name, s] of Object.entries(sp.p)) {
        const kk = name === "lift" ? 60 : name === "f" ? 150 : name === "o" ? 120 : k;
        const cc = name === "lift" ? 2 * Math.sqrt(60) * 0.72 : name === "o" ? 2 * Math.sqrt(120) : c;
        if (s.step(dt, kk, cc)) moving = true;
      }
      if (sp.fadeAfter != null && Math.hypot(sp.p.x.t - sp.p.x.v, sp.p.y.t - sp.p.y.v) > 30) sp.p.o.v = Math.max(sp.p.o.v, 1);
      sp.draw();
      if (!moving) sp.flying = false;
      if (sp.dying && sp.p.o.v < 0.03) { sp.el.remove(); this.sprites.delete(key); continue; }
      if (moving) busy = true;
      sp.el.classList.toggle("moving", moving);
    }
    if (this.arrows.length) { this.drawArrows(now); busy = true; }
    if (busy) requestAnimationFrame((t) => this.frame(t));
    else this.running = false;
  }

  // ---------------------------------------------------------------- arrows
  // the sprite a reference names: a permanent id, a bare "#N", a name on the battlefield, or "stack#N"
  spriteOf(ref) {
    const r = String(ref).trim();
    let sp = r.startsWith("stack#") ? this.sprites.get("s:" + r) : this.sprites.get(r);
    if (!sp && /^\d+$/.test(r)) sp = [...this.sprites.values()].find((s) => s.key.endsWith("#" + r));
    if (!sp && !r.startsWith("stack#")) sp = [...this.sprites.values()].find((s) => s.zone === "battlefield" && baseName(s.name) === baseName(r));
    return sp && !sp.dying ? sp : null;
  }

  // -> {x, y} plus the shape to stop at: {hw, hh} for a card, {r} for a player's crest
  posOf(ref) {
    if (!ref) return null;
    const r = String(ref).trim();
    const hd = r.match(/^(P\d)(\(|$)/);
    if (hd) { const m = this.geo.mats[hd[1]]; return m ? { ...this.crestCenter(hd[1]), r: Math.min(m.crest.w, m.crest.h) / 2 + 4 } : null; }
    const sp = this.spriteOf(r);
    if (!sp) return null;
    const w = sp.p.s.v * W0, h = w * AR, side = Math.abs(((sp.p.r.v % 180) + 180) % 180 - 90) < 45;
    return { x: sp.p.x.v, y: sp.p.y.v, hw: (side ? h : w) / 2, hh: (side ? w : h) / 2, stack: sp.zone === "stack" };
  }

  setArrows(list) {
    // list: [{from: ref|fn, to: ref|fn, color, kind}]
    const now = performance.now();
    const keep = new Map(this.arrows.map((a) => [a.id, a]));
    const next = [];
    for (const a of list) {
      const id = `${a.kind}:${a.from}>${a.to}`;
      const old = keep.get(id);
      if (old) { keep.delete(id); old.depth = a.depth; next.push(old); continue; }
      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.classList.add("arrow", ...a.kind.split(" "));
      g.style.setProperty("--c", a.color);
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      const head = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.classList.add("shaft"); head.classList.add("head");
      g.append(path, head);
      this.svg.append(g);
      next.push({ ...a, id, el: g, path, head, born: now });
    }
    for (const a of keep.values()) { a.el.classList.add("gone"); setTimeout(() => a.el.remove(), 400); }
    this.arrows = next;
    if (next.length) this.kick();
  }

  drawArrows(now) {
    // where a ray from a shape's center toward (tx, ty) leaves the shape
    const exit = (b, tx, ty, pad) => {
      const dx = tx - b.x, dy = ty - b.y, L = Math.hypot(dx, dy) || 1;
      if (b.hw) { const k = Math.min(b.hw / (Math.abs(dx) || 1e-6), b.hh / (Math.abs(dy) || 1e-6)); return { x: b.x + dx * k + dx / L * pad, y: b.y + dy * k + dy / L * pad }; }
      const rr = (b.r || 16) + pad;
      return { x: b.x + dx / L * rr, y: b.y + dy / L * rr };
    };
    for (const a of this.arrows) {
      const p = this.posOf(a.from), q = this.posOf(a.to);
      if (!p || !q) { a.path.setAttribute("d", ""); a.head.setAttribute("d", ""); continue; }
      const dx = q.x - p.x, dy = q.y - p.y, L = Math.hypot(dx, dy) || 1;
      let cx, cy;
      if (p.stack && q.stack) {                       // spell at spell: arc over the fan
        cx = (p.x + q.x) / 2; cy = Math.min(p.y - p.hh, q.y - q.hh) - Math.max(70, L * 0.45);
      } else {
        const bend = Math.min(90, L * 0.22) * (a.kind === "block" ? -1 : 1);
        cx = (p.x + q.x) / 2 - dy / L * bend; cy = (p.y + q.y) / 2 + dx / L * bend;
      }
      const s0 = exit(p, cx, cy, 2), e = exit(q, cx, cy, 5);
      const ang = Math.atan2(e.y - cy, e.x - cx);
      const hl = 12, hw = 0.5;
      const head = `M${(e.x + Math.cos(ang) * 4).toFixed(1)},${(e.y + Math.sin(ang) * 4).toFixed(1)} L${(e.x - Math.cos(ang - hw) * hl).toFixed(1)},${(e.y - Math.sin(ang - hw) * hl).toFixed(1)} L${(e.x - Math.cos(ang + hw) * hl).toFixed(1)},${(e.y - Math.sin(ang + hw) * hl).toFixed(1)} Z`;
      a.path.setAttribute("d", `M${s0.x.toFixed(1)},${s0.y.toFixed(1)} Q${cx.toFixed(1)},${cy.toFixed(1)} ${e.x.toFixed(1)},${e.y.toFixed(1)}`);
      a.head.setAttribute("d", head);
    }
  }

  refreshArrows() {
    const st = this.state;
    const list = [];
    const stack = (st && st.stack) || [];
    stack.forEach((it, k) => {
      const behind = this.copyAim && this.copyAim.sid === it.sid ? 1 : 0;     // its copy's aim is the live one
      for (const t of it.targets || []) list.push({ from: it.sid, to: t, color: seatColor(it.seat), kind: "target", depth: stack.length - 1 - k + behind });
    });
    if (this.copyAim) for (const t of this.copyAim.targets) list.push({ from: this.copyAim.sid, to: t, color: seatColor(this.copyAim.seat), kind: "target copy" });
    if (this.combat) {
      for (const a of this.combat.attacks) for (const id of a.ids) list.push({ from: id, to: a.target, color: seatColor(a.seat), kind: "attack" });
      for (const [atk, blockers] of Object.entries(this.combat.blocks || {}))
        for (const b of [].concat(blockers || [])) list.push({ from: b, to: atk, color: seatColor(this.combat.defenderOf?.[atk] || ""), kind: "block" });
    }
    this.setArrows(list);
    for (const a of this.arrows) a.el.classList.toggle("deep", (a.depth || 0) > 0);
    // what's being aimed at wears a dashed ring in the caster's color
    this.el.querySelectorAll(".targeted").forEach((e) => e.classList.remove("targeted"));
    for (const a of list) {
      if (!a.kind.startsWith("target")) continue;
      const hd = String(a.to).match(/^(P\d)(\(|$)/);
      const el = hd ? this.matEls[hd[1]]?.crest : this.spriteOf(a.to)?.el;
      if (!el) continue;
      if (!el.classList.contains("targeted") || !a.depth) el.style.setProperty("--tc", a.color);
      el.classList.add("targeted");
    }
  }

  // ---------------------------------------------------------------- intent
  // ev: compiled event (already rendered). Adds the non-state layer.
  play(ev) {
    const seat = ev.seat;
    // a copy's new targets hold while its own ↳ effects play out, then give way
    if (ev.kind === "copy") this.copyAim = ev.targets.length ? { sid: ev.sid, targets: ev.targets, seat } : null;
    else if (!/^\s*↳/.test(ev.line || "")) this.copyAim = null;
    switch (ev.kind) {
      case "says": this.bubble(seat, ev.text, "say"); break;
      case "thinks": if (S.thoughts && (!this.viewer || S.showHidden || seat === this.viewer)) this.bubble(seat, ev.text, "think"); break;
      case "turn":
        this.combat = null;
        fx.banner(this.fxl, { turn: ev.turn, seat, deck: this.seatOf(seat)?.deck, commander: this.seatOf(seat)?.commanders?.[0] });
        sfx("turn", seat);
        this.clearBubbles();
        break;
      case "attack": {
        this.combat = this.combat && !this.combat.resolved ? this.combat : { attacks: [], blocks: {}, defenderOf: {} };
        this.combat.attacks.push({ seat, target: ev.target, ids: ev.ids });
        for (const id of ev.ids) this.combat.defenderOf[id] = ev.target;
        sfx("attack", seat);
        this.render(this.state);
        break;
      }
      case "block":
        if (this.combat) Object.assign(this.combat.blocks, ev.blocks || {});
        if (Object.keys(ev.blocks || {}).length) sfx("block", seat);
        break;
      case "combat":
        if (this.combat) this.combat.resolved = true;
        sfx("combat", this.state.active);
        break;
      case "land": case "announce": case "activate":
        if (this.combat && this.combat.resolved) { this.combat = null; this.render(this.state); }
        if (ev.kind === "land") sfx("land", seat);
        if (ev.kind === "activate") this.pulse(ev.card, seat);
        break;
      case "elim": {
        const me = this.matEls[seat];
        if (me) me.el.querySelector(".elim-how").textContent = ev.text;
        break;
      }
      case "reveal":
        fx.reveal(this.fxl, this.geo, ev.cards, seat);
        sfx("reveal", seat);
        break;
      case "coin": case "roll":
        fx.dice(this.fxl, this.geo, ev.kind === "coin" ? ev.result.toLowerCase() : `d${ev.sides}: ${ev.result}`);
        sfx("coin");
        break;
      case "countered": case "fizzle": sfx("counter", seat); break;
      case "judge": fx.judge(this.fxl, ev.text, ev.ruling); sfx("judge"); break;
      case "correct": this.bubble(seat, ev.text, "correct"); break;
      case "offer": this.bubble(seat, ev.text, "say"); break;
      case "answer": this.bubble(seat, ev.text, "answer"); break;
    }
    this.refreshArrows();
  }

  seatOf(hdl) { return this.seats.find((s) => s.handle === hdl); }

  pulse(ref, seat) {
    const r = String(ref);
    const sp = this.sprites.get(r) || [...this.sprites.values()].find((s) => s.zone === "battlefield" && s.seat === seat && baseName(s.name) === baseName(r));
    if (!sp) return;
    sp.el.classList.remove("pulse"); void sp.el.offsetWidth; sp.el.classList.add("pulse");
    sp.el.style.setProperty("--seat", seatColor(seat));
    sp.p.lift.vel += 3; this.kick();
  }

  bubble(seat, text, kind) {
    const slot = this.geo && this.geo.slots[seat];
    if (!slot || !text) return;
    fx.bubble(this.fxl, this.cols[slot.col], seat, slot, text, kind, seatColor(seat));
    if (kind === "say") sfx("talk", seat);
  }
  clearBubbles() { fx.clearBubbles(this.fxl); }

  // reconstruct non-state intent after a seek: combat marks since the turn began
  restoreIntent(events, idx) {
    this.combat = null;
    this.copyAim = null;
    for (let k = idx; k >= 0; k--) {
      const e = events[k];
      if (e.kind === "copy") { this.copyAim = e.targets.length ? { sid: e.sid, targets: e.targets, seat: e.seat } : null; break; }
      if (!/^\s*↳/.test(e.line || "")) break;
    }
    for (let k = idx; k >= 0; k--) {
      const e = events[k];
      if (e.kind === "turn") break;
      if (e.kind === "attack" || e.kind === "block" || e.kind === "combat") {
        let j = k;
        while (j > 0 && events[j - 1].kind !== "turn" && !["land", "announce", "activate"].includes(events[j - 1].kind)) j--;
        const c = { attacks: [], blocks: {}, defenderOf: {} };
        for (let q = j; q <= idx; q++) {
          const x = events[q];
          if (x.kind === "attack") { c.attacks.push({ seat: x.seat, target: x.target, ids: x.ids }); x.ids.forEach((id) => (c.defenderOf[id] = x.target)); }
          if (x.kind === "block") Object.assign(c.blocks, x.blocks || {});
          if (x.kind === "combat") c.resolved = true;
          if (["land", "announce", "activate"].includes(x.kind) && c.resolved) { c.attacks = []; }
        }
        this.combat = c.attacks.length ? c : null;
        break;
      }
      if (["land", "announce", "activate"].includes(e.kind)) break;
    }
    fx.clearAll(this.fxl);
    this.render(this.state, { animate: false });
    this.refreshArrows();
  }
}
