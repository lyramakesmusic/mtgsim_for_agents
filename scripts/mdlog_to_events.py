#!/usr/bin/env python3
"""Shadow-replay a pre-events-era game log (.md) into an events.jsonl.

The engine's log lines are machine-generated from fixed format strings, so we
can parse them back into state mutations and reconstruct board state per line.
Known drift (cosmetic only): activation taps aren't logged (cards may render
untapped), unknown opening hands render as card backs ("Unknown").

  uv run scripts/mdlog_to_events.py games/20260731_002159.md
"""
import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mtgsim.cards import DECK_DIR, load_db, parse_decklist  # noqa: E402

DB = load_db()

HDR = re.compile(r"^# Pod: (.+?) — seed")
SEAT = re.compile(r"(P\d)\(([^)]+)\)")
TURN = re.compile(r"^## Turn (\d+) — (P\d)\(")
LAND = re.compile(r"^(P\d)\([^)]*\) plays land: (.+)$")
CAST = re.compile(r"^(P\d)\([^)]*\) casts ([^,—]+?)(?: \(from command zone\))?(?:, tapping (\[[^\]]*\]))?(?: —|$)")
ATTACK = re.compile(r"^(P\d)\([^)]*\) attacks P\d\([^)]*\) with: (\[[^\]]*\])")
CREATES = re.compile(r"^↳ (P\d)\([^)]*\) creates (\d+)x (.+?) token\(s\)")
DRAWS = re.compile(r"^↳ (P\d)\([^)]*\) draws (\d+)(?: \((.+)\))?$")
LIFE = re.compile(r"^↳ (P\d)\([^)]*\) [+-]\d+ life \(now (-?\d+)\)")
MOVEPERM = re.compile(r"^↳ (.+?#\d+) → (P\d)\([^)]*\)'s (\w+)$")
TOKENGONE = re.compile(r"^↳ (.+?#\d+) → \w+ \(token; ceases to exist\)$")
MOVECARD = re.compile(r"^↳ (P\d)\([^)]*\): (.+?) (hand|graveyard|exile|command zone|command) → (\w+)$")
TOPMOVE = re.compile(r"^↳ (P\d)\([^)]*\): top (\d+) of library → (\w+)(?: \((.+)\))?")
SEARCH = re.compile(r"^↳ (P\d)\([^)]*\) searches library: (.+?) → (\w+)")
MILL = re.compile(r"^↳ (P\d)\([^)]*\) mills (\d+) \(library (\d+)\)")
SETLINE = re.compile(r"^↳ (.+?#\d+): (.+)$")
COUNTERS = re.compile(r"^↳ (\d+) \+1/\+1 counter\(s\) on (.+?#\d+)")
WHOLEHAND = re.compile(r"^↳ (P\d)\([^)]*\): entire hand \((\d+) cards\) → (\w+)")
COUNTERED = re.compile(r"^↳ (.+?) is COUNTERED")
CZBACK = re.compile(r"^↳ (?:.+?#\d+ → command zone|.+? (?:stays in|returns to) (?:the )?command zone)")
ELIM = re.compile(r"^\*\*(P\d)\([^)]*\) is ELIMINATED")


class Shadow:
    def __init__(self, seats):
        self.next_id = 1
        self.p = {}
        for h, deck in seats:
            path = DECK_DIR / f"{deck}.txt"
            cmdrs = parse_decklist(path.read_text())[1] if path.exists() else ["?"]
            self.p[h] = {"handle": h, "name": f"{h}({deck})", "life": 40, "alive": True,
                         "hand": ["Unknown"] * 7, "graveyard": [], "exile": [], "library": 92,
                         "commanders": cmdrs, "command_zone": {c: True for c in cmdrs},
                         "commander_tax": {c: 0 for c in cmdrs}, "battlefield": []}
        self.turn = 0
        self.last_caster = None

    def perm(self, ph, name, sick=True, explicit_id=None):
        if explicit_id:
            num = int(explicit_id.rsplit("#", 1)[1])
            self.next_id = max(self.next_id, num + 1)
            pid = explicit_id
        else:
            pid = f"{name}#{self.next_id}"
            self.next_id += 1
        d = DB.get(name, {})
        x = {"id": pid, "name": name, "tapped": False, "sick": sick and bool(d.get("pt")),
             "counters": 0, "token": False, "pt": list(d["pt"]) if d.get("pt") else None}
        self.p[ph]["battlefield"].append(x)
        return x

    def find(self, ident):
        s = str(ident)
        for pl in self.p.values():
            for x in pl["battlefield"]:
                if x["id"] == s or x["name"] == s:
                    return pl, x
        if s.isdigit():
            for pl in self.p.values():
                for x in pl["battlefield"]:
                    if x["id"].endswith(f"#{s}"):
                        return pl, x
        return None, None

    def remove_hand(self, ph, name):
        h = self.p[ph]["hand"]
        if name in h:
            h.remove(name)
        elif "Unknown" in h:
            h.remove("Unknown")

    def taps(self, listtxt):
        for tok in re.findall(r"'([^']+)'|(\d+)", listtxt):
            ident = tok[0] or tok[1]
            _, x = self.find(ident)
            if x:
                x["tapped"] = True

    def zone_put(self, ph, name, zone, explicit_id=None):
        pl = self.p[ph]
        z = {"graveyard": "graveyard", "exile": "exile", "hand": "hand",
             "battlefield": None, "command": None, "library_top": None, "library_bottom": None}
        if zone == "battlefield":
            self.perm(ph, name, explicit_id=explicit_id)
        elif zone in ("command", "command zone"):
            if name in pl["command_zone"]:
                pl["command_zone"][name] = True
        elif zone in ("library_top", "library_bottom"):
            pl["library"] += 1
        elif z.get(zone):
            pl[z[zone]].append(name)

    def state(self):
        return {"turn": self.turn,
                "players": [copy.deepcopy(self.p[h]) for h in sorted(self.p)]}

    def apply(self, t):
        m = TURN.match(t)
        if m:
            self.turn = int(m.group(1))
            for x in self.p[m.group(2)]["battlefield"]:
                x["tapped"] = False
                x["sick"] = False
            return
        m = LAND.match(t)
        if m:
            self.remove_hand(m.group(1), m.group(2))
            self.perm(m.group(1), m.group(2), sick=False)
            return
        m = CAST.match(t)
        if m:
            ph, name = m.group(1), m.group(2).strip()
            from_cz = "(from command zone)" in t
            if m.group(3):
                self.taps(m.group(3))
            if from_cz and name in self.p[ph]["command_zone"]:
                self.p[ph]["command_zone"][name] = False
                self.p[ph]["commander_tax"][name] += 2
            else:
                self.remove_hand(ph, name)
            typ = DB.get(name, {}).get("type", "")
            if any(k in typ for k in ("Creature", "Artifact", "Enchantment", "Land")) \
                    and "Sorcery" not in typ and "Instant" not in typ:
                self.perm(ph, name)
            else:
                self.p[ph]["graveyard"].append(name)
            self.last_caster = ph
            return
        m = ATTACK.match(t)
        if m:
            self.taps(m.group(2))
            return
        m = CREATES.match(t)
        if m:
            for _ in range(int(m.group(2))):
                x = self.perm(m.group(1), m.group(3))
                x["token"] = True
                x["pt"] = x["pt"] or [1, 1]
            return
        m = DRAWS.match(t)
        if m:
            pl = self.p[m.group(1)]
            n = int(m.group(2))
            names = [s.strip() for s in m.group(3).split(",")] if m.group(3) else ["Unknown"] * n
            pl["hand"] += names
            pl["library"] = max(0, pl["library"] - n)
            return
        m = LIFE.match(t)
        if m:
            self.p[m.group(1)]["life"] = int(m.group(2))
            return
        m = TOKENGONE.match(t)
        if m:
            pl, x = self.find(m.group(1))
            if x:
                pl["battlefield"].remove(x)
            return
        m = MOVEPERM.match(t)
        if m:
            pl, x = self.find(m.group(1))
            if x:
                pl["battlefield"].remove(x)
                self.zone_put(m.group(2), x["name"], m.group(3))
            return
        m = MOVECARD.match(t)
        if m:
            ph, card, frm, to = m.groups()
            pl = self.p[ph]
            if frm == "hand":
                self.remove_hand(ph, card)
            elif frm in ("graveyard", "exile") and card in pl[frm]:
                pl[frm].remove(card)
            elif frm.startswith("command") and card in pl["command_zone"]:
                pl["command_zone"][card] = False
            self.zone_put(ph, card, to)
            return
        m = TOPMOVE.match(t)
        if m:
            ph, n, to = m.group(1), int(m.group(2)), m.group(3)
            pl = self.p[ph]
            pl["library"] = max(0, pl["library"] - n)
            names = [s.strip() for s in m.group(4).split(",")] if m.group(4) and to != "hand" else []
            for nm in names if names else (["Unknown"] * n if to in ("graveyard", "exile") else []):
                self.zone_put(ph, nm, to)
            return
        m = SEARCH.match(t)
        if m:
            self.p[m.group(1)]["library"] -= 1
            self.zone_put(m.group(1), m.group(2), m.group(3))
            return
        m = MILL.match(t)
        if m:
            self.p[m.group(1)]["library"] = int(m.group(3))
            return
        m = COUNTERS.match(t)
        if m:
            _, x = self.find(m.group(2))
            if x:
                x["counters"] += int(m.group(1))
            return
        m = SETLINE.match(t)
        if m:
            _, x = self.find(m.group(1))
            if not x:
                return
            chg = m.group(2)
            mm = re.search(r"tapped=(True|False)", chg)
            if mm:
                x["tapped"] = mm.group(1) == "True"
            mm = re.search(r"sick=(True|False)", chg)
            if mm:
                x["sick"] = mm.group(1) == "True"
            mm = re.search(r"counters([+-]\d+)", chg)
            if mm:
                x["counters"] += int(mm.group(1))
            mm = re.search(r"pt=\[(-?\d+),\s*(-?\d+)\]", chg)
            if mm:
                x["pt"] = [int(mm.group(1)), int(mm.group(2))]
            return
        m = WHOLEHAND.match(t)
        if m:
            pl = self.p[m.group(1)]
            pl["library"] += len(pl["hand"])
            pl["hand"] = []
            return
        m = COUNTERED.match(t)
        if m and self.last_caster:
            self.p[self.last_caster]["graveyard"].append(m.group(1))
            return
        m = ELIM.match(t)
        if m:
            self.p[m.group(1)]["alive"] = False
            self.p[m.group(1)]["battlefield"] = []
            return


if __name__ == "__main__":
    path = Path(sys.argv[1])
    lines = path.read_text().splitlines()
    m = HDR.match(lines[0])
    seats = SEAT.findall(lines[0])
    shadow = Shadow(seats)
    out = path.with_suffix(".md.events.jsonl") if not path.name.endswith(".md") else Path(str(path) + ".events.jsonl")
    n_priv = 0
    with open(out, "w") as f:
        for raw in lines:
            private = raw.startswith("[private] ")
            line = raw[len("[private] "):] if private else raw
            if not line.strip():
                continue
            try:
                shadow.apply(line.strip())
            except Exception as e:
                print(f"  !! parse skip: {line[:80]!r} ({e})")
            n_priv += private
            f.write(json.dumps({"line": line, "private": private, "state": shadow.state()}) + "\n")
    final = {h: (p["life"], p["alive"]) for h, p in shadow.p.items()}
    print(f"{out.name}: {sum(1 for _ in open(out))} events ({n_priv} private) — final: {final}")
