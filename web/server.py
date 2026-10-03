#!/usr/bin/env python3
"""Web GUI server for the agent sim: decks, pods, live games, replays.

  uv run web/server.py              # http://127.0.0.1:8765
  uv run web/server.py --port 9000 --open

Games run as `play.py` subprocesses writing games/<id>.md + .events.jsonl, the same
files the CLI writes; this server tails and compiles them. Stdlib only.
"""
import argparse
import gzip
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parent
ROOT = WEB.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(WEB))
from events import Compiler, compile_file  # noqa: E402
from mtgsim.cards import (DECK_DIR, deck_personality, deck_scouting, deck_strategy,  # noqa: E402
                          deck_tags, load_db, mana_value, parse_decklist)

GAMES = ROOT / "games"
ART = ROOT / "data" / "art"
STATIC = WEB / "static"
CACHE = ROOT / ".cache" / "web"
(CACHE / "compiled").mkdir(parents=True, exist_ok=True)
GAMES.mkdir(exist_ok=True)

DEFAULT_MODELS = {"claude": "opus", "codex": "gpt-5.6-terra", "openrouter": "", "local": ""}
SETTINGS_PATH = CACHE / "settings.json"
SETTINGS_DEFAULT = {"agent": "codex", "scribe": "codex", "models": dict(DEFAULT_MODELS), "codex_effort": "", "codex_tier": "",
                    "max_turns": 20, "max_actions": 150}


def settings():
    got = json.loads(SETTINGS_PATH.read_text()) if SETTINGS_PATH.exists() else {}
    return {**SETTINGS_DEFAULT, **got, "models": {**SETTINGS_DEFAULT["models"], **got.get("models", {})}}


PROCS = {}            # game id -> Popen (games launched by this server process)
JOBS = {}             # job id -> {"done": bool, "ok": bool, "log": [str], "result": any}
LOCK = threading.Lock()


def slug(name):
    return re.sub(r"[^\w]+", "_", name).strip("_").lower()


# ------------------------------------------------------------------ game files
def gid_of(events_path):
    rel = events_path.relative_to(GAMES).as_posix()
    return rel[: -len(".md.events.jsonl")].replace("/", "~")


def paths_of(gid):
    base = GAMES / (gid.replace("~", "/") + ".md")
    if ".." in gid or not base.resolve().is_relative_to(GAMES.resolve()):
        raise KeyError(gid)
    return {"md": base, "events": Path(f"{base}.events.jsonl"), "judge": Path(f"{base}.judge"),
            "meta": base.with_suffix(".web.json"), "console": base.with_suffix(".console.log"),
            "postmortem": base.with_suffix(".postmortem.md")}


def read_meta(gid):
    p = paths_of(gid)["meta"]
    return json.loads(p.read_text()) if p.exists() else {}


def write_meta(gid, **kw):
    m = {**read_meta(gid), **kw}
    paths_of(gid)["meta"].write_text(json.dumps(m, indent=1))
    return m


def is_running(gid):
    pr = PROCS.get(gid)
    if pr is not None:
        return pr.poll() is None
    pid = read_meta(gid).get("pid")
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
    return "play.py" in cmd or "humanplay.py" in cmd


def last_state(events_path, tail=400_000):
    """Final engine event's state (reads only the file's tail)."""
    with open(events_path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - tail))
        chunk = f.read().decode("utf-8", "replace")
    for ln in reversed(chunk.splitlines()):
        try:
            return json.loads(ln).get("state") or {}
        except json.JSONDecodeError:
            continue
    return {}


INDEX_CACHE = CACHE / "index.json"
_index = json.loads(INDEX_CACHE.read_text()) if INDEX_CACHE.exists() else {}

POD = re.compile(r"^# Pod: (.*?) — seed (\S+) — (\S+ \S+)")
TURN = re.compile(r"^## Turn (\d+) — (P\d)")
OVER = re.compile(r"^\*\*GAME OVER: (?:(P\d)\(([^)]*)\) WINS \((.*)\) on turn (\d+)|DRAW on turn (\d+) — (.*?)\.)")
CAP = re.compile(r"^\*\*Turn cap (\d+) reached")
ELIM = re.compile(r"^\*\*(P\d)\([^)]*\) is ELIMINATED — (.*?)\. Their")
KINDS = {"ClaudeAgent": "claude", "CodexAgent": "codex", "OpenRouterAgent": "openrouter",
         "LocalAgent": "local", "HumanAgent": "human", "MockAgent": "mock"}


def summarize(events_path):
    """-> index entry for one game (from its .md log; falls back to the jsonl)."""
    md = Path(str(events_path)[: -len(".events.jsonl")])
    lines = md.read_text(errors="replace").splitlines() if md.exists() else \
        [json.loads(l).get("line", "") for l in events_path.read_text().splitlines() if l.strip()]
    e = {"seats": [], "seed": None, "date": None, "turns": 0, "rounds": 0, "result": None,
         "winner": None, "how": None, "elims": [], "lines": len(lines)}
    for raw in lines:
        t = raw.strip()
        if t.startswith("[private]"):
            continue
        if (m := POD.match(t)) and not e["seats"]:
            e["seats"] = [{"handle": h, "deck": d} for h, d in re.findall(r"(P\d)\(([^)]*)\)", m.group(1))]
            e["seed"], e["date"] = m.group(2), m.group(3)[:19]
        elif m := TURN.match(t):
            e["rounds"] = max(e["rounds"], int(m.group(1)))
            e["turns"] += 1
        elif m := ELIM.match(t):
            e["elims"].append({"seat": m.group(1), "how": m.group(2)})
        elif m := OVER.match(t):
            if m.group(1):
                e.update(result="win", winner=m.group(1), how=m.group(3))
            else:
                e.update(result="draw", how=m.group(6))
        elif CAP.match(t):
            e.update(result="cap", how="turn cap")
    st = last_state(events_path)
    kinds = [KINDS.get(s.get("kind"), s.get("kind")) for s in st.get("sessions") or []]
    for n, s in enumerate(e["seats"]):
        s["agent"] = kinds[n] if n < len(kinds) else None
        pl = (st.get("players") or [{}] * 4)[n] if n < len(st.get("players") or []) else {}
        s["life"], s["alive"] = pl.get("life"), pl.get("alive", True)
        s["commanders"] = pl.get("commanders") or []
    if not e["date"]:
        e["date"] = datetime.fromtimestamp(events_path.stat().st_mtime).isoformat(" ")[:19]
    return e


def game_entry(events_path):
    st = events_path.stat()
    key = str(events_path)
    c = _index.get(key)
    if not c or c["mtime"] != st.st_mtime or c["size"] != st.st_size:
        try:
            c = {"mtime": st.st_mtime, "size": st.st_size, "entry": summarize(events_path)}
        except Exception as ex:          # one unreadable log must not take the library down
            c = {"mtime": st.st_mtime, "size": st.st_size,
                 "entry": {"seats": [], "error": f"{type(ex).__name__}: {ex}", "lines": 0}}
        _index[key] = c
    gid = gid_of(events_path)
    running = is_running(gid)
    meta = read_meta(gid)
    status = "live" if running or meta.get("restarting") else (c["entry"].get("result") and "done") \
        or ("paused" if meta.get("paused") else "stopped")
    return {"id": gid, **c["entry"], "status": status, "size": st.st_size,
            "title": meta.get("title"), "starred": meta.get("starred", False),
            "pausing": bool(meta.get("pausing")), "restarting": bool(meta.get("restarting")),
            "branch_of": meta.get("branch_of"), "mtime": st.st_mtime}


def list_games():
    out = []
    for p in GAMES.glob("**/*.md.events.jsonl"):
        if p.name.startswith("."):
            continue
        out.append(game_entry(p))
    INDEX_CACHE.write_text(json.dumps(_index))
    out.sort(key=lambda g: g["mtime"], reverse=True)
    return out


def game_db(decks):
    """Card data for a pod's decks; a deck that has since been deleted is read from its
    newest copy in data/decks/.trash/, so old replays keep their card text."""
    db = load_db([d for d in decks if (DECK_DIR / f"{d}.cards.json").exists()])
    for d in decks:
        if (DECK_DIR / f"{d}.cards.json").exists():
            continue
        old = sorted((DECK_DIR / ".trash").glob(f"*/{d}.cards.json"))
        if old:
            for n, c in json.loads(old[-1].read_text()).items():
                db.setdefault(n, {**c, "pt": tuple(c["pt"]) if c.get("pt") else None})
    return db


def rename_deck(old, new):
    """Rename a deck's files and relabel its seats in every game log, P1(old) -> P1(new),
    after backing the touched game files up to .cache/web/. -> {"to", "games", "backup"}"""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", new):
        raise ValueError("deck names are lowercase letters, digits, - and _")
    if new == old:
        return {"to": new, "games": 0}
    if (DECK_DIR / f"{new}.txt").exists():
        raise ValueError(f"there's already a deck called {new}")
    files = [f for f in DECK_DIR.glob(f"{old}.*") if f.name.split(".")[0] == old]
    if not files:
        raise KeyError(old)
    label, relabel = f"({old})".encode(), f"({new})".encode()
    touched = [f for f in GAMES.rglob("*") if f.is_file() and not f.name.startswith(".")
               and f.suffix in (".md", ".jsonl", ".err", ".json") and label in f.read_bytes()]
    live = [f for f in touched if f.name.endswith(".events.jsonl") and is_running(gid_of(f))]
    if live:
        raise ValueError(f"{old} is in a game that's still running; rename it after that game ends")
    backup = CACHE / f"backup_rename_{old}_{datetime.now():%Y%m%d_%H%M%S}"
    for f in touched:
        dst = backup / f.relative_to(GAMES)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
        f.write_bytes(f.read_bytes().replace(label, relabel))
    for f in files:
        f.rename(DECK_DIR / (new + f.name[len(old):]))
    for c in (CACHE / "compiled").glob("*.json.gz"):
        c.unlink()
    games = {gid_of(f) for f in touched if f.name.endswith(".events.jsonl")}
    return {"to": new, "games": len(games), "backup": str(backup.relative_to(ROOT)) if touched else None}


def card_types(gid):
    """{card name: type line} for the pod's decks"""
    decks = [s["deck"] for s in game_entry(paths_of(gid)["events"]).get("seats", [])]
    return {n: d.get("type", "") + (" enchant player" if d.get("text", "").lower().startswith("enchant player") else "")
            for n, d in game_db(decks).items()}


COMPILER_V = int(Path(__file__).with_name("events.py").stat().st_mtime)   # a compiler edit recompiles every replay


def compiled_events(gid):
    """-> gzipped json bytes of the compiled event list (cached for finished games)."""
    p = paths_of(gid)["events"]
    st = p.stat()
    cp = CACHE / "compiled" / f"{gid}.{int(st.st_mtime)}.{st.st_size}.{COMPILER_V}.json.gz"
    if cp.exists():
        return cp.read_bytes()
    body = gzip.compress(json.dumps(compile_file(p, card_types(gid)), separators=(",", ":")).encode(), 6)
    if not is_running(gid):
        for old in (CACHE / "compiled").glob(f"{gid}.*.json.gz"):
            old.unlink()
        tmp = cp.with_suffix(f".{uuid.uuid4().hex[:6]}.tmp")
        tmp.write_bytes(body)
        tmp.replace(cp)
    return body


# ------------------------------------------------------------------ token art
TOKENS = ART / "tokens"
TOKENS.mkdir(parents=True, exist_ok=True)
TOKEN_INDEX = TOKENS / "_index.json"
_tokens = json.loads(TOKEN_INDEX.read_text()) if TOKEN_INDEX.exists() else {}   # name -> path str | None
_token_lock = threading.Lock()
FILLER = re.compile(r"\b(white|blue|black|red|green|colorless|legendary|artifact|enchantment|creature|token|"
                    r"\d+/\d+|copy|storm)\b", re.I)


def _scryfall(q):
    url = "https://api.scryfall.com/cards/search?" + urllib.parse.urlencode(
        {"q": q, "unique": "art", "order": "released", "dir": "desc"})
    req = urllib.request.Request(url, headers={"User-Agent": "agents-mtg-sim/0.1 (web replays)",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r).get("data") or []
    except Exception:
        return None
    for card in data:
        uris = card.get("image_uris") or (card.get("card_faces") or [{}])[0].get("image_uris") or {}
        if uris.get("normal"):
            return uris["normal"]
    return None


def token_art(name):
    """-> Path of an image for a token as the agents named it, or None."""
    with _token_lock:
        if name in _tokens:
            hit = _tokens[name]
            return Path(hit) if hit and Path(hit).exists() else None
        copying = re.search(r"\(cop(?:y|ying) (?:of )?([^)]+)\)", name)
        clean = re.sub(r"\([^)]*\)", "", name).strip()
        cands = [c for c in (copying and copying.group(1).strip(), clean) if c]
        found = None
        for c in cands:                                  # token copies of real cards wear the card
            if (ART / f"{slug(c)}.jpg").exists():
                found = ART / f"{slug(c)}.jpg"
                break
        if not found:
            core = " ".join(FILLER.sub(" ", clean).split()) or clean
            url = _scryfall(f'!"{clean}" t:token') or (core != clean and _scryfall(f'!"{core}" t:token')) \
                or _scryfall(f"t:token t:{core.split()[-1]}" if core else "") if clean else None
            if url:
                req = urllib.request.Request(url, headers={"User-Agent": "agents-mtg-sim/0.1 (web replays)"})
                try:
                    with urllib.request.urlopen(req, timeout=15) as r:
                        found = TOKENS / f"{slug(core or clean)}.jpg"
                        found.write_bytes(r.read())
                except Exception:
                    found = None
        _tokens[name] = str(found) if found else None
        TOKEN_INDEX.write_text(json.dumps(_tokens, indent=1))
        return found


# ------------------------------------------------------------------ decks
COLOR_SYM = re.compile(r"\{([^}]*)\}")


def color_identity(names, db):
    cols = set()
    for n in names:
        d = db.get(n) or {}
        cols |= {ch for ch in d.get("cost", "") if ch in "WUBRG"}
        for sym in COLOR_SYM.findall(d.get("text", "")):
            cols |= {ch for ch in sym if ch in "WUBRG"}
    return "".join(c for c in "WUBRG" if c in cols)


def deck_names():
    return sorted(p.stem for p in DECK_DIR.glob("*.txt"))


def deck_records():
    rec = {}
    for g in list_games():
        for s in g.get("seats", []):
            r = rec.setdefault(s["deck"], {"games": 0, "wins": 0})
            if g.get("result"):
                r["games"] += 1
                r["wins"] += g.get("winner") == s["handle"]
    return rec


def deck_summary(name, rec=None):
    main, cmdrs = parse_decklist((DECK_DIR / f"{name}.txt").read_text())
    db = load_db([name])
    return {"name": name, "commanders": cmdrs, "count": len(main) + len(cmdrs),
            "colors": color_identity(cmdrs, db), "scouting": deck_scouting(name),
            "missing": sorted({c for c in main + cmdrs if c not in db}),
            "record": (rec or {}).get(name, {"games": 0, "wins": 0})}


def deck_detail(name):
    text = (DECK_DIR / f"{name}.txt").read_text()
    main, cmdrs = parse_decklist(text)
    db = load_db([name])
    counts = {}
    for c in main:
        counts[c] = counts.get(c, 0) + 1
    games = [g for g in list_games() if any(s["deck"] == name for s in g.get("seats", []))]
    return {**deck_summary(name, deck_records()), "text": text,
            "main": [{"name": c, "qty": q} for c, q in counts.items()],
            "tags": {t: [{"name": c, "qty": q} for c, q in cs] for t, cs in deck_tags(name).items()},
            "strategy": deck_strategy(name), "personality": deck_personality(name),
            "cards": cards_payload(db), "games": games[:40]}


def cards_payload(db):
    return {n: {**d, "mv": mana_value(d.get("cost")), "art": (ART / f"{slug(n)}.jpg").exists()}
            for n, d in db.items()}


# ------------------------------------------------------------------ jobs + processes
def run_job(title, steps):
    """steps: [argv]. Runs sequentially in a thread; -> job id."""
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"title": title, "done": False, "ok": None, "log": []}

    def go():
        ok = True
        for argv in steps:
            if callable(argv):                  # python step: log(str) -> bool
                try:
                    ok = argv(JOBS[jid]["log"].append)
                except Exception as ex:
                    JOBS[jid]["log"].append(f"{type(ex).__name__}: {ex}")
                    ok = False
                if not ok:
                    break
                continue
            JOBS[jid]["log"].append("$ " + " ".join(argv[2:] if argv[:2] == ["uv", "run"] else argv))
            pr = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True)
            for ln in pr.stdout:
                JOBS[jid]["log"].append(ln.rstrip())
            if pr.wait() != 0:
                ok = False
                break
        JOBS[jid].update(done=True, ok=ok)
    threading.Thread(target=go, daemon=True).start()
    return jid


TAGS_PROMPT = """Read these screenshot files with your Read tool: {paths}

They show a Magic deck on a deckbuilding site, grouped by the builder's own tags or categories.
Reply with ONLY a JSON object mapping each tag name, spelled as shown, to the card names listed under it.
Use exact names from this decklist, and skip anything that is not in it:
{names}"""


def tags_from_images(name, images):
    """images: [data-URL]. -> python job step that writes data/decks/<name>.tags.json"""
    import base64
    up = CACHE / "uploads"
    up.mkdir(exist_ok=True)
    paths = []
    for k, url in enumerate(images):
        head, _, b64 = url.partition(",")
        ext = "png" if "png" in head else "jpg"
        p = up / f"{name}_{uuid.uuid4().hex[:6]}_{k}.{ext}"
        p.write_bytes(base64.b64decode(b64))
        paths.append(str(p))

    def step(log):
        main, cmdrs = parse_decklist((DECK_DIR / f"{name}.txt").read_text())
        names = sorted(set(main + cmdrs))
        prompt = TAGS_PROMPT.format(paths=", ".join(paths), names="\n".join(names))
        model = settings()["models"].get("claude") or "opus"
        log(f"reading {len(paths)} screenshot{'s' if len(paths) != 1 else ''} with claude ({model})")
        r = subprocess.run(["claude", "-p", prompt, "--model", model, "--allowedTools", "Read", "--output-format", "json"],
                           capture_output=True, text=True, timeout=600, cwd=up)
        reply = json.loads(r.stdout).get("result", "") if r.stdout.strip().startswith("{") else r.stdout
        m = re.search(r"\{.*\}", reply, re.S)
        if not m:
            log("no tag JSON in the reply:\n" + reply[-800:])
            return False
        known = {n.lower(): n for n in names}
        tags, dropped = {}, []
        for tag, cards in json.loads(m.group(0)).items():
            got = [known[c.lower()] for c in cards if c.lower() in known]
            dropped += [c for c in cards if c.lower() not in known]
            if got:
                tags[tag] = got
        (DECK_DIR / f"{name}.tags.json").write_text(json.dumps(tags, indent=1))
        log(f"{len(tags)} tags, {sum(map(len, tags.values()))} cards" + (f"; not in the list: {', '.join(dropped)}" if dropped else ""))
        return True
    return step


QA_BRIEF = """You answer questions about one game of an LLM-agent Commander (Magic: The Gathering) simulation.
The evidence is in files under {root}. Read them with your tools (rg, grep, sed); never guess.
- {md}: the full game log, one line per event. "## Turn N — Px(deck)" headers start each seat's turn,
  where N is the round. Lines beginning "[private]" are a seat's hidden thinking. "↳" lines are effects,
  "!!" lines are engine warnings.
- {events}: the same log as JSON lines, each with a full state snapshot (hands, libraries, battlefield,
  life). It is large: grep it for what you need.
- data/decks/<deck>.txt (decklist and memos) and data/decks/<deck>.cards.json (oracle text) for: {decks}.

Answer concisely and plainly. Count by actually counting log lines, and say what you counted.
Cite turns as T<round> (for example T7) and quote the decisive log lines. If the log cannot answer
the question, say so.

Question: {q}"""
QA = {}               # (game id, agent kind) -> agent session


def qa_step(gid, question, kind, jid):
    def step(log):
        from mtgsim.agents import ClaudeAgent, CodexAgent
        p = paths_of(gid)
        cfg = settings()
        key = (gid, kind)
        first = key not in QA
        if first:
            cls = ClaudeAgent if kind == "claude" else CodexAgent
            QA[key] = cls(f"qa-{gid}", model=cfg["models"].get(kind) or None, resume=True)
        agent = QA[key]
        if first:
            decks = ", ".join(s["deck"] for s in game_entry(p["events"]).get("seats", []))
            prompt = QA_BRIEF.format(root=ROOT, md=p["md"].relative_to(ROOT), events=p["events"].relative_to(ROOT),
                                     decks=decks, q=question)
        else:
            prompt = question
        log(f"asking {kind}")
        reply = agent.ask(prompt).strip()
        if getattr(agent, "gave_up", False):
            QA.pop(key, None)
            log("the agent gave up; ask again for a fresh session")
            return False
        JOBS[jid]["result"] = reply
        return True
    return step


def new_game_id(prefix="web"):
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    gid, n = f"{prefix}_{stamp}", 2
    while paths_of(gid)["events"].exists() or gid in PROCS:
        gid, n = f"{prefix}_{stamp}_{n}", n + 1
    return gid


def spawn(gid, argv, meta, script=None, append_console=False):
    p = paths_of(gid)
    con = open(p["console"], "a" if append_console else "w")
    script = script or ("web/humanplay.py" if any(x.startswith("human") for x in ",".join(argv).replace(",", " ").split()) else "play.py")
    pr = subprocess.Popen(["uv", "run", script, *argv, "--log", str(p["md"].relative_to(ROOT))],
                          cwd=ROOT, stdin=subprocess.DEVNULL, stdout=con, stderr=subprocess.STDOUT,
                          start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    PROCS[gid] = pr
    write_meta(gid, pid=pr.pid, started=time.time(), **meta)
    for _ in range(100):                  # the log files exist once the Game is constructed
        if p["events"].exists() or pr.poll() is not None:
            break
        time.sleep(0.1)
    if pr.poll() is not None and not p["events"].exists():
        raise RuntimeError("play.py exited at startup:\n" + p["console"].read_text()[-3000:])
    return gid


def start_game(body):
    seats = body.get("seats") or []
    if not 2 <= len(seats) <= 4:
        raise ValueError("a pod is 2-4 seats")
    cfg = settings()
    body = {**{k: cfg[k] for k in ("codex_effort", "codex_tier", "max_turns", "max_actions")},
            **{k: v for k, v in body.items() if v not in (None, "")}}
    pod = []
    for s in seats:
        if s["deck"] not in deck_names():
            raise ValueError(f"no deck {s['deck']!r}")
        kind = s.get("agent") or cfg["agent"]
        model = "" if kind == "human" else (s.get("model") or "").strip() or cfg["models"].get(kind, "")
        pod.append(f"{kind}@{model}:{s['deck']}" if model else f"{kind}:{s['deck']}")
    argv = ["--pod", ",".join(pod)]
    if any(s.get("agent") == "human" for s in seats):
        argv += ["--human-agent", cfg.get("scribe") or "codex"]
    for kind, flag in (("claude", "--claude-model"), ("codex", "--codex-model")):
        if cfg["models"].get(kind):
            argv += [flag, cfg["models"][kind]]
    for k, flag in (("seed", "--seed"), ("max_turns", "--max-turns"), ("max_actions", "--max-actions"),
                    ("codex_tier", "--codex-tier"), ("codex_effort", "--codex-effort")):
        if body.get(k) not in (None, ""):
            argv += [flag, str(body[k])]
    if body.get("mock"):
        argv.append("--mock")
    gid = new_game_id()
    return spawn(gid, argv, {"title": body.get("title") or None, "spec": body})


def branch_game(gid, body):
    src = paths_of(gid)["events"]
    argv = ["--resume", str(src.relative_to(ROOT)), "--at", str(int(body.get("at", 0))),
            "--minds", body.get("minds") or "fresh"]
    if body.get("max_turns"):
        argv += ["--max-turns", str(body["max_turns"])]
    if body.get("agents"):
        decks = [s["deck"] for s in game_entry(src)["seats"]]
        argv += ["--pod", ",".join(f"{a}:{d}" for a, d in zip(body["agents"], decks))]
    cfg = settings()
    for kind, flag in (("claude", "--claude-model"), ("codex", "--codex-model")):
        if cfg["models"].get(kind):
            argv += [flag, cfg["models"][kind]]
    nid = new_game_id("branch")
    return spawn(nid, argv, {"branch_of": {"id": gid, "at": int(body.get("at", 0))},
                             "title": body.get("title") or None})


TURN_LINE = re.compile(rb'"line": "\\n## Turn \d+')


def cut_at_turn(gid):
    """Trim a stopped game's log to just before its last turn header, a clean point to
    continue from. -> events kept"""
    p = paths_of(gid)
    lines = p["events"].read_bytes().splitlines(keepends=True)
    k = max((i for i, ln in enumerate(lines) if TURN_LINE.search(ln)), default=None)
    if k is None or k == 0:
        return len(lines)
    keep = lines[:k]
    p["events"].write_bytes(b"".join(keep))
    evs = [json.loads(l) for l in keep if l.strip()]
    p["md"].write_text("".join(("[private] " if e.get("private") else "") + e.get("line", "") + "\n" for e in evs))
    return len(keep)


def pause_game(gid, then_resume=False):
    """Stop the game at its next turn boundary and trim it to that point; optionally continue
    it straight away on fresh code. Runs in the background; status lives in the meta file."""
    if not is_running(gid):
        raise ValueError("game isn't running")
    write_meta(gid, pausing=True, restarting=then_resume)
    p = paths_of(gid)

    def go():
        size = p["events"].stat().st_size
        while is_running(gid):
            with open(p["events"], "rb") as f:
                f.seek(size)
                new = f.read()
            if TURN_LINE.search(new):
                break
            time.sleep(0.3)
        if is_running(gid):
            pr = PROCS.get(gid)
            pid = pr.pid if pr else read_meta(gid).get("pid")
            # the whole process group has to be gone before the cut: uv's python child keeps
            # writing for a moment after the uv parent exits
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
                for k in range(100):
                    time.sleep(0.1)
                    os.killpg(pgid, 0)                   # raises once the group is empty
                    if k == 50:
                        os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
            if pr:
                pr.wait()
            cut_at_turn(gid)
            for c in (CACHE / "compiled").glob(f"{gid}.*.json.gz"):
                c.unlink()
            write_meta(gid, pausing=False, paused=True)
            if then_resume:
                resume_game(gid)
        else:
            write_meta(gid, pausing=False, restarting=False)       # it ended on its own
    threading.Thread(target=go, daemon=True).start()


def resume_game(gid):
    p = paths_of(gid)
    meta = read_meta(gid)
    spec = meta.get("spec") or {}
    human = any((s or {}).get("agent") == "human" for s in spec.get("seats") or [])
    argv = ["--resume", str(p["events"].relative_to(ROOT)), "--continue", "--minds", "cloned"]
    if spec.get("mock"):
        argv.append("--mock")
    if spec.get("max_turns"):
        argv += ["--max-turns", str(spec["max_turns"])]
    if spec.get("max_actions"):
        argv += ["--max-actions", str(spec["max_actions"])]
    cfg = settings()
    for kind, flag in (("claude", "--claude-model"), ("codex", "--codex-model")):
        if cfg["models"].get(kind):
            argv += [flag, cfg["models"][kind]]
    if human:
        argv += ["--human-agent", cfg.get("scribe") or "codex"]
    write_meta(gid, paused=False)
    spawn(gid, argv, {"restarting": False}, script="web/humanplay.py" if human else "play.py", append_console=True)
    return gid


def stop_game(gid):
    pr = PROCS.get(gid)
    pid = pr.pid if pr else read_meta(gid).get("pid")
    if not pid or not is_running(gid):
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGINT)      # SIGINT lets play.py's atexit export run
    except OSError:
        return False

    def reap():
        for _ in range(50):
            if not is_running(gid):
                return
            time.sleep(0.2)
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            pass
    threading.Thread(target=reap, daemon=True).start()
    return True


CLAUDE_USAGE = Path.home() / ".claude" / "state" / "rate_limits.json"
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"


def usage():
    """Plan limits as the CLIs report them. -> {"claude": [Window], "codex": [Window]}
    Window = {"name": "5h"|"7d", "used": 0-100, "resets_at": epoch s, "seen": epoch s}
    Claude's come from a statusline script that saves the rate_limits Claude Code hands it;
    Codex's from the newest token_count event in its session logs."""
    out = {"claude": [], "codex": []}
    now = time.time()
    if CLAUDE_USAGE.exists():
        d = json.loads(CLAUDE_USAGE.read_text())
        for key, name in (("five_hour", "5h"), ("seven_day", "7d")):
            w = (d.get("rate_limits") or {}).get(key)
            if w and w.get("resets_at", 0) > now:
                out["claude"].append({"name": name, "used": w.get("used_percentage"), "resets_at": w.get("resets_at"),
                                      "seen": d.get("seen")})
    newest = sorted(CODEX_SESSIONS.glob("*/*/*/*.jsonl"), key=lambda f: f.stat().st_mtime)[-12:] if CODEX_SESSIONS.exists() else []
    best = None
    for f in reversed(newest):
        with open(f, "rb") as fh:
            fh.seek(max(0, f.stat().st_size - 200_000))
            tail = fh.read().decode("utf-8", "replace")
        for ln in reversed(tail.splitlines()):
            if '"rate_limits"' not in ln:
                continue
            try:
                ev = json.loads(ln)
            except json.JSONDecodeError:
                continue
            rl = (ev.get("payload") or {}).get("rate_limits") or ev.get("rate_limits")
            if rl:
                best = (rl, f.stat().st_mtime)
                break
        if best:
            break
    if best:
        rl, seen = best
        for key in ("primary", "secondary"):
            w = rl.get(key)
            if w and w.get("used_percent") is not None:
                mins = w.get("window_minutes") or 0
                out["codex"].append({"name": "5h" if mins and mins <= 600 else "7d" if mins >= 7000 else f"{mins}m",
                                     "used": w["used_percent"], "resets_at": w.get("resets_at"), "seen": seen})
    return out


def agents_info():
    return [{"kind": "claude", "available": bool(shutil.which("claude")), "model": DEFAULT_MODELS["claude"]},
            {"kind": "codex", "available": bool(shutil.which("codex")), "model": DEFAULT_MODELS["codex"]},
            {"kind": "human", "available": True, "model": ""}]


# ------------------------------------------------------------------ http
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if args and str(args[1])[:1] in "45":
            sys.stderr.write(f"{self.command} {self.path} -> {args[1]}\n")

    def _send(self, code, body=b"", ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, separators=(",", ":")).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def _route(self, method):
        url = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(url.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        try:
            if path.startswith("/api/"):
                return self._api(method, path[5:].strip("/").split("/"), q)
            if method != "GET":
                return self._send(405, {"error": "method"})
            if path.startswith("/tokenart/"):
                f = token_art(path[len("/tokenart/"):])
                return self._file(f, "image/jpeg", "public, max-age=604800") if f else \
                    self._send(404, b"", "text/plain", {"Cache-Control": "max-age=3600"})
            if path.startswith("/art/"):
                return self._file(ART / Path(path[5:]).name, "image/jpeg", "public, max-age=604800")
            if path in ("/", "/index.html"):
                return self._file(STATIC / "index.html", "text/html; charset=utf-8", "no-cache")
            f = (STATIC / path.lstrip("/")).resolve()
            if f.is_relative_to(STATIC.resolve()) and f.is_file():
                ctype = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
                         ".woff2": "font/woff2", ".png": "image/png", ".html": "text/html"}.get(
                    f.suffix, "application/octet-stream")
                return self._file(f, ctype, "no-cache")
            return self._send(404, {"error": "not found"})
        except KeyError as ex:
            self._send(404, {"error": f"not found: {ex}"})
        except (ValueError, RuntimeError) as ex:
            self._send(400, {"error": str(ex)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as ex:
            traceback.print_exc()
            self._send(500, {"error": f"{type(ex).__name__}: {ex}"})

    def _file(self, f, ctype, cache):
        if not f.is_file():
            return self._send(404, {"error": "not found"})
        self._send(200, f.read_bytes(), ctype, {"Cache-Control": cache})

    def _api(self, method, parts, q):
        head, rest = parts[0], parts[1:]
        if head == "boot" and method == "GET":
            return self._send(200, {"agents": agents_info(), "settings": settings(), "art": sorted(p.stem for p in ART.glob("*.jpg")),
                                    "decks": deck_names(),
                                    "commanders": {n: parse_decklist((DECK_DIR / f"{n}.txt").read_text())[1]
                                                   for n in deck_names()}})
        if head == "decks":
            if not rest and method == "GET":
                rec = deck_records()
                return self._send(200, [deck_summary(n, rec) for n in deck_names()])
            name = rest[0]
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", name):
                raise ValueError("deck names are lowercase letters, digits, - and _")
            if method == "GET":
                if not (DECK_DIR / f"{name}.txt").exists():
                    raise KeyError(name)
                return self._send(200, deck_detail(name))
            if method == "DELETE":
                # into data/decks/.trash/<stamp>/, recoverable by moving the files back
                files = [f for f in DECK_DIR.glob(f"{name}.*") if f.name.split(".")[0] == name]
                if not files:
                    raise KeyError(name)
                trash = DECK_DIR / ".trash" / f"{datetime.now():%Y%m%d_%H%M%S}"
                trash.mkdir(parents=True, exist_ok=True)
                for f in files:
                    f.rename(trash / f.name)
                return self._send(200, {"trashed": [f.name for f in files], "to": str(trash.relative_to(ROOT))})
            if method == "POST" and rest[1:] == ["rename"]:
                return self._send(200, rename_deck(name, (self._json_body().get("to") or "").strip().lower()))
            if method == "POST" and rest[1:] == ["fetch"]:
                main, cmdrs = parse_decklist((DECK_DIR / f"{name}.txt").read_text())
                return self._send(200, {"job": run_job(f"fetch {name}", [
                    ["uv", "run", "scripts/fetch_oracle.py", f"data/decks/{name}.txt"],
                    ["uv", "run", "scripts/fetch_art.py", *sorted(set(main + cmdrs))]])})
            if method == "POST" and rest[1:] == ["tags-images"]:
                imgs = self._json_body().get("images") or []
                if not imgs:
                    raise ValueError("no images")
                return self._send(200, {"job": run_job(f"tags {name}", [tags_from_images(name, imgs)])})
            if method in ("PUT", "POST"):
                body = self._json_body()
                text = body.get("text", "")
                main, cmdrs = parse_decklist(text)
                if not cmdrs:
                    raise ValueError("no commander found — use a 'Commander' section or a *CMDR* marker")
                (DECK_DIR / f"{name}.txt").write_text(text)
                if body.get("tags"):
                    (DECK_DIR / f"{name}.tags.json").write_text(json.dumps(body["tags"], indent=1))
                # memo and ordering edits save instantly; only cards the database lacks send it to scryfall
                side = DECK_DIR / f"{name}.cards.json"
                known = json.loads(side.read_text()) if side.exists() else {}
                new_cards = sorted({c for c in main + cmdrs if c not in known})
                if not new_cards and not body.get("images"):
                    return self._send(200, {"job": None, "cards": len(main) + len(cmdrs)})
                steps = [["uv", "run", "scripts/fetch_oracle.py", f"data/decks/{name}.txt"],
                         ["uv", "run", "scripts/fetch_art.py", *new_cards]] if new_cards else []
                if body.get("images"):
                    steps.append(tags_from_images(name, body["images"]))
                jid = run_job(f"fetch {name}", steps)
                return self._send(200, {"job": jid, "cards": len(main) + len(cmdrs)})
        if head == "usage" and method == "GET":
            return self._send(200, usage())
        if head == "settings":
            if method == "POST":
                b = self._json_body()
                cur = settings()
                cur.update({k: b[k] for k in SETTINGS_DEFAULT if k in b and k != "models"})
                cur["models"].update(b.get("models") or {})
                SETTINGS_PATH.write_text(json.dumps(cur, indent=1))
            return self._send(200, settings())
        if head == "import" and method == "POST":
            url = (self._json_body().get("url") or "").strip()
            r = subprocess.run(["uv", "run", "--with", "curl_cffi", str(WEB / "importer.py"), url], cwd=ROOT,
                               capture_output=True, text=True, timeout=90)
            if r.returncode != 0:
                raise ValueError((r.stderr.strip().splitlines() or ["import failed"])[-1])
            return self._send(200, r.stdout)
        if head == "jobs" and rest:
            return self._send(200, JOBS[rest[0]])
        if head == "cards" and method == "GET":
            decks = [d for d in q.get("decks", "").split(",") if d]
            return self._send(200, cards_payload(game_db(decks)))
        if head == "games":
            if not rest:
                if method == "GET":
                    return self._send(200, list_games())
                if method == "POST":
                    return self._send(200, {"id": start_game(self._json_body())})
            if rest[0] == "import" and method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                lines = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]
                if not lines or "state" not in lines[0]:
                    raise ValueError("not an events.jsonl replay")
                gid = new_game_id("import")
                p = paths_of(gid)
                p["events"].write_text("\n".join(json.dumps(l) for l in lines) + "\n")
                p["md"].write_text("\n".join(("[private] " if l.get("private") else "") + l.get("line", "")
                                             for l in lines) + "\n")
                return self._send(200, {"id": gid})
            gid, sub = rest[0], (rest[1] if len(rest) > 1 else "")
            p = paths_of(gid)
            if not p["events"].exists():
                raise KeyError(gid)
            if sub == "" and method == "GET":
                return self._send(200, {**game_entry(p["events"]), "meta": read_meta(gid),
                                        "postmortem": p["postmortem"].exists()})
            if sub == "events":
                return self._send(200, compiled_events(gid), "application/json",
                                  {"Content-Encoding": "gzip", "Cache-Control": "no-cache"})
            if sub == "stream":
                return self._stream(gid)
            if sub == "download":
                return self._send(200, gzip.compress(p["events"].read_bytes()), "application/gzip",
                                  {"Content-Disposition": f'attachment; filename="{gid}.events.jsonl.gz"'})
            if sub == "console":
                return self._send(200, p["console"].read_text()[-20000:] if p["console"].exists() else "",
                                  "text/plain; charset=utf-8")
            if sub == "meta" and method == "POST":
                b = self._json_body()
                return self._send(200, write_meta(gid, **{k: b[k] for k in ("title", "starred") if k in b}))
            if sub in ("pause", "restart") and method == "POST":
                pause_game(gid, then_resume=(sub == "restart"))
                return self._send(200, {"ok": True})
            if sub == "resume" and method == "POST":
                if is_running(gid):
                    raise ValueError("game is already running")
                return self._send(200, {"id": resume_game(gid)})
            if sub == "stop" and method == "POST":
                return self._send(200, {"stopped": stop_game(gid)})
            if sub == "judge" and method == "POST":
                b = self._json_body()
                text = " ".join((b.get("text") or "").split())
                if not text and not b.get("ask"):
                    raise ValueError("empty")
                with open(p["judge"], "a") as f:
                    f.write((f"JUDGE {text}" if b.get("ask") else text).strip() + "\n")
                return self._send(200, {"ok": True})
            if sub == "branch" and method == "POST":
                return self._send(200, {"id": branch_game(gid, self._json_body())})
            if sub == "human":
                chan = Path(f"{p['md']}.human")
                if method == "POST":
                    b = self._json_body()
                    chan.mkdir(exist_ok=True)
                    with open(chan / "in.jsonl", "a") as f:
                        f.write(json.dumps({"t": time.time(), "seat": b.get("seat"), "text": str(b.get("text", ""))[:4000]}) + "\n")
                    return self._send(200, {"ok": True})
                since = int(q.get("since") or 0)
                out = (chan / "out.jsonl").read_text().splitlines() if (chan / "out.jsonl").exists() else []
                state = json.loads((chan / "state.json").read_text()) if (chan / "state.json").exists() else {}
                return self._send(200, {"state": state, "n": len(out),
                                        "out": [json.loads(l) for l in out[since:] if l.strip()]})
            if sub == "ask" and method == "POST":
                b = self._json_body()
                q = (b.get("question") or "").strip()
                if not q:
                    raise ValueError("empty question")
                kind = b.get("agent") if b.get("agent") in ("claude", "codex") else "codex"
                jid = uuid.uuid4().hex[:10]
                JOBS[jid] = {"title": f"ask {gid}", "done": False, "ok": None, "log": []}
                step = qa_step(gid, q, kind, jid)

                def go():
                    ok = False
                    try:
                        ok = step(JOBS[jid]["log"].append)
                    except Exception as ex:
                        JOBS[jid]["log"].append(f"{type(ex).__name__}: {ex}")
                    JOBS[jid].update(done=True, ok=ok)
                threading.Thread(target=go, daemon=True).start()
                return self._send(200, {"job": jid})
            if sub == "postmortem":
                if method == "POST":
                    return self._send(200, {"job": run_job(f"postmortem {gid}", [
                        ["uv", "run", "scripts/postmortem.py", str(p["md"].relative_to(ROOT))]])})
                return self._send(200, p["postmortem"].read_text() if p["postmortem"].exists() else "",
                                  "text/markdown; charset=utf-8")
        return self._send(404, {"error": "no such endpoint"})

    def _stream(self, gid):
        """SSE: every compiled event from the start, then tails the file while the game runs.
        data frames carry arrays of compiled events; `event: end` closes."""
        p = paths_of(gid)["events"]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        comp, buf, idle = Compiler(card_types(gid) if p.exists() else None), b"", 0.0
        for _ in range(300):
            if p.exists() or not is_running(gid):
                break
            time.sleep(0.2)
        if not p.exists():
            self.wfile.write(b"event: end\ndata: {}\n\n")
            return
        with open(p, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if chunk:
                    buf += chunk
                    *whole, buf = buf.split(b"\n")
                    batch = []
                    for ln in whole:
                        if ln.strip():
                            try:
                                batch.append(comp.feed(json.loads(ln)))
                            except json.JSONDecodeError:
                                continue
                    for k in range(0, len(batch), 200):
                        self.wfile.write(b"data: " + json.dumps(batch[k:k + 200], separators=(",", ":")).encode()
                                         + b"\n\n")
                    self.wfile.flush()
                    idle = 0.0
                    continue
                if not is_running(gid):
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                    return
                time.sleep(0.2)
                idle += 0.2
                if idle >= 15:                           # heartbeat keeps proxies and the tab honest
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    idle = 0.0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--open", action="store_true", help="open the browser")
    args = ap.parse_args()
    os.chdir(ROOT)                     # agent subprocesses (Q&A, postmortem) work from the repo root
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    url = f"http://{args.host}:{args.port}/"
    print(f"mtgsim web: {url}")
    if args.open:
        import webbrowser
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
