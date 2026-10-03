// Settings: launch defaults (server-side, apply to every new game) and viewer preferences (this browser).
import { h, api, S } from "./util.js";
import { BOOT } from "./main.js";

export async function settingsScreen(root) {
  const cfg = await api("settings");
  const kinds = BOOT.agents.map((a) => a.kind).filter((k) => k !== "human");
  let t = null;
  const save = (el) => {
    clearTimeout(t);
    t = setTimeout(async () => {
      Object.assign(BOOT.settings, await api("settings", { method: "POST", body: cfg }));
      el.classList.remove("saved"); void el.offsetWidth; el.classList.add("saved");
    }, 250);
  };
  const row = (label, control, hint) => h("div.set-row", h("div.set-l", label, hint ? h("div.set-hint", hint) : null), control);
  const text = (get, set, ph = "") => {
    const el = h("input.set-in", { value: get() ?? "", placeholder: ph, oninput: (e) => { set(e.target.value.trim()); save(el); } });
    return el;
  };
  const num = (key, min, max) => {
    const el = h("input.set-in.num", { type: "number", min, max, value: cfg[key], oninput: (e) => { cfg[key] = +e.target.value || cfg[key]; save(el); } });
    return el;
  };
  const choice = (key, opts) => {
    const el = h("div.seg", opts.map((o) => h("button.seg-b" + (cfg[key] === o ? ".on" : ""), {
      onclick: () => { cfg[key] = o; el.querySelectorAll(".seg-b").forEach((b) => b.classList.toggle("on", b.textContent === (o || "default"))); save(el); },
    }, o || "default")));
    return el;
  };
  const local = (key, label, type, attrs = {}) => row(label, type === "check"
    ? h("input.set-check", { type: "checkbox", checked: !!S[key], onchange: (e) => (S[key] = e.target.checked) })
    : h("input.set-range", { type: "range", ...attrs, value: S[key], oninput: (e) => (S[key] = +e.target.value) }));

  root.replaceChildren(h("div.settings",
    h("section",
      h("h3", "agents"),
      row("new seats play as", choice("agent", kinds)),
      row("your scribe", choice("scribe", kinds), "turns your words into plays when you take a seat"),
      ...kinds.map((k) => row(`${k} model`, text(() => cfg.models[k], (v) => (cfg.models[k] = v),
        ""),
        k === "codex" ? "also the judge" : null)),
      row("codex effort", choice("codex_effort", ["", "minimal", "low", "medium", "high"])),
      row("codex tier", choice("codex_tier", ["", "fast", "priority", "flex"]))),
    h("section",
      h("h3", "games"),
      row("turn cap", num("max_turns", 1, 99)),
      row("actions per turn", num("max_actions", 5, 999))),
    h("section",
      h("h3", "viewer"),
      local("thoughts", "private thoughts", "check"),
      local("hands", "show hands", "check"),
      local("voice", "voice table talk", "check"),
      local("sound", "sound", "range", { min: 0, max: 1, step: 0.05 }),
      local("readSpeed", "reading speed", "range", { min: 0.4, max: 2.5, step: 0.1 }),
      local("motion", "motion snap", "range", { min: 0.5, max: 2.5, step: 0.1 }))));
}
