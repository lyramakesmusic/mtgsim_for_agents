#!/usr/bin/env python3
"""CLI glue — assemble a pod and run it.

Pod seats are agent:deck pairs (agent defaults to claude):

  uv run play.py --pod claude:snakes,claude:meren,codex:squirrels,codex:talrand
  uv run play.py --pod squirrels,snakes --mock          # plumbing test, no LLMs
  uv run play.py --pod claude:snakes,codex:talrand --max-turns 8 --log duel.md
  uv run play.py --stateless                            # no session resume (goldfish agents)
"""
import argparse
import random

from mtgsim.agents import AGENT_TYPES, MockAgent
from mtgsim.cards import deck_names, deck_strategy, load_db, load_deck
from mtgsim.engine import Game

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pod", default="claude:snakes,claude:squirrels",
                    help="2-4 comma-separated seats, each 'agent:deck' or just 'deck' "
                         f"(agents: {', '.join(AGENT_TYPES)}, human; decks: {', '.join(deck_names())})")
    ap.add_argument("--human-agent", default="codex", choices=["claude", "codex"],
                    help="which brain scribes for a human seat (translates your words "
                         "into protocol actions; default codex)")
    ap.add_argument("--show-hidden", action="store_true",
                    help="print other seats' private thinking and draw contents even with a "
                         "human playing (default: hidden when a human is seated — no wallhacks)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--max-turns", type=int, default=12)
    ap.add_argument("--claude-model", default="opus")
    ap.add_argument("--codex-model", default="gpt-5.6-terra",
                    help="codex model for all codex seats + judge + scribe (default gpt-5.6-terra; "
                         "want sol? --codex-model gpt-5.6-sol, or per-seat codex@gpt-5.6-sol:deck)")
    ap.add_argument("--codex-tier", default=None, choices=["fast", "priority", "flex"],
                    help="codex service_tier (what /fast sets); default: your ~/.codex config")
    ap.add_argument("--codex-effort", default=None,
                    choices=["minimal", "low", "medium", "high"],
                    help="codex model_reasoning_effort; default: your ~/.codex config")
    ap.add_argument("--mock", action="store_true", help="scripted dummy agents (plumbing test)")
    ap.add_argument("--stateless", action="store_true", help="no session continuity")
    ap.add_argument("--log", default=None, help="default: games/<timestamp>.md")
    ap.add_argument("--transcripts", default=None, help="per-agent transcript dir ('' to disable; default: logs/<timestamp>)")
    ap.add_argument("--resume", default=None, metavar="EVENTS",
                    help="branch a saved game: path to its .md or .md.events.jsonl. "
                         "Seats and decks come from the log; --pod overrides agent kinds.")
    ap.add_argument("--at", type=int, default=None,
                    help="event index to branch from (default: latest; snaps to a clean stack)")
    ap.add_argument("--edit", default=None,
                    help="state edits as inline JSON or @file: {\"P4\": {\"hand\": [...], \"life\": 30}}")
    ap.add_argument("--minds", default="fresh", choices=["fresh", "cloned"],
                    help="fresh: new sessions briefed with the game's public history. "
                         "cloned: each seat's recorded session copied and truncated to the "
                         "save point — same minds, same private plans, no memory of the future")
    ap.add_argument("--hold", action="store_true",
                    help="after mind-cloning, print the session file paths and wait — your "
                         "window to edit memories before play resumes")
    ap.add_argument("--dump-state", action="store_true",
                    help="print the chosen event's state as JSON and exit (edit it, then --edit @file)")
    args = ap.parse_args()
    from datetime import datetime
    from pathlib import Path
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    if not args.log:
        Path("games").mkdir(exist_ok=True)
        args.log = f"games/{stamp}.md"
    if args.transcripts is None:
        args.transcripts = f"logs/{stamp}"          # per-run dir: runs never clobber each other

    seed = args.seed if args.seed is not None else random.randrange(10**6)
    rng = random.Random(seed)

    resume_info = None
    if args.resume:
        import json
        import re as _re
        from mtgsim.branching import load_events, pick_event
        ev_path = args.resume if args.resume.endswith(".jsonl") else f"{args.resume}.events.jsonl"
        events = load_events(ev_path)
        idx = pick_event(events, args.at)
        if args.dump_state:
            print(json.dumps(events[idx]["state"], indent=1))
            raise SystemExit
        decknames = _re.findall(r"P\d\(([^)]+)\)", events[0]["line"])
        sess = events[idx]["state"].get("sessions") or [{} for _ in decknames]
        kindmap = {"ClaudeAgent": "claude", "CodexAgent": "codex",
                   "OpenRouterAgent": "openrouter", "LocalAgent": "local",
                   "HumanAgent": "human"}
        if args.pod != ap.get_default("pod"):     # explicit --pod overrides recorded kinds
            podkinds = [s.strip().rpartition(":")[0] or "codex" for s in args.pod.split(",")]
        else:
            podkinds = [kindmap.get(s.get("kind"), "codex") for s in sess]
        args.pod = ",".join(f"{k}:{d}" for k, d in zip(podkinds, decknames))
        resume_info = (ev_path, idx, sess)

    seats = []
    for spec in args.pod.split(","):
        spec = spec.strip()
        kind, _, deck = spec.rpartition(":")
        kind = kind or "claude"
        model_override = None
        if "@" in kind:                          # agent@model:deck, e.g. openrouter@moonshotai/kimi-k2:snakes
            kind, model_override = kind.split("@", 1)
        if kind not in AGENT_TYPES and kind != "human":
            raise SystemExit(f"unknown agent {kind!r} in seat {spec!r} (have: {', '.join(AGENT_TYPES)}, human)")
        seats.append((kind, deck, model_override))
    db = load_db([deck for _, deck, _m in seats])

    decks, agents = [], []
    for n, (kind, deck, model_override) in enumerate(seats):
        decks.append((deck, *load_deck(deck, db), deck_strategy(deck)))
        label = f"P{n+1}({deck})"
        if args.mock:
            agents.append(MockAgent(label, db))
        elif kind == "human":
            from mtgsim.agents import HumanAgent
            sk = args.human_agent
            smodel = model_override or {"codex": args.codex_model,
                                        "claude": args.claude_model}.get(sk)
            sextra = {"service_tier": args.codex_tier, "effort": args.codex_effort} \
                if sk == "codex" else {}
            scribe = AGENT_TYPES[sk](f"{label}-scribe", model=smodel, resume=True,
                                     transcript_dir=args.transcripts or None, **sextra)
            agents.append(HumanAgent(label, scribe))
        else:
            model = model_override or {"codex": args.codex_model,
                                       "claude": args.claude_model}.get(kind)
            extra = {"service_tier": args.codex_tier, "effort": args.codex_effort} \
                if kind == "codex" else {}
            agents.append(AGENT_TYPES[kind](
                label, model=model, resume=not args.stateless,
                transcript_dir=args.transcripts or None, **extra))

    def judge_factory():
        from mtgsim.agents import CodexAgent
        return CodexAgent("judge", model=args.codex_model, resume=True,
                          service_tier=args.codex_tier, effort=args.codex_effort,
                          transcript_dir=args.transcripts or None)

    human_handles = {f"P{n+1}" for n, (k, _, _) in enumerate(seats) if k == "human"}
    console_private = "all" if (args.show_hidden or not human_handles) else human_handles
    from_turn, from_seat = 1, 0
    if resume_info:
        ev_path, idx, sess = resume_info
        if args.minds == "cloned":
            from mtgsim.branching import clone_session
            for n, (a, s) in enumerate(zip(agents, sess)):
                nid, note = clone_session(s.get("kind"), s.get("id"), s.get("calls"))
                if nid and getattr(a, "resume", False):
                    a.session_id = nid
                    print(f"P{n+1}: mind branched -> {note}")
                else:
                    print(f"P{n+1}: fresh mind ({note})")
            if args.hold:
                input("edit the session files above if you like, then press enter to resume... ")
        edits = None
        if args.edit:
            import json
            raw = open(args.edit[1:]).read() if args.edit.startswith("@") else args.edit
            edits = json.loads(raw)
        from mtgsim.branching import restore_game
        game, from_turn, from_seat = restore_game(
            db, decks, agents, ev_path, args.log, args.max_turns, rng,
            at=idx, edits=edits, judge_factory=None if args.mock else judge_factory,
            console_private=console_private)
    else:
        game = Game(db, decks, agents, seed, args.log, args.max_turns, rng,
                    judge_factory=None if args.mock else judge_factory,
                    console_private=console_private)
    import atexit
    import subprocess as _sp
    atexit.register(lambda: _sp.run(
        ["uv", "run", "scripts/export_md.py", f"{args.log}.events.jsonl"],
        capture_output=True))
    print(f"⚖ judge channel: type into this terminal (or: echo 'msg' >> {args.log}.judge). "
          f"Plain text posts to the table as the judge; the keyword JUDGE [question] summons "
          f"a codex ruling in your stead.")
    if human_handles:
        vis = ("(--show-hidden is on: you can see everyone's private thinking — wallhacks)"
               if args.show_hidden else
               "Other seats' private thinking is hidden from this console (--show-hidden to peek).")
        print(f"you're seated ({args.human_agent} scribing). When the banner fires, type at "
              f"you> — plain words, the scribe handles the JSON. enter/'pass' passes a window, "
              f"'done' ends your turn, 'hand'/'board' reprint state, \"quotes\" go to the table. "
              f"Lines typed *between* prompts go to the judge channel, not your seat. {vis}")
    game.run(from_turn, from_seat)
    for a in agents:
        extra = f", ~${a.cost_usd:.2f} api-equiv (covered by subscription)" if a.cost_usd else ""
        print(f"{a.label} [{type(a).__name__}]: {a.calls} calls, "
              f"{a.tokens['in']}/{a.tokens['out']} tok in/out{extra}")
