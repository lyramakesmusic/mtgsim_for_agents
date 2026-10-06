// Plan-limit bars for Claude and Codex: how much of each window is left, draining as it's used.
import { h, api } from "./util.js";

let latest = null, timer = null;
const views = new Set();

const until = (t) => {
  const s = Math.max(0, t - Date.now() / 1000);
  const d = Math.floor(s / 86400), hh = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${hh}h` : hh ? `${hh}h ${m}m` : `${m}m`;
};

function render(el) {
  if (!latest) return;
  const groups = [];
  for (const [who, wins] of Object.entries(latest)) {
    if (!wins.length) continue;
    groups.push(h("div.ub-group", h("span.ub-who", who),
      wins.map((w) => {
        const left = Math.max(0, 100 - (w.used || 0));
        return h("div.ub-win" + (left < 8 ? ".crit" : left < 20 ? ".low" : ""),
          { title: `${who} ${w.name}: ${Math.round(w.used)}% used, ${Math.round(left)}% left · resets in ${until(w.resets_at)}` },
          h("span.ub-name", w.name), h("div.ub-bar", h("div.ub-fill", { style: { width: left + "%" } })));
      })));
  }
  el.replaceChildren(...groups);
}

async function poll() {
  try { latest = await api("usage", { quiet: true }); } catch { return; }   // the public port shows no bars
  for (const el of views) el.isConnected ? render(el) : views.delete(el);
}

// -> Element that stays current while it's on the page
export function usageBars() {
  const el = h("div.usage");
  views.add(el);
  if (latest) render(el);
  if (!timer) { poll(); timer = setInterval(poll, 30000); }
  return el;
}
