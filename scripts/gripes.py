#!/usr/bin/env python3
"""What agents say the engine got wrong, in their own words.

  uv run scripts/gripes.py                      # every game in games/
  uv run scripts/gripes.py games/tourney_2026*  # just these
  uv run scripts/gripes.py --all                # every correction, not just the pointed ones

scripts/complaints.py reads the engine's `!!` lines — the gaps it knows about.
This reads the other side: corrections and notes where a seat describes what the
engine did instead of what the card says. Those name the gaps that leave no
diagnostic, because an engine can only complain about what it knows it can't do.

A line saying "same as every turn" or "third time on the same fix" is a seat
telling you a repair has become routine, which is the strongest signal there is.
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEAKS = re.compile(r"(?:corrects the board — |note \([^)]*\): )(.+?)\s*$")
POINTED = re.compile(
    r"\bthe engine\b|wasn'?t applied|weren'?t applied|did ?n[o']?t apply|does not model|"
    r"cannot (?:cast|address|apply|model|handle)|can'?t be (?:declared|expressed)|"
    r"not supported|no way to|failed verification|was (?:ignored|dropped|silently)",
    re.I)
ROUTINE = re.compile(
    r"same as (?:every|last|the prior)|again, same|third time|second time|every turn|"
    r"every prior|as usual|once more|repeat(?:ed|ing) (?:fix|repair|correction)", re.I)


def topic(line):
    """Group by the subject of the complaint, not its wording."""
    s = line.lower()
    for pat, name in (
        (r"untap", "untap step"), (r"upkeep|beginning of (?:my|your) turn", "upkeep trigger"),
        (r"enters? tapped|entered untapped", "entering tapped"),
        (r"cascad|from exile|flashback|graveyard cast", "casting from another zone"),
        (r"land ?(?:drop|play)", "land drop"), (r"counters?\b", "counters"),
        (r"library[ _-]?bottom|bottom of (?:my|their|the) library", "library bottom"),
        (r"discard", "discard"), (r"token", "tokens"), (r"combat|damage", "combat"),
        (r"experience|poison|energy", "player counters"),
    ):
        if re.search(pat, s):
            return name
    return "other"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--all", action="store_true", help="every correction, not just pointed ones")
    args = ap.parse_args()

    roots = [Path(p) for p in args.paths] if args.paths else [ROOT / "games"]
    logs = []
    for r in roots:
        logs += [p for p in ([r] if r.is_file() else r.rglob("g*.md"))
                 if not p.name.endswith((".pretty.md", ".postmortem.md"))]
    if not logs:
        sys.exit(f"no game logs under {', '.join(str(r) for r in roots)}")

    by_topic, routine, seen = defaultdict(Counter), Counter(), set()
    for p in logs:
        for line in p.read_text(errors="replace").splitlines():
            if line.lstrip().startswith(">"):
                continue
            m = SPEAKS.search(line)
            if not m:
                continue
            said = m.group(1)
            if not (args.all or POINTED.search(said)):
                continue
            t = topic(said)
            by_topic[t][said[:150]] += 1
            if ROUTINE.search(said):
                routine[t] += 1
            seen.add(said[:150])

    total = sum(sum(c.values()) for c in by_topic.values())
    print(f"{total} corrections where a seat describes what the engine did, "
          f"across {len(logs)} game logs\n")
    print(f"{'count':>6}  {'routine':>7}  topic")
    for t, c in sorted(by_topic.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"{sum(c.values()):>6}  {routine[t]:>7}  {t}")
        for said, n in c.most_common(2):
            print(f"{'':>17}{n}x {said[:110]}")
