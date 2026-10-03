"""Game event compiler: engine events.jsonl → typed, delta-compressed events for the web client.

Each engine event is {"line": str, "private": bool, "state": snapshot}. The compiler
classifies the line, tracks what snapshots leave implicit (the live stack, player
counters, the active seat), trims the state to what a viewer renders, and replaces
players that did not change since the previous event with null.

Compiled event:
  {"i": int, "line": str, "priv": bool, "kind": str, "seat": "P1"|None, ...kind fields,
   "state": {"turn": int, "active": "P1"|None, "stack": [StackItem],
             "attach": {attachment_perm_id: host_perm_id or seat handle "P1"},
             "players": [PlayerState | None]}}      # None = same as previous event
  StackItem   = {"sid": "stack#4", "card": str, "seat": "P1", "targets": [str],
                 "ability": bool, "countered": bool}
  PlayerState = {"handle", "name", "deck", "life", "alive", "hand": [str], "graveyard": [str],
                 "exile": [str], "library": int, "command_zone": {name: bool},
                 "commanders": [str], "commander_tax": {name: int}, "counters": {kind: int},
                 "battlefield": [Perm]}
"""
import ast
import json
import re

SEAT = r"(P\d)\(([^)]*)\)"
R = {k: re.compile(v, re.S) for k, v in {
    "pod": r"^# Pod: (.*?) — seed (\S+)",
    "turn": r"^## Turn (\d+) — " + SEAT,
    "says": r"^" + SEAT + r' says: "(.*)"$',
    "thinks": r"^" + SEAT + r' thinks: "(.*)"$',
    "land": r"^" + SEAT + r" plays land: (.+?)( \(tapped\))?$",
    "announce": r"^" + SEAT + r" announces (activation of )?(.+?)(?: \((stack#\d+)\))?(?: targeting (.+?))?(?: — (.*))?\.\.\.$",
    "cast_fizzle": r"^" + SEAT + r" casts (.+?) — FIZZLES on resolution: (.*)$",
    "cast": r"^" + SEAT + r" casts (.+?)( \(from command zone\))?(?:, tapping (\[.*?\]))?(?: — (.*))?$",
    "activate": r"^" + SEAT + r" activates (.+?)(?: — (.*))?$",
    "attack": r"^" + SEAT + r" attacks " + SEAT + r" with: (\[.*\])$",
    "block": r"^" + SEAT + r" blocks: (\{.*?\}) —\s?(.*)$",
    "combat": r"^combat result — (.*)$",
    "correct": r"^" + SEAT + r" corrects the board — (.*)$",
    "offer": r"^" + SEAT + r" offers the table: (.*)$",
    "e_answer": r"^" + SEAT + r" answers: (.*)$",
    "look_top": r"^" + SEAT + r" looks at the top (\d+) cards?",
    "mull": r"^" + SEAT + r" (mulligans|bottoms|keeps)(.*)$",
    "untap": r"^\(" + SEAT + r" untaps",
    "elim": r"^\*\*" + SEAT + r" is ELIMINATED — (.*?)\. Their permanents",
    "gameover": r"^\*\*GAME OVER: (?:" + SEAT + r" WINS \((.*)\) on turn (\d+)|DRAW on turn (\d+) — (.*?)\.)",
    "turncap": r"^\*\*Turn cap (\d+) reached\. Standings: (.*?)\*\*",
    "claim": r"^\*\*" + SEAT + r" (claims they WIN THE GAME|says .*? HAS ALREADY WON|claims the game is a DRAW|declares .*? LOSES THE GAME)(?::)? ?(.*?)\*?\*?$",
    "judge": r"^⚖ JUDGE( RULES)?: (.*)$",
    "caption": r"^\[(.+?) — (.*?) — (.*?) — (.*)\]$",
    "abil_caption": r"^\[([^\]:]+?): (.*)\]$",
    "drew": r"^\((P\d) drew: (.*)\)$",
    "passed_over": r"^\(passed over: (.*)\)$",
    "no_window": r"^\(" + SEAT + r" holds nothing playable",
    # ↳ effect lines (leading "↳ " stripped)
    "e_life": r"^" + SEAT + r" ([+-]\d+) life \(now (-?\d+)\)",
    "e_draw": r"^" + SEAT + r" draws (\d+)",
    "e_token": r"^" + SEAT + r" creates (\d+)x (.+?) token",
    "e_pcounters": r"^" + SEAT + r": (.+) \(now (.+)\)$",
    "e_reveal_hand": r"^" + SEAT + r" REVEALS HAND: (.*)$",
    "e_reveal_top": r"^" + SEAT + r" reveals top \d+: (.*)$",
    "e_countered_mark": r"^(\S+#\d+) (.+) is countered",
    "e_countered": r"^(?:activation of )?(.+?) is COUNTERED",
    "e_fizzle": r"^(.+?) FIZZLES on resolution",
    "e_left_stack": r"^(.+?) left the stack before it resolved",
    "e_leaves_stack": r"^(.+?) \((stack#\d+)\) leaves the stack",
    "e_stays_cz": r"^(.+?) stays in command zone",
    "e_coin": r"^coin flip: (\w+)",
    "e_roll": r"^d(\d+) roll: (\d+)",
    "e_note": r"^note \((P\d)\): (.*)$",
    "e_search": r"^" + SEAT + r" searches library: (.+?) → (\S+)",
    "e_concede": r"^" + SEAT + r" (CONCEDES|DISPUTES|AGREES)",
    "e_damage": r"^(\S.*?#\d+) takes (\d+) damage",
    "e_fight": r"^(\S.*?#\d+) fights (\S.*?#\d+)",
    "e_copy": r"^" + SEAT + r" copies (.+?) \((stack#\d+)\) x(\d+)(?:, new targets: (.+?))? — the copies resolve",
}.items()}

PLAYER_KEYS = ("handle", "name", "life", "alive", "hand", "graveyard", "exile", "library",
               "command_zone", "commanders", "commander_tax")


def _lit(s, default):
    try:
        return ast.literal_eval(s)
    except (ValueError, SyntaxError):
        return default


def split_targets(s):
    """'Stella Lee, Wild Card#21, Island#20' -> ['Stella Lee, Wild Card#21', 'Island#20']"""
    tg = [x.strip() for x in s.split(",")] if s else []
    # an id-less piece right before a "…#N" piece is the front of one comma'd name
    k = 0
    while k < len(tg) - 1:
        if not re.search(r"#\d+$|^P\d|^stack#", tg[k]) and re.search(r"#\d+$", tg[k + 1]):
            tg[k:k + 2] = [f"{tg[k]}, {tg[k + 1]}"]
        k += 1
    return tg


def classify(line, private):
    """-> dict of kind fields (always has "kind"; "seat" when a seat acts)."""
    t = line.strip()
    if not t:
        return {"kind": "blank"}
    m = R["pod"].match(t)
    if m:
        seats = [{"handle": h, "deck": d} for h, d in re.findall(SEAT, m.group(1))]
        return {"kind": "pod", "seats": seats, "seed": m.group(2)}
    m = R["turn"].match(t)
    if m:
        return {"kind": "turn", "turn": int(m.group(1)), "seat": m.group(2)}
    m = R["thinks"].match(t)
    if m:
        return {"kind": "thinks", "seat": m.group(1), "text": m.group(3)}
    m = R["says"].match(t)
    if m:
        return {"kind": "says", "seat": m.group(1), "text": m.group(3)}
    m = R["judge"].match(t)
    if m:
        return {"kind": "judge", "ruling": bool(m.group(1)), "text": m.group(2)}
    m = R["elim"].match(t)
    if m:
        return {"kind": "elim", "seat": m.group(1), "text": m.group(3)}
    m = R["gameover"].match(t)
    if m:
        if m.group(1):
            return {"kind": "gameover", "winner": m.group(1), "text": m.group(3), "turn": int(m.group(4))}
        return {"kind": "gameover", "winner": None, "text": m.group(6), "turn": int(m.group(5))}
    m = R["turncap"].match(t)
    if m:
        return {"kind": "gameover", "winner": None, "text": f"turn cap {m.group(1)}", "turncap": True,
                "standings": m.group(2)}
    m = R["claim"].match(t)
    if m:
        return {"kind": "claim", "seat": m.group(1), "text": t.strip("*")}
    m = R["land"].match(t)
    if m:
        return {"kind": "land", "seat": m.group(1), "card": m.group(3), "tapped": bool(m.group(4))}
    m = R["announce"].match(t)
    if m:
        tg = split_targets(m.group(6))
        return {"kind": "announce", "seat": m.group(1), "card": m.group(4), "sid": m.group(5) or "",
                "targets": tg, "ability": bool(m.group(3)), "text": m.group(7) or ""}
    m = R["cast_fizzle"].match(t)
    if m:
        return {"kind": "cast", "seat": m.group(1), "card": m.group(3), "fizzled": True, "text": m.group(4)}
    m = R["cast"].match(t)
    if m:
        return {"kind": "cast", "seat": m.group(1), "card": m.group(3), "from_cz": bool(m.group(4)),
                "tapping": _lit(m.group(5), []) if m.group(5) else [], "text": m.group(6) or ""}
    m = R["activate"].match(t)
    if m:
        return {"kind": "activate", "seat": m.group(1), "card": m.group(3), "text": m.group(4) or ""}
    m = R["attack"].match(t)
    if m:
        return {"kind": "attack", "seat": m.group(1), "target": m.group(3), "ids": _lit(m.group(5), [])}
    m = R["block"].match(t)
    if m:
        return {"kind": "block", "seat": m.group(1), "blocks": _lit(m.group(3), {}), "text": m.group(4)}
    m = R["combat"].match(t)
    if m:
        return {"kind": "combat", "text": m.group(1)}
    m = R["correct"].match(t)
    if m:
        return {"kind": "correct", "seat": m.group(1), "text": m.group(3)}
    m = R["offer"].match(t)
    if m:
        return {"kind": "offer", "seat": m.group(1), "text": m.group(3)}
    m = R["look_top"].match(t)
    if m:
        return {"kind": "look", "seat": m.group(1), "n": int(m.group(3))}
    m = R["mull"].match(t)
    if m:
        return {"kind": "mull", "seat": m.group(1), "verb": m.group(3), "text": t}
    m = R["untap"].match(t)
    if m:
        return {"kind": "untap", "seat": m.group(1)}
    m = R["no_window"].match(t)
    if m:
        return {"kind": "no_window", "seat": m.group(1)}
    m = R["drew"].match(t)
    if m:
        return {"kind": "drew", "seat": m.group(1), "cards": [c.strip() for c in m.group(2).split(", ")]}
    m = R["caption"].match(t)
    if m:
        return {"kind": "caption", "card": m.group(1)}
    if t.startswith("[") and t.endswith("]"):
        m = R["abil_caption"].match(t)
        return {"kind": "peek" if (m and m.group(1).startswith(("the top", "top", "bottom", "their")))
                else "caption", "card": m.group(1) if m else None, "text": t[1:-1]}
    if t.startswith("!!"):
        return {"kind": "warn", "text": t.lstrip("! ").strip()}
    if t.startswith("↳"):
        return _effect(t[1:].strip())
    if t.startswith("("):
        return {"kind": "aside", "text": t}
    return {"kind": "misc", "text": t}


def _effect(t):
    out = {"kind": "effect", "text": t}
    for key, kind in (("e_life", "life"), ("e_draw", "draw"), ("e_token", "token"),
                      ("e_reveal_hand", "reveal"), ("e_reveal_top", "reveal"),
                      ("e_search", "search"), ("e_concede", "concede")):
        m = R[key].match(t)
        if m:
            out.update(kind=kind, seat=m.group(1))
            if kind == "life":
                out.update(delta=int(m.group(3)), now=int(m.group(4)))
            elif kind == "draw":
                out["n"] = int(m.group(3))
            elif kind == "token":
                out.update(n=int(m.group(3)), card=m.group(4))
            elif kind == "reveal":
                out["cards"] = [c.strip() for c in m.group(3).split(", ")]
            elif kind == "search":
                out.update(card=m.group(3), to=m.group(4))
            elif kind == "concede":
                out["verb"] = m.group(3)
            return out
    m = R["e_copy"].match(t)
    if m:
        return {**out, "kind": "copy", "seat": m.group(1), "card": m.group(3), "sid": m.group(4),
                "n": int(m.group(5)), "targets": split_targets(m.group(6))}
    m = R["e_answer"].match(t)
    if m:
        return {**out, "kind": "answer", "seat": m.group(1), "text": m.group(3)}
    m = R["e_pcounters"].match(t)
    if m and ("{" in m.group(4) or m.group(4) == "none"):
        now = {} if m.group(4) == "none" else _lit(m.group(4), {})
        return {**out, "kind": "pcounters", "seat": m.group(1), "counters": now}
    m = R["e_countered_mark"].match(t)
    if m:
        return {**out, "kind": "countered_mark", "sid": m.group(1)}
    for key, kind in (("e_countered", "countered"), ("e_fizzle", "fizzle"),
                      ("e_left_stack", "left_stack"), ("e_stays_cz", "left_stack")):
        m = R[key].match(t)
        if m:
            return {**out, "kind": kind, "card": m.group(1)}
    m = R["e_leaves_stack"].match(t)
    if m:
        return {**out, "kind": "left_stack", "card": m.group(1), "sid": m.group(2)}
    m = R["e_coin"].match(t)
    if m:
        return {**out, "kind": "coin", "result": m.group(1)}
    m = R["e_roll"].match(t)
    if m:
        return {**out, "kind": "roll", "sides": int(m.group(1)), "result": int(m.group(2))}
    m = R["e_note"].match(t)
    if m:
        return {**out, "kind": "note", "seat": m.group(1), "text": m.group(2)}
    m = R["e_damage"].match(t)
    if m:
        return {**out, "kind": "damage", "id": m.group(1), "n": int(m.group(2))}
    m = R["e_fight"].match(t)
    if m:
        return {**out, "kind": "fight", "a": m.group(1), "b": m.group(2)}
    return out


ATTACH_VERB = re.compile(r"\b(attach|attached|attaches|attaching|equip|equipped|equips|equipping|enchants|enchanting|"
                         r"enchanted by|wears|wearing|fortif(?:y|ies|ied)|onto)\b", re.I)
SENTENCE = re.compile(r"(?<=[.;!?])\s+|\s+—\s+")


class Compiler:
    """Stateful: feed engine events in order, get compiled events back.
    types: {card name: type line (+ " enchant player" for auras that enchant players)},
    used to tell auras and equipment apart."""

    def __init__(self, types=None):
        self.types = types or {}
        self.attach = {}              # attachment perm id -> host perm id, or a seat handle ("P1") for player attachments
        self.pending = {}             # attachment card name -> host perm id / seat, named before the card hit the battlefield
        self.resolved = None          # stack item popped by the current cast/activate line
        self.i = 0
        self.stack = []
        self.counters = {}            # handle -> {kind: n}
        self.active = None
        self.prev_players = []        # json strings of the previous event's players
        self.decks = {}               # handle -> deck name

    def _pop(self, card=None, seat=None, sid=None, ability=None):
        for k in range(len(self.stack) - 1, -1, -1):
            it = self.stack[k]
            if sid and it["sid"] != sid:
                continue
            if card and it["card"] != card:
                continue
            if seat and it["seat"] != seat:
                continue
            if ability is not None and it["ability"] != ability:
                continue
            return self.stack.pop(k)
        return None

    def _track(self, ev):
        k = ev["kind"]
        if k == "pod":
            self.decks = {s["handle"]: s["deck"] for s in ev["seats"]}
        elif k == "turn":
            self.active = ev["seat"]
            self.pending = {}          # an unfulfilled "onto X" doesn't outlive its turn
            self.stack = []           # nothing on the stack survives a turn boundary
        elif k == "announce":
            ev["sid"] = ev["sid"] or f"stack#e{self.i}"
            self.stack.append({"sid": ev["sid"], "card": ev["card"], "seat": ev["seat"],
                               "targets": ev["targets"], "ability": ev["ability"], "countered": False})
        elif k == "cast":
            self.resolved = self._pop(card=ev["card"], seat=ev["seat"], ability=False) or self._pop(card=ev["card"])
        elif k == "activate":
            self.resolved = self._pop(card=ev["card"], seat=ev["seat"], ability=True) or self._pop(card=ev["card"])
        elif k in ("countered", "fizzle", "left_stack"):
            self._pop(sid=ev.get("sid"), card=None if ev.get("sid") else ev["card"])
        elif k == "countered_mark":
            for it in self.stack:
                if it["sid"] == ev["sid"]:
                    it["countered"] = True
        elif k == "pcounters":
            self.counters[ev["seat"]] = ev["counters"]

    def _type(self, name):
        return self.types.get(re.sub(r"#\d+$", "", str(name)), "")

    def _attachments(self, ev, st):
        """Infer attachments from aura casts, equip activations and agents' notes."""
        perms = {x["id"]: (pl.get("handle"), x) for pl in st.get("players", []) for x in pl.get("battlefield", [])}
        if not perms:
            return
        by_name = {}
        for pid, (hdl, x) in perms.items():
            by_name.setdefault(x["name"], []).append((pid, hdl))

        def resolve(ref, prefer=None):
            ref = ref.strip().rstrip(".,;:")
            if ref in perms:
                return ref
            hits = by_name.get(re.sub(r"#\d+$", "", ref), [])
            mine = [pid for pid, h in hits if h == prefer]
            return (mine or [pid for pid, _ in hits] or [None])[-1]

        def prefix_perm(text, prefer=None):
            """the battlefield permanent whose id or name the text starts with (longest wins)"""
            best = None
            for pid, (hdl, x) in perms.items():
                for cand in (pid, x["name"]):
                    if text.startswith(cand) and (not best or len(cand) > best[1] or (len(cand) == best[1] and hdl == prefer)):
                        best = (pid, len(cand))
            return best[0] if best else None

        k, seat = ev["kind"], ev.get("seat")
        it, self.resolved = self.resolved, None
        for pid, (h, x) in perms.items():           # "fetches Brilliant Wings onto Light-Paws" lands now
            host = self.pending.get(x["name"])
            if host and pid not in self.attach and (host in perms or re.fullmatch(r"P\d", host)):
                self.attach[pid] = host
                del self.pending[x["name"]]
        if k == "cast" and it and it.get("targets") and self._enchants_player(ev["card"]):
            att = [pid for pid, (h, x) in perms.items() if x["name"] == ev["card"] and h == seat and pid not in self.attach]
            who = re.match(r"P\d", it["targets"][0])
            if att and who:
                self.attach[att[-1]] = who.group(0)
            elif who:
                self.pending[ev["card"]] = who.group(0)
        elif k == "cast" and it and it.get("targets") and self._is_attachment(ev["card"]) and "Aura" in self._type(ev["card"]):
            att = [pid for pid, (h, x) in perms.items() if x["name"] == ev["card"] and h == seat and pid not in self.attach]
            host = resolve(it["targets"][0])
            if att and host and host != att[-1]:
                self.attach[att[-1]] = host
            elif host:                         # still under a trigger on the stack: attach when it lands
                self.pending[ev["card"]] = host
        elif k == "activate" and it and it.get("targets") and "Equipment" in self._type(ev["card"]):
            att, host = resolve(ev["card"], seat), resolve(it["targets"][0])
            if att and host and att != host:
                self.attach[att] = host
        if k in ("note", "cast", "activate", "correct", "announce") and ev.get("text"):
            # an aura's own cast narration names its host with plain "targeting"
            aura_cast = k == "cast" and (self._is_attachment(ev.get("card", "")) or self._enchants_player(ev.get("card", "")))
            for sentence in SENTENCE.split(ev["text"]):
                if ATTACH_VERB.search(sentence) or (aura_cast and re.search(r"\btarget(s|ing|ed)?\b", sentence, re.I)):
                    self._attach_from_text(sentence, perms, seat)
                    self._pending_from_text(sentence, perms)
        # either side leaving the battlefield ends the attachment
        seats = {pl.get("handle") for pl in st.get("players", [])}
        self.attach = {a: h for a, h in self.attach.items() if a in perms and (h in perms or h in seats)}

    def _is_attachment(self, name):
        t = self._type(name)
        if "Curse" in t or "enchant player" in t:             # these enchant players, not permanents
            return False
        return "Aura" in t or "Equipment" in t or "Fortification" in t

    def _pending_from_text(self, text, perms):
        """Attachments named with a host before they're on the battlefield: remember the host."""
        on = {x["name"] for _, x in perms.values()}
        for name in self.types:
            if name in on or name not in text or not (self._is_attachment(name) or self._enchants_player(name)):
                continue
            i = text.index(name)
            if self._enchants_player(name):
                seats = [m for m in re.finditer(r"\bP\d\b(?!\()", text)]
                if seats:
                    self.pending[name] = min(seats, key=lambda m: abs(m.start() - i)).group(0)
                continue
            # (start, end, pid); the host named after the verb wins, else the one with the smallest gap
            hosts = [(text.find(nm), text.find(nm) + len(nm), pid) for pid, (_, x) in perms.items()
                     if not self._is_attachment(x["name"])
                     for nm in {x["name"], x["name"].split(",")[0]} if len(nm) >= 4 and nm in text]
            verb = ATTACH_VERB.search(text, i + len(name))
            after = sorted(hp for hp in hosts if verb and hp[0] >= verb.end())
            gap = lambda hp: hp[0] - (i + len(name)) if hp[0] >= i else i - hp[1]
            if after or hosts:
                self.pending[name] = (after[0] if after else min(hosts, key=gap))[2]

    def _enchants_player(self, name):
        t = self._type(name)
        return "Curse" in t or "enchant player" in t

    def _attach_from_text(self, text, perms, seat):
        """Pair every aura/equipment mentioned in a sentence with its nearest mentioned host."""
        cands = []
        for pid, (hdl, x) in perms.items():
            cands.append((pid, pid, hdl))
            cands.append((x["name"], pid, hdl))
            short = x["name"].split(",")[0]                     # "Light-Paws" for "Light-Paws, Emperor's Voice"
            if short != x["name"] and len(short) >= 4:
                cands.append((short, pid, hdl))
        cands.sort(key=lambda c: -len(c[0]))
        taken, mentions = [False] * len(text), []
        for needle, pid, hdl in cands:
            start = 0
            while (i := text.find(needle, start)) >= 0:
                start = i + 1
                if any(taken[i:i + len(needle)]):
                    continue
                for j in range(i, i + len(needle)):
                    taken[j] = True
                mentions.append((i, needle, pid, hdl))
        # a bare name matches every copy; keep one per mention, preferring the speaker's, then the unattached
        pos = {}
        for i, needle, pid, hdl in mentions:
            best = pos.get(i)
            score = (needle == pid) * 4 + (hdl == seat) * 2 + (pid not in self.attach)
            if not best or score > best[0]:
                pos[i] = (score, pid, i + len(needle))
        found = sorted((i, pid) for i, (_, pid, _e) in pos.items())
        end = {i: e for i, (_, _p, e) in pos.items()}
        atts = [(i, pid) for i, pid in found if self._is_attachment(perms[pid][1]["name"])]
        hosts = [(i, pid) for i, pid in found if not self._is_attachment(perms[pid][1]["name"])]
        # seats named in the sentence ("P3", not "P3(name)", which is a speaker prefix)
        players = [(m.start(), m.group(0)) for m in re.finditer(r"\bP\d\b(?!\()", text) if not any(taken[m.start():m.end()])]
        # a curse goes to the seat named after the verb ("Curse (P2) enchants P1"), else the nearest one
        verb = ATTACH_VERB.search(text)
        after = [pp for pp in players if verb and pp[0] > verb.start()]
        for i, att in [(i, pid) for i, pid in found if self._enchants_player(perms[pid][1]["name"])]:
            if after or players:
                self.attach[att] = (after[0] if after else min(players, key=lambda pp: abs(pp[0] - i)))[1]
        hosts = [(i, pid) for i, pid in hosts if not self._enchants_player(perms[pid][1]["name"])]
        if not atts or not hosts:
            return
        for i, att in atts:
            host = min(hosts, key=lambda hp: hp[0] - end[i] if hp[0] >= i else i - end[hp[0]])[1]   # gap between the names
            if host != att:
                self.attach[att] = host

    def feed(self, raw):
        """raw: one engine event dict. -> compiled event dict."""
        line, private = raw.get("line", ""), bool(raw.get("private"))
        ev = classify(line, private)
        self._track(ev)
        st = raw.get("state") or {}
        self._attachments(ev, st)
        if st.get("stack_empty") is True:
            self.stack = []
        players, cur = [], []
        for pl in st.get("players", []):
            p = {k: pl.get(k) for k in PLAYER_KEYS}
            p["deck"] = self.decks.get(pl.get("handle"), (pl.get("name") or "").partition("(")[2].rstrip(")"))
            p["counters"] = pl.get("counters") or self.counters.get(pl.get("handle"), {})
            p["battlefield"] = [{k: v for k, v in x.items() if v not in (None, False, 0, {}, [])
                                 or k in ("id", "name")} for x in pl.get("battlefield", [])]
            s = json.dumps(p, sort_keys=True)
            cur.append(s)
            n = len(cur) - 1
            players.append(None if n < len(self.prev_players) and self.prev_players[n] == s else p)
        self.prev_players = cur
        out = {"i": self.i, "line": line, "priv": private, **ev,
               "state": {"turn": st.get("turn", 0), "active": self.active,
                         "stack": [dict(x) for x in self.stack], "attach": dict(self.attach), "players": players}}
        self.i += 1
        return out


def compile_file(path, types=None):
    """-> [compiled event] for a whole events.jsonl (malformed lines skipped)."""
    c, out = Compiler(types), []
    with open(path) as f:
        for ln in f:
            if not ln.strip():
                continue
            try:
                raw = json.loads(ln)
            except json.JSONDecodeError:
                continue            # a live game's torn last line
            out.append(c.feed(raw))
    return out
