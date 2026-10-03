// Games library: live games, finished games, imports.
import { h, icon, api, seatColor, timeAgo, store } from "./util.js";
import { artCropStyle } from "./cards.js";
import { go } from "./main.js";
import { podEditor } from "./setup.js";

export async function libraryScreen(root, params) {
  let games = await api("games");
  let q = store("libq") || "", filter = store("libf") || "all";
  const search = h("input.search", { placeholder: "deck, commander, title", value: q,
    oninput: (e) => { q = e.target.value; store("libq", q); draw(); } });
  const seg = h("div.seg", ["all", "starred", "live", "finished"].map((f) =>
    h("button.seg-b" + (f === filter ? ".on" : ""), { onclick: () => { filter = f; store("libf", f); seg.querySelectorAll(".seg-b").forEach((b) => b.classList.toggle("on", b.textContent === f)); draw(); } }, f)));
  const file = h("input", { type: "file", accept: ".jsonl,.gz,.json", hidden: true, onchange: async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const r = await api("games/import", { method: "POST", body: f });
    go(`#/game/${encodeURIComponent(r.id)}`);
  } });
  const list = h("div.glist");
  const decks = await api("decks");
  const editor = podEditor(decks, { seed: params.get("decks") });
  root.replaceChildren(h("div.lib", editor,
    h("div.lib-bar", search, seg, h("div.grow"),
      h("button.btn.ghost", { onclick: () => file.click() }, icon("upload"), " import replay"), file),
    list));

  function matches(g) {
    if (filter === "starred" && !g.starred) return false;
    if (filter === "live" && g.status !== "live") return false;
    if (filter === "finished" && !g.result) return false;
    if (!q) return true;
    const hay = [g.title, g.id, ...g.seats.flatMap((s) => [s.deck, ...(s.commanders || [])])].join(" ").toLowerCase();
    return q.toLowerCase().split(/\s+/).every((w) => hay.includes(w));
  }

  function row(g) {
    const winner = g.seats.find((s) => s.handle === g.winner);
    const star = h("button.star" + (g.starred ? ".on" : ""), { title: "star", onclick: async (e) => {
      e.stopPropagation();
      g.starred = !g.starred;
      star.classList.toggle("on", g.starred);
      star.replaceChildren(icon(g.starred ? "star-fill" : "star"));
      await api(`games/${encodeURIComponent(g.id)}/meta`, { method: "POST", body: { starred: g.starred } });
    } }, icon(g.starred ? "star-fill" : "star"));
    const titleEl = h("span.g-title" + (g.title ? "" : ".empty"), { title: "double-click to rename" }, g.title || "title");
    titleEl.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      const inp = h("input.g-title-in", { value: g.title || "", placeholder: "title" });
      const save = async () => {
        g.title = inp.value.trim() || null;
        titleEl.textContent = g.title || "title";
        titleEl.classList.toggle("empty", !g.title);
        inp.replaceWith(titleEl);
        await api(`games/${encodeURIComponent(g.id)}/meta`, { method: "POST", body: { title: g.title } });
      };
      inp.addEventListener("keydown", (k) => { if (k.key === "Enter") save(); if (k.key === "Escape") inp.replaceWith(titleEl); k.stopPropagation(); });
      inp.addEventListener("blur", save);
      inp.addEventListener("click", (k) => k.stopPropagation());
      titleEl.replaceWith(inp); inp.focus();
    });
    const res = g.status === "live" ? h("span.g-live", h("span.dot"), "live")
      : g.result === "win" && winner ? h("span.g-win", { style: { color: seatColor(winner.handle) } }, winner.deck)
      : g.status === "paused" ? h("span.g-draw", "paused")
      : g.result === "draw" ? h("span.g-draw", "draw") : g.result === "cap" ? h("span.g-draw", "turn cap")
      : h("span.g-stop", g.error ? "unreadable" : "stopped");
    return h("div.g-row" + (g.status === "live" ? ".is-live" : ""), { onclick: () => go(`#/game/${encodeURIComponent(g.id)}`), title: g.how || "" },
      star,
      h("div.g-pod", g.seats.map((s) => h("div.g-seat" + (s.alive === false ? ".out" : "") + (s.handle === g.winner ? ".won" : ""),
        { style: { "--seat": seatColor(s.handle) } },
        h("div.g-art", { style: artCropStyle((s.commanders || [])[0] || "") }),
        h("div.g-deck", s.deck), h("div.g-agent", s.agent || "")))),
      h("div.g-mid", titleEl, h("div.g-how", g.branch_of ? `branch of ${g.branch_of.id} at ${g.branch_of.at}` : g.result === "win" ? g.how : "")),
      h("div.g-res", res),
      h("div.g-rounds", g.rounds ? `${g.rounds} rounds` : ""),
      h("div.g-when", timeAgo(g.date)));
  }

  function draw() {
    const shown = games.filter(matches);
    shown.sort((a, b) => (b.status === "live") - (a.status === "live") || b.mtime - a.mtime);
    list.replaceChildren(...(shown.length ? shown.map(row) : [h("div.empty", games.length ? "no matches" : h("a", { href: "#/" }, "start a game"))]));
  }
  draw();
  const poll = setInterval(async () => {
    if (!games.some((g) => g.status === "live")) return;
    games = await api("games");
    draw();
  }, 5000);
  return () => clearInterval(poll);
}
