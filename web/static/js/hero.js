// The wide art banner at the top of a screen: slanted art slices, a title, and actions.
import { h } from "./util.js";
import { artCropStyle } from "./cards.js";

// slices: [{card, label, sub, color}]; actions: [Element]
export function hero({ slices, title, actions, body, cls = "" }) {
  const art = h("div.hero-arts", slices.map((s) => h("div.hero-art", { style: artCropStyle(s.card || "") })));
  const tags = h("div.hero-tags", slices.map((s) => h("div.hero-slot",
    s.label ? h("div.hero-tag", h("b", { style: s.color ? { color: s.color } : {} }, s.label), s.sub ? h("span.hero-agent", s.sub) : null) : null)));
  return h("div.hero" + (cls ? "." + cls : ""), art, tags,
    body || h("div.hero-body", h("div.hero-title", title), h("div.hero-actions", actions)));
}
