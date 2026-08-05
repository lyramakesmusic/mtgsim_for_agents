"""Branch finished (or interrupted) games from any saved moment.

Every line in a game's events.jsonl carries a full state snapshot, so every
event is a save point. restore_game() rebuilds a live Game from one: players,
libraries (exact order when recorded; reconstructed-and-shuffled for logs
older than the library_cards field), the stack machinery counters, standing
effects, and the public table log — so resumed seats receive the game's whole
public history in their first prompt.

Minds branch too. clone_session() copies a seat's recorded claude/codex
session file, truncates it to the save point (a session file at game end
remembers the future — a branched mind must not), rebinds it to a fresh id,
and returns that id for the resumed agent. Session files are jsonl: claude
turns end at "last-prompt" records, codex turns at "task_complete" events.
"""
import copy
import json
import re
import uuid
from collections import Counter
from pathlib import Path

from .engine import Game

TURN_HEADER = re.compile(r"^## Turn (\d+) — (P\d)\(")


def load_events(events_path):
    return [json.loads(l) for l in Path(events_path).read_text().splitlines() if l.strip()]


def pick_event(events, at=None):
    """Resolve a resume index: the given index (or last event), snapped back
    to the nearest clean state (empty stack). Old logs without the
    stack_empty field count as clean."""
    idx = len(events) - 1 if at is None else max(0, min(int(at), len(events) - 1))
    while idx > 0 and events[idx]["state"].get("stack_empty") is False:
        idx -= 1
    return idx


def resume_point(table):
    """(turn, seat_index) of the half-turn after the save point: the last
    turn header in the log names the half-turn in progress, and play resumes
    with the following seat."""
    last = None
    for line in table:
        m = TURN_HEADER.match(line.lstrip())
        if m:
            last = (int(m.group(1)), int(m.group(2)[1]) - 1)
    if last is None:
        return 1, 0
    turn, seat = last
    return (turn, seat + 1)


def _rebuild_library(decklist, ps):
    """Library for logs that predate library_cards: everything from the deck
    not visible in a zone, order unknown."""
    lib = Counter(decklist)
    for name in ps["hand"] + ps["graveyard"] + ps["exile"] + \
            [x["name"] for x in ps["battlefield"] if not x.get("token")]:
        if lib.get(name):
            lib[name] -= 1
    return list(lib.elements())


def restore_game(db, decks, agents, events_path, log_path, max_turns, rng,
                 at=None, edits=None, judge_factory=None, console_private="all"):
    """Rebuild a live Game from an event. Returns (game, from_turn, from_seat).
    edits: {"P4": {"hand": [...], "life": 30, ...}} — merged onto the seat
    after restore; any Player field goes."""
    events = load_events(events_path)
    idx = pick_event(events, at)
    state = events[idx]["state"]
    table = [e["line"] for e in events[:idx + 1] if not e.get("private")]

    g = Game(db, decks, agents, 0, log_path, max_turns, rng,
             judge_factory=judge_factory, console_private=console_private)
    g.turn = state["turn"]
    g.next_id = state.get("next_id") or 1
    g.stack_seq = state.get("stack_seq") or 0
    g.standing = [dict(st) for st in state.get("standing") or []]
    for pl, ps in zip(g.p, state["players"]):
        pl.life = ps["life"]
        pl.alive = ps["alive"]
        pl.hand = list(ps["hand"])
        pl.graveyard = list(ps["graveyard"])
        pl.exile = list(ps["exile"])
        pl.command_zone = ps["command_zone"]
        pl.commander_tax = ps["commander_tax"]
        pl.lands_played = ps.get("lands_played", 0)
        pl.drew_this_turn = ps.get("drew_this_turn", 0)
        pl.battlefield = copy.deepcopy(ps["battlefield"])
        pl.library = list(ps.get("library_cards")
                          if ps.get("library_cards") is not None
                          else _rebuild_library(pl.decklist, ps))
        if ps.get("library_cards") is None:
            rng.shuffle(pl.library)
        if pl.battlefield:
            top = max(int(x["id"].rsplit("#", 1)[1]) for x in pl.battlefield
                      if "#" in x["id"])
            g.next_id = max(g.next_id, top + 1)
    for handle, patch in (edits or {}).items():
        pl = next(q for q in g.p if q.handle == handle)
        for field, value in patch.items():
            setattr(pl, field, copy.deepcopy(value))
        g.log(f"⚖ JUDGE: branch edit — {handle} {', '.join(patch)} changed by the lab.")
    g.table = table + g.table            # public history travels with the state
    g.log_sent = [0] * len(g.p)
    g.force_full = [True] * len(g.p)
    from_turn, from_seat = resume_point(table)
    g.log(f"(branched from {Path(str(events_path)).name} at event {idx}, "
          f"turn {state['turn']} — play resumes)")
    return g, from_turn, from_seat


# ---------------- mind surgery ----------------

def _claude_dir():
    slug = str(Path.cwd()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug


def clone_claude_session(session_id, calls):
    """Copy + truncate a claude session to its first `calls` exchanges,
    rebound to a fresh id. Returns the new id, or None if the file is gone."""
    src = _claude_dir() / f"{session_id}.jsonl"
    if not src.exists():
        return None
    new_id = str(uuid.uuid4())
    kept, seen = [], 0
    for line in src.read_text().splitlines():
        kept.append(line.replace(session_id, new_id))
        if '"type":"last-prompt"' in line.replace(" ", "") or json.loads(line).get("type") == "last-prompt":
            seen += 1
            if seen >= calls:
                break
    (src.parent / f"{new_id}.jsonl").write_text("\n".join(kept) + "\n")
    return new_id


def clone_codex_session(session_id, calls):
    """Copy + truncate a codex rollout to its first `calls` turns, rebound to
    a fresh id. Returns the new id, or None if the file is gone."""
    hits = list((Path.home() / ".codex" / "sessions").glob(f"*/*/*/rollout-*-{session_id}.jsonl"))
    if not hits:
        return None
    src = hits[0]
    new_id = str(uuid.uuid4())
    kept, seen = [], 0
    for line in src.read_text().splitlines():
        kept.append(line.replace(session_id, new_id))
        if json.loads(line).get("payload", {}).get("type") == "task_complete":
            seen += 1
            if seen >= calls:
                break
    dst = src.parent / src.name.replace(session_id, new_id)
    dst.write_text("\n".join(kept) + "\n")
    return dst, new_id


def clone_session(kind, session_id, calls):
    """Branch one mind. Returns (new_session_id, path_note) or (None, reason)."""
    if not session_id or not calls:
        return None, "no recorded session"
    if kind == "ClaudeAgent":
        nid = clone_claude_session(session_id, calls)
        return (nid, str(_claude_dir() / f"{nid}.jsonl")) if nid else (None, "session file missing")
    if kind == "CodexAgent":
        r = clone_codex_session(session_id, calls)
        return (r[1], str(r[0])) if r else (None, "session file missing")
    return None, f"{kind} sessions aren't cloneable"
