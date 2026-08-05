#!/usr/bin/env python3
"""The counterfactual lab: pick a moment from a finished game, run futures.

  uv run scripts/branch.py games/x.md --list                 # find the moment
  uv run scripts/branch.py games/x.md --at 214 --n 5         # 5 rollouts from it
  uv run scripts/branch.py games/x.md --at 214 --n 5 --edit '{"P4":{"hand":["Fog"]}}'

--list prints event indices for turn starts, eliminations, and endings.
--n spawns parallel play.py --resume branches (each its own seed and log) into
games/branch_<stamp>/; point scripts/postmortem.py at the directory afterward
to compare the futures. Every play.py resume flag passes through: --minds,
--edit, --pod, --max-turns.
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mtgsim.branching import load_events, pick_event  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("game", help="game .md or .md.events.jsonl")
    ap.add_argument("--list", action="store_true", help="print branchable moments")
    ap.add_argument("--at", type=int, default=None)
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--edit", default=None)
    ap.add_argument("--minds", default="fresh", choices=["fresh", "cloned"])
    ap.add_argument("--pod", default=None)
    ap.add_argument("--max-turns", type=int, default=12)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    ev_path = args.game if args.game.endswith(".jsonl") else f"{args.game}.events.jsonl"
    events = load_events(ev_path)

    if args.list:
        for i, e in enumerate(events):
            line = e["line"]
            if line.startswith(("## Turn", "**")) or "ELIMINATED" in line:
                clean = "" if e["state"].get("stack_empty", True) else "  (mid-stack; snaps back)"
                print(f"{i:5d}  {line.strip()[:100]}{clean}")
        sys.exit(0)

    idx = pick_event(events, args.at)
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    bdir = ROOT / "games" / f"branch_{stamp}"
    bdir.mkdir(parents=True)
    base = args.seed if args.seed is not None else int(time.time()) % 10**6
    procs = []
    for k in range(args.n):
        cmd = [sys.executable, str(ROOT / "play.py"), "--resume", ev_path,
               "--at", str(idx), "--minds", args.minds,
               "--seed", str(base + k), "--max-turns", str(args.max_turns),
               "--log", str(bdir / f"b{k+1:02d}.md"),
               "--transcripts", str(ROOT / "logs" / f"branch_{stamp}" / f"b{k+1:02d}")]
        if args.edit:
            cmd += ["--edit", args.edit]
        if args.pod:
            cmd += ["--pod", args.pod]
        procs.append(subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, cwd=ROOT))
        print(f"b{k+1:02d}: branched from event {idx} (seed {base + k})")
    for p in procs:
        p.wait()
    print(f"\nfutures in {bdir.relative_to(ROOT)} — "
          f"grep 'GAME OVER' {bdir.relative_to(ROOT)}/*.md for outcomes, "
          f"or run scripts/postmortem.py on the directory")
