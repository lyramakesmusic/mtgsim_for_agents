// Game screen: live or replay. Director paces compiled events onto the table, feed and timeline.
import { h, icon, api, seatColor, clamp, S, onSetting, modal } from "./util.js";
import { addCards, artCropStyle, hidePreview } from "./cards.js";
import { Table } from "./table.js";
import { Feed } from "./feed.js";
import { Timeline } from "./timeline.js";
import { renderAnalysis } from "./analysis.js";
import { HumanPanel } from "./human.js";
import { usageBars } from "./usage.js";
import { baseName } from "./cards.js";
import * as fx from "./fx.js";
import { sfx, unlockAudio, setVolume } from "./sfx.js";
import { go } from "./main.js";

// ms at 1x for each event kind
function pace(ev) {
  const r = S.readSpeed || 1;
  const read = (base, per, cap) => Math.min(cap, base + (ev.text || "").length * per) / r;
  switch (ev.kind) {
    case "turn": return 1500;
    case "says": return read(1100, 40, 8000);
    case "thinks": return S.thoughts ? read(800, 30, 6000) : 0;
    case "announce": return 1500;
    case "cast": case "activate": return 950;
    case "land": return 750;
    case "attack": return 1300;
    case "block": return 1100;
    case "combat": return read(900, 12, 3500);
    case "life": return 450;
    case "draw": return 380;
    case "drew": return 200;
    case "token": return 520;
    case "countered": case "fizzle": return 1000;
    case "reveal": return 1800;
    case "search": return 700;
    case "coin": case "roll": return 1300;
    case "judge": return read(1600, 45, 16000);
    case "elim": return 3000;
    case "gameover": return 4000;
    case "claim": return read(1200, 30, 5000);
    case "correct": return read(700, 25, 3500);
    case "offer": return read(1200, 35, 7000);
    case "answer": return read(700, 30, 4000);
    case "note": return read(300, 12, 1600);
    case "mull": return 350;
    case "effect": case "pcounters": return 280;
    case "warn": case "aside": case "look": return 150;
    default: return 0;
  }
}

// optional spoken table talk
const voice = {
  voices: null,
  pick(seat) {
    if (!("speechSynthesis" in window)) return null;
    this.voices = this.voices || speechSynthesis.getVoices().filter((v) => /^en/.test(v.lang));
    if (!this.voices.length) return null;
    const k = { P1: 0, P2: 1, P3: 2, P4: 3 }[seat] || 0;
    const good = this.voices.filter((v) => !/Google|Novelty|Bells|Bubbles|Organ|Boing|Cellos|Jester|Whisper|Zarvox|Trinoids|Bad News|Good News|Wobble|Superstar|Albert/i.test(v.name));
    const list = good.length >= 4 ? good : this.voices;
    return list[(k * 7) % list.length];
  },
  speak(text, seat, rate) {
    return new Promise((res) => {
      if (!("speechSynthesis" in window)) return res();
      const u = new SpeechSynthesisUtterance(text);
      const v = this.pick(seat);
      if (v) u.voice = v;
      u.rate = clamp(rate, 0.6, 2);
      u.pitch = { P1: 1, P2: 0.9, P3: 0.8, P4: 1.1 }[seat] || 1;
      const t = setTimeout(res, 30000);
      u.onend = u.onerror = () => { clearTimeout(t); res(); };
      speechSynthesis.speak(u);
    });
  },
  stop() { if ("speechSynthesis" in window) speechSynthesis.cancel(); },
};

export async function gameScreen(root, id, params) {
  const meta = await api(`games/${encodeURIComponent(id)}`);
  let live = meta.status === "live";
  // a seat you pilot: its hand is yours, everyone else's hidden info is hidden unless you ask
  const specSeats = (meta.meta && meta.meta.spec && meta.meta.spec.seats) || [];
  const me = (meta.seats.find((s) => s.agent === "human") || {}).handle
    || (specSeats.findIndex((s) => s.agent === "human") >= 0 ? `P${specSeats.findIndex((s) => s.agent === "human") + 1}` : null);
  const events = [];
  let prevPlayers = [];
  let seats = meta.seats.map((s) => ({ ...s }));
  const cardsP = api(`cards?decks=${seats.map((s) => s.deck).join(",")}`).then(addCards);

  // ---------------------------------------------------------------- shell
  const stage = h("div.stage");
  const analysis = h("div.analysis", { hidden: true });
  const feedWrap = h("div.feed");
  const askWrap = h("div.feed.ask-col", { hidden: true });
  const main = h("div.game-main", h("div.stage-wrap", stage, analysis), feedWrap, askWrap);
  const bottom = h("div.game-bottom");
  const statusSlot = h("div.status-slot");
  const viewSeg = h("div.seg",
    h("button.seg-b.on", { dataset: { v: "watch" }, onclick: () => setView("watch") }, "watch"),
    h("button.seg-b", { dataset: { v: "analyze" }, onclick: () => setView("analyze") }, "analyze"));
  const title = h("div.gtitle",
    h("div.gt-pod", seats.map((s) => h("span.gt-seat", { style: { "--seat": seatColor(s.handle) } },
      h("span.gt-art", { style: artCropStyle((s.commanders || [])[0] || "") }),
      new Set(seats.map((x) => x.deck)).size < seats.length ? h("span.gt-handle", s.handle) : null, s.deck))),
    titleEditor());
  const top = h("div.game-top",
    h("button.ibtn", { title: "library", onclick: () => go("#/") }, icon("arrow-left")),
    title, h("div.grow"), usageBars(), viewSeg, statusSlot,
    h("button.ibtn", { title: "branch from here", onclick: () => branchDialog() }, icon("signpost-split")),
    h("button.ibtn", { title: "download replay", onclick: () => (location.href = `/api/games/${encodeURIComponent(id)}/download`) }, icon("download")),
    h("button.ibtn", { title: "log", onclick: () => toggleFeed() }, icon("layout-sidebar-reverse")),
    h("button.ibtn", { title: "settings", onclick: () => settingsDrawer() }, icon("sliders")));
  const screen = h("div.game-screen", top, main, bottom);

  // the game's title renames in place: click, type, enter or click away
  function titleEditor() {
    const el = h("div.gt-title" + (meta.title ? "" : ".empty"), { title: "rename" }, meta.title || "title");
    el.addEventListener("click", () => {
      const inp = h("input.gt-title-in", { value: meta.title || "", placeholder: "title", spellcheck: false });
      let done = false;
      const save = async (keep) => {
        if (done) return;
        done = true;
        const t = inp.value.trim();
        if (keep && t !== (meta.title || "")) {
          await api(`games/${encodeURIComponent(id)}/meta`, { method: "POST", body: { title: t || null } });
          meta.title = t || null;
        }
        el.textContent = meta.title || "title";
        el.classList.toggle("empty", !meta.title);
        inp.replaceWith(el);
      };
      inp.addEventListener("keydown", (e) => { if (e.key === "Enter") save(true); if (e.key === "Escape") save(false); e.stopPropagation(); });
      inp.addEventListener("blur", () => save(true));
      el.replaceWith(inp); inp.focus(); inp.select();
    });
    return el;
  }
  root.replaceChildren(screen);
  screen.classList.toggle("feed-hidden", !S.feed);

  const loading = h("div.loading", h("div.spin"));
  stage.append(loading);
  await cardsP;

  // ---------------------------------------------------------------- director
  let table = null, feed = null, timeline = null, over = null, human = null;
  const dir = {
    events, seats, idx: -1, playing: false, speed: S.speed, timer: null, following: live, busy: false,
    toggle() { this.playing ? this.pause() : this.play(); },
    play() {
      unlockAudio();
      if (this.idx >= events.length - 1 && !live) this.seek(0);
      this.playing = true; timeline.setPlaying(true); this.schedule(0);
    },
    pause() { this.playing = false; clearTimeout(this.timer); voice.stop(); timeline.setPlaying(false); syncLive(); },
    setSpeed(v) { this.speed = v; fx.setScale(v); if (table) table.timeScale = v; },
    effSpeed() {
      const backlog = events.length - 1 - this.idx;
      return live && backlog > 20 ? Math.min(24, this.speed * (1 + backlog / 20)) : this.speed;
    },
    schedule(ms) {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.tick(), ms);
    },
    async tick() {
      if (!this.playing || this.busy) return;
      if (this.idx >= events.length - 1) { if (!live) this.pause(); syncLive(); return; }
      this.busy = true;
      // zero-duration events apply in the same frame
      let wait = 0, ev;
      do {
        this.idx++;
        ev = events[this.idx];
        this.apply(ev, true);
        wait = pace(ev);
      } while (wait === 0 && this.idx < events.length - 1);
      const sp = this.effSpeed();
      table.timeScale = sp; fx.setScale(sp);
      if (S.voice && ev.kind === "says" && sp <= 2) await voice.speak(ev.text, ev.seat, 1.05 * sp);
      this.busy = false;
      if (this.playing) this.schedule(S.voice && ev.kind === "says" ? 250 : wait / sp);
    },
    apply(ev, animate) {
      table.render(ev.state, { animate });
      if (animate) table.play(ev);
      feed.showUpTo(ev.i);
      timeline.draw();
      if (animate && ev.kind === "gameover") gameOver(ev);
      if (animate && ev.kind === "elim") sfx("elim", ev.seat);
    },
    seek(i) {
      i = clamp(i, 0, events.length - 1);
      voice.stop();
      this.idx = i;
      const ev = events[i];
      table.state = ev.state;
      table.restoreIntent(events, i);
      feed.showUpTo(i);
      timeline.draw();
      if (ev.kind === "gameover") gameOver(ev, true); else hideOver();
      syncLive();
      if (this.playing) this.schedule(350);
    },
    step(d) {
      this.pause();
      if (d > 0 && this.idx < events.length - 1) {
        // step to the next event that shows something
        let k = this.idx + 1;
        while (k < events.length - 1 && pace(events[k]) === 0) k++;
        for (let j = this.idx + 1; j <= k; j++) { this.idx = j; this.apply(events[j], true); }
      } else if (d < 0) {
        let k = this.idx - 1;
        while (k > 0 && pace(events[k]) === 0) k--;
        this.seek(k);
      }
      syncLive();
    },
    turnStep(d) {
      const turns = events.filter((e) => e.kind === "turn").map((e) => e.i);
      if (d > 0) { const n = turns.find((t) => t > this.idx); this.seek(n ?? events.length - 1); }
      else { const p = turns.filter((t) => t < this.idx - 2).pop(); this.seek(p ?? 0); }
    },
    goLive() { this.seek(events.length - 1); this.play(); },
  };

  window.__dir = dir;
  let lastArrival = performance.now();
  function ingest(ev) {
    lastArrival = performance.now();
    ev.state.players = ev.state.players.map((p, k) => p || prevPlayers[k]);
    prevPlayers = ev.state.players;
    events.push(ev);
    feed.add(ev);
  }

  function ensureTable(first) {
    if (table) return;
    seats = first.state.players.map((p, k) => ({ ...(meta.seats[k] || {}), handle: p.handle, deck: p.deck,
      commanders: p.commanders }));
    dir.seats = seats;
    loading.remove();
    table = new Table(stage, seats);
    table.viewer = me;
    table.timeScale = dir.speed;
    feed = new Feed(feedWrap, seats, (i) => { dir.pause(); dir.seek(i); }, me);
    if (live && me) {
      human = new HumanPanel(feedWrap, id, me);
      table.onCardClick = (sp) => (sp.seat === me && (sp.zone === "hand" || sp.zone === "battlefield") ? (human.insert(baseName(sp.name)), true) : false);
    } else if (live) feedWrap.append(judgeComposer());
    timeline = new Timeline(bottom, dir);
  }

  function syncLive() {
    if (!timeline) return;
    timeline.setLive(live, dir.idx >= events.length - 1);
  }

  // ---------------------------------------------------------------- data
  let es = null;
  if (live) {
    let settled = false, quiet = null;
    await new Promise((resolve) => {
      es = new EventSource(`/api/games/${encodeURIComponent(id)}/stream`);
      es.onmessage = (m) => {
        const batch = JSON.parse(m.data).filter((ev) => ev.i >= events.length);   // a reconnect replays from 0
        if (!batch.length) return;
        ensureTable(batch[0].state.players.length ? batch[0] : batch[batch.length - 1]);
        const wasEnd = dir.idx >= events.length - 1;
        for (const ev of batch) ingest(ev);
        timeline.invalidate();
        if (!settled) {
          clearTimeout(quiet);
          quiet = setTimeout(() => { settled = true; dir.seek(events.length - 1); dir.play(); resolve(); }, 250);
        } else {
          timeline.draw();
          if (dir.playing && wasEnd) dir.schedule(0);
          syncLive();
        }
      };
      es.addEventListener("end", async () => {
        es.close();
        // a pause or restart ends the stream too: wait for the cut, then reload onto the new log
        const m = await api(`games/${encodeURIComponent(id)}`);
        if (m.pausing || m.restarting || m.status === "paused") {
          statusSlot.replaceChildren(h("span.st-done", m.restarting ? "restarting" : "pausing"));
          for (let k = 0; k < 600; k++) {
            await new Promise((r) => setTimeout(r, 1000));
            const n = await api(`games/${encodeURIComponent(id)}`);
            if (!n.pausing && !n.restarting) { go(location.hash); return; }
          }
        }
        live = false;
        syncLive();
        if (human) { clearInterval(human.timer); human.waiting = false; human.sync(); }
        statusSlot.replaceChildren(h("span.st-done", "ended"));
        if (!settled) { settled = true; dir.seek(events.length - 1); resolve(); }
      });
      es.onerror = () => {};
    });
    const after = (verb) => async (e) => {
      e.currentTarget.disabled = true;
      await api(`games/${encodeURIComponent(id)}/${verb}`, { method: "POST" });
      statusSlot.querySelector(".st-live").replaceChildren(h("span.dot"), verb === "pause" ? "pausing after this turn" : "restarting after this turn");
    };
    statusSlot.replaceChildren(h("span.st-live", h("span.dot"), meta.pausing ? "pausing after this turn" : meta.restarting ? "restarting after this turn" : "live"),
      h("button.ibtn", { title: "pause at the next turn", onclick: after("pause") }, icon("pause-fill")),
      h("button.ibtn", { title: "restart the engine at the next turn (picks up new code)", onclick: after("restart") }, icon("arrow-clockwise")),
      h("button.ibtn.danger", { title: "stop game", onclick: async (e) => {
        e.currentTarget.disabled = true;
        await api(`games/${encodeURIComponent(id)}/stop`, { method: "POST" });
      } }, icon("stop-fill")));
  } else {
    const all = await api(`games/${encodeURIComponent(id)}/events`);
    if (!all.length) { loading.replaceWith(h("div.empty", "no events")); return () => {}; }
    ensureTable(all[0]);
    for (const ev of all) ingest(ev);
    timeline.invalidate();
    const res = meta.result === "win" ? h("span.st-res", { style: { color: seatColor(meta.winner) } },
      (seats.find((s) => s.handle === meta.winner) || {}).deck + " won") : h("span.st-res", meta.result || meta.status);
    statusSlot.replaceChildren(res, ...(meta.status === "paused" ? [h("button.btn.ghost", { onclick: async (e) => {
      e.currentTarget.disabled = true;
      await api(`games/${encodeURIComponent(id)}/resume`, { method: "POST" });
      go(location.hash);
    } }, icon("play-fill"), " resume")] : []));
    const start = params.get("at") != null ? +params.get("at") : Math.max(0, events.findIndex((e) => e.kind === "turn"));
    dir.seek(start);
    if (params.get("at") == null && params.get("paused") == null && params.get("view") !== "analyze") setTimeout(() => { if (stage.hidden === false) dir.play(); }, 600);
  }
  if (params.get("view") === "analyze") setView("analyze");

  // ---------------------------------------------------------------- pieces
  function setView(v) {
    viewSeg.querySelectorAll(".seg-b").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
    const an = v === "analyze";
    stage.hidden = an; analysis.hidden = !an;
    feedWrap.hidden = an; askWrap.hidden = !an;
    screen.classList.toggle("analyzing", an);
    if (an) {
      dir.pause();
      renderAnalysis(analysis, events, seats, id, (i) => { setView("watch"); dir.seek(i); }, askWrap);
    }
  }

  function toggleFeed() {
    S.feed = !S.feed;
    screen.classList.toggle("feed-hidden", !S.feed);
  }

  function hideOver() { if (over) { over.remove(); over = null; } }
  function gameOver(ev, quiet = false) {
    hideOver();
    if (!quiet) sfx(ev.winner ? "victory" : "turn");
    const s = seats.find((x) => x.handle === ev.winner);
    over = h("div.gameover", { style: { "--seat": seatColor(ev.winner) } },
      h("div.go-card",
        s ? h("div.go-art", { style: artCropStyle((s.commanders || [])[0] || "") }) : null,
        h("div.go-body",
          h("div.go-head", s ? (s.commanders || [])[0] || s.deck : "draw"),
          h("div.go-sub", s ? `${s.deck} wins` : ev.text),
          s ? h("div.go-how", ev.text) : null,
          h("div.go-actions",
            h("button.btn", { onclick: () => { hideOver(); setView("analyze"); } }, "analyze"),
            h("button.btn.ghost", { onclick: () => { hideOver(); dir.seek(0); dir.play(); } }, "replay"),
            h("button.btn.ghost", { onclick: hideOver }, icon("x-lg"))))));
    stage.append(over);
  }

  function judgeComposer() {
    const input = h("input.jc-in", { placeholder: "say something to the table as the judge", maxLength: 600 });
    const send = async (ask) => {
      const text = input.value.trim();
      if (!text && !ask) return;
      input.value = "";
      input.classList.add("sent");
      setTimeout(() => input.classList.remove("sent"), 600);
      await api(`games/${encodeURIComponent(id)}/judge`, { method: "POST", body: { text, ask } });
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") send(e.metaKey || e.ctrlKey); e.stopPropagation(); });
    return h("div.judge-composer", icon("bank2"), input,
      h("button.ibtn", { title: "summon a ruling (⌘↵)", onclick: () => send(true) }, icon("patch-question")));
  }

  function branchDialog() {
    dir.pause();
    const at = Math.max(0, dir.idx);
    const kinds = ["claude", "codex"];
    const agents = seats.map((s) => s.agent && kinds.includes(s.agent) ? s.agent : "codex");
    const rows = seats.map((s, k) => h("div.br-row",
      h("span.br-seat", { style: { color: seatColor(s.handle) } }, s.deck),
      h("div.seg.small", kinds.map((kd) => h("button.seg-b" + (agents[k] === kd ? ".on" : ""), {
        onclick: (e) => { agents[k] = kd; e.currentTarget.parentNode.querySelectorAll(".seg-b").forEach((b) => b.classList.toggle("on", b === e.currentTarget)); },
      }, kd)))));
    let minds = "fresh";
    const mindSeg = h("div.seg.small", ["fresh", "cloned"].map((m) => h("button.seg-b" + (m === minds ? ".on" : ""), {
      onclick: (e) => { minds = m; mindSeg.querySelectorAll(".seg-b").forEach((b) => b.classList.toggle("on", b === e.currentTarget)); },
    }, m)));
    const turns = h("input.num", { type: "number", value: 20, min: 1, max: 99 });
    const err = h("div.err");
    const go_ = h("button.btn", { onclick: async () => {
      go_.disabled = true; go_.textContent = "starting";
      try {
        const r = await api(`games/${encodeURIComponent(id)}/branch`, { method: "POST",
          body: { at: events[at].i, minds, agents, max_turns: +turns.value } });
        close(); go(`#/game/${encodeURIComponent(r.id)}`);
      } catch (e) { err.textContent = e.message; go_.disabled = false; go_.textContent = "branch"; }
    } }, "branch");
    const { close } = modal(h("div.dialog",
      h("h3", "branch"),
      h("div.br-at", `from event ${at} · turn ${(timeline.turnAt(at) || {}).turn || 0}`),
      rows, h("div.br-row", h("span.br-seat", "minds"), mindSeg),
      h("div.br-row", h("span.br-seat", "turn cap"), turns),
      err, h("div.dlg-actions", h("button.btn.ghost", { onclick: () => close() }, "cancel"), go_)));
  }

  function settingsDrawer() {
    const toggle = (k, label) => h("label.opt", h("span", label),
      h("input", { type: "checkbox", checked: !!S[k], onchange: (e) => { S[k] = e.target.checked; } }));
    const range = (k, label, min, max, step) => h("label.opt", h("span", label),
      h("input", { type: "range", min, max, step, value: S[k], oninput: (e) => { S[k] = +e.target.value; } }));
    const { close } = modal(h("div.drawer",
      h("h3", "settings"),
      me ? toggle("showHidden", "show hidden info") : null,
      toggle("thoughts", "private thoughts"),
      toggle("hands", "show hands"),
      toggle("voice", "voice table talk"),
      range("sound", "sound", 0, 1, 0.05),
      range("readSpeed", "reading speed", 0.4, 2.5, 0.1),
      range("motion", "motion snap", 0.5, 2.5, 0.1)), { side: true });
  }

  // live idle: seconds since the engine last spoke, and a typing mark on the seat whose turn it is
  const idleTimer = live ? setInterval(() => {
    if (!timeline || !table) return;
    const secs = Math.floor((performance.now() - lastArrival) / 1000);
    const waiting = dir.idx >= events.length - 1 && secs >= 3 && !!es && es.readyState !== 2;
    timeline.setIdle(waiting ? secs : null);
    const act = table.state && table.state.active;
    table.setWaiting(waiting && act !== me ? act : null);
  }, 1000) : null;

  // ---------------------------------------------------------------- lifecycle
  const offSet = onSetting((k, v) => {
    if ((k === "hands" || k === "showHidden") && table && table.state) table.render(table.state);
    if (k === "showHidden" && feed) feed.applyFilter();
    if (k === "thoughts" && feed) { feed.applyFilter(); }
    if (k === "sound") setVolume(v);
    if (k === "voice" && !v) voice.stop();
  });
  const onKey = (e) => {
    if (e.target.closest("input, textarea, select")) return;
    if (e.key === " ") { e.preventDefault(); dir.toggle(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); e.shiftKey ? dir.turnStep(1) : dir.step(1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); e.shiftKey ? dir.turnStep(-1) : dir.step(-1); }
  };
  document.addEventListener("keydown", onKey);
  screen.addEventListener("pointerdown", unlockAudio, { once: true });
  return () => {
    clearInterval(idleTimer);
    dir.pause();
    clearTimeout(dir.timer);
    if (es) es.close();
    document.removeEventListener("keydown", onKey);
    offSet();
    voice.stop();
    hidePreview();
    table?.destroy(); timeline?.destroy(); human?.destroy();
  };
}

