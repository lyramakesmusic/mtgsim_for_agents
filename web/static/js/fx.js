// Transient overlays on the table: speech, banners, floating numbers, reveals, rulings.
import { h, seatColor, clamp } from "./util.js";
import { cardFace, artCropStyle } from "./cards.js";

let scale = 1;
export const setScale = (s) => (scale = s || 1);
const later = (el, ms, cls = "out", after = 320) => {
  el._t = setTimeout(() => { el.classList.add(cls); setTimeout(() => el.remove(), after); }, ms / scale);
};

// one bubble per seat, in its column of the talk band: top seats hang from the band's top edge,
// bottom seats rise from its bottom edge, each on its own side of the table
function makeBubble(col, seat, slot, kind, color) {
  const layer = col.parentNode;
  const old = layer.querySelector(`.bubble[data-seat="${seat}"]`);
  if (old) { clearTimeout(old._t); old.remove(); }
  const el = h("div.bubble." + kind, { dataset: { seat, pos: slot.pos, side: slot.side }, style: { "--seat": color } });
  col.append(el);
  return el;
}

export function bubble(layer, col, seat, slot, text, kind, color) {
  const el = makeBubble(col, seat, slot, kind, color);
  el.append(h("div.bubble-text", text));
  const ms = kind === "think" ? 1400 + text.length * 38 : 1800 + text.length * 48;
  later(el, clamp(ms, 2200, 14000));
}

// a thinking mark for the seat the live game is waiting on; null clears it
export function waiting(layer, col, seat, slot, color) {
  layer.querySelectorAll(".bubble.wait").forEach((b) => { if (b.dataset.seat !== seat) b.remove(); });
  if (!col || layer.querySelector(`.bubble[data-seat="${seat}"]`)) return;
  const el = makeBubble(col, seat, slot, "think wait", color);
  el.append(h("span.typing", h("i"), h("i"), h("i")));
}
export const clearBubbles = (layer) => layer.querySelectorAll(".bubble").forEach((b) => { clearTimeout(b._t); b.remove(); });

export function banner(layer, { turn, seat, deck, commander }) {
  layer.querySelectorAll(".banner").forEach((b) => b.remove());
  const el = h("div.banner", { style: { "--seat": seatColor(seat) } },
    h("div.banner-art", { style: artCropStyle(commander || "") }),
    h("div.banner-body",
      h("div.banner-turn", `Turn ${turn}`),
      h("div.banner-name", commander || deck || seat),
      h("div.banner-seat", `${seat} · ${deck || ""}`)));
  layer.append(el);
  later(el, 1500, "out", 500);
}

export function floatText(layer, at, text, cls, orient) {
  const el = h("div.float." + cls, text);
  Object.assign(el.style, { left: at.x + "px", top: at.y + "px" });
  el.style.setProperty("--dir", orient === "top" ? 1 : -1);
  el.style.animationDuration = 1400 / scale + "ms";
  layer.append(el);
  setTimeout(() => el.remove(), 1450 / scale);
}

export function reveal(layer, geo, cards, seat) {
  layer.querySelectorAll(".reveal").forEach((b) => b.remove());
  const n = cards.length;
  const cw = clamp(Math.min(geo.W * 0.6 / Math.max(n, 1), 150), 60, 150);
  const el = h("div.reveal", { style: { "--seat": seatColor(seat), "--cw": cw + "px" } },
    h("div.reveal-row", cards.map((c, i) => h("div.reveal-card", { dataset: { card: c }, style: { animationDelay: i * 60 / scale + "ms" } }, cardFace(c)))));
  layer.append(el);
  later(el, 1800 + n * 250, "out", 400);
}

export function dice(layer, geo, text) {
  const el = h("div.dice", text);
  layer.append(el);
  later(el, 1600);
}

export function judge(layer, text, ruling) {
  layer.querySelectorAll(".judge").forEach((b) => b.remove());
  const el = h("div.judge", h("div.judge-head", ruling ? "judge ruling" : "judge"), h("div.judge-text", text));
  layer.append(el);
  later(el, clamp(2500 + text.length * 45, 3000, 20000), "out", 400);
}

export const clearAll = (layer) => {
  for (const el of [...layer.children]) {
    if (el.classList.contains("talk-col")) el.replaceChildren();
    else el.remove();
  }
};
