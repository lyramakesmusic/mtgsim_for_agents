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
                         f"(agents: {', '.join(AGENT_TYPES)}; decks: {', '.join(deck_names())})")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--max-turns", type=int, default=12)
    ap.add_argument("--claude-model", default="opus")
    ap.add_argument("--codex-model", default=None, help="default: codex config default")
    ap.add_argument("--mock", action="store_true", help="scripted dummy agents (plumbing test)")
    ap.add_argument("--stateless", action="store_true", help="no session continuity")
    ap.add_argument("--log", default=None, help="default: games/<timestamp>.md")
    ap.add_argument("--transcripts", default=None, help="per-agent transcript dir ('' to disable; default: logs/<timestamp>)")
    args = ap.parse_args()
    from datetime import datetime
    from pathlib import Path
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    if not args.log:
        Path("games").mkdir(exist_ok=True)
        args.log = f"games/{stamp}.md"
    if args.transcripts is None:
        args.transcripts = f"logs/{stamp}"          # per-run dir: runs never clobber each other

    db = load_db()
    seed = args.seed if args.seed is not None else random.randrange(10**6)
    rng = random.Random(seed)

    seats = []
    for spec in args.pod.split(","):
        spec = spec.strip()
        kind, _, deck = spec.rpartition(":")
        kind = kind or "claude"
        if kind not in AGENT_TYPES:
            raise SystemExit(f"unknown agent {kind!r} in seat {spec!r} (have: {', '.join(AGENT_TYPES)})")
        seats.append((kind, deck))

    decks, agents = [], []
    for n, (kind, deck) in enumerate(seats):
        decks.append((deck, *load_deck(deck, db), deck_strategy(deck)))
        label = f"P{n+1}({deck})"
        if args.mock:
            agents.append(MockAgent(label, db))
        else:
            model = args.codex_model if kind == "codex" else args.claude_model
            agents.append(AGENT_TYPES[kind](
                label, model=model, resume=not args.stateless,
                transcript_dir=args.transcripts or None))

    def judge_factory():
        from mtgsim.agents import CodexAgent
        return CodexAgent("judge", model=args.codex_model, resume=True,
                          transcript_dir=args.transcripts or None)

    game = Game(db, decks, agents, seed, args.log, args.max_turns, rng,
                judge_factory=None if args.mock else judge_factory)
    import atexit
    import subprocess as _sp
    atexit.register(lambda: _sp.run(
        ["uv", "run", "scripts/export_md.py", f"{args.log}.events.jsonl"],
        capture_output=True))
    print(f"⚖ judge channel: type into this terminal (or: echo 'msg' >> {args.log}.judge). "
          f"Plain text posts to the table as the judge; the keyword JUDGE [question] summons "
          f"a codex ruling in your stead.")
    game.run()
    for a in agents:
        extra = f", ~${a.cost_usd:.2f} api-equiv (covered by subscription)" if a.cost_usd else ""
        print(f"{a.label} [{type(a).__name__}]: {a.calls} calls, "
              f"{a.tokens['in']}/{a.tokens['out']} tok in/out{extra}")
