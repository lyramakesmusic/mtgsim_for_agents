// Post-game (or mid-game) analysis computed from the compiled event list.
import { h, icon, api, seatColor, markdown, clamp, store } from "./util.js";
import { cardChip, artCropStyle, typeClass, baseName, ptOf } from "./cards.js";

const NS = "http://www.w3.org/2000/svg";
const svg = (tag, attrs = {}) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };

// series: [{handle, pts: [[i, v|null]]}]  -> element with hover readout, click to jump
function chart({ series, n, height, turns, first, onJump, fmt = (v) => v, small = false, zero = true }) {
  const wrap = h("div.chart" + (small ? ".small" : ""));
  const read = h("div.chart-read", { hidden: true });
  const s = svg("svg", { preserveAspectRatio: "none" });
  wrap.append(s, read);
  let lo = zero ? 0 : Infinity, hi = -Infinity;
  for (const se of series) for (const [, v] of se.pts) if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  if (!isFinite(hi)) { hi = 1; lo = 0; }
  if (hi === lo) hi = lo + 1;
  const draw = () => {
    const W = wrap.clientWidth, H = height;
    if (!W) return;
    s.setAttribute("viewBox", `0 0 ${W} ${H}`); s.setAttribute("width", W); s.setAttribute("height", H);
    s.replaceChildren();
    const pad = small ? 3 : 8;
    const X = (i) => (n > 1 ? i / (n - 1) : 0) * W;
    const Y = (v) => H - pad - ((v - lo) / (hi - lo)) * (H - 2 * pad);
    const g = svg("g", { class: "grid" });
    for (const t of turns) if (t.seat === first) g.append(svg("line", { x1: X(t.i), x2: X(t.i), y1: 0, y2: H }));
    if (lo < 0 && hi > 0) g.append(svg("line", { class: "zero", x1: 0, x2: W, y1: Y(0), y2: Y(0) }));
    s.append(g);
    if (!small) {
      const lab = svg("g", { class: "labels" });
      let r = 0;
      for (const t of turns) if (t.seat === first) { r++; if (r % Math.max(1, Math.round(turns.length / 4 / 12)) === 0) { const tx = svg("text", { x: X(t.i) + 3, y: 11 }); tx.textContent = r; lab.append(tx); } }
      for (const v of [lo, hi]) { const tx = svg("text", { x: W - 3, y: clamp(Y(v) + (v === hi ? 12 : -3), 10, H - 2), "text-anchor": "end" }); tx.textContent = fmt(v); lab.append(tx); }
      s.append(lab);
    }
    for (const se of series) {
      let d = "", open = false, py = 0;
      for (const [i, v] of se.pts) {
        if (v == null) { if (open) d += `H${X(i).toFixed(1)}`; open = false; continue; }
        const x = X(i).toFixed(1), y = Y(v).toFixed(1);
        d += open ? `H${x}V${y}` : `M${x},${y}`;
        open = true; py = y;
      }
      if (open) d += `H${W}`;
      s.append(svg("path", { d, stroke: seatColor(se.handle), class: "line" }));
    }
    const cross = svg("line", { class: "cross", y1: 0, y2: H, x1: -10, x2: -10 });
    s.append(cross);
    wrap._cross = cross; wrap._X = X;
  };
  const valueAt = (se, i) => { let v = null; for (const [j, x] of se.pts) { if (j > i) break; v = x; } return v; };
  wrap.addEventListener("pointermove", (e) => {
    const r = wrap.getBoundingClientRect();
    const i = Math.round(clamp((e.clientX - r.left) / r.width, 0, 1) * (n - 1));
    wrap._cross?.setAttribute("x1", wrap._X(i)); wrap._cross?.setAttribute("x2", wrap._X(i));
    read.hidden = false;
    read.replaceChildren(...series.map((se) => h("span", { style: { color: seatColor(se.handle) } }, fmt(valueAt(se, i) ?? "—"))));
    read.style.left = clamp(e.clientX - r.left + 10, 0, r.width - 120) + "px";
    wrap._i = i;
  });
  wrap.addEventListener("pointerleave", () => { read.hidden = true; wrap._cross?.setAttribute("x1", -10); wrap._cross?.setAttribute("x2", -10); });
  wrap.addEventListener("click", () => wrap._i != null && onJump(events_i(wrap._i)));
  const events_i = (i) => i;
  new ResizeObserver(draw).observe(wrap);
  return wrap;
}

// askHost: where the Q&A thread goes (the log's column while analyzing)
export function renderAnalysis(root, events, seats, gameId, onJump, askHost) {
  const n = events.length;
  const handles = seats.map((s) => s.handle);
  const turns = events.filter((e) => e.kind === "turn").map((e) => ({ i: e.i, seat: e.seat, turn: e.turn }));
  const first = handles[0];
  const stat = Object.fromEntries(handles.map((hd) => [hd, { casts: 0, abilities: 0, lands: 0, tokens: 0, drawn: 0,
    lifeLost: 0, lifeGained: 0, dealt: 0, says: 0, words: 0, thinks: 0, corrections: 0, warns: 0, attacks: 0, cardsCast: {} }]));
  const series = (fn) => handles.map((hd, k) => {
    const pts = [];
    let last;
    for (const e of events) {
      const p = e.state.players[k];
      const v = p ? (p.alive || fn === lifeOf ? fn(p) : null) : null;
      if (v !== last) { pts.push([e.i, v]); last = v; }
    }
    return { handle: hd, pts };
  });
  const lifeOf = (p) => (p.alive ? p.life : null);
  const moments = [];
  const winnerEv = events.find((e) => e.kind === "gameover");
  let active = null;
  for (const e of events) {
    if (e.kind === "turn") active = e.seat;
    const st = stat[e.seat];
    switch (e.kind) {
      case "announce": if (st) { e.ability ? st.abilities++ : st.casts++; if (!e.ability) st.cardsCast[e.card] = (st.cardsCast[e.card] || 0) + 1; } break;
      case "land": if (st) st.lands++; break;
      case "token": if (st) st.tokens += e.n; break;
      case "draw": if (st) st.drawn += e.n; break;
      case "says": if (st) { st.says++; st.words += e.text.split(/\s+/).length; } break;
      case "thinks": if (st) st.thinks++; break;
      case "correct": if (st) st.corrections++; break;
      case "attack": if (st) st.attacks += e.ids.length; break;
      case "life":
        if (!st) break;
        if (e.delta < 0) { st.lifeLost -= e.delta; if (active && active !== e.seat && stat[active]) stat[active].dealt -= e.delta; }
        else st.lifeGained += e.delta;
        if (Math.abs(e.delta) >= 8) moments.push({ i: e.i, kind: "swing", seat: e.seat, text: `${e.delta > 0 ? "+" : ""}${e.delta} life → ${e.now}` });
        break;
      case "elim": moments.push({ i: e.i, kind: "elim", seat: e.seat, text: `eliminated — ${e.text}` }); break;
      case "gameover": moments.push({ i: e.i, kind: "over", seat: e.winner, text: e.winner ? `wins — ${e.text}` : e.text }); break;
      case "judge": moments.push({ i: e.i, kind: "judge", text: e.text }); break;
      case "countered": moments.push({ i: e.i, kind: "counter", text: `${e.card} countered` }); break;
      case "claim": moments.push({ i: e.i, kind: "claim", seat: e.seat, text: e.text.replace(/^P\d\([^)]*\)\s*/, "") }); break;
    }
    if (e.kind === "warn") { const m = e.text.match(/P\d/); if (m && stat[m[0]]) stat[m[0]].warns++; }
  }
  // board wipes: 4+ permanents gone in one event
  for (let k = 1; k < n; k++) {
    const a = events[k - 1].state.players, b = events[k].state.players;
    let gone = 0;
    a.forEach((p, j) => { if (p && b[j] && p !== b[j] && b[j].alive) gone += Math.max(0, p.battlefield.filter(ptOf).length - b[j].battlefield.filter(ptOf).length); });
    if (gone >= 4) moments.push({ i: k, kind: "wipe", text: `${gone} creatures leave the battlefield`, line: events[k].line });
  }
  moments.sort((a, b) => a.i - b.i);

  const lifeChart = chart({ series: series(lifeOf), n, height: 220, turns, first, onJump, zero: false });
  const count = (f) => (p) => f(p);
  const smalls = [
    ["creatures", count((p) => p.battlefield.filter(ptOf).length)],
    ["lands", count((p) => p.battlefield.filter((x) => typeClass(x.name, x) === "land").length)],
    ["other permanents", count((p) => p.battlefield.filter((x) => typeClass(x.name, x) === "other").length)],
    ["hand", count((p) => p.hand.length)],
    ["graveyard", count((p) => p.graveyard.length)],
    ["library", count((p) => p.library)],
  ].map(([label, f]) => h("div.sm", h("div.sm-l", label), chart({ series: series(f), n, height: 70, turns, first, onJump, small: true })));

  const rows = [
    ["spells cast", "casts"], ["abilities", "abilities"], ["lands", "lands"], ["tokens", "tokens"], ["cards drawn", "drawn"],
    ["attackers sent", "attacks"], ["damage on own turn", "dealt"], ["life lost", "lifeLost"], ["life gained", "lifeGained"],
    ["table talk lines", "says"], ["private thoughts", "thinks"], ["board corrections", "corrections"], ["engine warnings", "warns"],
  ];
  const final = events[n - 1].state.players;
  const head = h("div.an-seats", seats.map((s, k) => {
    const p = final[k];
    const elim = moments.find((m) => m.kind === "elim" && m.seat === s.handle);
    const won = winnerEv && winnerEv.winner === s.handle;
    return h("div.an-seat" + (won ? ".won" : "") + (p && !p.alive ? ".dead" : ""), { style: { "--seat": seatColor(s.handle) } },
      h("div.an-art", { style: artCropStyle((s.commanders || [])[0] || "") }),
      h("div.an-body", h("div.an-deck", h("span.an-handle", s.handle), s.deck), h("div.an-cmdr", (s.commanders || []).join(" & ")),
        h("div.an-agent", s.agent || "")),
      h("div.an-res", won ? h("span.an-win", icon("trophy-fill"), " won") :
        elim ? h("span.an-out", `out T${turnOf(elim.i)}`) : h("span.an-life", p ? p.life : "")));
  }));
  function turnOf(i) { let t = 0; for (const x of turns) { if (x.i > i) break; t = x.turn; } return t; }

  const table = h("table.an-table",
    h("thead", h("tr", h("th"), seats.map((s) => h("th", { style: { color: seatColor(s.handle) } }, s.deck)))),
    h("tbody", rows.map(([label, key]) => {
      const vals = handles.map((hd) => stat[hd][key]);
      const mx = Math.max(...vals);
      return h("tr", h("td", label), vals.map((v) => h("td" + (v && v === mx && vals.filter((x) => x === mx).length === 1 ? ".top" : ""), v)));
    })));

  const topCards = h("div.an-cards", seats.map((s) => {
    const cc = Object.entries(stat[s.handle].cardsCast).sort((a, b) => b[1] - a[1]).slice(0, 8);
    return h("div.an-col", h("div.an-col-h", { style: { color: seatColor(s.handle) } }, s.deck),
      cc.length ? cc.map(([c, k]) => h("div.an-card", cardChip(c), k > 1 ? h("span.x", `×${k}`) : null)) : h("div.dim", "—"));
  }));

  const momentList = h("div.moments", moments.map((m) => h("button.moment.mk-" + m.kind, {
    onclick: () => onJump(Math.max(0, m.i - 1)), style: { "--seat": seatColor(m.seat) } },
    h("span.m-t", `T${turnOf(m.i)}`), m.seat ? h("span.m-who", (seats.find((s) => s.handle === m.seat) || {}).deck) : null,
    h("span.m-text", m.text))));

  const pm = h("div.postmortem");
  const loadPm = async () => {
    const txt = await api(`games/${encodeURIComponent(gameId)}/postmortem`);
    if (txt && txt.trim()) { pm.replaceChildren(markdown(txt)); return; }
    const log = h("pre.joblog", { hidden: true });
    const btn = h("button.btn.ghost", { onclick: async () => {
      btn.disabled = true; btn.textContent = "running"; log.hidden = false;
      const { job } = await api(`games/${encodeURIComponent(gameId)}/postmortem`, { method: "POST" });
      const poll = async () => {
        const j = await api(`jobs/${job}`);
        log.textContent = j.log.slice(-30).join("\n");
        if (!j.done) return setTimeout(poll, 1500);
        if (j.ok) loadPm(); else btn.textContent = "failed — retry", (btn.disabled = false);
      };
      poll();
    } }, "run codex postmortem");
    pm.replaceChildren(btn, log);
  };
  loadPm();

  askHost.replaceChildren(askBox(gameId, events, onJump));
  root.replaceChildren(h("div.an-layout",
    h("div.an-scroll",
      head,
      h("section", h("h4", "life"), lifeChart),
      h("section", h("div.smalls", smalls)),
      h("div.an-split",
        h("section", h("h4", "moments"), moments.length ? momentList : h("div.dim", "—")),
        h("section", h("h4", "by seat"), table)),
      h("section", h("h4", "most cast"), topCards),
      h("section", h("h4", "postmortem"), pm))));
}

// ------------------------------------------------------------------ ask
// a conversation with a read-only agent that greps this game's logs; one session per game and agent
function askBox(gameId, events, onJump) {
  const key = `qa:${gameId}`;
  const thread = store(key) || [];
  let agent = store("qaAgent") || "codex";
  const list = h("div.qa-list");
  const roundStart = (n) => {
    let r = 0, first = null;
    for (const e of events) {
      if (e.kind !== "turn") continue;
      if (first === null) first = e.seat;
      if (e.seat === first) r++;
      if (r === n) return e.i;
    }
    return null;
  };
  // T7 / turn 7 in answers jump the replay
  const linkify = (el) => {
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walk.nextNode()) nodes.push(walk.currentNode);
    for (const n of nodes) {
      const parts = n.textContent.split(/\b(T\d{1,2}|[Tt]urn \d{1,2})\b/);
      if (parts.length < 2) continue;
      const frag = document.createDocumentFragment();
      parts.forEach((p, k) => {
        const r = k % 2 ? +p.match(/\d+/)[0] : null;
        const at = r ? roundStart(r) : null;
        frag.append(at != null ? h("button.qa-turn", { onclick: () => onJump(at) }, p) : p);
      });
      n.replaceWith(frag);
    }
    return el;
  };
  const entry = (qa) => h("div.qa",
    h("div.qa-q", qa.q),
    qa.a != null ? h("div.qa-a" + (qa.failed ? ".qa-fail" : ""), linkify(markdown(qa.a))) : qa.el);
  const draw = () => { list.replaceChildren(...thread.map(entry)); list.scrollTop = list.scrollHeight; };
  const input = h("textarea.qa-in", { rows: 1, placeholder: "ask about this game" });
  input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = Math.min(160, input.scrollHeight) + "px"; });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  const seg = h("div.seg.small", ["codex", "claude"].map((k) => h("button.seg-b" + (k === agent ? ".on" : ""), {
    onclick: () => { agent = k; store("qaAgent", k); seg.querySelectorAll(".seg-b").forEach((b) => b.classList.toggle("on", b.textContent === k)); } }, k)));
  async function send() {
    const q = input.value.trim();
    if (!q) return;
    input.value = ""; input.style.height = "auto";
    const t0 = performance.now();
    const pending = h("div.qa-a.qa-pending", h("span.typing", h("i"), h("i"), h("i")), h("span.qa-secs"));
    const qa = { q, agent, a: null, el: pending };
    thread.push(qa); draw();
    const tick = setInterval(() => (pending.querySelector(".qa-secs").textContent = ` ${Math.round((performance.now() - t0) / 1000)}s`), 1000);
    try {
      const { job } = await api(`games/${encodeURIComponent(gameId)}/ask`, { method: "POST", body: { question: q, agent } });
      let j;
      do { await new Promise((r) => setTimeout(r, 1200)); j = await api(`jobs/${job}`); } while (!j.done);
      qa.a = j.ok ? j.result : (j.log[j.log.length - 1] || "failed");
      qa.failed = !j.ok;
    } catch (e) { qa.a = e.message; qa.failed = true; }
    clearInterval(tick);
    delete qa.el;
    store(key, thread.filter((x) => x.a != null && !x.failed).slice(-40));
    draw();
  }
  draw();
  const clear = h("button.qa-clear", { title: "clear the thread", onclick: () => { thread.length = 0; store(key, []); draw(); } }, icon("trash3"));
  return h("div.ask",
    h("div.qa-head", h("h4", "ask"), clear),
    list,
    h("div.qa-compose", input, h("div.qa-row", seg, h("button.btn", { onclick: send }, icon("arrow-up")))));
}
