#!/usr/bin/env python3
"""Run N games of the same pod in parallel, then summarize results.

  uv run scripts/tourney.py --pod codex:meren,codex:snakes,codex:aurelia,codex:squirrels --n 8
  uv run scripts/tourney.py --pod ... --n 8 --codex-tier fast --max-turns 12

Each game gets its own log (games/tourney_<stamp>/gNN.md), transcript dir and
seed. stdin is /dev/null so the judge channel stays quiet. Human seats are
refused — nobody can type at eight terminals at once. When all games finish,
prints the results table; feed the directory to scripts/postmortem.py next.
"""
import argparse
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pod", required=True, action="append",
                    help="repeatable; games cycle through the given pods (vary the opposition)")
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--max-turns", type=int, default=12)
    ap.add_argument("--seed", type=int, default=None, help="base seed; game k uses seed+k")
    ap.add_argument("--codex-tier", default=None, choices=["fast", "priority", "flex"])
    ap.add_argument("--codex-effort", default=None, choices=["minimal", "low", "medium", "high"])
    ap.add_argument("--claude-model", default=None)
    ap.add_argument("--codex-model", default=None)
    args = ap.parse_args()

    if any("human" in p for p in args.pod):
        sys.exit("tourney pods can't seat a human — nobody can type at 8 terminals at once")

    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    gdir = ROOT / "games" / f"tourney_{stamp}"
    gdir.mkdir(parents=True)
    base = args.seed if args.seed is not None else int(time.time()) % 10**6

    procs = []
    for k in range(args.n):
        pod = args.pod[k % len(args.pod)]
        log = gdir / f"g{k+1:02d}.md"
        cmd = [sys.executable, str(ROOT / "play.py"), "--pod", pod,
               "--seed", str(base + k), "--max-turns", str(args.max_turns),
               "--log", str(log), "--transcripts", str(ROOT / "logs" / f"tourney_{stamp}" / f"g{k+1:02d}")]
        for flag in ("codex_tier", "codex_effort", "claude_model", "codex_model"):
            v = getattr(args, flag)
            if v:
                cmd += [f"--{flag.replace('_', '-')}", v]
        procs.append((k + 1, log, subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT, cwd=ROOT)))
        print(f"g{k+1:02d}: launched (seed {base + k}) [{pod}] -> {log.relative_to(ROOT)}")

    print(f"\n{args.n} games running; tail any log to spectate. Waiting...")
    done = set()
    while len(done) < len(procs):
        time.sleep(15)
        for k, log, p in procs:
            if k in done or p.poll() is None:
                continue
            done.add(k)
            text = log.read_text() if log.exists() else ""
            m = re.search(r"\*\*GAME OVER: (.+?) WINS \((.+?)\) on turn (\d+)", text) \
                or re.search(r"\*\*Turn cap.*?Standings: (.+?)\.", text)
            print(f"g{k:02d}: DONE ({len(done)}/{len(procs)}) — "
                  + (m.group(0).strip("*") if m else f"rc={p.returncode} (no verdict line?)"))

    print(f"\nall games in {gdir.relative_to(ROOT)}")
    print(f"next: uv run scripts/postmortem.py {gdir.relative_to(ROOT)}")
