// The game log panel: one entry per meaningful event, revealed up to the playhead.
import { h, icon, seatColor, S } from "./util.js";
import { cardChip, baseName } from "./cards.js";

const TALK = new Set(["says", "thinks", "judge", "turn", "elim", "gameover", "claim"]);
const SKIP = new Set(["caption", "no_window", "blank", "pod", "untap"]);

export class Feed {
  constructor(root, seats, onSeek, viewer = null) {
    this.viewer = viewer;
    this.seats = Object.fromEntries(seats.map((s) => [s.handle, s]));
    this.onSeek = onSeek;
    this.list = h("div.feed-list");
    this.entries = [];                       // index-aligned with events; null = no entry
    this.shown = -1;
    this.follow = true;
    this.list.addEventListener("scroll", () => {
      this.follow = this.list.scrollHeight - this.list.scrollTop - this.list.clientHeight < 40;
    });
    this.list.addEventListener("click", (e) => {
      const en = e.target.closest(".fe");
      if (en && !e.target.closest(".cardref")) this.onSeek(+en.dataset.i);
    });
    root.append(this.list);
    this.applyFilter();
  }

  applyFilter() {
    this.list.dataset.filter = S.feedFilter;
    this.list.classList.toggle("no-thoughts", !S.thoughts);
    this.list.classList.toggle("privacy", !!this.viewer && !S.showHidden);
  }

  who(hdl) {
    const s = this.seats[hdl];
    return h("span.who", { style: { color: seatColor(hdl) } }, s ? s.deck : hdl || "");
  }

  // a target reference as the reader knows it: a player, a spell on the stack by name, or a card
  target(t, ev) {
    if (/^P\d/.test(t)) return this.who(t.slice(0, 2));
    if (/^stack#/.test(t)) {
      const it = (ev.state.stack || []).find((x) => x.sid === t);
      if (it) return it.ability ? h("span", cardChip(it.card), h("span.t", "'s ability")) : cardChip(it.card);
    }
    return cardChip(t);
  }

  add(ev) {
    const el = this.build(ev);
    this.entries[ev.i] = el;
    if (!el) return;
    el.dataset.i = ev.i;
    el.classList.add("fe", "k-" + ev.kind, TALK.has(ev.kind) ? "talk" : "play");
    if (ev.priv) el.classList.add("priv");
    if (this.viewer && ev.priv && ev.seat && ev.seat !== this.viewer) el.classList.add("hidden-info");
    if (ev.i > this.shown) el.classList.add("future");
    // consecutive lines from one speaker read as one message
    const pv = this.prev;
    if (pv && (ev.kind === "says" || ev.kind === "thinks") && pv.seat === ev.seat) {
      if (pv.kind === "says") el.classList.add(ev.kind === "says" ? "cont" : "cont-t");
      else if (pv.kind === "thinks") el.classList.add(ev.kind === "thinks" ? "cont" : "cont-t");
    }
    this.prev = ev.kind === "says" || ev.kind === "thinks" ? ev : null;
    this.list.append(el);
  }

  build(ev) {
    if (SKIP.has(ev.kind)) return null;
    const who = ev.seat ? this.who(ev.seat) : null;
    const text = (t) => h("span.t", t);
    switch (ev.kind) {
      case "turn": {
        const s = this.seats[ev.seat];
        return h("div", { style: { "--seat": seatColor(ev.seat) } }, h("span.turn-n", `Turn ${ev.turn}`), who,
          s && s.commanders ? h("span.cmdr", s.commanders[0]) : null);
      }
      case "says": return h("div", { style: { "--seat": seatColor(ev.seat) } }, who, h("div.q", ev.text));
      case "thinks": return h("div", { style: { "--seat": seatColor(ev.seat) } }, who, h("div.q", ev.text));
      case "announce":
        return h("div", who, text(ev.ability ? " activates " : " casts "), cardChip(ev.card),
          ev.targets.length ? h("span.tg", icon("arrow-right-short"), ev.targets.map((t, i) => [i ? ", " : "", this.target(t, ev)])) : null,
          ev.text ? h("div.n", ev.text) : null);
      case "cast":
        return h("div", cardChip(ev.card), text(ev.fizzled ? " fizzles" : ev.from_cz ? " resolves from the command zone" : " resolves"),
          ev.text && ev.fizzled ? h("div.n", ev.text) : null);
      case "activate": return h("div", cardChip(ev.card), text(" resolves"), ev.text ? h("div.n", ev.text) : null);
      case "land": return h("div", who, text(" plays "), cardChip(ev.card), ev.tapped ? text(" tapped") : null);
      case "attack":
        return h("div", who, text(" attacks "), this.who(ev.target), h("div.chips", ev.ids.map((id) => cardChip(id))));
      case "block": {
        const pairs = Object.entries(ev.blocks || {});
        return h("div", who, text(pairs.length ? " blocks" : " takes it"),
          pairs.length ? h("div.chips", pairs.map(([a, bs]) => h("span.pair", [].concat(bs).map((b) => cardChip(b)), icon("shield"), cardChip(a)))) : null,
          ev.text ? h("div.n", ev.text) : null);
      }
      case "combat": return h("div", icon("lightning-charge"), h("span.n", ev.text || "combat damage"));
      case "life":
        return h("div", who, h("span.delta" + (ev.delta < 0 ? ".neg" : ".pos"), ` ${ev.delta > 0 ? "+" : ""}${ev.delta}`), h("span.now", ` ${ev.now}`));
      case "draw": return h("div", who, text(` draws ${ev.n}`));
      case "drew": return h("div", who, text(" drew "), ev.cards.map((c, i) => [i ? ", " : "", cardChip(c)]));
      case "token": return h("div", who, text(` creates ${ev.n} × `), cardChip(ev.card, "token"));
      case "search": return h("div", who, text(" searches for "), cardChip(ev.card), text(` → ${ev.to}`));
      case "reveal": return h("div", who, text(" reveals "), ev.cards.map((c, i) => [i ? ", " : "", cardChip(c)]));
      case "pcounters": return h("div", who, text(" " + Object.entries(ev.counters).map(([k, v]) => `${k} ${v}`).join(", ") || " counters cleared"));
      case "countered": return h("div", cardChip(ev.card), h("span.bad", " is countered"));
      case "countered_mark": return null;
      case "fizzle": return h("div", cardChip(ev.card), h("span.bad", " fizzles"));
      case "left_stack": return h("div.dim", ev.text);
      case "elim": return h("div", who, h("div.fe-big", "eliminated"), h("div.n", ev.text));
      case "gameover":
        return h("div", ev.winner ? [this.who(ev.winner), h("div.fe-big", "wins")] : h("div.fe-big", "draw"), h("div.n", ev.text));
      case "claim": return h("div", who, h("div.n", ev.text.replace(/^P\d\([^)]*\)\s*/, "")));
      case "judge": return h("div", h("div.jh", ev.ruling ? "judge ruling" : "judge"), h("div.q", ev.text));
      case "correct": return h("div", who, text(" corrects the board"), h("div.n", ev.text));
      case "offer": return h("div", who, text(" offers the table"), h("div.q", ev.text));
      case "answer": return h("div", who, h("span.t", " answers "), h("span.q", ev.text));
      case "note": return h("div", who, h("span.n", " " + ev.text));
      case "warn": return h("div", icon("exclamation-triangle"), text(" " + ev.text));
      case "mull": return h("div", who, text(" " + ev.text.replace(/^P\d\([^)]*\)\s*/, "")));
      case "look": return h("div", who, text(` looks at the top ${ev.n}`));
      case "coin": return h("div", icon("coin"), text(` coin flip: ${ev.result.toLowerCase()}`));
      case "roll": return h("div", icon("dice-5"), text(` d${ev.sides}: ${ev.result}`));
      case "concede": return h("div", who, text(" " + ev.verb.toLowerCase()));
      case "peek": return h("div", h("span.t", ev.text));
      default: return h("div", h("span.t", (ev.text || ev.line || "").replace(/^\s*↳\s*/, "")));
    }
  }

  showUpTo(i) {
    if (i > this.shown) for (let k = this.shown + 1; k <= i; k++) this.entries[k]?.classList.remove("future");
    else for (let k = i + 1; k <= this.shown; k++) this.entries[k]?.classList.add("future");
    this.list.querySelector(".fe.cur")?.classList.remove("cur");
    for (let k = i; k >= 0; k--) {
      const e = this.entries[k];
      if (e && !e.classList.contains("k-effect") && getComputedStyle(e).display !== "none") { e.classList.add("cur"); break; }
    }
    this.shown = i;
    if (this.follow) this.list.scrollTop = this.list.scrollHeight;
  }

  jumpToCurrent() {
    this.follow = false;
    const cur = this.list.querySelector(".fe.cur");
    if (cur) cur.scrollIntoView({ block: "center" });
  }
}
