#!/usr/bin/env python3
"""Styled markdown export of a game — the console view, in document form.

  uv run scripts/export_md.py games/20260731_030747.md.events.jsonl
  → games/20260731_030747.pretty.md

Works mid-game (the events file streams). Mapping mirrors the terminal:
plays bold ▶, thinking italic, speech with bold speaker, effects/warnings
quoted, judge bold, turn banners as headers."""
import json
import re
import sys
from pathlib import Path

SAYS = re.compile(r'^(P\d\([^)]*\)) says: "(.*)"\s*$', re.S)
THINKS = re.compile(r'^(P\d\([^)]*\)) thinks: "(.*)"\s*$', re.S)
TURN = re.compile(r"^## Turn ")
PLAY = re.compile(r"^(P\d\([^)]*\)) (plays land|announces|casts|activates|attacks|blocks|mulligans|keeps)")


def style(line, private):
    t = line.strip()
    if not t:
        return ""
    if TURN.match(t):
        return f"\n{t}\n"
    m = THINKS.match(t)
    if m and private:
        return f"*{m.group(1)} thinks: “{m.group(2)}”*\n"
    m = SAYS.match(t)
    if m:
        return f"**{m.group(1)}** says: “{m.group(2)}”\n"
    if t.startswith("⚖"):
        return f"**{t}**\n"
    if t.startswith("!!"):
        return f"> **{t}**\n"
    if t.startswith("↳") or t.startswith("("):
        return f"> {t}\n"
    if PLAY.match(t):
        return f"**▶ {t}**\n"
    if t.startswith("**"):          # eliminations, game over — already bold
        return f"{t}\n"
    if t.startswith("#"):           # game header
        return f"{t}\n"
    return f"{t}\n"


if __name__ == "__main__":
    src = Path(sys.argv[1])
    out = Path(re.sub(r"\.md\.events\.jsonl$", "", str(src)) + ".pretty.md")
    lines = []
    for raw in src.open():
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        lines.append(style(ev["line"], ev.get("private", False)))
    out.write_text("\n".join(l for l in lines if l))
    print(f"wrote {out} ({sum(1 for l in lines if l)} lines)")
