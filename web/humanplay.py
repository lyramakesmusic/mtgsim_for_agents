#!/usr/bin/env python3
"""play.py with human seats piloted from the web GUI.

  uv run web/humanplay.py --pod human:squirrels,codex:meren --log games/<id>.md

Runs play.py unchanged. Inside mtgsim.agents only, input() and print() are rebound to a
file channel next to the log, so the HumanAgent's `you>` loop (and its scribe) talk to the
browser instead of a terminal:

  <log>.human/out.jsonl    everything a human seat's REPL prints: {"t", "seat", "text", "kind"}
  <log>.human/in.jsonl     lines typed in the browser: {"t", "seat", "text"}
  <log>.human/state.json   {"seat", "waiting", "since"} while a seat waits at its prompt
"""
import inspect
import json
import os
import re
import runpy
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

if "--log" in sys.argv:
    log = sys.argv[sys.argv.index("--log") + 1]
else:                                    # --resume ... --continue writes to the resumed game's log
    r = sys.argv[sys.argv.index("--resume") + 1]
    log = r[: -len(".events.jsonl")] if r.endswith(".events.jsonl") else r
CHAN = Path(f"{log}.human")
CHAN.mkdir(parents=True, exist_ok=True)
OUT, IN, STATE = CHAN / "out.jsonl", CHAN / "in.jsonl", CHAN / "state.json"
IN.touch()
ANSI = re.compile(r"\x1b\[[0-9;]*m|\a")
consumed = {}         # seat -> lines of in.jsonl already read for that seat
for _l in IN.read_text().splitlines():  # a continued game has already answered these
    if _l.strip():
        _s = json.loads(_l).get("seat")
        consumed[_s] = consumed.get(_s, 0) + 1


def seat():
    """The handle of the HumanAgent whose REPL is calling, from the call stack."""
    for fr in inspect.stack():
        me = fr.frame.f_locals.get("self")
        if me is not None and hasattr(me, "scribe") and hasattr(me, "label"):
            return me.label.split("(")[0]
    return None


def emit(rec):
    with OUT.open("a") as f:
        f.write(json.dumps({"t": time.time(), **rec}) + "\n")


def web_print(*args, sep=" ", end="\n", **kw):
    text = ANSI.sub("", sep.join(str(a) for a in args)).strip("\n")
    print(text, sep=sep, end=end, flush=True)          # the console log keeps a copy
    if text.strip():
        emit({"seat": seat(), "text": text.strip(), "kind": "banner" if text.strip().startswith("---") else "out"})


def web_input(prompt=""):
    me = seat()
    STATE.write_text(json.dumps({"seat": me, "waiting": True, "since": time.time()}))
    while True:
        lines = [json.loads(l) for l in IN.read_text().splitlines() if l.strip()]
        mine = [l for l in lines if l.get("seat") in (me, None)]
        if len(mine) > consumed.get(me, 0):
            line = mine[consumed.get(me, 0)]["text"]
            consumed[me] = consumed.get(me, 0) + 1
            STATE.write_text(json.dumps({"seat": me, "waiting": False, "since": time.time()}))
            emit({"seat": me, "text": line, "kind": "you"})
            return line
        time.sleep(0.2)


import mtgsim.agents as agents  # noqa: E402

agents.print = web_print
agents.input = web_input
sys.argv = [str(ROOT / "play.py"), *sys.argv[1:]]
runpy.run_path(str(ROOT / "play.py"), run_name="__main__")
