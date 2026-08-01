"""Engine — the deliberately dumb abacus, multiplayer edition.

Owns exactly two things: hidden information and arithmetic. All game LOGIC —
what a card does, whether a line is legal, combat math, threat assessment,
politics — lives in the agents. The engine applies whatever atoms the agents
declare, logs its doubts publicly, and never adjudicates rules.

Pod play: 2–4 seats (P1..P4), turn order rotation, elimination, last seat
standing wins. Everything the engine logs is the PUBLIC TABLE LOG; its tail is
serialized into every prompt, and any agent may attach "table_talk" to any
reply — that's the social game's medium.

THE ATOMS. Agents compose arbitrary card behavior out of these; there are no
card-specific code paths. Two classes:

Bookkeeping (visible state, applied verbatim):
  {"move":{"id":"Sol Ring#4","to":"graveyard"}}            # battlefield → zone
  {"move":{"player":"P2","from":"library_top","n":8,"to":"graveyard"}}   # mill
  {"move":{"player":"self","from":"hand","card":"Fog","to":"graveyard"}} # discard
  {"move":{"player":"self","from":"graveyard","card":"X","to":"battlefield","tapped":false}}
     zones: hand, battlefield, graveyard, exile, library_top, library_bottom, command
     tokens moved off the battlefield cease to exist. Moves are verified: the
     named card must actually be in the source zone.
  {"life":{"player":"P3","delta":-6}}          # damage and lifegain alike
  {"create":{"player":"self","name":"Drake","n":2,"pt":[2,2],"tapped":false}}
  {"set":{"id":"Scurry Oak#9","tapped":false,"sick":false,"counters":3,"pt":[5,5]}}
     counters is a DELTA; pt overrides base p/t (layers are your problem)
  {"eliminate":{"player":"P3","reason":"Thassa's Oracle"}}
     self-elimination applies instantly; eliminating another seat gives that
     seat an accept/dispute vote (kitchen table, not the Pro Tour)
  {"note":"..."}   # ongoing constraints, emblems, roles — the log is memory

Hidden-information services (the engine executes these because agents can't):
  {"draw":{"player":"self","n":2}}             # counts toward drew_this_turn
  {"search":{"player":"self","card":"Forest","to":"battlefield","tapped":true,"shuffle":true}}
     engine verifies the card is really in that library — no lying tutors
  {"shuffle":{"player":"self"}}
  {"reveal":{"player":"self","zone":"hand"}} or {"zone":"library_top","n":3}
  {"random":{"coin":true}} or {"random":{"die":6}}   # engine-owned, logged

Wins the engine can't see (Thassa's Oracle, Approach, demonstrated loops):
  {"action":"claim_win","how":"..."} → every other seat votes concede/dispute;
  unanimous concession ends the game. Or eliminate the table seat by seat.

Actions: play_land, cast, activate, attack, respond, block, claim_win, pass.
  cast/activate carry "tap":[permanent ids] (mana payment; engine taps them)
  and "effects":[atoms] (all consequences, agent-declared).
  attack: {"attacks":{"P2":[attacker ids],"P4":[...]}}
Player references: seat handle ("P3"); "self" always works; "opponent" only
when exactly one other seat is alive.

Knows nothing about how agents are implemented: it is handed objects with a
single method  ask(prompt: str) -> str  (raw model text; engine parses JSON).
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ZONES = ("hand", "battlefield", "graveyard", "exile", "library_top", "library_bottom", "command")

# ---- terminal color (tty only; files/prompts always plain) ----
# seat identity: P1 cyan, P2 yellow, P3 magenta, P4 green
_SEAT_COLORS = {"P1": "\033[96m", "P2": "\033[93m", "P3": "\033[95m", "P4": "\033[92m"}
_DIM, _RED, _BOLD, _ITAL, _GREY, _RESET = "\033[2m", "\033[91m", "\033[1m", "\033[3m", "\033[90m", "\033[0m"


_SEAT_RE = re.compile(r"\bP[1-4]\b")


def _colorize(s):
    """Seat colors everywhere a player owns a line. Private thinking grey
    italic, public speech plain white, plays bold, headers seat-colored."""
    if not sys.stdout.isatty():
        return s
    t = s.lstrip()
    seat_color = next((c for seat, c in _SEAT_COLORS.items() if t.startswith(seat)), "")
    if t.startswith("⚖"):
        return f"\033[1;97m{s}{_RESET}"                   # judge: bright white bold
    if t.startswith("!!"):
        return f"{_RED}{s}{_RESET}"                       # engine doubts / failures
    if t.startswith(("↳", "(note")):
        return f"{_DIM}{s}{_RESET}"                       # bookkeeping effects
    if t.startswith(("##", "**", "#")):                   # headers/eliminations: seat-colored bold
        m = _SEAT_RE.search(t)
        c = _SEAT_COLORS.get(m.group(0), "") if m else ""
        return f"{_BOLD}{c}{s}{_RESET}"
    if " thinks: " in t and seat_color:                   # PRIVATE reasoning: grey italic
        name, _, thought = s.partition(" thinks: ")
        return f"{seat_color}{name}{_RESET} {_GREY}{_ITAL}thinks: {thought}{_RESET}"
    if " says: " in t and seat_color:                     # PUBLIC speech: plain white
        name, _, speech = s.partition(" says: ")
        return f"{seat_color}{name}{_RESET} says: {speech}"
    if seat_color:                                        # actual plays: full bold seat color
        return f"{_BOLD}{seat_color}▶ {s}{_RESET}"
    return s

PROTOCOL = """=== PROTOCOL ===
Reply with exactly one JSON object — the engine parses it mechanically, so anything outside
the braces is lost. Optional in any reply:
 "thinking" — a private announcement channel to the humans watching the game (other players
   *never* see it). This is not your reasoning scratchpad — do your actual thinking silently,
   then announce the headline: one to three punchy sentences. What you're really up to, who
   you actually fear, what the table_talk is covering for. You don't need to disclose
   everything — just what makes the moment legible. Include it every reply ("not much here,
   playing the mana dork" is plenty); save the longer beat for genuinely pivotal turns.
 "narration" — brief description of the mechanics of your play (logged with the action).
 "table_talk" — public speech, in character at the table; every seat sees it. A sentence or
   three, tops: banter, deals, threats, reactions, needling. Analysis and reasoning belong in
   "thinking" instead — spoken analysis reads like a commentator rather than a player, and it
   hands your plans to the table. Silence is fine; you don't have to talk every window.
ACTIONS: {"action":"play_land","card":...} | {"action":"cast","card":...,"tap":[ids],
"targets":[permanent ids or seat handles],"effects":[...]} |
{"action":"activate","source":id,"tap_source":bool,"tap":[ids],"effects":[...]} |
{"action":"attack","attacks":{"P2":[attacker ids],...}} | {"action":"claim_win","how":"..."} |
{"action":"peek","n":N} | {"action":"pass"}
peek = private top-of-library looks (scry, surveil, Realmwalker, Sylvan Library, Sensei's
Top-style effects): the engine shows you the cards privately, then you declare the ordering
and any granted actions. Other players learn only that you looked.
Activated abilities that do more than make mana are announced and use the stack — other
players get response windows before they resolve, same as spells (pure mana abilities skip
this, as in the real rules). If the source dies in response, the ability still resolves.
THE STACK: announced spells and non-mana abilities become stack objects (stack#N) and are
open to responses — each response goes on top and resolves first, response windows reopen
whenever the stack changes, and you get the last window on your own spells (that is holding
priority: cast, then respond to yourself). Counter things by stack id:
{"counter":{"target":"stack#N"}} in your effects. If your card genuinely has split second,
declare "split_second": true on the cast — no response windows open, and the table will
check the claim. Mana abilities never use the stack.
List "targets" whenever your spell targets. Targets naming specific permanent ids ("X#12")
are checked at resolution: if every id-target has left the battlefield (someone responded),
the spell fizzles — graveyard, mana spent, no effects. Name-only targets (graveyard cards,
players, spells on the stack) are yours to adjudicate honestly. If responses resolve
while your announced spell waits, you'll be asked to confirm or adjust it against the changed
board before it resolves — the world you announced into may not be the world it resolves into.
EFFECT ATOMS (declare every consequence of your plays; the engine applies them verbatim and
has no card logic of its own — *you* are the rules engine, so anything you don't declare
simply doesn't happen). The engine already handles the cast/played card's own zone change
(battlefield or graveyard) and your mana taps — effects are for the *additional* consequences
(tokens, triggers, targets, costs like sacrifices):
 {"move":{"id":perm_id,"to":zone}}  zones: hand|battlefield|graveyard|exile|library_top|library_bottom|command
 {"move":{"id":perm_id,"to":"battlefield","control":"P2"}}  — control change (Mind Control,
   theft, donation). Dead/bounced permanents always route to their owner's zones.
 {"move":{"player":P,"from":zone,"card":name|"n":N|"all":true,"to":zone,"tapped":bool}}
   (verified against real zone contents; "all" empties the zone — wheels, mass discard)
 {"life":{"player":P,"delta":±N}}
 {"create":{"player":P,"name":...,"n":N,"pt":[p,t],"tapped":bool}}
 {"set":{"id":perm_id,"tapped":bool,"sick":bool,"counters":±delta,"pt":[p,t]}}
 {"draw":{"player":P,"n":N,"from":"top"|"bottom"}}   {"shuffle":{"player":P}}
 {"search":{"player":P,"card":name,"to":zone,"tapped":bool,"shuffle":bool}}  (engine verifies)
 {"reveal":{"player":P,"zone":"hand"|"library_top","n":N}}
 {"random":{"coin":true}|{"die":N}}   (engine rolls — never claim your own randomness)
 {"eliminate":{"player":P,"reason":...}}   {"note":"ongoing constraints — the log is the table's memory"}
Player refs: seat handles ("P1".."P4") or "self". Declare upkeep/beginning-of-turn triggers
in your first main-phase action's effects. States the engine can't hold (emblems, roles,
"can't" effects, delayed triggers) go in notes — and get honored by everyone."""


class GameOver(Exception):
    def __init__(self, winner, how):
        self.winner, self.how = winner, how


class Player:
    def __init__(self, handle, deckname, decklist, commander, rng, strategy=""):
        self.handle = handle                  # "P1"
        self.strategy = strategy              # gameplan blurb, delivered in the brief
        self.name = f"{handle}({deckname})"   # "P1(snakes)"
        self.decklist = decklist[:]           # full 99, for deck-knowledge prompts
        self.library = decklist[:]
        rng.shuffle(self.library)
        self.hand = [self.library.pop(0) for _ in range(7)]
        self.commander = commander
        self.command_zone = True
        self.commander_tax = 0
        self.battlefield = []   # dicts: id,name,tapped,sick,counters,token,pt
        self.graveyard = []
        self.exile = []
        self.life = 40
        self.alive = True
        self.lands_played = 0
        self.drew_this_turn = 0


class Game:
    def __init__(self, db, decks, agents, seed, log_path, max_turns, rng, log_tail=60,
                 judge_factory=None):
        """db: card-name -> {cost,type,text,pt}.
        decks: [(deckname, decklist, commander)] for 2..4 seats.
        agents: objects with .ask(prompt)->str, index-aligned with decks."""
        assert 2 <= len(decks) <= 4, "pod size 2-4"
        self.db = db
        self.rng = rng
        self.p = [Player(f"P{n+1}", spec[0], spec[1], spec[2], rng,
                         strategy=spec[3] if len(spec) > 3 else "")
                  for n, spec in enumerate(decks)]
        self.agents = agents
        self.turn = 0
        self.next_id = 1
        self.max_turns = max_turns
        self.log_tail = log_tail
        self.table = []           # public table log (every line ever logged)
        self.log_sent = [0] * len(decks)          # table index each seat has seen
        self.oracle_shown = [set() for _ in decks]  # card names whose text each seat has
        self.force_full = [True] * len(decks)     # full re-sync pending per seat
        self.board_full = [False] * len(decks)    # full board state due (own turn start)
        self.pending_talk = []                    # table_talk queued to land AFTER the play
        self.stack = []                           # live stack objects (announce -> resolve)
        self.stack_seq = 0
        self.judge_inbox = Path(f"{log_path}.judge")
        self.judge_factory = judge_factory
        self.judge_agent = None
        self.logf = open(log_path, "w")
        self.eventsf = open(f"{log_path}.events.jsonl", "w")
        self.log(f"# Pod: {', '.join(pl.name for pl in self.p)} — seed {seed} — {datetime.now()}\n")

    # ---------------- public table log ----------------
    def snapshot(self):
        """Full authoritative state as plain data — the renderer's ground
        truth. Diffs between consecutive snapshots are the animations."""
        return {
            "turn": self.turn,
            "players": [{
                "handle": pl.handle, "name": pl.name, "life": pl.life, "alive": pl.alive,
                "hand": list(pl.hand), "graveyard": list(pl.graveyard), "exile": list(pl.exile),
                "library": len(pl.library), "command_zone": pl.command_zone,
                "commander": pl.commander, "commander_tax": pl.commander_tax,
                "battlefield": [dict(x) for x in pl.battlefield],
            } for pl in self.p],
        }

    def _emit(self, line, private=False):
        self.eventsf.write(json.dumps(
            {"line": line, "private": private, "state": self.snapshot()}) + "\n")
        self.eventsf.flush()

    def log(self, s):
        print(_colorize(s))
        self.table.append(s)
        self.logf.write(s + "\n")
        self.logf.flush()
        self._emit(s)

    def log_private(self, s):
        """Spectator + file only. NEVER appended to self.table, so no agent
        prompt ever contains it — other seats cannot see it."""
        print(_colorize(s))
        self.logf.write(f"[private] {s}\n")
        self.logf.flush()
        self._emit(s, private=True)

    # ---------------- judge channel ----------------
    def check_judge(self):
        """Spectator interjections. Two inputs: lines typed into the game
        terminal, or appended to <log>.judge from another shell. A plain
        message posts to the table verbatim as the judge speaking. The literal
        keyword JUDGE (optionally followed by a question) never reaches the
        table — it summons a codex judge with the full true state, and the
        RULING is what gets posted in the spectator's stead."""
        msgs = []
        try:
            if self.judge_inbox.exists():
                text = self.judge_inbox.read_text()
                if text.strip():
                    self.judge_inbox.write_text("")
                    msgs += [l.strip() for l in text.splitlines() if l.strip()]
        except OSError:
            pass
        try:
            import select
            while sys.stdin.isatty() and select.select([sys.stdin], [], [], 0)[0]:
                line = sys.stdin.readline()
                if not line:
                    break
                if line.strip():
                    msgs.append(line.strip())
        except Exception:
            pass
        for m in msgs:
            if m == "JUDGE" or m.startswith("JUDGE "):
                self._summon_judge(m[5:].strip())
            else:
                self.log(f'⚖ JUDGE: {m}')

    def _true_state(self):
        """Full authoritative state — judge-only; includes hidden hands."""
        out = []
        for pl in self.p:
            if not pl.alive:
                out.append(f"{pl.name}: eliminated")
                continue
            bf = "; ".join(
                f"{x['id']}{'(' + str(x['pt'][0]+x['counters']) + '/' + str(x['pt'][1]+x['counters']) + ')' if x['pt'] else ''}"
                f"{'[T]' if x['tapped'] else ''}{'[sick]' if x['sick'] else ''}"
                for x in pl.battlefield) or "(empty)"
            cz = f"commander in CZ (tax +{pl.commander_tax})" if pl.command_zone else "commander not in CZ"
            out.append(f"{pl.name}: life {pl.life}, {cz}\n  HAND: {', '.join(pl.hand) or '(empty)'}\n"
                       f"  BATTLEFIELD: {bf}\n  GRAVEYARD: {', '.join(pl.graveyard) or '(empty)'}\n"
                       f"  library {len(pl.library)} cards")
        return "\n".join(out)

    def _summon_judge(self, question):
        if not self.judge_factory:
            self.log(f'⚖ JUDGE: {question or "(judge call — no judge configured)"}')
            return
        if self.judge_agent is None:
            self.judge_agent = self.judge_factory()
        print(_colorize("⚖ (game frozen — judge deliberating...)"))
        prompt = ("You are the JUDGE overseeing a kitchen-table game of Magic: The Gathering (Commander "
                  "pod). The engine is a pure bookkeeper; the players are LLM agents who declare their own "
                  "rules interpretations, and they sometimes drift from the true state. You see the full "
                  "authoritative engine state below, including hidden hands — never reveal hidden "
                  "information in your ruling beyond what is strictly needed to resolve the issue.\n"
                  "Your reply will be posted verbatim to the public table log as a binding ruling that all "
                  "players read and must honor. Be concise and concrete: name any state or rules errors you "
                  "find, prescribe the exact corrections (players apply them via their effect atoms), and "
                  "answer the spectator's question if one was asked. Reply with plain text, not JSON.\n\n"
                  f"=== SPECTATOR'S QUESTION ===\n{question or '(none — general review of the current situation)'}\n\n"
                  f"=== TRUE GAME STATE (authoritative, includes hidden info) ===\n{self._true_state()}\n\n"
                  f"=== PUBLIC TABLE LOG (recent) ===\n" + "\n".join(self.table[-250:]))
        ruling = self.judge_agent.ask(prompt).strip()
        self.log(f'⚖ JUDGE RULES: {ruling}')

    # ---------------- lookups ----------------
    def perm(self, pl, name, token=False, tapped=False, pt=None):
        d = self.db.get(name, {})
        p = {"id": f"{name}#{self.next_id}", "name": name, "tapped": tapped,
             "sick": True, "counters": 0, "token": token, "owner": pl.handle,
             "pt": pt or d.get("pt")}
        if not p["pt"]:                      # noncreatures don't get sick
            p["sick"] = False
        self.next_id += 1
        pl.battlefield.append(p)
        return p

    def find(self, ident, prefer=None):
        """Resolve a permanent reference: full id ('Sol Ring#4'), plain name,
        or bare number ('4' / 4 — agents shorthand the #id suffix). With
        duplicate names across seats, a bare-name reference prefers the
        acting player's copy (prefer=seat index) and logs the ambiguity."""
        if ident is None:
            return None, None
        s = str(ident)
        matches = [(pl, perm) for pl in self.p for perm in pl.battlefield
                   if perm["id"] == s or perm["name"] == s]
        if len(matches) == 1:
            return matches[0]
        if matches:
            if prefer is not None:
                own = [(pl, x) for pl, x in matches if pl is self.p[prefer]]
                if own:
                    if len(matches) > 1:
                        self.log(f"  !! ambiguous reference {s!r} (multiple copies on battlefield) — "
                                 f"using {own[0][1]['id']} ({own[0][0].handle}'s); use full ids")
                    return own[0]
            self.log(f"  !! ambiguous reference {s!r} — using {matches[0][1]['id']} "
                     f"({matches[0][0].handle}'s); use full ids")
            return matches[0]
        if s.isdigit():
            for pl in self.p:
                for perm in pl.battlefield:
                    if perm["id"].endswith(f"#{s}"):
                        return pl, perm
        return None, None

    def resolve_player(self, i, ref):
        me = self.p[i]
        if ref in (None, "self", "you", me.handle, me.name):
            return me
        if ref == "opponent":
            others = [p for p in self.p if p.alive and p is not me]
            return others[0] if len(others) == 1 else None
        for pl in self.p:
            if ref == pl.handle or ref == pl.name or pl.name.startswith(str(ref) + "("):
                return pl
        return None

    def others(self, i):
        """Alive seats other than i, in turn order starting after i."""
        n = len(self.p)
        return [self.p[j % n] for j in range(i + 1, i + n) if self.p[j % n].alive]

    # ---------------- life & death ----------------
    def eliminate(self, pl, how):
        pl.alive = False
        # CR 800.4a: what they OWN leaves with them; control effects they had
        # end, so permanents they merely stole revert to their owners.
        strays = [x for x in pl.battlefield
                  if x.get("owner") and x["owner"] != pl.handle and not x.get("token")]
        pl.battlefield.clear()
        self.log(f"\n**{pl.name} is ELIMINATED — {how}. Their permanents leave the battlefield.**")
        for x in strays:
            owner = next((q for q in self.p if q.handle == x["owner"] and q.alive), None)
            if owner:
                x["sick"] = bool(x.get("pt"))
                owner.battlefield.append(x)
                self.log(f"  ↳ {x['id']} reverts to {owner.name} — the control effect "
                         f"ended with {pl.handle}'s departure (CR 800.4a)")
        # ...and everything they OWN leaves the game, wherever it's being held
        for q in self.p:
            if q is pl:
                continue
            for x in [y for y in q.battlefield if y.get("owner") == pl.handle]:
                q.battlefield.remove(x)
                self.log(f"  ↳ {x['id']} leaves the game with its owner {pl.handle} "
                         f"(was under {q.handle}'s control)")
        alive = [p for p in self.p if p.alive]
        if len(alive) == 1:
            raise GameOver(self.p.index(alive[0]), "last seat standing")

    def draw(self, pl, n, frm="top"):
        drawn = []
        for _ in range(n):
            if not pl.library:
                self.eliminate(pl, "tried to draw from an empty library (decked)")
                return drawn
            c = pl.library.pop(-1 if frm == "bottom" else 0)
            pl.hand.append(c)
            drawn.append(c)
        pl.drew_this_turn += n
        return drawn

    # ---------------- the atoms ----------------
    def _zone_put(self, pl, name, to, tapped=False):
        """Route a card name into a zone of pl."""
        if to == "battlefield":
            self.perm(pl, name, tapped=tapped)
        elif to == "hand":
            pl.hand.append(name)
        elif to == "graveyard":
            pl.graveyard.append(name)
        elif to == "exile":
            pl.exile.append(name)
        elif to == "library_top":
            pl.library.insert(0, name)
        elif to == "library_bottom":
            pl.library.append(name)
        elif to == "command":
            pl.command_zone = True

    def _atom_move(self, i, mv):
        me = self.p[i]
        to = mv.get("to")
        if to not in ZONES:
            self.log(f"  !! move: bad destination {to!r}; skipped")
            return
        # battlefield permanent by id
        if "id" in mv:
            pl, perm = self.find(mv["id"], prefer=i)
            if not perm:
                self.log(f"  !! move: no permanent {mv['id']!r}; skipped")
                return
            if to == "battlefield" and mv.get("control"):
                new_pl = self.resolve_player(i, mv["control"])
                if not new_pl or not new_pl.alive:
                    self.log(f"  !! move: can't resolve controller {mv['control']!r}; skipped")
                    return
                pl.battlefield.remove(perm)
                perm["sick"] = bool(perm.get("pt"))   # control change: no attacking yet
                new_pl.battlefield.append(perm)
                self.log(f"  ↳ {perm['id']} changes control: {pl.handle} → {new_pl.handle} "
                         f"(owner {perm.get('owner', pl.handle)})")
                return
            pl.battlefield.remove(perm)
            if perm["token"] and to != "battlefield":
                self.log(f"  ↳ {perm['id']} → {to} (token; ceases to exist)")
                return
            home = next((q for q in self.p if q.handle == perm.get("owner")), pl)
            self._zone_put(home, perm["name"], to, tapped=bool(mv.get("tapped")))
            self.log(f"  ↳ {perm['id']} → {home.name}'s {to}"
                     + (f" (owner)" if home is not pl else ""))
            return
        # card(s) from a named zone
        pl = self.resolve_player(i, mv.get("player", "self"))
        if not pl:
            self.log(f"  !! move: can't resolve player {mv.get('player')!r}; skipped")
            return
        frm = mv.get("from")
        if frm == "library_top":
            n = int(mv.get("n", 1))
            names = [pl.library.pop(0) for _ in range(min(n, len(pl.library)))]
            for name in names:
                self._zone_put(pl, name, to, tapped=bool(mv.get("tapped")))
            shown = ", ".join(names) if to not in ("hand", "library_bottom") else f"{len(names)} cards"
            self.log(f"  ↳ {pl.name}: top {len(names)} of library → {to} ({shown})")
            if not pl.library and to != "library_bottom":
                self.log(f"  (note: {pl.name}'s library is now empty)")
            return
        srcs = {"hand": pl.hand, "graveyard": pl.graveyard, "exile": pl.exile}
        if frm == "command":
            if pl.command_zone and mv.get("card") == pl.commander:
                pl.command_zone = False
                self._zone_put(pl, pl.commander, to, tapped=bool(mv.get("tapped")))
                self.log(f"  ↳ {pl.commander}: command zone → {to}")
            else:
                self.log(f"  !! move: commander not in {pl.name}'s command zone; skipped")
            return
        if frm not in srcs:
            self.log(f"  !! move: bad source {frm!r}; skipped")
            return
        if mv.get("all"):                       # wheels, discard-hand, mass exile
            names = list(srcs[frm])
            srcs[frm].clear()
            for name in names:
                self._zone_put(pl, name, to, tapped=bool(mv.get("tapped")))
            self.log(f"  ↳ {pl.name}: entire {frm} ({len(names)} cards) → {to}")
            return
        card = mv.get("card")
        if card not in srcs[frm]:
            self.log(f"  !! move: {card!r} not in {pl.name}'s {frm}; skipped (VERIFICATION FAILED)")
            return
        srcs[frm].remove(card)
        if card == pl.commander and to == "command":
            pl.command_zone = True
            self.log(f"  ↳ {card}: {frm} → command zone")
            return
        self._zone_put(pl, card, to, tapped=bool(mv.get("tapped")))
        self.log(f"  ↳ {pl.name}: {card} {frm} → {to}")

    def apply_effects(self, i, effects):
        me = self.p[i]
        for e in effects or []:
            if not isinstance(e, dict):
                continue
            if "move" in e:
                self._atom_move(i, e["move"])
            elif "life" in e:
                l = e["life"]
                tgt = self.resolve_player(i, l.get("player", "self"))
                if not tgt:
                    self.log(f"  !! life: can't resolve player {l.get('player')!r}; skipped")
                    continue
                d = int(l.get("delta", 0))
                tgt.life += d
                self.log(f"  ↳ {tgt.name} {'+' if d >= 0 else ''}{d} life (now {tgt.life})")
                if tgt.life <= 0 and tgt.alive:
                    self.eliminate(tgt, f"reduced to {tgt.life} life")
            elif "create" in e:
                t = e["create"]
                tgt = self.resolve_player(i, t.get("player", "self")) or me
                for _ in range(int(t.get("n", 1))):
                    self.perm(tgt, t.get("name", "Token"), token=True,
                              tapped=bool(t.get("tapped")), pt=tuple(t.get("pt", (1, 1))))
                self.log(f"  ↳ {tgt.name} creates {t.get('n',1)}x {t.get('name')} token(s)")
            elif "set" in e:
                s = e["set"]
                _, perm = self.find(s.get("id"), prefer=i)
                if not perm:
                    self.log(f"  !! set: no permanent {s.get('id')!r}; skipped")
                    continue
                changes = []
                if "tapped" in s:
                    perm["tapped"] = bool(s["tapped"]); changes.append(f"tapped={s['tapped']}")
                if "sick" in s:
                    perm["sick"] = bool(s["sick"]); changes.append(f"sick={s['sick']}")
                if "counters" in s:
                    perm["counters"] += int(s["counters"]); changes.append(f"counters{int(s['counters']):+d}")
                if "pt" in s:
                    perm["pt"] = tuple(s["pt"]); changes.append(f"pt={s['pt']}")
                self.log(f"  ↳ {perm['id']}: {', '.join(changes) or 'no-op'}")
            elif "draw" in e:
                d = e["draw"]
                tgt = self.resolve_player(i, d.get("player", "self"))
                if not tgt:
                    self.log(f"  !! draw: can't resolve player {d.get('player')!r}; skipped")
                    continue
                got = self.draw(tgt, int(d["n"]), frm=d.get("from", "top"))
                self.log(f"  ↳ {tgt.name} draws {len(got)}"
                         + (" from the BOTTOM" if d.get("from") == "bottom" else ""))
                if got:
                    self.log_private(f"  ({tgt.handle} drew: {', '.join(got)})")
            elif "search" in e:
                s = e["search"]
                tgt = self.resolve_player(i, s.get("player", "self"))
                if not tgt:
                    self.log(f"  !! search: can't resolve player {s.get('player')!r}; skipped")
                    continue
                card = s.get("card")
                if card not in tgt.library:
                    self.log(f"  !! search: {card!r} is NOT in {tgt.name}'s library (VERIFICATION FAILED); "
                             f"library shuffled anyway" if s.get("shuffle", True) else "")
                    if s.get("shuffle", True):
                        self.rng.shuffle(tgt.library)
                    continue
                tgt.library.remove(card)
                to = s.get("to", "hand")
                self._zone_put(tgt, card, to, tapped=bool(s.get("tapped")))
                if s.get("shuffle", True):
                    self.rng.shuffle(tgt.library)
                self.log(f"  ↳ {tgt.name} searches library: {card} → {to}" +
                         (" (shuffled)" if s.get("shuffle", True) else ""))
            elif "shuffle" in e:
                s = e["shuffle"]
                tgt = self.resolve_player(i, (s or {}).get("player", "self"))
                if tgt:
                    self.rng.shuffle(tgt.library)
                    self.log(f"  ↳ {tgt.name} shuffles their library")
            elif "reveal" in e:
                r = e["reveal"]
                tgt = self.resolve_player(i, r.get("player", "self"))
                if not tgt:
                    continue
                if r.get("zone") == "hand":
                    self.log(f"  ↳ {tgt.name} REVEALS HAND: {', '.join(tgt.hand) or '(empty)'}")
                else:
                    n = int(r.get("n", 1))
                    self.log(f"  ↳ {tgt.name} reveals top {n}: {', '.join(tgt.library[:n]) or '(library empty)'}")
            elif "random" in e:
                r = e["random"]
                if r.get("coin"):
                    self.log(f"  ↳ coin flip: {self.rng.choice(['HEADS', 'TAILS'])}")
                else:
                    sides = int(r.get("die", 6))
                    self.log(f"  ↳ d{sides} roll: {self.rng.randint(1, sides)}")
            elif "eliminate" in e:
                el = e["eliminate"]
                tgt = self.resolve_player(i, el.get("player", "self"))
                reason = el.get("reason", "unspecified")
                if not tgt or not tgt.alive:
                    continue
                if tgt is me:
                    self.eliminate(tgt, f"loses the game: {reason}")
                else:
                    self.log(f"**{me.name} declares {tgt.name} LOSES THE GAME: {reason}**")
                    v = self.ask(self.p.index(tgt),
                        f"{me.name} declares you lose the game: {reason}. Per the actual rules, is this "
                        f"correct? Reply {{\"accept\": true/false, \"reason\": \"...\"}}. Accept only if "
                        f"it is genuinely rules-correct.",
                        schema_hint='{"accept": bool, "reason": str}')
                    if v.get("accept"):
                        self.eliminate(tgt, f"accepted: {reason}")
                    else:
                        self.log(f"  ↳ {tgt.name} DISPUTES ({v.get('reason','')}) — play continues, table judges")
            elif "note" in e:
                self.log(f"  ↳ note ({me.handle}): {e['note']}")
            else:
                self.log(f"  !! unknown effect atom {list(e.keys())}; skipped")

    # ---------------- actions ----------------

    # ---------------- the stack ----------------
    def _stack_line(self):
        return " -> ".join(f"{o['id']} {o['name']} ({self.p[o['caster']].handle})"
                           for o in reversed(self.stack)) or "(empty)"

    def _pay_spell(self, i, a):
        """Costs are paid at announcement. Returns from_cz, or None if illegal."""
        me = self.p[i]
        c = a.get("card")
        from_cz = (c == me.commander and me.command_zone and c not in me.hand)
        if not (c in me.hand or from_cz):
            return None
        for ident in a.get("tap", []):
            _, perm = self.find(ident, prefer=i)
            if perm:
                if perm["tapped"]:
                    self.log(f"  !! {ident} already tapped (payment dubious; logged)")
                perm["tapped"] = True
            else:
                self.log(f"  !! tap: no permanent {ident!r}; payment not recorded")
        if from_cz:
            me.command_zone = False
            me.commander_tax += 2   # next cast from CZ costs 2 more
        else:
            me.hand.remove(c)
        return from_cz

    def _resolve_spell(self, i, a, from_cz):
        """Resolution: fizzle check on id-targets, then placement + effects."""
        me = self.p[i]
        c = a.get("card")
        targets = [str(t) for t in (a.get("targets") or [])]
        if targets:
            gone = [t for t in targets if "#" in t and not self.find(t, prefer=i)[1]]
            if gone and len(gone) == len(targets):
                if from_cz:
                    me.command_zone = True
                    self.log(f"{me.name} casts {c} — FIZZLES on resolution: no remaining "
                             f"legal targets ({', '.join(gone)}). Commander returns to the "
                             f"command zone; mana stays spent.")
                else:
                    me.graveyard.append(c)
                    self.log(f"{me.name} casts {c} — FIZZLES on resolution: no remaining "
                             f"legal targets ({', '.join(gone)}); to graveyard, mana stays spent.")
                return None
            if gone:
                self.log(f"  !! {len(gone)} of {c}'s targets gone at resolution "
                         f"({', '.join(gone)}) — caster resolves honestly against what remains")
        typ = self.db.get(c, {}).get("type", "")
        if any(k in typ for k in ("Creature", "Artifact", "Enchantment", "Land")) \
                and "Sorcery" not in typ and "Instant" not in typ:
            self.perm(me, c)
        else:
            me.graveyard.append(c)
        self.log(f"{me.name} casts {c}" + (" (from command zone)" if from_cz else "") +
                 (f", tapping {a.get('tap')}" if a.get("tap") else "") +
                 (f" — {a.get('narration','')}" if a.get("narration") else ""))
        d_ = self.db.get(c)
        if d_:                        # spectator card-text caption
            pt_ = f" {d_['pt'][0]}/{d_['pt'][1]}" if d_.get("pt") else ""
            self.log_private(f"  [{c} — {d_['cost'] or 'Land'} — {d_['type']}{pt_} — {d_['text']}]")
        self.apply_effects(i, a.get("effects"))
        return c

    def _pay_ability(self, i, a):
        me = self.p[i]
        src = a.get("source")
        _, perm = self.find(src, prefer=i)
        if perm and a.get("tap_source"):
            if perm["sick"]:
                self.log(f"  !! activating {src} while summoning-sick — agent claims legality "
                         f"({a.get('narration','no justification')}); allowed, logged")
            perm["tapped"] = True
        for ident in a.get("tap", []):
            _, pm = self.find(ident, prefer=i)
            if pm:
                pm["tapped"] = True
            else:
                self.log(f"  !! tap: no permanent {ident!r}; payment not recorded")
        return perm

    def _resolve_ability(self, i, a, perm=None):
        me = self.p[i]
        src = a.get("source")
        self.log(f"{me.name} activates {src}" + (f" — {a.get('narration','')}" if a.get('narration') else ""))
        if perm is None:
            _, perm = self.find(src, prefer=i)
        if perm and (d_ := self.db.get(perm["name"])):
            self.log_private(f"  [{perm['name']}: {d_['text']}]")
        self.apply_effects(i, a.get("effects"))

    def resolve_on_stack(self, i, plan, kind="spell", depth=0):
        """Announce onto the stack, pay costs, run priority (responses recurse
        and resolve first — the call stack IS the stack), then resolve, get
        countered, or fizzle. Returns True if it resolved."""
        me = self.p[i]
        name = plan.get("card") if kind == "spell" else plan.get("source", "?")
        if kind == "spell":
            paid = self._pay_spell(i, plan)
            if paid is None:
                self.log(f"  !! cast of {name} by {me.name} ignored (not in hand/CZ)")
                return False
        else:
            src_perm = self._pay_ability(i, plan)
        self.stack_seq += 1
        obj = {"id": f"stack#{self.stack_seq}", "caster": i, "kind": kind,
               "name": name, "countered": False}
        self.stack.append(obj)
        verb = "" if kind == "spell" else "activation of "
        self.log(f"{me.name} announces {verb}{name} ({obj['id']})...")
        self._flush_talk()
        responded = False
        try:
            if plan.get("split_second"):
                self.log(f"  ↳ {name} has split second (agent-declared; the table will check): "
                         f"no responses possible.")
            elif depth >= 5:
                self.log(f"  !! stack depth cap reached — {obj['id']} gets no response windows")
            else:
                responded = self._priority_rounds(obj, depth)
            if obj["countered"]:
                if kind == "spell":
                    if paid:
                        me.command_zone = True
                        self.log(f"  ↳ {name} is COUNTERED — commander returns to the command "
                                 f"zone (tax now +{me.commander_tax}); mana stays spent.")
                    else:
                        me.graveyard.append(name)
                        self.log(f"  ↳ {name} is COUNTERED (mana stays spent).")
                else:
                    self.log(f"  ↳ activation of {name} is COUNTERED (costs still paid).")
                return False
            if responded:
                confirm = self.ask(i,
                    f"Responses resolved while {name} ({obj['id']}) was on the stack — the board "
                    f"may have changed (see log). Give the final resolution: the same action with "
                    f"targets/effects adjusted to the board as it is *now*"
                    + (" (if the source left the battlefield, the ability still resolves)" if kind == "ability" else "")
                    + f", or {{\"action\":\"fizzle\"}} if it no longer has a legal target "
                    f"(costs stay paid).",
                    schema_hint='{"action":"cast"|"activate"|"fizzle", "targets":[ids], "effects":[...], "narration":str}')
                if confirm.get("action") == "fizzle":
                    if kind == "spell":
                        if paid:
                            me.command_zone = True
                            self.log(f"  ↳ commander stays in command zone (tax now +{me.commander_tax})")
                        else:
                            me.graveyard.append(name)
                    self.log(f"  ↳ {name} FIZZLES on resolution (caster's call); costs paid.")
                    return False
                if confirm.get("action") in ("cast", "activate"):
                    keep = {"targets", "effects", "narration"}
                    plan = {**plan, **{k: v for k, v in confirm.items() if k in keep}}
            if kind == "spell":
                return self._resolve_spell(i, plan, paid) is not None
            self._resolve_ability(i, plan, src_perm)
            return True
        finally:
            self.stack.remove(obj)

    def _priority_rounds(self, obj, depth):
        """Rotate priority until everyone passes on the current stack state.
        Any response recurses; after it fully resolves, the rotation restarts
        (the stack changed, so passes reset). Caster gets the last window in
        each rotation — that is what holding priority means."""
        responded = False
        rounds = 0
        while rounds < 4 and not obj["countered"]:
            rounds += 1
            acted = False
            order = [self.p.index(pl) for pl in self.others(obj["caster"])] + [obj["caster"]]
            for j in order:
                pl = self.p[j]
                if not pl.alive or not self._can_respond(pl):
                    continue
                caster_name = self.p[obj["caster"]].name
                verb = "casting" if obj["kind"] == "spell" else "activating"
                r = self.ask(j,
                    f"RESPONSE WINDOW: {caster_name} is {verb} {obj['name']}. "
                    f"STACK (top resolves first): {self._stack_line()}. "
                    f"You may cast an instant/flash or activate an instant-speed ability in "
                    f"response — it goes on top and resolves first — or pass. To counter "
                    f"something, include {{\"counter\":{{\"target\":\"{obj['id']}\"}}}} "
                    f"(any stack id) in your effects.",
                    schema_hint='{"action":"cast"|"activate"|"pass", "card":str, "source":str, '
                                '"tap":[ids], "targets":[ids], "effects":[...], "narration":str}')
                if r.get("action") not in ("cast", "activate"):
                    continue
                effects = r.get("effects") or []
                counters = [str(e["counter"].get("target")) for e in effects
                            if isinstance(e, dict) and isinstance(e.get("counter"), dict)]
                if any(isinstance(e, dict) and e.get("counter_spell") for e in effects):
                    counters.append(obj["id"])
                sub = {**r, "effects": [e for e in effects if not (isinstance(e, dict)
                       and ("counter" in e or "counter_spell" in e))]}
                ok = self.resolve_on_stack(j, sub,
                                           kind="spell" if r.get("action") == "cast" else "ability",
                                           depth=depth + 1)
                if ok:
                    for tid in counters:
                        tgt = next((o for o in self.stack if o["id"] == tid), None)
                        if tgt:
                            tgt["countered"] = True
                            self.log(f"  ↳ {tgt['id']} {tgt['name']} is countered by "
                                     f"{sub.get('card') or sub.get('source', '?')}.")
                        else:
                            self.log(f"  !! counter target {tid!r} is not on the stack; no effect")
                responded = True
                acted = True
                break                       # stack changed — restart the rotation
            if not acted:
                break
        if rounds >= 4:
            self.log(f"  !! priority rounds cap on {obj['id']} — resolving")
        return responded

    def do_action(self, i, a):
        try:
            return self._do_action(i, a)
        finally:
            self._flush_talk()

    def _do_action(self, i, a):
        me = self.p[i]
        act = a.get("action")
        if act == "play_land":
            c = a.get("card")
            if c in me.hand and me.lands_played < 2:  # Rites/etc: agent responsible; hard cap 2
                me.hand.remove(c)
                me.lands_played += 1
                self.perm(me, c)
                self.log(f"{me.name} plays land: {c}")
            else:
                self.log(f"  !! illegal/ignored land play by {me.name}: {c}")
        elif act == "cast":
            c = a.get("card")
            from_cz = self._pay_spell(i, a)
            if from_cz is None:
                self.log(f"  !! cast of {c} by {me.name} ignored (not in hand/CZ)")
            else:
                return self._resolve_spell(i, a, from_cz)
        elif act == "activate":
            perm = self._pay_ability(i, a)
            self._resolve_ability(i, a, perm)
        elif act == "pass":
            pass
        elif act == "claim_win":
            self.log(f"**{me.name} claims they WIN THE GAME: {a.get('how')}"
                     + (f" — loop: {a.get('loop')}" if a.get("loop") else "") + "**")
            for pl in list(self.others(i)):
                verdict = self.ask(self.p.index(pl),
                    f"{me.name} claims a rules-based win (see table log). "
                    "Reply JSON {\"concede\": true/false, \"reason\": \"...\"}. Concede only if the claim is "
                    "genuinely sound and you have no answer available.",
                    schema_hint='{"concede": bool, "reason": str}')
                if verdict.get("concede"):
                    self.log(f"  ↳ {pl.name} CONCEDES: {verdict.get('reason','')}")
                    self.eliminate(pl, "conceded to claimed win")
                else:
                    self.log(f"  ↳ {pl.name} DISPUTES: {verdict.get('reason')} — play continues")
        return None

    def digest(self, i, full_board=False):
        """Compact authoritative state: numbers and ids, no prose. Oracle text
        only for names this seat hasn't been shown yet this session. full_board
        adds graveyard contents, commander status and draw counts — the
        start-of-turn planning view."""
        me = self.p[i]
        seats = "; ".join(
            f"{pl.handle} life {pl.life}, hand {len(pl.hand)}, lib {len(pl.library)}, gy {len(pl.graveyard)}"
            + (", cmdr in CZ" if pl.command_zone else "")
            if pl.alive else f"{pl.handle} eliminated"
            for pl in self.p)
        def bf(pl):
            out = []
            for x in pl.battlefield:
                flags = "".join(("T" if x["tapped"] else "", "S" if x["sick"] else ""))
                pt = f" {x['pt'][0]+x['counters']}/{x['pt'][1]+x['counters']}" if x["pt"] else ""
                out.append(x["id"] + pt + (f"[{flags}]" if flags else "")
                           + (f"[+{x['counters']}]" if x["counters"] else ""))
            return ", ".join(out) or "(empty)"
        boards = "\n".join(f"{pl.handle}: {bf(pl)}" for pl in self.p if pl.alive)
        new_names = [n for n in
                     me.hand + [x["name"] for pl in self.p for x in pl.battlefield]
                     if n in self.db and n not in self.oracle_shown[i]]
        texts = ""
        if new_names:
            seen = set()
            lines = []
            for n in new_names:
                if n in seen:
                    continue
                seen.add(n)
                d = self.db[n]
                pt = f" {d['pt'][0]}/{d['pt'][1]}" if d["pt"] else ""
                lines.append(f"  * {n} — {d['cost'] or 'Land'} — {d['type']}{pt} — {d['text']}")
                self.oracle_shown[i].add(n)
            texts = "\nORACLE TEXT (cards new to you since last time):\n" + "\n".join(lines)
        extra = ""
        if full_board:
            cz = "\n".join(
                f"{pl.handle}: commander {pl.commander} "
                + (f"in CZ (tax +{pl.commander_tax})" if pl.command_zone else "on the move")
                + f", drew {pl.drew_this_turn} this turn"
                for pl in self.p if pl.alive)
            gys = "\n".join(f"{pl.handle}: {', '.join(pl.graveyard) or '(empty)'}"
                             for pl in self.p if pl.alive)
            extra = f"\nCOMMANDERS:\n{cz}\nGRAVEYARDS:\n{gys}"
        stackline = f"\nSTACK (top first): {self._stack_line()}" if self.stack else ""
        return (f"TURN {self.turn}. Seats: {seats}\n"
                f"YOUR HAND ({len(me.hand)}): {', '.join(me.hand)}\n"
                f"BATTLEFIELDS:\n{boards}\n"
                f"Lands you've played this turn: {me.lands_played}.{stackline}{extra}{texts}")

    # -------- agent plumbing --------
    def ask(self, i, instruction, schema_hint=""):
        self._flush_talk()
        self.check_judge()
        me = self.p[i]
        n_alive = sum(1 for p in self.p if p.alive)
        agent = self.agents[i]
        stateful = bool(getattr(agent, "resume", False)) and getattr(agent, "session_id", None)
        if stateful and not self.force_full[i]:
            new_lines = "\n".join(self.table[self.log_sent[i]:]) or "(nothing since your last reply)"
            fb = self.board_full[i]
            self.board_full[i] = False
            hdr = "FULL STATE (start of your turn)" if fb else "STATE DIGEST"
            prompt = (f"=== TABLE LOG since your last decision ===\n{new_lines}\n\n"
                      f"=== {hdr} (authoritative) ===\n{self.digest(i, full_board=fb)}\n\n"
                      f"=== INSTRUCTION ===\n{instruction}\n"
                      + (f"Schema: {schema_hint}\n" if schema_hint else "")
                      + "Reply per the established protocol: exactly one JSON object.\n")
            self.log_sent[i] = len(self.table)
            raw = agent.ask(prompt)
            return self._parse_reply(i, raw)
        prompt = (f"You are playing Magic: The Gathering (Commander pod, {n_alive} players alive, 40 life "
                  f"start, free-for-all, last seat standing wins). You are an expert player and a table "
                  f"politician: threat-assess privately, make deals, needle people — but talk like a player "
                  f"at a kitchen table, not a commentator narrating the game. Follow the comprehensive rules "
                  f"yourself — the engine only does bookkeeping and trusts your legality, because it has no "
                  f"rules knowledge of its own. It logs doubts publicly and illegal plays get argued at the "
                  f"table, so rules-precision (summoning sickness, mana payment, timing) is what keeps your "
                  f"plays standing. A human judge watches the game: lines marked ⚖ in the table log are "
                  f"authoritative — when one flags an error or issues a ruling, address it in your next "
                  f"reply (correct state via effect atoms) before advancing your own plans.\n"
                  + (f"\nYOUR DECK'S GAMEPLAN: {me.strategy}\n" if me.strategy else "")
                  + f"\n{PROTOCOL}\n\n"
                  f"=== GAME STATE ===\n{self.view(i)}\n\n=== INSTRUCTION ===\n{instruction}\n"
                  + (f"Schema: {schema_hint}\n" if schema_hint else ""))
        self.force_full[i] = False
        self.log_sent[i] = len(self.table)
        self.oracle_shown[i].update(
            n for n in self.p[i].hand + [x["name"] for pl in self.p for x in pl.battlefield]
            if n in self.db)
        raw = self.agents[i].ask(prompt)
        return self._parse_reply(i, raw)

    def _parse_reply(self, i, raw):
        me = self.p[i]
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            obj = json.loads(m.group(0)) if m else {"action": "pass"}
        except Exception:
            self.log(f"  !! unparseable agent reply from {me.name}; treating as pass")
            return {"action": "pass"}
        thinking = obj.pop("thinking", None)
        if thinking:                       # spectator-visible, table-invisible
            self.log_private(f'{me.name} thinks: "{thinking}"')
        talk = obj.pop("table_talk", None)
        if talk:
            # speech lands AFTER the play it accompanies, like a real table
            self.pending_talk.append(f'{me.name} says: "{talk}"')
        if obj.get("action") in (None, "pass"):   # no play follows — speak now
            self._flush_talk()
        return obj

    def _flush_talk(self):
        while self.pending_talk:
            self.log(self.pending_talk.pop(0))

    def view(self, i):
        me = self.p[i]

        def bf(pl):
            out = []
            for x in pl.battlefield:
                pt = f" {x['pt'][0]+x['counters']}/{x['pt'][1]+x['counters']}" if x["pt"] else ""
                flags = []
                if x["tapped"]: flags.append("tapped")
                if x["sick"]: flags.append("summoning-sick")
                if x["counters"]: flags.append(f"+1/+1 x{x['counters']}")
                if x["token"]: flags.append("token")
                out.append(f"  - {x['id']}{pt}" + (f" [{', '.join(flags)}]" if flags else ""))
            return "\n".join(out) or "  (empty)"

        def texts(names):
            seen, out = set(), []
            for n in names:
                if n in seen or n not in self.db:
                    continue
                seen.add(n)
                d = self.db[n]
                pt = f" {d['pt'][0]}/{d['pt'][1]}" if d["pt"] else ""
                out.append(f"  * {n} — {d['cost'] or 'Land'} — {d['type']}{pt} — {d['text']}")
            return "\n".join(out)

        seats = []
        for pl in self.p:
            if not pl.alive:
                seats.append(f"  {pl.handle} {pl.name} — eliminated")
                continue
            cz = f"in command zone (tax +{pl.commander_tax})" if pl.command_zone else "*not* in command zone"
            seats.append(f"  {pl.handle} {pl.name}{' <- YOU' if pl is me else ''} — life {pl.life}, "
                         f"hand {len(pl.hand)}, drew {pl.drew_this_turn} this turn — "
                         f"commander {pl.commander} ({cz})")
        boards = "\n".join(
            f"{pl.handle} {pl.name}{' (YOU)' if pl is me else ''} BATTLEFIELD:\n{bf(pl)}"
            for pl in self.p if pl.alive)
        relevant = me.hand + [x["name"] for pl in self.p for x in pl.battlefield] \
                   + [pl.commander for pl in self.p if pl.alive] + me.graveyard
        graves = "\n".join(f"  {pl.handle}: {', '.join(pl.graveyard) or '(empty)'}"
                           for pl in self.p if pl.alive)
        tail = "\n".join(self.table[-self.log_tail:])
        return f"""TURN {self.turn}. You are {me.name} (seat {me.handle}). SEATS in turn order:
{chr(10).join(seats)}
YOUR HAND ({len(me.hand)}): {', '.join(me.hand)}
{boards}
Your library: {len(me.library)} cards. Graveyards:
{graves}
Lands you've played this turn: {me.lands_played}.
ORACLE TEXT (your hand + graveyard, all battlefields, all commanders):
{texts(relevant)}
=== PUBLIC TABLE LOG (recent) ===
{tail}"""

    def response_windows(self, caster_i, context):
        """One rotation of instant-speed windows (combat and other non-stack
        contexts). Anything cast here goes onto the stack and resolves fully
        — counter wars included — before play continues."""
        for pl in list(self.others(caster_i)):
            j = self.p.index(pl)
            if not self._can_respond(pl):
                continue
            r = self.ask(j,
                f"RESPONSE WINDOW: {context}. You may cast an instant/flash or activate an "
                f"instant-speed ability (it goes on the stack and can be responded to), or pass.",
                schema_hint='{"action":"cast"|"activate"|"pass", "card":str, "source":str, '
                            '"tap":[ids], "targets":[ids], "effects":[...], "narration":str}')
            if r.get("action") in ("cast", "activate"):
                self.resolve_on_stack(j, r, kind="spell" if r.get("action") == "cast" else "ability")

    def _can_respond(self, pl):
        """Could this seat conceivably act at instant speed? Errs open — the
        agent judges payability (alternative costs, phyrexian mana, rituals).
        No untapped-mana requirement: free spells exist. Battlefield counts if
        any permanent has a non-mana activated ability, tapped or not (sac
        outlets don't tap)."""
        for c in pl.hand:
            d = self.db.get(c, {})
            if "Instant" in d.get("type", "") or "Flash" in d.get("text", ""):
                return True
        for x in pl.battlefield:
            d = self.db.get(x["name"], {})
            text = d.get("text", "")
            if "Basic Land" in d.get("type", "") or ":" not in text:
                continue
            # skip permanents whose every ability is a mana ability
            if any(not seg.lstrip().startswith("Add") for seg in text.split(":")[1:]):
                return True
        return False

    def half_turn(self, i):
        me = self.p[i]
        if not me.alive:
            return
        me.lands_played = 0
        self.board_full[i] = True                 # full board read at own turn start
        for pl in self.p:
            pl.drew_this_turn = 0
        for x in me.battlefield:
            x["tapped"] = False
            x["sick"] = False
        self.log(f"\n## Turn {self.turn} — {me.name} — life: " +
                 ", ".join(f"{pl.handle} {pl.life}" for pl in self.p if pl.alive))
        if not (self.turn == 1 and i == 0 and len(self.p) == 2):
            self.draw(me, 1)  # CR 103.8: only 2-player pods skip the first draw
        # main phase: up to N sequential decisions
        for _step in range(24):
            if not me.alive:
                return
            plan = self.ask(i,
                "It is your MAIN PHASE (pre- or post-combat as you prefer; the engine doesn't distinguish — "
                "sequence responsibly). Give one action per protocol: play_land, cast, activate, attack, "
                "claim_win, or pass (pass ends your turn). For cast/activate: name every permanent you tap "
                "for mana in \"tap\" and declare every consequence as effect atoms. Declare upkeep/beginning "
                "phase triggers (Meren, Phyrexian Arena, vanishing...) in your first action's effects, since "
                "the engine won't remember them for you. "
                "For attack you may split attackers among players; only untapped, non-sick (or haste-granted, "
                "justify in narration) creatures; attacking taps them unless vigilance (use set to untap). "
                "One attack step per turn unless an effect grants extra combats (Aurelia, Aggravated "
                "Assault...) — untap your attackers via set atoms and justify in narration.",
                schema_hint='{"action":str, ...per protocol..., "narration":str, "table_talk":str?}')
            act = plan.get("action", "pass")
            if act == "pass":
                break
            if act == "peek":
                n = max(1, min(int(plan.get("n", 1)), len(me.library)))
                top = list(me.library[:n])
                self.log(f"{me.name} looks at the top {n} card{'s' if n != 1 else ''} of their library.")
                r = self.ask(i,
                    f"PRIVATE LOOK — only you see this. Top of your library, in order: "
                    f"{', '.join(top)}. Declare what the authorizing card does with them: reply "
                    f'{{"action":"order","top":[...],"bottom":[...]}} using exactly those names '
                    f"(top list becomes the new top order, bottom goes to the bottom in order), "
                    f"plus any effect atoms the card grants (e.g. move a revealed card to hand). "
                    f"Or pass to leave the library untouched.",
                    schema_hint='{"action":"order"|"pass","top":[names],"bottom":[names],"effects":[...]}')
                if r.get("action") == "order":
                    newtop = list(r.get("top") or [])
                    newbot = list(r.get("bottom") or [])
                    if sorted(newtop + newbot) == sorted(top):
                        del me.library[:n]
                        me.library[:0] = newtop
                        me.library.extend(newbot)
                        if newbot:
                            self.log(f"  ↳ {me.name} puts {len(newbot)} on the bottom.")
                    else:
                        self.log("  !! order reply doesn't match the looked-at cards; library unchanged")
                if r.get("effects"):
                    self.apply_effects(i, r.get("effects"))
                continue
            if act == "attack":
                self.combat(i, plan)
                continue
            if act == "activate":
                fx = plan.get("effects") or []
                substantive = any(k in e for e in fx if isinstance(e, dict)
                                  for k in ("create", "draw", "life", "move", "search",
                                            "mill", "set", "eliminate"))
                if substantive:
                    self.resolve_on_stack(i, plan, kind="ability")
                else:
                    self.do_action(i, plan)     # mana abilities skip the stack
                continue
            if act == "cast":
                self.resolve_on_stack(i, plan, kind="spell")
                continue
            self.do_action(i, plan)
        else:
            self.log(f"  !! {me.name} hit the action cap ({24}/turn) — declare any unresolved "
                     f"triggers at your end step or next window; the table should hold them to it.")
        # end step triggers the agent wants
        if me.alive:
            endstep = self.ask(i, "END STEP: declare any end-of-turn triggers you control (Meren of Clan "
                                  "Nel Toth's return, Throne of the God-Pharaoh, Fevered Visions, The Ten "
                                  "Rings...) as effect atoms, or pass.",
                               schema_hint='{"action":"pass"|"activate","effects":[...],"narration":str}')
            if endstep.get("action") != "pass":
                self.apply_effects(i, endstep.get("effects"))
        if me.alive and len(me.hand) > 7:
            r = self.ask(i,
                f"CLEANUP: you have {len(me.hand)} cards in hand. Maximum hand size is seven "
                f"unless an effect says otherwise (Reliquary Tower, Thought Vessel, The Ten "
                f"Rings...). Discard down to your maximum via move atoms (hand → graveyard), "
                f"or cite the effect that raises it — the table will check.",
                schema_hint='{"action":"cleanup","effects":[{"move":{"player":"self","from":"hand","card":"...","to":"graveyard"}}],"narration":str}')
            if r.get("effects"):
                self.apply_effects(i, r.get("effects"))

    def combat(self, i, plan):
        me = self.p[i]
        attacks = plan.get("attacks")
        if not attacks and plan.get("attackers"):
            others = self.others(i)
            if len(others) == 1:
                attacks = {others[0].handle: plan["attackers"]}
            else:
                self.log(f"  !! {me.name} attacked without naming defenders in a multiplayer pod; ignored")
                return
        if not attacks:
            return
        assault = []  # (defender_player, [perm, ...])
        for ref, ids in attacks.items():
            dfd = self.resolve_player(i, ref)
            if not dfd or dfd is me or not dfd.alive:
                self.log(f"  !! attack on unresolvable/dead seat {ref!r}; ignored")
                continue
            atk = []
            for ident in ids:
                _, perm = self.find(ident, prefer=i)
                if perm and not perm["tapped"]:
                    perm["tapped"] = True  # vigilance: agent untaps via set; keep simple
                    atk.append(perm)
            if atk:
                assault.append((dfd, atk))
                self.log(f"{me.name} attacks {dfd.name} with: {[x['id'] for x in atk]}")
        if not assault:
            return
        # declare-attackers priority: anyone (defender or bystander) may act
        # before blocks — pre-block removal, fogs, political rescues
        self.response_windows(i, f"{me.name} has declared attackers (see above) — "
                                 f"window before blocks are declared")
        if self._can_respond(me):
            r = self.ask(i, "RESPONSE WINDOW: your attackers are declared, responses (if any) have "
                            "resolved, and blocks are about to be chosen. You may cast one "
                            "instant/flash now — pre-block pumps and protection change what dares "
                            "to block — or pass.",
                         schema_hint='{"action":"cast"|"pass", "card":str, "tap":[ids], "targets":[ids], "effects":[...]}')
            if r.get("action") == "cast":
                self.do_action(i, r)
        for dfd, atk in assault:
            j = self.p.index(dfd)
            blocks = self.ask(j,
                f"COMBAT: {me.name} attacks YOU with {[x['id'] for x in atk]}. Declare blocks "
                f"(only your untapped creatures; respect any 'can't block' effects) or none.",
                schema_hint='{"action":"block","blocks":{attacker_id:[blocker_ids]},"narration":str}')
            self.log(f"{dfd.name} blocks: {blocks.get('blocks', {})} — {blocks.get('narration','')}")
        # one trick window each: defenders first (turn order), attacker last
        defenders = [dfd for dfd, _ in assault]
        for pl in self.others(i):
            if pl in defenders and self._can_respond(pl):
                r = self.ask(self.p.index(pl),
                    "RESPONSE WINDOW: blocks are declared; combat tricks/removal window. "
                    "Cast one instant/flash or pass.",
                    schema_hint='{"action":"cast"|"pass", "card":str, "tap":[ids], "effects":[...]}')
                if r.get("action") == "cast":
                    self.do_action(self.p.index(pl), r)
        trick = self.ask(i, "Blocks are declared. You may cast one combat trick/instant now or pass.",
                         schema_hint='{"action":"cast"|"pass", "card":str, "tap":[ids], "effects":[...]}')
        if trick.get("action") == "cast":
            self.do_action(i, trick)
        # attacker adjudicates damage; defenders may dispute in table talk (logged only)
        result = self.ask(i,
            "Blocks and tricks are final (see table log). Compute the combat damage honestly and completely "
            "per the rules — every seat reads the same log, and sloppy math becomes a public dispute that "
            "stalls the game. Unblocked attackers hit their defending player; blocked ones trade with "
            "blockers (trample overflows). Report every consequence as effect atoms: life deltas per "
            "defending player, move-to-graveyard for every creature that dies on any side, and all triggers.",
            schema_hint='{"action":"activate","effects":[...],"narration":str}')
        self.log(f"combat result — {result.get('narration','')}")
        self.apply_effects(i, result.get("effects"))

    def mulligans(self):
        """Opening hands. Commander house rules: first mulligan free, then
        London-style — draw 7, put one extra card on the bottom per mull."""
        for i, pl in enumerate(self.p):
            lands = sum(1 for c in pl.decklist if "Land" in self.db.get(c, {}).get("type", ""))
            producers = sum(1 for c in pl.decklist
                            if "Land" not in self.db.get(c, {}).get("type", "")
                            and "Add {" in self.db.get(c, {}).get("text", ""))
            mulls = 0
            while mulls < 6:
                r = self.ask(i,
                    f"OPENING HAND decision ({mulls} mulligan{'s' if mulls != 1 else ''} taken; the "
                    f"first is free). Keep this hand or mulligan? After the free one, each further "
                    f"mulligan draws 7 and bottoms one more card. Your deck's mana base: {lands} lands "
                    f"and {producers} nonland mana sources (dorks/rocks) in 99 — judge land counts in "
                    f"this hand against that reality, your early plays, and your commander's curve. "
                    f"Reply {{\"action\":\"keep\"}} or {{\"action\":\"mulligan\"}}.",
                    schema_hint='{"action":"keep"|"mulligan"}')
                if r.get("action") != "mulligan":
                    break
                mulls += 1
                pl.library.extend(pl.hand)
                pl.hand.clear()
                self.rng.shuffle(pl.library)
                self.draw(pl, 7)
                self.log(f"{pl.name} mulligans to a new 7" + (" (free)." if mulls == 1 else "."))
                if mulls > 1:
                    n = mulls - 1
                    b = self.ask(i,
                        f"London mulligan: choose {n} card{'s' if n != 1 else ''} from your new hand "
                        f"to put on the bottom of your library. Reply {{\"bottom\": [\"Card Name\", ...]}}.",
                        schema_hint='{"bottom": [str]}')
                    put = 0
                    for c in (b.get("bottom") or []):
                        if put < n and c in pl.hand:
                            pl.hand.remove(c)
                            pl.library.append(c)
                            put += 1
                    while put < n and pl.hand:      # enforce the count if the reply was short
                        pl.library.append(pl.hand.pop())
                        put += 1
                    self.log(f"{pl.name} bottoms {n} — {len(pl.hand)} cards in hand.")
            if mulls == 0:
                self.log(f"{pl.name} keeps their opening seven.")
        for pl in self.p:
            pl.drew_this_turn = 0

    def run(self):
        try:
            self.mulligans()
            for self.turn in range(1, self.max_turns + 1):
                for i in range(len(self.p)):
                    self.half_turn(i)
            standings = sorted((pl for pl in self.p if pl.alive), key=lambda p: -p.life)
            self.log(f"\n**Turn cap {self.max_turns} reached. Standings: " +
                     ", ".join(f"{pl.name} {pl.life}" for pl in standings) +
                     ". Highest life is the moral victor.**")
        except GameOver as g:
            self.log(f"\n**GAME OVER: {self.p[g.winner].name} WINS ({g.how}) on turn {self.turn}.**")
        finally:
            self.logf.close()
            self.eventsf.close()
