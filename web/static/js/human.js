// A human seat's console: the REPL of the CLI human seat, in the browser. The engine asks, the
// scribe (an LLM) turns your words into plays; this panel shows the prompt, your transcript, and
// the shortcuts that fit the moment.
import { h, icon, api, seatColor } from "./util.js";
import { cardChip } from "./cards.js";
import { sfx } from "./sfx.js";

// banner -> label and shortcuts; each shortcut is the exact line the CLI REPL understands
const MODES = {
  "OPENING HAND": { label: "opening hand", keys: [["keep", "keep"], ["mulligan", "mull"]] },
  "YOUR TURN": { label: "your turn", keys: [["end turn", "done"]] },
  "RESPOND?": { label: "respond?", keys: [["pass", "pass"]] },
  "BLOCK?": { label: "blocks?", keys: [["no blocks", "pass"]] },
  "DECIDE": { label: "decide", keys: [["yes", "yes"], ["no", "no"]] },
  "CONFIRM": { label: "confirm", keys: [["resolve", "resolve"], ["fizzle", "fizzle"]] },
  "COMBAT DAMAGE": { label: "combat damage", keys: [["from the board", ""]] },
  "YOUR CALL": { label: "your call", keys: [["pass", "pass"]] },
};

export class HumanPanel {
  constructor(root, gameId, seat) {
    this.gameId = gameId; this.seat = seat;
    this.n = 0; this.mode = null; this.waiting = false; this.title = document.title;
    this.log = h("div.hp-log");
    this.label = h("span.hp-mode", "waiting for the table");
    this.keys = h("div.hp-keys");
    this.input = h("textarea.hp-in", { rows: 1, placeholder: "say what you do — \"quotes\" talk to the table" });
    this.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this.send(this.input.value); }
      e.stopPropagation();
    });
    this.input.addEventListener("input", () => { this.input.style.height = "auto"; this.input.style.height = Math.min(120, this.input.scrollHeight) + "px"; });
    this.el = h("div.hp", { style: { "--seat": seatColor(seat) } },
      h("div.hp-head", h("span.hp-you", `you · ${seat}`), this.label, h("div.grow"), this.keys),
      this.log,
      h("div.hp-compose", this.input, h("button.ibtn", { title: "send", onclick: () => this.send(this.input.value) }, icon("arrow-up"))));
    root.append(this.el);
    this.timer = setInterval(() => this.poll(), 700);
    this.poll();
  }

  destroy() { clearInterval(this.timer); document.title = this.title; this.el.remove(); }

  insert(text) {
    const i = this.input, a = i.selectionStart ?? i.value.length;
    const pad = a && !/\s$/.test(i.value.slice(0, a)) ? " " : "";
    i.value = i.value.slice(0, a) + pad + text + i.value.slice(i.selectionEnd ?? a);
    i.focus();
    const at = a + pad.length + text.length;
    i.setSelectionRange(at, at);
  }

  async send(text) {
    if (!this.waiting) { this.el.classList.remove("nudge"); void this.el.offsetWidth; this.el.classList.add("nudge"); return; }
    this.input.value = ""; this.input.style.height = "auto";
    this.waiting = false; this.sync();
    await api(`games/${encodeURIComponent(this.gameId)}/human`, { method: "POST", body: { seat: this.seat, text } });
  }

  async poll() {
    let r;
    try { r = await api(`games/${encodeURIComponent(this.gameId)}/human?since=${this.n}`); } catch { return; }
    for (const o of r.out) if (!o.seat || o.seat === this.seat) this.line(o);
    this.n = r.n;
    const waiting = !!(r.state && r.state.waiting && r.state.seat === this.seat);
    if (waiting && !this.waiting) { sfx("turn", this.seat); }
    this.waiting = waiting;
    this.sync();
  }

  sync() {
    const m = MODES[this.mode] || null;
    this.el.classList.toggle("waiting", this.waiting);
    this.label.textContent = this.waiting ? (m ? m.label : "your move") : "waiting for the table";
    this.keys.replaceChildren(...(this.waiting && m ? m.keys.map(([lbl, line]) => h("button.hp-key", { onclick: () => this.send(line) }, lbl)) : []));
    document.title = this.waiting ? `● ${m ? m.label : "your move"} — mtgsim` : this.title;
  }

  // one transcript line from the seat's REPL
  line(o) {
    const t = o.text;
    if (o.kind === "banner") {
      this.mode = t.replace(/-/g, "").trim();
      this.log.append(h("div.hp-banner", MODES[this.mode] ? MODES[this.mode].label : this.mode.toLowerCase()));
    } else if (o.kind === "you") {
      this.log.append(h("div.hp-you-line", t || "↵"));
    } else if (t.startsWith("scribe:")) {
      this.log.append(h("div.hp-scribe", t.slice(7).trim()));
    } else if (t.startsWith("YOUR HAND")) {
      const cards = t.replace(/^YOUR HAND \(\d+\):\s*/, "").split(/;\s*/).filter(Boolean);
      this.log.append(h("div.hp-hand", cards.map((c) => h("button.hp-card", { onclick: () => this.insert(c) }, cardChip(c)))));
    } else if (t.startsWith("→")) {
      this.log.append(h("div.hp-action", t));
    } else if (t.startsWith("!!")) {
      this.log.append(h("div.hp-warn", t.replace(/^!+\s*/, "")));
    } else {
      this.log.append(h("div.hp-note", t.replace(/^\(|\)$/g, "")));
    }
    this.log.scrollTop = this.log.scrollHeight;
  }
}
