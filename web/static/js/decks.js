// Deck browser, deck inspector, deck editor/importer.
import { h, icon, api, seatColor, timeAgo, store, markdown, modal } from "./util.js";
import { addCards, artCropStyle, artUrl, cardFace, info, manaCost, typeClass } from "./cards.js";
import { colorPips } from "./setup.js";
import { go } from "./main.js";
import { hero } from "./hero.js";

export async function decksScreen(root) {
  const decks = await api("decks");
  const tile = (d) => h("a.deck-tile", { href: `#/decks/${d.name}` },
    h("div.dt-art", { style: artCropStyle(d.commanders[0]) }),
    h("div.dt-body",
      h("div.dt-name", d.name, h("span.sc-colors", colorPips(d.colors))),
      h("div.dt-cmdr", d.commanders.join(" & ")),
      h("div.dt-rec", d.record.games ? h("span", h("b", d.record.wins), `–${d.record.games - d.record.wins}`,
        h("span.dim", ` ${Math.round(100 * d.record.wins / d.record.games)}%`)) : h("span.dim", "unplayed"),
        d.missing.length ? h("span.warn", icon("exclamation-triangle"), ` ${d.missing.length} unknown`) : null)));
  // banner art: the most-played commanders, then a few at random
  const byPlay = [...decks].sort((a, b) => b.record.games - a.record.games);
  const pick = [...byPlay.slice(0, 3), ...byPlay.slice(3).sort(() => Math.random() - 0.5)].slice(0, 5);
  const banner = hero({ slices: pick.map((d) => ({ card: d.commanders[0] })), cls: "hero-new-deck", body: newDeckForm(decks.map((d) => d.name)) });
  root.replaceChildren(h("div.decks", banner, h("div.deck-grid", decks.map(tile))));
}

export async function deckScreen(root, name, params) {
  root._cleanup?.();
  if (name === "new") return editor(root, null);
  const d = await api(`decks/${name}`);
  addCards(d.cards);
  if (params.get("edit") != null) return editor(root, d);
  const hasTags = Object.keys(d.tags).length > 0;
  let group = (store("deckGroup") || {})[name] || (hasTags ? "tags" : "type");        // per deck
  const all = [...d.commanders.map((c) => ({ name: c, qty: 1, cmdr: true })), ...d.main];
  // curve / colors / types
  const nonland = d.main.filter((c) => !/Land/.test((d.cards[c.name] || {}).type || ""));
  const curve = Array(8).fill(0);
  for (const c of nonland) curve[Math.min(7, (d.cards[c.name] || {}).mv || 0)] += c.qty;
  const cmax = Math.max(...curve, 1);
  const pips = { W: 0, U: 0, B: 0, R: 0, G: 0 };
  for (const c of d.main) for (const ch of ((d.cards[c.name] || {}).cost || "")) if (ch in pips) pips[ch] += c.qty;
  const types = {};
  for (const c of d.main) {
    const t = (d.cards[c.name] || {}).type || "";
    const k = ["Creature", "Land", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Battle"].find((x) => t.includes(x)) || "Other";
    types[k] = (types[k] || 0) + c.qty;
  }
  const lands = types.Land || 0;
  const avg = nonland.reduce((a, c) => a + ((d.cards[c.name] || {}).mv || 0) * c.qty, 0) / Math.max(1, nonland.reduce((a, c) => a + c.qty, 0));

  const stats = h("div.deck-stats",
    h("div.curve", curve.map((n, i) => h("div.cbar", { title: `${n} at ${i}${i === 7 ? "+" : ""}` },
      h("div.cfill", { style: { height: (n / cmax) * 100 + "%" } }), h("div.cn", n || ""), h("div.ci", i === 7 ? "7+" : i)))),
    h("div.st-col",
      h("div.st-line", h("b", avg.toFixed(2)), " avg mana value"),
      h("div.st-line", h("b", lands), " lands"),
      h("div.pipbar", Object.entries(pips).filter(([, n]) => n).map(([c, n]) => h("div.pb", { style: { flex: n, background: `var(--mana-${c})` }, title: `${n} ${c} pips` })))),
    h("div.st-types", Object.entries(types).sort((a, b) => b[1] - a[1]).map(([t, n]) => h("div.st-type", h("b", n), " ", t.toLowerCase()))));

  const cardsEl = h("div.deck-cards");
  const groupSeg = h("div.seg.small", ["tags", "type", "curve"].map((g) => h("button.seg-b" + (g === group ? ".on" : ""),
    { onclick: () => { group = g; store("deckGroup", { ...(typeof store("deckGroup") === "object" && store("deckGroup") || {}), [name]: g }); groupSeg.querySelectorAll(".seg-b").forEach((b) => b.classList.toggle("on", b.textContent === g)); drawCards(); } }, g)));
  function drawCards() {
    let groups;
    if (group === "tags" && Object.keys(d.tags).length) {
      groups = Object.entries(d.tags).map(([t, cs]) => [t, cs]);
      const tagged = new Set(groups.flatMap(([, cs]) => cs.map((c) => c.name)));
      const rest = d.main.filter((c) => !tagged.has(c.name));
      if (rest.length) groups.push(["untagged", rest]);
    } else if (group === "curve") {
      const by = {};
      for (const c of d.main) {
        const dd = d.cards[c.name] || {};
        const k = /Land/.test(dd.type || "") ? "lands" : `${Math.min(7, dd.mv || 0)}${(dd.mv || 0) >= 7 ? "+" : ""}`;
        (by[k] = by[k] || []).push(c);
      }
      groups = Object.entries(by).sort((a, b) => (a[0] === "lands") - (b[0] === "lands") || parseInt(a[0]) - parseInt(b[0]));
    } else {
      const by = {};
      for (const c of d.main) {
        const t = (d.cards[c.name] || {}).type || "";
        const k = ["Creature", "Planeswalker", "Instant", "Sorcery", "Artifact", "Enchantment", "Battle", "Land"].find((x) => t.includes(x)) || "Other";
        (by[k] = by[k] || []).push(c);
      }
      groups = Object.entries(by).map(([k, v]) => [k.toLowerCase(), v.sort((a, b) => ((d.cards[a.name] || {}).mv || 0) - ((d.cards[b.name] || {}).mv || 0))]);
    }
    cardsEl.replaceChildren(...groups.map(([label, cs]) => h("section.dgroup",
      h("h4", label, h("span.dim", ` ${cs.reduce((a, c) => a + c.qty, 0)}`)),
      h("div.dcards", cs.map((c) => h("div.dcard", { dataset: { card: c.name } }, cardFace(c.name), c.qty > 1 ? h("div.qty", `×${c.qty}`) : null))))));
  }
  drawCards();

  // tags from screenshots: paste or drop images on this page, or pick files
  const tagStatus = h("span.tag-status");
  const tagFiles = h("input", { type: "file", accept: "image/*", multiple: true, hidden: true, onchange: (e) => tagsFrom([...e.target.files]) });
  async function tagsFrom(files) {
    const imgs = await Promise.all(files.filter((f) => f.type.startsWith("image/")).map((f) => new Promise((res) => {
      const rd = new FileReader(); rd.onload = () => res(rd.result); rd.readAsDataURL(f);
    })));
    if (!imgs.length) return;
    tagStatus.textContent = `reading ${imgs.length} screenshot${imgs.length > 1 ? "s" : ""}…`;
    const { job } = await api(`decks/${name}/tags-images`, { method: "POST", body: { images: imgs } });
    const poll = async () => {
      const j = await api(`jobs/${job}`);
      tagStatus.textContent = j.log[j.log.length - 1] || "";
      if (!j.done) return setTimeout(poll, 1000);
      if (j.ok) { store("deckGroup", { ...(typeof store("deckGroup") === "object" && store("deckGroup") || {}), [name]: "tags" }); setTimeout(() => deckScreen(root, name, params), 600); }
    };
    poll();
  }
  const onPaste = (e) => {
    if (e.target.closest("input, textarea")) return;
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) { e.preventDefault(); tagsFrom(files); }
  };
  document.addEventListener("paste", onPaste);
  root._cleanup = () => document.removeEventListener("paste", onPaste);
  root.addEventListener("dragover", (e) => e.preventDefault());
  root.addEventListener("drop", (e) => { e.preventDefault(); tagsFrom([...e.dataTransfer.files]); });
  const tagBtn = h("button.btn.ghost", { title: "tags from screenshots (or paste / drop images)", onclick: () => tagFiles.click() }, icon("image"), " tags from screenshots");

  const memo = (label, text) => text ? h("div.memo", h("div.memo-l", label), h("div.memo-t", text)) : null;
  root.replaceChildren(h("div.deck-page",
    h("aside.deck-side",
      h("a.backlink", { href: "#/decks" }, icon("arrow-left"), " decks"),
      h("div.deck-cmdrs", d.commanders.map((c) => h("div.dcmdr", { dataset: { card: c } }, cardFace(c)))),
      renameTitle(d),
      h("div.deck-actions",
        h("a.btn", { href: `#/?decks=${d.name}` }, icon("play-fill"), " play"),
        h("a.btn.ghost", { href: `#/decks/${d.name}?edit` }, icon("pencil"), " edit"),
        deleteButton(d.name)),
      d.missing.length ? missingBox(d) : null,
      memo("scouting", d.scouting), memo("strategy", d.strategy), memo("personality", d.personality),
      h("div.memo", h("div.memo-l", "record"),
        h("div.memo-t", d.record.games ? `${d.record.wins} won of ${d.record.games}` : "unplayed"),
        h("div.deck-games", d.games.slice(0, 12).map((g) => {
          const me = g.seats.find((s) => s.deck === d.name);
          const won = g.winner && me && g.winner === me.handle;
          return h("a.dg", { href: `#/game/${encodeURIComponent(g.id)}` },
            h("span" + (won ? ".w" : g.result ? ".l" : ".dim"), won ? "won" : g.result === "win" ? "lost" : g.result || g.status),
            h("span.dim", g.seats.filter((s) => s.deck !== d.name).map((s) => s.deck).join(", ")),
            h("span.dim.r", timeAgo(g.date)));
        })))),
    h("div.deck-main", stats, h("div.deck-bar", groupSeg, h("span.dim", `${all.reduce((a, c) => a + c.qty, 0)} cards`), h("div.grow"), tagStatus, tagBtn, tagFiles), cardsEl)));
}

// new deck: paste a moxfield/archidekt link and create; the clipboard button pastes a plain list.
// The deck is named from its source (the site's deck title, or the commander) and renamed after.
function newDeckForm(existing) {
  const taken = new Set(existing);
  const slugName = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 36) || "deck";
  const unique = (base) => { let n = base, k = 2; while (taken.has(n)) n = `${base}_${k++}`; return n; };
  const status = h("div.nd-status");
  const link = h("input.nd-link", { placeholder: "moxfield or archidekt link", spellcheck: false });
  const create = h("button.nd-go", { onclick: () => fromLink() }, icon("plus-lg"), h("span", "create"));
  const pasteBtn = h("button.nd-paste", { title: "paste a list", onclick: () => pastePopup() }, icon("clipboard"));
  link.addEventListener("keydown", (e) => { if (e.key === "Enter") fromLink(); });
  link.addEventListener("paste", () => setTimeout(fromLink, 0));

  const busy = (on, label) => {
    for (const b of [create, pasteBtn, link]) b.disabled = on;
    create.replaceChildren(...(on ? [h("span.spin.small"), h("span", label)] : [icon("plus-lg"), h("span", "create")]));
  };

  async function save(name, text, tags) {
    status.textContent = "";
    busy(true, "fetching cards");
    try {
      const { job } = await api(`decks/${encodeURIComponent(name)}`, { method: "PUT", body: { text, tags } });
      if (!job) return go(`#/decks/${name}`);
      for (;;) {
        await new Promise((r) => setTimeout(r, 700));
        const j = await api(`jobs/${job}`);
        const last = j.log.filter((l) => !l.startsWith("$")).pop();
        if (last) status.textContent = last.slice(0, 120);
        if (!j.done) continue;
        if (j.ok) return go(`#/decks/${name}`);
        throw new Error("card fetch failed — " + (last || "see server log"));
      }
    } catch (e) { busy(false); status.textContent = e.message; status.classList.add("err"); }
  }

  async function fromLink() {
    const url = link.value.trim();
    if (!/^https?:\/\//.test(url)) { link.focus(); return; }
    status.classList.remove("err");
    busy(true, "importing");
    try {
      const d = await api("import", { method: "POST", body: { url } });
      await save(unique(slugName(d.name)), d.text, Object.keys(d.tags || {}).length ? d.tags : null);
    } catch (e) { busy(false); status.textContent = e.message; status.classList.add("err"); }
  }

  function pastePopup() {
    const text = h("textarea.nd-text", { spellcheck: false, placeholder: "Commander\n1 Meren of Clan Nel Toth\n\nDeck\n1 Sol Ring\n8 Forest\n..." });
    const go_ = h("button.nd-go", { onclick: () => {
      const t = text.value.trim();
      if (!t) return text.focus();
      const cmdr = (t.match(/^\s*commanders?[:\s]*\n\s*\d+x?\s+([^\n(]+)/im) || t.match(/^\s*\d+x?\s+([^\n(]+?)\s*\*CMDR\*/im) || [])[1];
      close();
      save(unique(slugName((cmdr || "deck").split(",")[0])), t, null);
    } }, icon("plus-lg"), h("span", "create"));
    text.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") go_.click(); e.stopPropagation(); });
    const { close } = modal(h("div.dialog.nd-dialog", h("h3", "paste a list"), text, h("div.dlg-actions", go_)));
    setTimeout(() => text.focus(), 30);
  }

  return h("div.nd-body",
    h("div.hero-title", "new deck"),
    h("div.nd-bottom", status, h("div.nd-row", link, create, pasteBtn)));
}

function editor(root, d) {
  const isNew = !d;
  const nameIn = h("input.deck-name-in", { placeholder: "deck name (lowercase, no spaces)", value: d ? d.name : "", disabled: !isNew });
  const text = h("textarea.deck-text", { spellcheck: false, placeholder: "Commander\n1 Meren of Clan Nel Toth\n\nDeck\n1 Sol Ring\n8 Forest\n...\n\n// strategy: private gameplan for this seat\n// scouting: what the table knows about this deck\n// personality: how this seat talks" }, d ? d.text : "");
  const log = h("pre.joblog", { hidden: true });
  const err = h("div.err");
  const save = h("button.btn", { onclick: run }, isNew ? "create" : "save");
  root.replaceChildren(h("div.deck-edit",
    h("div.de-bar", h("a.ibtn", { href: d ? `#/decks/${d.name}` : "#/decks", title: "back" }, icon("arrow-left")), nameIn, h("div.grow"), save),
    text, err, log));
  text.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); run(); } });
  async function run() {
    err.textContent = "";
    const name = nameIn.value.trim().toLowerCase();
    save.disabled = true; save.textContent = "saving";
    try {
      const { job } = await api(`decks/${encodeURIComponent(name)}`, { method: "PUT", body: { text: text.value } });
      if (!job) return go(`#/decks/${name}`);           // nothing new to fetch
      log.hidden = false;
      save.textContent = "fetching cards";
      const poll = async () => {
        const j = await api(`jobs/${job}`);
        log.textContent = j.log.join("\n");
        log.scrollTop = log.scrollHeight;
        if (!j.done) return setTimeout(poll, 700);
        if (j.ok) go(`#/decks/${name}`);
        else { save.disabled = false; save.textContent = "retry"; err.textContent = "card fetch failed — see log"; }
      };
      poll();
    } catch (e) {
      err.textContent = e.message; save.disabled = false; save.textContent = isNew ? "create" : "save";
    }
  }
}

// two-step delete in one slot: trash icon, then "delete deck?" for 4 seconds
function deleteButton(name) {
  let armed = null;
  const b = h("button.btn.ghost.del", { title: "delete deck", onclick: async () => {
    if (!armed) {
      b.classList.add("armed"); b.replaceChildren(icon("trash3"), " delete deck?");
      armed = setTimeout(() => { armed = null; b.classList.remove("armed"); b.replaceChildren(icon("trash3")); }, 4000);
      return;
    }
    clearTimeout(armed);
    b.disabled = true;
    await api(`decks/${encodeURIComponent(name)}`, { method: "DELETE" });
    go("#/decks");
  } }, icon("trash3"));
  return b;
}

// cards the database lacks, next to the action that fetches them
function missingBox(d) {
  const log = h("pre.joblog", { hidden: true });
  const btn = h("button.btn", { onclick: async () => {
    btn.disabled = true; btn.replaceChildren(h("span.spin.small"), " fetching");
    const { job } = await api(`decks/${encodeURIComponent(d.name)}/fetch`, { method: "POST" });
    log.hidden = false;
    const poll = async () => {
      const j = await api(`jobs/${job}`);
      log.textContent = j.log.slice(-12).join("\n");
      if (!j.done) return setTimeout(poll, 800);
      if (j.ok) location.reload();
      else { btn.disabled = false; btn.replaceChildren(icon("arrow-repeat"), " retry"); }
    };
    poll();
  } }, icon("cloud-download"), ` fetch ${d.missing.length} missing cards`);
  return h("div.missing", btn, log, h("div.memo-l", "not in the card database"), d.missing.map((m) => h("div.miss", m)));
}

// the deck name renames in place: click, type, enter
function renameTitle(d) {
  const name = h("span.dt-rename", { title: "rename" }, d.name);
  const el = h("div.deck-title", name, h("span.sc-colors", colorPips(d.colors)));
  name.addEventListener("click", () => {
    const inp = h("input.deck-rename-in", { value: d.name, spellcheck: false });
    const err = h("div.err");
    const done = async (save) => {
      const to = inp.value.trim().toLowerCase().replace(/\s+/g, "_");
      if (!save || !to || to === d.name) return el.replaceChildren(name, h("span.sc-colors", colorPips(d.colors)));
      inp.disabled = true;
      try {
        const r = await api(`decks/${encodeURIComponent(d.name)}/rename`, { method: "POST", body: { to } });
        go(`#/decks/${r.to}`);
      } catch (e) { inp.disabled = false; err.textContent = e.message; inp.focus(); }
    };
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") done(true); if (e.key === "Escape") done(false); e.stopPropagation(); });
    inp.addEventListener("blur", () => { if (!inp.disabled && !err.textContent) done(true); });
    el.replaceChildren(h("div.dt-renaming", inp, err));
    inp.focus(); inp.select();
  });
  return el;
}
