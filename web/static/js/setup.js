// New game: an options row, then the pod itself as a banner of seats. Each slice is a seat:
// click its art to change deck; agent and model sit on the slice.
import { h, icon, api, seatColor, store, modal } from "./util.js";
import { artCropStyle, manaCost } from "./cards.js";
import { go, BOOT } from "./main.js";
import { sfx } from "./sfx.js";

export const colorPips = (colors) => colors ? manaCost([...colors].join(""), "ci") : manaCost("C", "ci");

// -> Element. decks: /api/decks summaries. seed: optional deck name for seat 1.
export function podEditor(decks, { seed } = {}) {
  const byName = Object.fromEntries(decks.map((d) => [d.name, d]));
  const agents = BOOT.agents;
  const D = BOOT.settings;
  const saved = store("pod");
  const pick = () => decks[Math.floor(Math.random() * decks.length)].name;
  // four fixed slots; an empty slot is null and keeps its place
  const fresh = () => ({ deck: pick(), agent: D.agent, model: "" });
  const slots = [0, 1, 2, 3].map((k) => {
    const s = saved && saved.seats && saved.seats[k];
    return s && byName[s.deck] ? s : saved && saved.seats ? null : fresh();
  });
  for (let k = 0; slots.filter(Boolean).length < 2; k++) if (!slots[k]) slots[k] = fresh();
  if (seed && byName[seed]) slots[slots.findIndex(Boolean)].deck = seed;
  const seated = () => slots.filter(Boolean);
  const opts = { seed: "", max_turns: "", max_actions: "", mock: false, title: "", ...(saved && saved.opts) };
  delete opts.codex_effort; delete opts.codex_tier;          // those are global settings
  const persist = () => store("pod", { seats: slots, opts });

  const err = h("span.err.ng-err");
  const startBtn = h("button.btn.big", { onclick: start }, icon("play-fill"), " start");
  const field = (key, label, attrs = {}) => h("label.nf", h("span", label),
    h("input", { value: opts[key] ?? "", ...attrs, oninput: (e) => { opts[key] = attrs.type === "number" ? (e.target.value === "" ? "" : +e.target.value) : e.target.value; persist(); } }));
  const bar = h("div.ng-bar",
    h("div.ng-title", "new game"),
    h("div.grow"),
    field("title", "title", { placeholder: "", class: "wide" }),
    field("seed", "seed", { type: "number", placeholder: "random" }),
    field("max_turns", "turn cap", { type: "number", min: 1, max: 99, placeholder: D.max_turns }),
    field("max_actions", "actions", { type: "number", min: 5, max: 999, placeholder: D.max_actions }),
    h("label.nf.check", h("input", { type: "checkbox", checked: opts.mock, onchange: (e) => { opts.mock = e.target.checked; persist(); } }), h("span", "mock")),
    err,
    h("button.btn.ghost.big.ng-shuffle", { title: "shuffle decks", onclick: () => {
      const pool = decks.map((d) => d.name).sort(() => Math.random() - 0.5);
      seated().forEach((s, k) => (s.deck = pool[k])); draw();
    } }, icon("shuffle")),
    startBtn);

  const pod = h("div.pod-hero");
  function slice(s, k) {
    if (!s) return h("button.pod-seat.pod-empty", { title: "add seat", onclick: () => deckPicker(decks, null, (n) => {
      slots[k] = { deck: n, agent: (seated()[0] || {}).agent || D.agent, model: "" }; draw();
    }) },
      h("span.pod-plus", icon("plus-lg")));
    const d = byName[s.deck], hdl = `P${seated().indexOf(s) + 1}`;
    const stop = (e) => e.stopPropagation();
    const tag = h("div.pod-tag");
    const fillTag = (dd) => tag.replaceChildren(h("b", dd.name), h("div.pod-cmdr", dd.commanders.join(" & ")), h("span.pod-pips", colorPips(dd.colors)));
    fillTag(d);
    const reel = h("div.pod-reel", h("div.pod-art"), h("div.pod-art"));
    const el = h("div.pod-seat", { style: { "--seat": seatColor(hdl) }, onclick: () => deckPicker(decks, s.deck, (n) => { s.deck = n; draw(); }) },
      reel,
      h("div.pod-top",
        seated().length > 2 ? h("button.pod-x", { title: "remove seat", onclick: (e) => { stop(e); slots[k] = null; draw(); } }, icon("x-lg")) : h("span"),
        tag),
      h("div.pod-ctl", { onclick: stop },
        h("div.seg.small", agents.map((a) => h("button.seg-b" + (s.agent === a.kind ? ".on" : "") + (a.available ? "" : ".na"),
          { title: a.kind === "human" ? "you play this seat" : a.available ? a.kind : `${a.kind} (not configured)`, onclick: () => { s.agent = a.kind; draw(); } },
          a.kind === "human" ? icon("person-fill") : a.kind))),
        s.agent === "human" ? h("div.pod-you", "you") : h("input.pod-model", { placeholder: D.models[s.agent] || "model", value: s.model || "",
          oninput: (e) => { s.model = e.target.value; persist(); } })));
    spinner(el, reel, s, fillTag);
    return el;
  }

  // slot-machine reel: the art is a vertical strip whose position (in decks) springs toward a target
  // that each wheel notch moves by one; it overshoots a hair and settles, ticking as decks pass
  function spinner(el, reel, s, fillTag) {
    const n = decks.length, mod = (i) => ((i % n) + n) % n;
    const idx = decks.findIndex((d) => d.name === s.deck);
    const st = { pos: idx, target: idx, vel: 0, shown: idx, raf: 0, acc: 0 };
    const [a, b] = reel.children;
    const paint = () => {
      const i0 = Math.floor(st.pos), f = st.pos - i0, H = reel.clientHeight || 250;
      Object.assign(a.style, artCropStyle(byName[decks[mod(i0)].name].commanders[0]));
      Object.assign(b.style, artCropStyle(byName[decks[mod(i0 + 1)].name].commanders[0]));
      a.style.transform = `translate3d(0, ${(-f * H).toFixed(1)}px, 0)`;
      b.style.transform = `translate3d(0, ${((1 - f) * H).toFixed(1)}px, 0)`;
      reel.style.setProperty("--blur", Math.min(3, Math.abs(st.vel) * 0.18).toFixed(2) + "px");
      const near = Math.round(st.pos);
      if (near !== st.shown) {
        st.shown = near;
        fillTag(byName[decks[mod(near)].name]);
        tagRoll(el);
        sfx("tick");
      }
    };
    let last = 0;
    const frame = (t) => {
      const dt = Math.min(0.032, (t - (last || t)) / 1000) || 0.016;
      last = t;
      const k = 240, c = 2 * Math.sqrt(k) * 0.66;             // a little under critical: overshoot, then settle
      st.vel += (k * (st.target - st.pos) - c * st.vel) * dt;
      st.pos += st.vel * dt;
      if (Math.abs(st.target - st.pos) < 0.001 && Math.abs(st.vel) < 0.01) {
        st.pos = st.target; st.vel = 0; paint(); st.raf = 0; last = 0;
        reel.classList.remove("spinning");
        s.deck = decks[mod(st.target)].name;
        persist();
        return;
      }
      paint();
      st.raf = requestAnimationFrame(frame);
    };
    el.addEventListener("wheel", (e) => {
      e.preventDefault();
      if (e.target.closest(".pod-ctl")) return;
      st.acc += Math.abs(e.deltaY) > Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
      if (Math.abs(st.acc) < 40) return;                      // trackpads send many small deltas; one notch is one deck
      const dir = Math.sign(st.acc);
      st.acc = 0;
      if (Math.abs(st.target + dir - st.pos) > 6) return;     // momentum, bounded
      st.target += dir;
      reel.classList.add("spinning");
      if (!st.raf) st.raf = requestAnimationFrame(frame);
    }, { passive: false });
    paint();
  }
  const tagRoll = (el) => { const t = el.querySelector(".pod-tag"); t.classList.remove("roll"); void t.offsetWidth; t.classList.add("roll"); };
  function draw() {
    pod.replaceChildren(...slots.map(slice));
    persist();
  }
  draw();

  async function start() {
    err.textContent = "";
    startBtn.disabled = true;
    startBtn.replaceChildren(h("span.spin.small"), " starting");
    try {
      const r = await api("games", { method: "POST", body: { ...opts, seats: seated().map((s) => ({ deck: s.deck, agent: s.agent, model: s.model || "" })) } });
      opts.title = "";                     // a title belongs to one game
      persist();
      go(`#/game/${encodeURIComponent(r.id)}`);
    } catch (e) {
      err.textContent = e.message;
      startBtn.disabled = false;
      startBtn.replaceChildren(icon("play-fill"), " start");
    }
  }
  return h("div.newgame-sec", bar, pod);
}

export function deckPicker(decks, current, onPick) {
  let q = "";
  const grid = h("div.dp-grid");
  const drawGrid = () => grid.replaceChildren(...decks.filter((d) => !q || (d.name + " " + d.commanders.join(" ") + " " + d.scouting).toLowerCase().includes(q))
    .map((d) => h("button.dp-tile" + (d.name === current ? ".on" : ""), { onclick: () => { onPick(d.name); close(); } },
      h("div.dp-art", { style: artCropStyle(d.commanders[0]) }),
      h("div.dp-body", h("div.dp-name", d.name, h("span.sc-colors", colorPips(d.colors))),
        h("div.dp-cmdr", d.commanders.join(" & ")),
        h("div.dp-rec", d.record.games ? `${d.record.wins}–${d.record.games - d.record.wins}` : "")))));
  const input = h("input.search", { placeholder: "search decks", oninput: (e) => { q = e.target.value.toLowerCase(); drawGrid(); } });
  drawGrid();
  const { close } = modal(h("div.dialog.wide", input, grid));
  setTimeout(() => { input.focus(); grid.querySelector(".dp-tile.on")?.scrollIntoView({ block: "center" }); }, 30);
}
