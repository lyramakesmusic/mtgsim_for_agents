// Boot + hash router.
import { h, icon, api } from "./util.js";
import { setArt, initPreview } from "./cards.js";
import { usageBars } from "./usage.js";

export const BOOT = { agents: [], decks: [] };
const app = document.getElementById("app");
let cleanup = null;
let seq = 0;

export function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

function shell(active) {
  const nav = (href, label, key) => h("a.nav" + (active === key ? ".on" : ""), { href }, label);
  const content = h("main.page");
  const header = h("header.app-head",
    h("a.brand", { href: "#/" }, "mtgsim"),
    h("nav.navs", nav("#/", "games", "games"), nav("#/decks", "decks", "decks")),
    h("div.grow"),
    usageBars(),
    h("a.ibtn.gear" + (active === "settings" ? ".on" : ""), { href: "#/settings", title: "settings", "aria-label": "settings" }, icon("gear")));
  app.replaceChildren(h("div.shell", header, content));
  return content;
}

async function route() {
  const my = ++seq;
  if (cleanup) { try { cleanup(); } catch (e) { console.error(e); } cleanup = null; }
  document.querySelectorAll(".modal-back").forEach((m) => m.remove());
  const raw = location.hash.slice(1) || "/";
  const [path, qs] = raw.split("?");
  const params = new URLSearchParams(qs || "");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  try {
    let c;
    if (parts[0] === "game" && parts[1]) {
      app.replaceChildren();
      const { gameScreen } = await import("./game.js");
      c = await gameScreen(app, parts[1], params);
    } else if (parts[0] === "settings") {
      const { settingsScreen } = await import("./settings.js");
      c = await settingsScreen(shell("settings"));
    } else if (parts[0] === "decks" && parts[1]) {
      const { deckScreen } = await import("./decks.js");
      c = await deckScreen(shell("decks"), parts[1], params);
    } else if (parts[0] === "decks") {
      const { decksScreen } = await import("./decks.js");
      c = await decksScreen(shell("decks"));
    } else {
      const { libraryScreen } = await import("./library.js");
      c = await libraryScreen(shell("games"), params);
    }
    if (my !== seq) { if (typeof c === "function") c(); return; }
    cleanup = typeof c === "function" ? c : null;
  } catch (e) {
    console.error(e);
    if (my === seq) app.replaceChildren(h("div.fatal", h("div", e.message), h("a", { href: "#/" }, "back to games")));
  }
}

(async () => {
  initPreview();
  document.addEventListener("pointerdown", () => import("./sfx.js").then((m) => m.unlockAudio()), { once: true });
  const b = await api("boot");
  Object.assign(BOOT, b);
  setArt(b.art);
  window.addEventListener("hashchange", route);
  route();
})();
