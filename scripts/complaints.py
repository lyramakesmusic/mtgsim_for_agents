#!/usr/bin/env python3
"""What the engine couldn't do, counted across game logs.

  uv run scripts/complaints.py                     # every game in games/
  uv run scripts/complaints.py games/tourney_2026* # just these
  uv run scripts/complaints.py --examples 3        # show sample lines per kind

Every line the engine logs with `!!` is a place an agent asked for something
the rules layer couldn't express — a zone it doesn't search, an action with no
atom, a declaration it had to drop. Grouped by shape and sorted by how often
they bite, they read as a work list.
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPLAINT = re.compile(r"!!\s*(.+?)\s*$")


def shape(line):
    """Group by what went wrong, not which card it happened to."""
    s = re.sub(r"'[^']*'", "'X'", line)
    s = re.sub(r"\bstack#\d+", "stack#N", s)
    s = re.sub(r"#\d+", "#N", s)
    s = re.sub(r"\bP\d\b", "PN", s)
    s = re.sub(r"\b\d+\b", "N", s)
    return s[:100]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", default=None)
    ap.add_argument("--examples", type=int, default=0)
    args = ap.parse_args()

    roots = [Path(p) for p in args.paths] if args.paths else [ROOT / "games"]
    logs = []
    for r in roots:
        logs += [p for p in ([r] if r.is_file() else r.rglob("g*.md"))
                 if not p.name.endswith((".pretty.md", ".postmortem.md"))]
    if not logs:
        sys.exit(f"no game logs under {', '.join(str(r) for r in roots)}")

    counts, samples, games = Counter(), defaultdict(list), defaultdict(set)
    last = {}
    for p in logs:
        for line in p.read_text(errors="replace").splitlines():
            m = COMPLAINT.search(line)
            if not m or line.lstrip().startswith(">"):
                continue
            k = shape(m.group(1))
            counts[k] += 1
            games[k].add(str(p))
            stamp = p.parent.name
            last[k] = max(last.get(k, ''), stamp)
            if len(samples[k]) < 5:
                samples[k].append((p.name, m.group(1)[:150]))

    print(f"{sum(counts.values())} complaints across {len(logs)} game logs\n")
    print(f"{'count':>6}  {'games':>5}  {'last seen':<16}  shape")
    for k, v in counts.most_common():
        print(f"{v:>6}  {len(games[k]):>5}  {last[k][-15:]:<16}  {k}")
        for name, ex in samples[k][:args.examples]:
            print(f"{'':>15}{name}: {ex}")
