// Transport + scrubber. The scrubber draws each seat's life across the game as a minimap.
import { h, icon, seatColor, clamp, S } from "./util.js";

const SPEEDS = [0.5, 1, 2, 4, 8, 16];

export class Timeline {
  constructor(root, dir) {
    this.dir = dir;                          // director: {toggle, step, turnStep, seek, setSpeed, events, idx, playing}
    this.canvas = h("canvas.scrub-canvas");
    this.head = h("div.scrub-head");
    this.hover = h("div.scrub-hover", { hidden: true });
    this.scrub = h("div.scrub", this.canvas, this.head, this.hover);
    this.playBtn = h("button.tbtn.play", { title: "play / pause", onclick: () => dir.toggle() }, icon("play-fill"));
    this.pos = h("div.tpos");
    this.speedBtn = h("button.tbtn.speed", { title: "speed", onclick: () => this.cycleSpeed(1),
      oncontextmenu: (e) => { e.preventDefault(); this.cycleSpeed(-1); } }, "1×");
    this.muteBtn = h("button.tbtn", { title: "sound", onclick: () => {
      if (S.sound > 0) { S.lastSound = S.sound; S.sound = 0; } else S.sound = S.lastSound || 0.6;
      this.syncMute();
    } });
    this.syncMute();
    this.liveBtn = h("button.tbtn.live", { hidden: true, onclick: () => dir.goLive() }, h("span.dot"), "live");
    this.el = h("div.timeline",
      h("div.tctl",
        h("button.tbtn", { title: "previous turn", onclick: () => dir.turnStep(-1) }, icon("skip-start-fill")),
        h("button.tbtn", { title: "step back", onclick: () => dir.step(-1) }, icon("chevron-left")),
        this.playBtn,
        h("button.tbtn", { title: "step", onclick: () => dir.step(1) }, icon("chevron-right")),
        h("button.tbtn", { title: "next turn", onclick: () => dir.turnStep(1) }, icon("skip-end-fill"))),
      this.pos, this.scrub, this.liveBtn, this.muteBtn, this.speedBtn);
    root.append(this.el);
    this.series = null;
    this.dirty = true;
    let dragging = false;
    const at = (e) => {
      const r = this.scrub.getBoundingClientRect();
      return Math.round(clamp((e.clientX - r.left) / r.width, 0, 1) * Math.max(0, this.dir.events.length - 1));
    };
    this.scrub.addEventListener("pointerdown", (e) => { dragging = true; this.scrub.setPointerCapture(e.pointerId); this.dir.seek(at(e)); });
    this.scrub.addEventListener("pointermove", (e) => {
      const i = at(e);
      if (dragging) this.dir.seek(i);
      this.showHover(e, i);
    });
    this.scrub.addEventListener("pointerup", () => (dragging = false));
    this.scrub.addEventListener("pointerleave", () => (this.hover.hidden = true));
    this.ro = new ResizeObserver(() => { this.dirty = true; this.draw(); });
    this.ro.observe(this.scrub);
    this.setSpeed(S.speed);
  }

  syncMute() { this.muteBtn.replaceChildren(icon(S.sound > 0 ? "volume-up" : "volume-mute")); }

  cycleSpeed(d) {
    const k = SPEEDS.indexOf(S.speed);
    this.setSpeed(SPEEDS[clamp((k < 0 ? 1 : k) + d, 0, SPEEDS.length - 1)]);
  }
  setSpeed(v) {
    S.speed = v;
    this.speedBtn.textContent = (v < 1 ? "½" : v) + "×";
    this.dir.setSpeed(v);
  }

  setPlaying(p) { this.playBtn.replaceChildren(icon(p ? "pause-fill" : "play-fill")); }
  setIdle(secs) {
    this.liveBtn.dataset.idle = secs == null ? "" : secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`;
  }
  setLive(show, atEdge) { this.liveBtn.hidden = !show; this.liveBtn.classList.toggle("edge", !!atEdge); }

  // events grew or seats changed
  invalidate() { this.dirty = true; }

  showHover(e, i) {
    const ev = this.dir.events[i];
    if (!ev) return;
    const r = this.scrub.getBoundingClientRect();
    this.hover.hidden = false;
    const t = this.turnAt(i);
    this.hover.textContent = t ? `turn ${t.turn} · ${t.deck}` : "pregame";
    this.hover.style.left = clamp(e.clientX - r.left, 40, r.width - 40) + "px";
  }

  turnAt(i) {
    const T = this.turns || [];
    let lo = 0, hi = T.length - 1, best = null;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (T[m].i <= i) { best = T[m]; lo = m + 1; } else hi = m - 1; }
    return best;
  }

  build() {
    const ev = this.dir.events, n = ev.length;
    const seats = this.dir.seats;
    this.turns = [];
    this.elims = [];
    const life = seats.map(() => []);
    let min = Infinity, max = -Infinity;
    for (let i = 0; i < n; i++) {
      const e = ev[i];
      if (e.kind === "turn") this.turns.push({ i, turn: e.turn, seat: e.seat, deck: (seats.find((s) => s.handle === e.seat) || {}).deck });
      if (e.kind === "elim") this.elims.push({ i, seat: e.seat });
      e.state.players.forEach((p, k) => {
        if (!p || !life[k]) return;
        const v = p.alive ? p.life : null;
        const L = life[k];
        if (!L.length || L[L.length - 1][1] !== v) L.push([i, v]);
        if (v != null) { min = Math.min(min, v); max = Math.max(max, v); }
      });
    }
    if (!isFinite(min)) { min = 0; max = 40; }
    const padv = Math.max(2, (max - min) * 0.08);
    this.series = { life, min: min - padv, max: max + padv, n };
  }

  draw() {
    const c = this.canvas, dpr = devicePixelRatio || 1;
    const w = this.scrub.clientWidth, hh = this.scrub.clientHeight;
    if (!w || !hh) return;
    if (this.dirty) {
      this.build();
      c.width = w * dpr; c.height = hh * dpr;
      const g = c.getContext("2d");
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, w, hh);
      const { life, min, max, n } = this.series;
      const X = (i) => (n > 1 ? i / (n - 1) : 0) * w;
      const Y = (v) => hh - 5 - ((v - min) / (max - min || 1)) * (hh - 10);
      // round ticks: every seat-1 turn begins a round
      g.fillStyle = "rgba(255,255,255,0.06)";
      const first = this.dir.seats[0]?.handle;
      for (const t of this.turns) if (t.seat === first) g.fillRect(Math.round(X(t.i)), 0, 1, hh);
      if (min < 0) { g.fillStyle = "rgba(255,255,255,0.09)"; g.fillRect(0, Math.round(Y(0)), w, 1); }
      life.forEach((L, k) => {
        g.strokeStyle = seatColor(this.dir.seats[k].handle);
        g.globalAlpha = 0.9;
        g.lineWidth = 1.6;
        g.beginPath();
        let open = false;
        L.forEach(([i, v], j) => {
          if (v == null) { if (open) { g.lineTo(X(i), g._y); } open = false; return; }
          const x = X(i), y = Y(v);
          if (!open) { g.moveTo(x, y); open = true; }
          else g.lineTo(x, g._y);
          g.lineTo(x, y); g._y = y;
        });
        if (open) g.lineTo(w, g._y);
        g.stroke();
      });
      g.globalAlpha = 1;
      for (const e of this.elims) {
        g.fillStyle = seatColor(e.seat);
        g.beginPath(); g.arc(X(e.i), hh - 4, 2.5, 0, Math.PI * 2); g.fill();
      }
      this.dirty = false;
    }
    const n = this.dir.events.length;
    this.head.style.left = ((n > 1 ? this.dir.idx / (n - 1) : 0) * 100).toFixed(3) + "%";
    const t = this.turnAt(this.dir.idx);
    this.pos.replaceChildren(h("span.tp-turn", t ? `T${t.turn}` : "—"), h("span.tp-seat", { style: { color: t ? seatColor(t.seat) : "" } }, t ? t.deck : ""));
  }

  destroy() { this.ro.disconnect(); }
}
