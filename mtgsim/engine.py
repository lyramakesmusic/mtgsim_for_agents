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
 {"move":{"player":"P2","from":"hand","n":1,"to":"graveyard","chooser":...}}  # leave "card" out
   when the zone is hidden from you. chooser "owner" (default) is "discards a card",
   "self" is Duress and Thoughtseize (you look and pick), "random" is "at random"
   and the engine rolls it so neither of you chooses. Add "types" or "not_types"
   for a restricted pick — Duress is not_types:["Creature","Land"].
  {"move":{"player":"self","from":"graveyard","card":"X","to":"battlefield","tapped":false}}
  {"move":{"from":"stack","card":"Swan Song","to":"hand"}}   # back up a cast that wasn't legal
 {"move":{"card":"Approach of the Second Sun","to":"library_depth","depth":7}}
   — a card put into the library a set distance down, not just top or bottom
 {"move":{"from":"library_bottom","n":1,"to":"hand"}} or {"from":"library","card":"X","to":"hand"}
   — anything you can move a card TO you can move one FROM, the bottom included,
   and naming a card in the library moves it without the shuffle a search forces
     zones: hand, battlefield, graveyard, exile, library_top, library_bottom, command
     tokens moved off the battlefield cease to exist. Moves are verified: the
     named card must actually be in the source zone.
  {"life":{"player":"P3","delta":-6}}          # damage and lifegain alike
  {"create":{"player":"self","name":"Drake","n":2,"pt":[2,2],"tapped":false,"counters":{"+1/+1":1}}}
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
  {"damage":{"id":"Bear#3","n":4,"from":"Bolt"}}  # marked damage; it wears off at end of turn
 {"fight":{"a":"Bear#3","b":"Wolf#9"}}           # each deals its power to the other
   — Prey Upon, Savage Stomp, Bushwhack, Ram Through, an Apex Altisaur trigger. Damage
   at or above toughness is pointed out, never acted on: whether it dies is yours.
 {"random":{"coin":true}} or {"random":{"die":6}}   # engine-owned, logged
 {"dig":{"player":P,"until":{"max_mv":0,"not_types":["Land"]},"found":"exile",
         "rest":"library_bottom"}}
   walk a library from the top until a card matches, in one atom instead of a
   chain of peeks: cascade (max_mv one less than the spell, not_types Land),
   Umbris and Etali (types ["Land"], rest "exile"), discover. until takes
   names/types/not_types/max_mv/min_mv; rest defaults to the library bottom in
   random order, "shuffle_rest":false to keep them in order.

Wins the engine can't see (Thassa's Oracle, Approach, demonstrated loops):
  {"action":"claim_win","how":"..."} → every other seat votes concede/dispute;
  unanimous concession ends the game. Or eliminate the table seat by seat.
  {"action":"claim_draw","how":"...","loop":"..."} → for a compulsory loop nobody
  can break: every other seat votes agree/dispute, unanimous agreement draws.

Actions: play_land, cast, activate, attack, respond, block, claim_win, claim_draw, pass.
  cast/activate carry "tap":[permanent ids] (mana payment; engine taps them)
  and "effects":[atoms] (all consequences, agent-declared).
  attack: {"attacks":{"P2":[attacker ids],"P4":[...]}, "vigilance":[ids] or true}
     attackers tap unless you list them under "vigilance" (true covers them all)
Player references: seat handle ("P3"); "self" always works; "opponent" only
when exactly one other seat is alive.

Knows nothing about how agents are implemented: it is handed objects with a
single method  ask(prompt: str) -> str  (raw model text; engine parses JSON).
"""
import collections
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from mtgsim.cards import deck_tags, mana_value

# the only tokens the if_yes/if_no shortcut matches; every other answer is
# relayed verbatim for the asking seat to resolve
AFFIRM = {"yes", "y", "pay"}
DENY = {"no", "n", "decline"}
ZONES = ("hand", "battlefield", "graveyard", "exile", "library_top", "library_bottom",
         "library_depth", "command")

# ---- terminal color (tty only; files/prompts always plain) ----
# seat identity: P1 cyan, P2 yellow, P3 magenta, P4 green
_SEAT_COLORS = {"P1": "\033[96m", "P2": "\033[93m", "P3": "\033[95m", "P4": "\033[92m"}
_DIM, _RED, _BOLD, _ITAL, _GREY, _RESET = "\033[2m", "\033[91m", "\033[1m", "\033[3m", "\033[90m", "\033[0m"


_SEAT_RE = re.compile(r"\bP[1-4]\b")

_REMINDER = re.compile(r"\([^)]*\)")
_ADDS = re.compile(r"\bAdd (\{|one|two|three|four|five|six|seven|X|that much)")
_TREASURE = re.compile(r"\bcreates? [^.]*\b(Treasure|Gold)\b", re.I)
_THEIRS = re.compile(r"(its|that player|each opponent|controller) (may )?creates?", re.I)


def makes_mana(text):
    """Is this card a mana source for its controller? Reminder text is stripped
    first, so a Treasure token's '{T}, Sacrifice: Add one mana' parenthetical
    doesn't make every card that mentions Treasures look like a rock. Treasure
    and Gold makers count; ones that hand the tokens to an opponent don't."""
    t = _REMINDER.sub("", text)
    return bool(_ADDS.search(t) or (_TREASURE.search(t) and not _THEIRS.search(t)))


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
ACTIONS: {"action":"play_land","card":...,"tapped":bool} | {"action":"cast","card":...,"tap":[ids],
  "from":"exile"|"graveyard"|"library",   # cascade, discover, foretell, flashback, escape,
                                          # impulse draws — omit it and hand/command zone is assumed
  "tapped":bool,"counters":{"+1/+1":N},   # how it ARRIVES: entering tapped, entering with counters
"targets":[permanent ids or seat handles],"effects":[...]} |
{"action":"activate","source":id,"tap_source":bool,"tap":[ids],"effects":[...]} |
{"action":"attack","attacks":{"P2":[attacker ids],...},"vigilance":[ids]} | {"action":"claim_win","how":"..."} |
{"action":"peek","n":N} → then {"action":"order","top":[],"bottom":[],"take":[],"to":"hand"}
  places what you looked at: top and bottom reorder the library, take pulls cards out of
  it into another zone (Lead the Stampede, hideaway, "look at N, put some in hand").
{"action":"correct","effects":[...],"narration":"what was wrong"} |
claim_win takes an optional "player": name another seat to say THEY have already won —
  the table votes on it the same way, and you can do this on any turn, including one
  where the win happened and nobody declared it.
{"action":"pass"}
correct = bookkeeping repair, not a game action: fixing your own earlier error, applying a
judge ruling, honoring a correction the table agreed on. It applies its atoms directly — no
stack, no announcement, no response windows — and logs as a correction so nobody reads it as
a play. Use it instead of dressing a fix up as a cast or activation, which reads as a new
(often illegal-looking) play and confuses the table. Available in any window, costs nothing.
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
(tokens, triggers, targets, costs like sacrifices). Triggers are never automatic: ETBs,
attack triggers, upkeep/end-step triggers and death triggers happen only when you declare
them as atoms in the relevant action — the engine drops nothing you declare and creates
nothing you don't:
 {"move":{"id":perm_id,"to":zone}}  zones: hand|battlefield|graveyard|exile|library_top|library_bottom|command
 {"move":{"id":perm_id,"to":"battlefield","control":"P2"}}  — control change (Mind Control,
   theft, donation). Dead/bounced permanents always route to their owner's zones.
 {"move":{"player":P,"from":zone,"card":name|"n":N|"all":true,"to":zone,"tapped":bool}}
   (verified against real zone contents; "all" empties the zone — wheels, mass discard;
   mill is from "library_top" with "n": {"move":{"player":"P2","from":"library_top","n":2,"to":"graveyard"}})
   A move from library_top or library_bottom is POSITIONAL: it takes that many cards off
   that end, and a "card" name there is refused rather than guessed at. After a look at
   your top few, take a specific one by first moving the ones above it to library_bottom,
   then taking the top card — that is Brainstorm, Ponder, Impulse and Sleight of Hand.
 {"life":{"player":P,"delta":±N}}
 {"create":{"player":P,"name":...,"n":N,"pt":[p,t],"tapped":bool}}
 {"set":{"id":perm_id,"tapped":bool,"sick":bool,"counters":±delta,"pt":[p,t],"skip_untaps":N}}
 {"set":{"player":"self","counters":{"experience":1}}}   # counters the PLAYER has, not a
   permanent: experience, poison, energy. They stay when the permanent that gave
   them leaves — that is what experience counters are for.
   skip_untaps: how many of your untap steps this permanent sits out — exerting it,
   freezing it, any "doesn't untap during your next untap step". It counts itself
   down. A stun counter does the same thing and shows on the board as a counter.
   counters is a map of named kinds — {"counters":{"ingenuity":1}}, {"experience":1},
   {"growth":2}, {"loyalty":-3}. A bare number is shorthand for {"+1/+1": n}. The engine
   tracks whatever you name and shows it on the board; only "+1/+1" is added to printed
   power, and only because you called it that
 {"draw":{"player":P,"n":N,"from":"top"|"bottom"}}   {"shuffle":{"player":P}}
 {"search":{"player":P,"card":name,"to":zone,"tapped":bool,"shuffle":bool}}  (engine verifies)
   The shuffle happens after the card is placed, because that is the order you asked for.
   A tutor whose card reads "reveal it, then shuffle and put that card on top" is three
   atoms in that order: search it to hand with "shuffle":false, then {"shuffle":{...}},
   then move it from hand to library_top. Sequence the atoms the way the card reads.
 {"look":{"player":P,"zone":"hand"|"library_top","n":N}} — a PRIVATE look, for Gitaxian Probe,
   Peek, Duress and anything worded "look at". The table is told only that you looked; the contents
   come back to you alone.
 {"reveal":{"player":P,"zone":"hand"|"library_top","n":N}} — a PUBLIC reveal, for cards that actually
   say "reveals". Everyone sees the names.
 {"random":{"coin":true}|{"die":N}}   (engine rolls — never claim your own randomness)
 {"copy":{"target":"stack#N","n":K,"targets":[...]}} — copy a spell that is still on the
   stack, K times, optionally choosing new targets. The engine checks it is really there and
   records the copies; you then declare what they do (the untap, the damage, the draws) with
   ordinary atoms, since the copies resolve before the original and the original stays on the
   stack.
 {"copy":{"target":perm_id,"n":K,"player":P,"tapped":bool}} — copy a permanent instead, which
   makes K token copies of it under P (yourself by default). A copy has the printed card's
   characteristics, so counters, auras and equipment on the original do not come along; if the
   card making the copy changes something ("except it's not legendary", "except it's a 4/4"),
   say so in narration and set it with a set atom. The legend rule is yours to apply.
   Cast-triggers do not fire for copies — a copy is not cast — but magecraft, worded
   "cast or copy", does, and so does anything else that says copy.
 {"ask":{"player":P,"question":"...","if_yes":[atoms],"if_no":[atoms]}} — a one-off decision
   that belongs to another seat, made at resolution time: punisher modes, "may" abilities,
   votes, splitting piles, naming a card or color. The question is yours to frame and the
   answer comes back in whatever form it asks for; the engine logs it publicly. Supply
   if_yes/if_no when the question is a yes/no and you want the engine to apply the branch
   for you — it matches the literal answers yes/y/pay and no/n/decline, and relays anything
   else verbatim for you to resolve from the log. Use this
   instead of assuming what an opponent would choose — assumed answers get disputed; asked
   answers are binding.
 {"standing":{"source":perm_id,"on":condition,"question":"...","if_yes":[...],"if_no":[...]}}
   — registers a *standing* tax/query for a repeating trigger (Smothering Tithe, Rhystic
   Study, Mystic Remora, or anything with the same shape). Declare it once when the source
   resolves. The engine knows nothing about the card — the declaration IS the behavior.
   If "on" is "draw" or "cast" (events the engine itself bookkeeps), it runs the question
   automatically against every other seat's draws/casts, multi-draws asked once with a
   count. Any other condition ("sacrifices a creature", "second spell each turn"...) can't
   be seen by the engine: it's kept in every seat's state digest as a standing reminder,
   and whoever's action meets the condition triggers it with an ask atom — with the whole
   table watching for misses. Entries expire by themselves when the source permanent
   leaves the battlefield.
 {"eliminate":{"player":P,"reason":...}}   {"note":"ongoing constraints — the log is the table's memory"}
Player refs: seat handles ("P1".."P4") or "self". Declare upkeep/beginning-of-turn triggers
in your first main-phase action's effects. States the engine can't hold (emblems, roles,
"can't" effects, delayed triggers) go in notes — and get honored by everyone."""


class GameOver(Exception):
    """winner is a seat index, or None when the table agreed on a draw."""

    def __init__(self, winner, how):
        self.winner, self.how = winner, how


def _norm_narration(t):
    """Comparison form for a narration: case and spacing don't distinguish two tellings."""
    return " ".join((t or "").lower().split())


def _counters(perm):
    """Counters as {kind: n}. A bare number from an older snapshot means +1/+1."""
    c = perm.get("counters") or {}
    return dict(c) if isinstance(c, dict) else {"+1/+1": int(c)}


def plus_counters(perm):
    """What the counters named "+1/+1" and "-1/-1" do to printed power. With
    "stun", which spends itself at the untap step, those are the only counter
    names the engine reads, and only because the agent chose them; every other
    kind is tracked and shown, and what it does is the agents' business."""
    c = _counters(perm)
    return c.get("+1/+1", 0) - c.get("-1/-1", 0)


class Player:
    def __init__(self, handle, deckname, decklist, commanders, rng, strategy="", scouting="",
                 personality=""):
        self.handle = handle                  # "P1"
        self.strategy = strategy              # gameplan blurb, private to this seat
        self.scouting = scouting              # public one-liner, shown to everyone
        self.personality = personality        # private voice direction for this seat
        self.deckname = deckname
        self.name = f"{handle}({deckname})"   # "P1(snakes)"
        self.decklist = decklist[:]           # full 99, for deck-knowledge prompts
        self.library = decklist[:]
        rng.shuffle(self.library)
        self.hand = [self.library.pop(0) for _ in range(7)]
        self.commanders = list(commanders)            # one, or two with partner
        self.command_zone = {c: True for c in self.commanders}
        self.commander_tax = {c: 0 for c in self.commanders}
        self.battlefield = []   # dicts: id,name,tapped,sick,counters,token,pt
        self.graveyard = []
        self.exile = []
        self.life = 40
        self.counters = {}          # experience, poison, energy: the player's, not a permanent's
        self.alive = True
        self.lands_played = 0
        self.drew_this_turn = 0
        self.spells_this_turn = 0
        self.copies_this_turn = 0


class Game:
    def __init__(self, db, decks, agents, seed, log_path, max_turns, rng, log_tail=60,
                 judge_factory=None, console_private="all", max_actions=150):
        """db: card-name -> {cost,type,text,pt}.
        decks: [(deckname, decklist, commanders)] for 2..4 seats.
        agents: objects with .ask(prompt)->str, index-aligned with decks."""
        assert 2 <= len(decks) <= 4, "pod size 2-4"
        self.max_actions = max_actions          # sequential main-phase actions per turn
        self.dead_calls = [0] * len(decks)      # consecutive calls a seat's brain gave up on
        self.idle_corrections = [0] * len(decks)  # corrections in a row that changed nothing
        self.db = db
        self.rng = rng
        self.p = [Player(f"P{n+1}", spec[0], spec[1], spec[2], rng,
                         strategy=spec[3] if len(spec) > 3 else "",
                         scouting=spec[4] if len(spec) > 4 else "",
                         personality=spec[5] if len(spec) > 5 else "")
                  for n, spec in enumerate(decks)]
        self.agents = agents
        self.turn = 0
        self.next_id = 1
        self.max_turns = max_turns
        self.log_tail = log_tail
        self.table = []           # public table log (every line ever logged)
        self.log_sent = [0] * len(decks)          # table index each seat has seen
        self.oracle_shown = [set(d[1]) | set(d[2]) for d in decks]   # own deck's text
        # rides the opening brief; the digest adds only cards a seat meets later
        self.force_full = [True] * len(decks)     # full re-sync pending per seat
        self.board_full = [False] * len(decks)    # full board state due (own turn start)
        self.pending_talk = []                    # table_talk queued to land AFTER the play
        self.narrated = set()                     # narrations already said at announce time
        self.stack = []                           # live stack objects (announce -> resolve)
        self.stack_seq = 0
        self.standing = []                        # standing taxes/queries (Tithe, Rhystic):
        self._standing_busy = False               # source-permanent-bound, swept per event
        self.judge_inbox = Path(f"{log_path}.judge")
        self.judge_factory = judge_factory
        # console privacy: "all" = spectator mode, everything prints. A set of
        # handles = a human is at this terminal; seat-owned private lines print
        # only for those seats (their own draws), everyone else's stay hidden.
        self.console_private = console_private
        self.judge_agent = None
        self.logf = open(log_path, "w")
        self.eventsf = open(f"{log_path}.events.jsonl", "w")
        self.log(f"# Pod: {', '.join(pl.name for pl in self.p)} — seed {seed} — {datetime.now()}\n")

    # ---------------- public table log ----------------
    def snapshot(self):
        """Full authoritative state as plain data. The renderer diffs
        consecutive snapshots for animations; restore_game() rebuilds a live
        Game from any one of them — every event is a save point."""
        return {
            "turn": self.turn,
            "next_id": self.next_id, "stack_seq": self.stack_seq,
            "stack_empty": not self.stack,
            "standing": [dict(st) for st in self.standing],
            # which minds played this moment — branching clones + truncates these
            "sessions": [{"kind": type(a).__name__,
                          "id": getattr(getattr(a, "scribe", a), "session_id", None),
                          "calls": getattr(getattr(a, "scribe", a), "calls", 0)}
                         for a in self.agents],
            "players": [{
                "handle": pl.handle, "name": pl.name, "life": pl.life, "alive": pl.alive,
                "hand": list(pl.hand), "graveyard": list(pl.graveyard), "exile": list(pl.exile),
                "library": len(pl.library), "library_cards": list(pl.library),
                "lands_played": pl.lands_played, "drew_this_turn": pl.drew_this_turn,
                "spells_this_turn": pl.spells_this_turn,
                "copies_this_turn": pl.copies_this_turn,
                "command_zone": dict(pl.command_zone),
                "commanders": list(pl.commanders), "commander_tax": dict(pl.commander_tax),
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

    def log_private(self, s, seat=None):
        """Spectator + file only. NEVER appended to self.table, so no agent
        prompt ever contains it — other seats cannot see it. seat marks whose
        private line this is: with a human at the console (console_private is
        a set of handles), only that human's own lines print; seat=None means
        public-knowledge convenience (card text) and always prints."""
        if self.console_private == "all" or seat is None or seat in self.console_private:
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
                f"{x['id']}{'(' + str(x['pt'][0]+plus_counters(x)) + '/' + str(x['pt'][1]+plus_counters(x)) + ')' if x['pt'] else ''}"
                f"{'[T]' if x['tapped'] else ''}{'[sick]' if x['sick'] else ''}"
                for x in pl.battlefield) or "(empty)"
            cz = "; ".join(f"{c} " + (f"in CZ (tax +{pl.commander_tax[c]})"
                                      if pl.command_zone[c] else "not in CZ")
                           for c in pl.commanders)
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
    def perm(self, pl, name, token=False, tapped=False, pt=None, counters=None):
        d = self.db.get(name, {})
        p = {"id": f"{name}#{self.next_id}", "name": name, "tapped": tapped,
             "sick": True, "counters": dict(counters or {}), "token": token,
             "owner": pl.handle, "pt": pt or d.get("pt"), "damage": 0}
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
    def _zone_put(self, pl, name, to, tapped=False, depth=0):
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
        elif to == "library_depth":
            pl.library.insert(min(int(depth or 0), len(pl.library)), name)
        elif to == "library_bottom":
            pl.library.append(name)
        elif to == "command":
            if name in pl.command_zone:
                pl.command_zone[name] = True

    def _atom_move(self, i, mv):
        me = self.p[i]
        to = mv.get("to")
        if to not in ZONES:
            self.log(f"  !! move: {to!r} is not a zone — nothing moved; {me.name} asked "
                     f"to redeclare it")
            if getattr(self, "_depth", 0) < 1:
                r = self.ask(i,
                    f"PROTOCOL ERROR — you moved something to {to!r}, which is not a zone, so "
                    f"nothing moved. The zones are: {', '.join(ZONES)}. Note that a library has "
                    f"two ends: library_top and library_bottom. Redeclare the move, or reply "
                    f"with an empty effects list if it should not happen.",
                    schema_hint='{"effects":[...]}')
                self.apply_effects(i, r.get("effects"), depth=1)
            return
        # battlefield permanent by id
        if "id" in mv:
            pl, perm = self.find(mv["id"], prefer=i)
            if not perm and str(mv["id"]).startswith("stack#"):
                # rewinding a spell that shouldn't have been cast, or bouncing one
                obj = next((o for o in self.stack if o["id"] == mv["id"]), None)
                if obj:
                    self.stack.remove(obj)
                    owner = self.p[obj["caster"]]
                    self._zone_put(owner, obj["name"], to)
                    self.log(f"  ↳ {obj['name']} ({mv['id']}) leaves the stack → {owner.name}'s {to}")
                else:
                    self.log(f"  !! move: {mv['id']} is not on the stack; skipped")
                return
            if not perm:
                # agents refer to cards by id from wherever they last saw them —
                # if the name sits in exactly one other zone, honor the intent
                name = str(mv["id"]).rsplit("#", 1)[0]
                hits = [(q, z) for q in self.p if q.alive
                        for z in ("graveyard", "exile", "hand")
                        if name in getattr(q, z)]
                hits += [(q, "command") for q in self.p if q.alive
                         and q.command_zone.get(name)]
                if len(hits) == 1:
                    q, z = hits[0]
                    self.log(f"  (move: {mv['id']} isn't on the battlefield — "
                             f"using {name} from {q.name}'s {z})")
                    mv = {k: v for k, v in mv.items() if k != "id"}
                    mv.update(player=q.handle, **{"from": z}, card=name)
                    return self._atom_move(i, mv)
                self.log(f"  !! move: no permanent {mv['id']!r}"
                         + (f" and {name!r} is in {len(hits)} public zones — ambiguous"
                            if hits else "") + "; skipped")
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
        if frm in ("library", "deck", "top") and not mv.get("card"):
            frm = "library_top"                  # what everyone means by "mill"
        if frm in ("library_bottom", "bottom"):
            n = max(1, int(mv.get("n", 1)))
            want = mv.get("card")
            if want:
                if want not in pl.library:
                    self.log(f"  !! move: {want!r} is not in {pl.name}'s library; skipped")
                    return
                names = [want]
                pl.library.remove(want)
            else:
                names = [pl.library.pop() for _ in range(min(n, len(pl.library)))]
            for name in names:
                self._zone_put(pl, name, to, tapped=bool(mv.get("tapped")), depth=mv.get("depth", 0))
            self.log(f"  ↳ {pl.name}: {', '.join(names)} from the bottom of library → {to}")
            return
        if frm == "library" and mv.get("card"):
            # a named card anywhere in the library, without the shuffle a search forces
            want = mv["card"]
            if want not in pl.library:
                self.log(f"  !! move: {want!r} is not in {pl.name}'s library; skipped")
                return
            pl.library.remove(want)
            self._zone_put(pl, want, to, tapped=bool(mv.get("tapped")),
                           depth=mv.get("depth", 0))
            self.log(f"  ↳ {pl.name}: {want} from library → {to} (no shuffle)")
            return
        if frm == "library_top":
            if mv.get("card"):
                # a positional move takes the top card; naming one and getting a
                # different one is worse than doing nothing, so ask instead
                self.log(f"  !! move: a move from library_top is positional and takes the top "
                         f"card, so naming {mv['card']!r} does nothing — {pl.name} asked to "
                         f"redeclare it")
                if getattr(self, "_depth", 0) < 1:
                    r = self.ask(i,
                        f"PROTOCOL ERROR — you moved {mv['card']!r} from library_top, but that "
                        f"move takes whatever is on top by position and ignores the name, so "
                        f"nothing was moved. To take a specific card you have looked at, first "
                        f'reorder the library so it is on top ({{"move":{{"from":"library_top",'
                        f'"n":N,"to":"library_bottom"}}}} moves the ones above it), then take the '
                        f"top card. To fetch a card you have not seen, use a search atom instead "
                        f"— it is verified and public. Redeclare it.",
                        schema_hint='{"effects":[...]}')
                    self.apply_effects(i, r.get("effects"), depth=1)
                return
            n = int(mv.get("n", 1))
            names = [pl.library.pop(0) for _ in range(min(n, len(pl.library)))]
            for name in names:
                self._zone_put(pl, name, to, tapped=bool(mv.get("tapped")), depth=mv.get("depth", 0))
            shown = ", ".join(names) if to not in ("hand", "library_bottom") else f"{len(names)} cards"
            self.log(f"  ↳ {pl.name}: top {len(names)} of library → {to} ({shown})")
            if not pl.library and to != "library_bottom":
                self.log(f"  (note: {pl.name}'s library is now empty)")
            return
        if frm == "stack":
            want = mv.get("card")
            obj = next((o for o in reversed(self.stack)
                        if o["id"] == want or o["name"] == want), None)
            if not obj:
                self.log(f"  !! move: {want!r} is not on the stack; skipped")
                return
            self.stack.remove(obj)
            owner = self.p[obj["caster"]]
            self._zone_put(owner, obj["name"], to, tapped=bool(mv.get("tapped")))
            self.log(f"  ↳ {obj['name']} ({obj['id']}) leaves the stack → {owner.name}'s {to}")
            return
        srcs = {"hand": pl.hand, "graveyard": pl.graveyard, "exile": pl.exile}
        if frm == "command":
            cmdr = mv.get("card") or (pl.commanders[0] if len(pl.commanders) == 1 else None)
            if pl.command_zone.get(cmdr):
                pl.command_zone[cmdr] = False
                self._zone_put(pl, cmdr, to, tapped=bool(mv.get("tapped")))
                self.log(f"  ↳ {cmdr}: command zone → {to}")
            else:
                self.log(f"  !! move: {cmdr!r} not in {pl.name}'s command zone; skipped")
            return
        if frm not in srcs:
            self.log(f"  !! move: bad source {frm!r}; skipped")
            return
        if not mv.get("card") and not mv.get("all") and frm in ("hand", "library"):
            # Plaguecrafter, edicts, "target player discards": the seat making
            # them do it can't see the zone, so the owner names the card
            n = max(1, int(mv.get("n", 1)))
            pool = srcs.get(frm) if frm in srcs else pl.library
            if not pool:
                self.log(f"  ({pl.name}'s {frm} is empty — nothing to {to})")
                return
            ok_types = [t.lower() for t in (mv.get("types") or [])]
            no_types = [t.lower() for t in (mv.get("not_types") or [])]

            def eligible(name):
                typ = (self.db.get(name, {}).get("type", "") or "").lower()
                if ok_types and not any(t in typ for t in ok_types):
                    return False
                return not any(t in typ for t in no_types)

            legal = [c for c in pool if eligible(c)] if (ok_types or no_types) else list(pool)
            if not legal:
                self.log(f"  ({pl.name}'s {frm} has nothing that qualifies — nothing moves)")
                return
            limit = ("" if legal == list(pool)
                     else f" You may only take: {', '.join(legal)}.")
            who = str(mv.get("chooser") or "owner").lower()
            if who == "random":
                # "at random" is nobody's choice — the engine rolls it
                picked = [legal[self.rng.randrange(len(legal))] for _ in range(min(n, len(legal)))]
                how = "at random"
            elif who in ("self", "me", "caster", "chooser"):
                # Duress, Thoughtseize: the card lets YOU pick out of their hand
                r = self.ask(i, f"{pl.name}'s {frm}: {', '.join(pool)}.{limit} Choose {n} to move "
                                f'to their {to}. Reply {{"cards":[names]}}.',
                             schema_hint='{"cards":[names],"table_talk":str}')
                picked = [str(x) for x in (r.get("cards") or [])][:n]
                how = f"{me.name}'s choice"
            else:
                r = self.ask(self.p.index(pl),
                             f"{me.name} is making you move {n} card{'s' if n != 1 else ''} from your "
                             f"{frm} to your {to}. You choose which. Your {frm}: {', '.join(pool)}."
                             f'{limit} Reply {{"cards":[names]}}.',
                             schema_hint='{"cards":[names],"table_talk":str}')
                picked = [str(x) for x in (r.get("cards") or [])][:n]
                how = "their choice"
            picked = [x for x in picked if x in legal] or legal[:n]
            for name in picked:
                pool.remove(name)
                self._zone_put(pl, name, to, tapped=bool(mv.get("tapped")), depth=mv.get("depth", 0))
            shown = ", ".join(picked) if to != "hand" else f"{len(picked)} cards"
            self.log(f"  ↳ {pl.name} moves {shown} from {frm} to {to} ({how})")
            return
        if mv.get("all"):                       # wheels, discard-hand, mass exile
            names = list(srcs[frm])
            srcs[frm].clear()
            for name in names:
                self._zone_put(pl, name, to, tapped=bool(mv.get("tapped")), depth=mv.get("depth", 0))
            self.log(f"  ↳ {pl.name}: entire {frm} ({len(names)} cards) → {to}")
            return
        card = mv.get("card")
        if card not in srcs[frm]:
            self.log(f"  !! move: {card!r} not in {pl.name}'s {frm}; skipped (VERIFICATION FAILED)")
            return
        srcs[frm].remove(card)
        if card in pl.command_zone and to == "command":
            pl.command_zone[card] = True
            self.log(f"  ↳ {card}: {frm} → command zone")
            return
        self._zone_put(pl, card, to, tapped=bool(mv.get("tapped")),
                       depth=mv.get("depth", 0))
        self.log(f"  ↳ {pl.name}: {card} {frm} → {to}")

    def check_standing(self, event, actor_i, n=1):
        """State-based query sweep. Standing effects (Smothering Tithe, Rhystic
        Study...) registered against a source permanent fire whenever the
        engine sees a qualifying event by another seat. An entry whose source
        has left the battlefield expires on its next check — the board is the
        registry. Busy-guard: branches that themselves draw/cast don't
        re-trigger the sweep (no Rhystic-into-Tithe recursion)."""
        if self._standing_busy or not self.standing:
            return
        self._standing_busy = True
        try:
            for st in list(self.standing):
                _, perm = self.find(st["source"])
                if not perm:
                    self.standing.remove(st)
                    self.log(f"  (standing effect from {st['source']} expired — source left the battlefield)")
                    continue
                if st["on"] != event or st["owner"] == actor_i or not self.p[st["owner"]].alive:
                    continue
                self._standing_query(st, actor_i, n)
        finally:
            self._standing_busy = False

    def _standing_query(self, st, actor_i, n):
        owner, pl = self.p[st["owner"]], self.p[actor_i]
        q = st["question"]
        times = (f' It triggers {n}x — add "n":K for how many you accept/pay for '
                 f'(default all).' if n > 1 else '')
        r = self.ask(actor_i,
                     f"DECISION: standing effect — {owner.name}'s {st['source']} asks you: "
                     f'{q} Answer as the question asks — {{"choice":"<your answer>"}}.{times} '
                     f'"table_talk" welcome.',
                     schema_hint='{"choice":str,"n":int}')
        answer = str(r.get("choice", "")).strip()
        yes = answer.lower() in AFFIRM
        try:
            k = max(0, min(n, int(r.get("n", n if yes else 0))))
        except (TypeError, ValueError):
            k = n if yes else 0
        if n > 1:
            self.log(f"  ↳ {pl.name} accepts/pays {k} of {n} — {st['source']}: {q}")
        else:
            self.log(f"  ↳ {pl.name} answers: {answer or '(no answer)'} — {st['source']}: {q}")
        for _ in range(k):
            self.apply_effects(st["owner"], st["if_yes"])
        for _ in range(n - k):
            self.apply_effects(st["owner"], st["if_no"])

    def _atom_ask(self, i, q):
        """A decision that belongs to another seat, made at resolution time
        (Tithe/Rhystic payments, punisher modes, 'may' abilities). The engine
        relays the question, logs the answer publicly, and applies the chosen
        branch as the asker — assumed answers get disputed, asked ones bind."""
        asker = self.p[i]
        pl = self.resolve_player(i, q.get("player"))
        if not pl or not pl.alive:
            self.log(f"  !! ask: can't resolve player {q.get('player')!r}; skipped")
            return
        question = str(q.get("question") or "?")[:400]
        if pl is asker:                    # your own choice needs no relay
            self.log(f"  !! ask: {asker.name} asked themselves {question!r}; treating as yes")
            self.apply_effects(i, q.get("if_yes") or [])
            return
        j = self.p.index(pl)
        r = self.ask(j,
            f"DECISION: {asker.name} asks you: {question} — this choice is yours, made now, "
            f"mid-resolution. Answer in whatever form the question asks for: yes or no, a "
            f"number, a name, a mode, a split into piles. "
            f'Reply {{"choice":"<your answer>"}}; "table_talk" welcome. Include effect atoms '
            f"only if your choice itself has a cost to record (e.g. tapping lands you name).",
            schema_hint='{"choice":str,"effects":[...],"table_talk":str}')
        answer = str(r.get("choice", "")).strip()
        self.log(f"  ↳ {pl.name} answers: {answer or '(no answer)'} — {question}")
        if r.get("effects"):
            self.apply_effects(j, r.get("effects"))
        low = answer.lower()
        if low in AFFIRM:
            branch = q.get("if_yes")
        elif low in DENY or not answer:
            branch = q.get("if_no")
        else:                     # an open answer: the asker resolves from what was said
            branch = None
            if q.get("if_yes") or q.get("if_no"):
                self.log(f"  (open answer — {asker.name} resolves {question!r} from it)")
        if branch:
            self.apply_effects(i, branch)

    ATOMS = ("move", "life", "create", "set", "draw", "copy", "counter", "ask", "standing", "fight",
             "dig", "search", "shuffle", "look", "reveal", "random", "eliminate", "note")

    def apply_effects(self, i, effects, depth=0):
        """Apply agent-declared atoms. Each atom is armored: a malformed one
        (bad key, wrong type) logs a red skip instead of unwinding a whole
        game — the bookkeeper errs loud and keeps going. An atom whose shape the
        engine doesn't recognise goes back to the seat once to be redeclared,
        since a silently dropped consequence is a consequence that didn't
        happen."""
        self._depth = depth
        for e in effects or []:
            if not isinstance(e, dict):
                continue
            try:
                self._apply_atom(i, e)
            except GameOver:
                raise
            except Exception as ex:
                self.log(f"  !! atom crashed the bookkeeper ({type(ex).__name__}: {str(ex)[:80]}) "
                         f"— skipped: {json.dumps(e, default=str)[:200]}")

    def _apply_atom(self, i, e):
        me = self.p[i]
        if "move" in e:
            self._atom_move(i, e["move"])
        elif "life" in e:
            l = e["life"]
            tgt = self.resolve_player(i, l.get("player", "self"))
            if not tgt:
                self.log(f"  !! life: can't resolve player {l.get('player')!r}; skipped")
                return
            if not tgt.alive:
                self.log(f"  ({tgt.name} is already eliminated — life change ignored)")
                return
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
                          tapped=bool(t.get("tapped")), pt=tuple(t.get("pt", (1, 1))),
                          counters=t.get("counters"))
            self.log(f"  ↳ {tgt.name} creates {t.get('n',1)}x {t.get('name')} token(s)")
        elif "set" in e:
            s = e["set"]
            if "player" in s and "id" not in s:
                tgt = self.resolve_player(i, s.get("player", "self"))
                if not tgt:
                    self.log(f"  !! set: can't resolve player {s.get('player')!r}; skipped")
                    return
                delta = s.get("counters") or {}
                if not isinstance(delta, dict):
                    delta = {"experience": int(delta)}
                for kind, n in delta.items():
                    tgt.counters[kind] = tgt.counters.get(kind, 0) + int(n)
                    if tgt.counters[kind] <= 0:
                        tgt.counters.pop(kind)
                self.log(f"  ↳ {tgt.name}: "
                         + ", ".join(f"{k}{int(v):+d}" for k, v in delta.items())
                         + f" (now {tgt.counters or 'none'})")
                return
            ident = s.get("id")
            _, perm = self.find(ident, prefer=i)
            if not perm:
                # a card that left and came back is a new object with a new id;
                # if exactly one permanent shares the name, that is plainly the one
                base = str(ident).split("#")[0].strip()
                same = [x for pl in self.p for x in pl.battlefield if x["name"] == base]
                if len(same) == 1:
                    perm = same[0]
                    self.log(f"  (set: {ident} is gone — applying to {perm['id']}, "
                             f"the only {base} on the battlefield)")
                else:
                    self.log(f"  !! set: no permanent {ident!r}; skipped"
                             + (f" ({len(same)} named {base} — say which)" if same else ""))
                    return
            changes = []
            if "tapped" in s:
                perm["tapped"] = bool(s["tapped"]); changes.append(f"tapped={s['tapped']}")
            if "sick" in s:
                perm["sick"] = bool(s["sick"]); changes.append(f"sick={s['sick']}")
            if "counters" in s:
                c = s["counters"]
                deltas = c if isinstance(c, dict) else {"+1/+1": c}
                cs = _counters(perm)
                for kind, delta in deltas.items():
                    cs[kind] = cs.get(kind, 0) + int(delta)
                    if cs[kind] <= 0:
                        cs.pop(kind)
                    changes.append(f"{kind}{int(delta):+d}")
                plus, minus = cs.get("+1/+1", 0), cs.get("-1/-1", 0)
                if plus and minus:            # CR 704.5q: they cancel in pairs
                    both = min(plus, minus)
                    cs["+1/+1"], cs["-1/-1"] = plus - both, minus - both
                    self.log(f"  ↳ {perm['id']}: {both} +1/+1 and {both} -1/-1 cancel")
                perm["counters"] = {k: v for k, v in cs.items() if v}
            if "skip_untaps" in s:
                perm["skip_untaps"] = max(0, int(s["skip_untaps"] or 0))
                changes.append(f"skip_untaps={perm['skip_untaps']}")
            if "pt" in s:
                # null is how a manland says it went back to being a land
                perm["pt"] = tuple(s["pt"]) if s["pt"] else None
                if not perm["pt"]:
                    perm["sick"] = False
                changes.append(f"pt={s['pt']}")
            self.log(f"  ↳ {perm['id']}: {', '.join(changes) or 'no-op'}")
            if perm["pt"] and perm["pt"][1] + plus_counters(perm) <= 0:
                # the engine only knows printed pt plus counters, so it says so
                # rather than acting — an anthem it hasn't been told about is
                # the seat's to account for
                self.log(f"  (note: {perm['id']} is at {perm['pt'][1] + plus_counters(perm)} "
                         f"toughness — if nothing is holding it up it belongs in the graveyard)")
        elif "draw" in e:
            d = e["draw"]
            tgt = self.resolve_player(i, d.get("player", "self"))
            if not tgt:
                self.log(f"  !! draw: can't resolve player {d.get('player')!r}; skipped")
                return
            got = self.draw(tgt, int(d.get("n", 1)), frm=d.get("from", "top"))
            self.log(f"  ↳ {tgt.name} draws {len(got)}"
                     + (" from the BOTTOM" if d.get("from") == "bottom" else ""))
            if got:
                self.log_private(f"  ({tgt.handle} drew: {', '.join(got)})", seat=tgt.handle)
                self.check_standing("draw", self.p.index(tgt), len(got))
        elif "copy" in e:
            c = e["copy"] or {}
            tid = str(c.get("target", ""))
            tgt = next((o for o in self.stack if o["id"] == tid or o["name"] == tid), None)
            n = max(1, int(c.get("n", 1)))
            if not tgt:                       # not a spell — a permanent, copied as a token
                owner, perm = self.find(tid)
                if not perm:
                    self.log(f"  !! copy: {tid!r} is neither on the stack nor a permanent; "
                             f"nothing copied")
                    return
                who = self.resolve_player(i, c.get("player", "self")) or me
                made = [self.perm(who, perm["name"], token=True,
                                  tapped=bool(c.get("tapped")),
                                  pt=c.get("pt") or perm["pt"]) for _ in range(n)]
                me.copies_this_turn += n
                self.log(f"  ↳ {me.name} copies {perm['id']} as {n} token"
                         f"{'s' if n != 1 else ''} under {who.name}: "
                         f"{', '.join(x['id'] for x in made)}")
                return
            aim = c.get("targets")
            me.copies_this_turn += n
            self.log(f"  ↳ {me.name} copies {tgt['name']} ({tgt['id']}) x{n}"
                     + (f", new targets: {', '.join(str(t) for t in aim)}" if aim else "")
                     + f" — the copies resolve before {tgt['id']} does; declare what they do")
        elif "counter" in e:
            # honored anywhere (responses, corrections): counter a live stack object
            tid = str((e["counter"] or {}).get("target", ""))
            tgt = next((o for o in self.stack if o["id"] == tid), None)
            if tgt:
                tgt["countered"] = True
                self.log(f"  ↳ {tgt['id']} {tgt['name']} is countered.")
            else:
                self.log(f"  !! counter: {tid!r} is not on the stack; no effect")
        elif "ask" in e:
            self._atom_ask(i, e["ask"])
        elif "standing" in e:
            st = e["standing"]
            src = str(st.get("source") or "")
            on = str(st.get("on") or "").strip()
            if src and "#" not in src:
                # a permanent registering its own trigger as it resolves doesn't
                # know its id yet — take the name if it points somewhere unambiguous
                same = [x for pl in self.p for x in pl.battlefield if x["name"] == src]
                if len(same) == 1:
                    src = same[0]["id"]
                    self.log(f"  (standing: {st.get('source')!r} → {src})")
            if not on or "#" not in src:
                self.log('  !! standing: needs "on" (condition) and a "source" permanent id'
                         + (f" — {st.get('source')!r} matches no single permanent" if src else "")
                         + "; skipped")
                return
            self.standing.append({
                "owner": i, "source": src, "on": on,
                "question": str(st.get("question") or "?")[:400],
                "if_yes": st.get("if_yes") or [], "if_no": st.get("if_no") or []})
            auto = on in ("draw", "cast")
            self.log(f"  ↳ standing effect: {src} — on {on!r}: \"{self.standing[-1]['question']}\""
                     + ("" if auto else " (engine can't see this event — it stays in every"
                                         " digest; trigger it with an ask atom when it happens)"))
        elif "damage" in e or "fight" in e:
            def hit(perm, n, why):
                perm["damage"] = perm.get("damage", 0) + int(n)
                tough = (perm["pt"][1] + plus_counters(perm)) if perm["pt"] else None
                self.log(f"  ↳ {perm['id']} takes {n} damage{why}"
                         + (f" ({perm['damage']}/{tough})" if tough is not None else ""))
                if tough is not None and perm["damage"] >= tough:
                    self.log(f"  (note: {perm['id']} has lethal damage marked — if nothing is "
                             f"holding it up it belongs in the graveyard)")

            if "fight" in e:
                f = e["fight"]
                _, a = self.find(str(f.get("a", "")), prefer=i)
                _, b = self.find(str(f.get("b", "")), prefer=i)
                if not a or not b:
                    self.log(f"  !! fight: can't find {f.get('a')!r} and/or {f.get('b')!r}; skipped")
                    return
                pa = (a["pt"][0] + plus_counters(a)) if a["pt"] else 0
                pb = (b["pt"][0] + plus_counters(b)) if b["pt"] else 0
                self.log(f"  ↳ {a['id']} fights {b['id']}")
                hit(b, pa, f" from {a['id']}")
                hit(a, pb, f" from {b['id']}")
                return
            d = e["damage"]
            _, tgt = self.find(str(d.get("id", "")), prefer=i)
            if not tgt:
                pl = self.resolve_player(i, d.get("id") or d.get("player") or "")
                if pl:      # damage to a player is life loss
                    self.apply_effects(i, [{"life": {"player": pl.handle,
                                                     "delta": -int(d.get("n", 0))}}])
                    return
                self.log(f"  !! damage: no permanent or player {d.get('id')!r}; skipped")
                return
            hit(tgt, int(d.get("n", 0)), f" from {d['from']}" if d.get("from") else "")
        elif "dig" in e:
            # "reveal/exile from the top until X" — cascade, Umbris, discover,
            # Etali. The seat writes the predicate; the engine just walks the
            # library so an 80-card whiff doesn't cost three prompts.
            dg = e["dig"]
            tgt = self.resolve_player(i, dg.get("player", "self"))
            if not tgt:
                self.log(f"  !! dig: can't resolve player {dg.get('player')!r}; skipped")
                return
            u = dg.get("until") or {}
            names = [str(x).lower() for x in (u.get("names") or [])]
            ok_t = [t.lower() for t in (u.get("types") or [])]
            no_t = [t.lower() for t in (u.get("not_types") or [])]

            def matches(card):
                d = self.db.get(card, {})
                typ = (d.get("type", "") or "").lower()
                if names and card.lower() not in names:
                    return False
                if ok_t and not any(t in typ for t in ok_t):
                    return False
                if any(t in typ for t in no_t):
                    return False
                mv = mana_value(d.get("cost", ""))
                if "max_mv" in u and mv > int(u["max_mv"]):
                    return False
                if "min_mv" in u and mv < int(u["min_mv"]):
                    return False
                return True

            cap = min(int(dg.get("max", len(tgt.library))), len(tgt.library))
            passed, found = [], None
            for _ in range(cap):
                card = tgt.library.pop(0)
                if matches(card):
                    found = card
                    break
                passed.append(card)
            rest_to = dg.get("rest", "library_bottom")
            if dg.get("shuffle_rest", True) and rest_to.startswith("library"):
                self.rng.shuffle(passed)
            for card in passed:
                self._zone_put(tgt, card, rest_to)
            if found is not None:
                self._zone_put(tgt, found, dg.get("found", "exile"))
            self.log(f"  ↳ {tgt.name} digs {len(passed) + (1 if found else 0)} deep: "
                     + (f"finds {found} → {dg.get('found', 'exile')}" if found
                        else "no match in the whole library")
                     + f"; {len(passed)} → {rest_to}")
            self.log_private(f"  (passed over: {', '.join(passed) or 'nothing'})",
                             seat=tgt.handle)
        elif "search" in e:
            s = e["search"]   # card, or types/not_types to search by description
            tgt = self.resolve_player(i, s.get("player", "self"))
            if not tgt:
                self.log(f"  !! search: can't resolve player {s.get('player')!r}; skipped")
                return
            card = s.get("card")
            ok_t = [t.lower() for t in (s.get("types") or [])]
            no_t = [t.lower() for t in (s.get("not_types") or [])]
            if card not in tgt.library and (ok_t or no_t):
                # "search for a basic land" — a category, not a name; the seat
                # writes the filter and takes the first thing that fits
                def fits(n):
                    typ = (self.db.get(n, {}).get("type", "") or "").lower()
                    return ((not ok_t or all(t in typ for t in ok_t))
                            and not any(t in typ for t in no_t))
                hit = next((n for n in tgt.library if fits(n)), None)
                if hit:
                    self.log(f"  (search: {card!r} is a description, not a card — taking {hit})")
                    card = hit
            if card not in tgt.library:
                shuffles = s.get("shuffle", True)
                self.log(f"  !! search: {card!r} is NOT in {tgt.name}'s library (VERIFICATION "
                         f"FAILED); nothing found"
                         + (", library shuffled anyway" if shuffles else ""))
                if shuffles:
                    self.rng.shuffle(tgt.library)
                return
            tgt.library.remove(card)
            to = s.get("to", "hand")
            self._zone_put(tgt, card, to, tapped=bool(s.get("tapped")))
            if s.get("shuffle", True):      # literally "then shuffle" — a card that
                self.rng.shuffle(tgt.library)   # shuffles first is three atoms, in order
            self.log(f"  ↳ {tgt.name} searches library: {card} → {to}" +
                     (" (shuffled)" if s.get("shuffle", True) else ""))
        elif "shuffle" in e:
            s = e["shuffle"]
            tgt = self.resolve_player(i, (s or {}).get("player", "self"))
            if tgt:
                self.rng.shuffle(tgt.library)
                self.log(f"  ↳ {tgt.name} shuffles their library")
        elif "look" in e:
            lk = e["look"]
            tgt = self.resolve_player(i, lk.get("player", "self"))
            if not tgt:
                self.log(f"  !! look: can't resolve player {lk.get('player')!r}; skipped")
                return
            zone = lk.get("zone", "hand")
            if zone == "hand":
                cards, what = list(tgt.hand), f"{tgt.name}'s hand"
            else:
                n = max(1, int(lk.get("n", 1)))
                cards, what = list(tgt.library[:n]), f"the top {n} of {tgt.name}'s library"
            self.log(f"  ↳ {me.name} looks at {what} ({len(cards)} cards).")
            self.log_private(f"  [{what}: {', '.join(cards) or '(empty)'}]", seat=me.handle)
            r = self.ask(i,
                f"PRIVATE LOOK — only you see this. {what}: {', '.join(cards) or '(empty)'}. "
                f"Declare anything the card does with that knowledge as effect atoms, or pass.",
                schema_hint='{"action":"pass"|"correct","effects":[...]}')
            if r.get("effects"):
                self.apply_effects(i, r["effects"])
        elif "reveal" in e:
            r = e["reveal"]
            tgt = self.resolve_player(i, r.get("player", "self"))
            if not tgt:
                return
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
                return
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
            self.log(f"  !! unknown effect atom {list(e.keys())} — nothing applied; "
                     f"{me.name} asked to redeclare it")
            if getattr(self, "_depth", 0) < 1:
                r = self.ask(i,
                    f"PROTOCOL ERROR — you declared {json.dumps(e)[:400]}, which is not an effect "
                    f"atom, so NONE of it happened. The engine reads only these atom names: "
                    f"{', '.join(self.ATOMS)}. Redeclare exactly that consequence using them "
                    f"(a player's experience/poison/energy counters are "
                    f'{{"set":{{"player":P,"counters":{{"experience":1}}}}}}; moving a card between '
                    f'zones is a "move"), or reply with an empty effects list if it should not '
                    f"happen at all.",
                    schema_hint='{"effects":[...]}')
                self.apply_effects(i, r.get("effects"), depth=1)

# ---------------- actions ----------------

# ---------------- the stack ----------------
    def _stack_line(self):
        def one(o):
            aim = f" -> {', '.join(o['targets'])}" if o.get("targets") else ""
            return f"{o['id']} {o['name']} ({self.p[o['caster']].handle}){aim}"
        return " -> ".join(one(o) for o in reversed(self.stack)) or "(empty)"

    ZONES_CASTABLE = {"hand": "hand", "graveyard": "graveyard", "exile": "exile",
                      "library": "library", "yard": "graveyard"}

    @staticmethod
    def face(name, zone):
        """The card in `zone` that a declared name refers to, or None. A
        double-faced card is stored under its full "Front // Back" name, and a
        seat naming either face — Malakir Mire for the land, Malakir Rebirth for
        the spell — means that card."""
        if name in zone:
            return name
        want = str(name or "").strip().lower()
        for card in zone:
            if any(f.strip().lower() == want for f in card.split(" // ")):
                return card
        return None

    def _pay_spell(self, i, a):
        """Costs are paid at announcement. Returns from_cz, or None if illegal.
        A spell can be cast from wherever the card says it can: hand by default,
        the command zone, or the zone named in "from" — exile for cascade,
        discover, foretell and impulse draws, the graveyard for flashback and
        escape, the library for the likes of Etali."""
        me = self.p[i]
        c = a.get("card")
        c = self.face(c, me.hand) or c
        from_cz = (me.command_zone.get(c, False) and c not in me.hand)
        frm = self.ZONES_CASTABLE.get(str(a.get("from") or "").lower())
        if not (c in me.hand or from_cz):
            zone = getattr(me, frm, None) if frm and frm != "hand" else None
            if zone is None or c not in zone:
                # the seat may have named no zone; find it in exactly one of its own
                hits = [z for z in ("exile", "graveyard", "library") if c in getattr(me, z)]
                if len(hits) != 1:
                    return None
                frm = hits[0]
                self.log(f"  (cast: {c} isn't in hand — casting it from {me.name}'s {frm})")
                zone = getattr(me, frm)
            zone.remove(c)
            for ident in a.get("tap", []):
                _, perm = self.find(ident, prefer=i)
                if perm:
                    perm["tapped"] = True
            return False
        for ident in a.get("tap", []):
            _, perm = self.find(ident, prefer=i)
            if perm:
                if perm["tapped"]:
                    self.log(f"  !! {ident} already tapped (payment dubious; logged)")
                perm["tapped"] = True
            else:
                self.log(f"  !! tap: no permanent {ident!r}; payment not recorded")
        if from_cz:
            me.command_zone[c] = False
            me.commander_tax[c] += 2   # next cast of this one from CZ costs 2 more
        else:
            me.hand.remove(c)
        return from_cz

    def _resolve_spell(self, i, a, from_cz):
        """Resolution: fizzle check on id-targets, then placement + effects."""
        me = self.p[i]
        c = a.get("card")
        targets = [str(t) for t in (a.get("targets") or [])]
        if targets:
            # stack#N targets are spells, not permanents — the battlefield
            # search can't see them, and countering is adjudicated elsewhere
            gone = [t for t in targets if "#" in t and not str(t).startswith("stack#")
                    and not self.find(t, prefer=i)[1]]
            if gone and len(gone) == len(targets):
                if from_cz:
                    me.command_zone[c] = True
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
            self.perm(me, c, tapped=bool(a.get("tapped")), counters=a.get("counters"))
        else:
            me.graveyard.append(c)
        told = self._fresh_narration(a.get("narration"))
        self.log(f"{me.name} casts {c}" + (" (from command zone)" if from_cz else "") +
                 (f", tapping {a.get('tap')}" if a.get("tap") else "") +
                 (f" — {told}" if told else ""))
        if not self.stack:            # cast outside the stack still wants its caption
            cap = self._card_caption(c)
            if cap:
                self.log_private(cap)
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
        told = self._fresh_narration(a.get("narration"))
        self.log(f"{me.name} activates {src}" + (f" — {told}" if told else ""))
        if perm is None:
            _, perm = self.find(src, prefer=i)
        if perm and (d_ := self.db.get(perm["name"])):
            self.log_private(f"  [{perm['name']}: {d_['text']}]")
        self.apply_effects(i, a.get("effects"))

    def _card_caption(self, name):
        """One-line oracle for the console, shown when a thing is announced —
        a response window is a judgement call about a card, so the card is on
        screen while the window is open."""
        d = self.db.get(name)
        if not d:
            return None
        pt = f" {d['pt'][0]}/{d['pt'][1]}" if d.get("pt") else ""
        return f"  [{name} — {d['cost'] or 'Land'} — {d['type']}{pt} — {d['text']}]"


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
        if kind == "spell":
            me.spells_this_turn += 1
        self.stack_seq += 1
        tgts = [str(t) for t in (plan.get("targets") or [])]
        obj = {"id": f"stack#{self.stack_seq}", "caster": i, "kind": kind,
               "name": name, "countered": False, "targets": tgts}
        self.stack.append(obj)
        verb = "" if kind == "spell" else "activation of "
        aim = f" targeting {', '.join(tgts)}" if tgts else ""
        says = plan.get("narration")
        if says:
            self.narrated.add(_norm_narration(says))
        self.log(f"{me.name} announces {verb}{name} ({obj['id']}){aim}"
                 + (f" — {says}" if says else "") + "...")
        cap = self._card_caption(name if kind == "spell" else plan.get("source", name))
        if cap:
            self.log_private(cap)
        if kind == "spell":
            d_t = self.db.get(name) or {}
            if "Instant" not in d_t.get("type", "") and "Flash" not in d_t.get("text", ""):
                if len(self.stack) > 1:
                    self.log(f"  !! {name} is sorcery-speed and there are spells already on "
                             f"the stack (timing dubious; logged)")
                elif i != getattr(self, "active", i):
                    self.log(f"  !! {name} is sorcery-speed and it is not {me.name}'s turn "
                             f"(timing dubious; logged)")
        self._flush_talk()
        if kind == "spell":
            self.check_standing("cast", i)        # Rhystic-style taxes fire on announce
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
                        me.command_zone[name] = True
                        self.log(f"  ↳ {name} is COUNTERED — commander returns to the command "
                                 f"zone (tax now +{me.commander_tax[name]}); mana stays spent.")
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
                            me.command_zone[name] = True
                            self.log(f"  ↳ {name} stays in command zone "
                                     f"(tax now +{me.commander_tax[name]})")
                        else:
                            me.graveyard.append(name)
                    self.log(f"  ↳ {name} FIZZLES on resolution (caster's call); costs paid.")
                    return False
                if confirm.get("action") in ("cast", "activate"):
                    keep = {"targets", "effects", "narration"}
                    plan = {**plan, **{k: v for k, v in confirm.items() if k in keep}}
            if obj not in self.stack:
                # a correction took it back off the stack while this was live —
                # an illegal cast rewound, a spell bounced in response
                self.log(f"  ↳ {name} left the stack before it resolved; it does not resolve.")
                return False
            if kind == "spell":
                return self._resolve_spell(i, plan, paid) is not None
            self._resolve_ability(i, plan, src_perm)
            return True
        finally:
            if obj in self.stack:
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
                if not pl.alive:
                    continue
                if not self._can_respond(pl, j):
                    self.log_private(f"({pl.name} holds nothing playable at instant speed — "
                                     f"no window)", seat=pl.handle)
                    continue
                caster_name = self.p[obj["caster"]].name
                verb = "casting" if obj["kind"] == "spell" else "activating"
                d_o = self.db.get(obj["name"]) or {}
                oracle = (f" [{obj['name']} — {d_o.get('cost') or 'Land'} — "
                          f"{d_o.get('type','')} — {d_o.get('text','')}]" if d_o else "")
                r = self.ask(j,
                    f"RESPONSE WINDOW: {caster_name} is {verb} {obj['name']}.{oracle} "
                    f"STACK (top resolves first): {self._stack_line()}. "
                    f"You may cast an instant/flash or activate an instant-speed ability in "
                    f"response — it goes on top and resolves first — or pass. To counter "
                    f"something, include {{\"counter\":{{\"target\":\"{obj['id']}\"}}}} "
                    f"(any stack id) in your effects.",
                    schema_hint='{"action":"cast"|"activate"|"pass", "card":str, "source":str, '
                                '"tap":[ids], "targets":[ids], "effects":[...], "narration":str}')
                if r.get("action") == "correct":
                    self.do_action(j, r)     # bookkeeping — doesn't touch the stack
                    continue
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
            held = self.face(c, me.hand)
            if held and me.lands_played < 2:   # Rites/etc: agent responsible; hard cap 2
                me.hand.remove(held)
                me.lands_played += 1
                tapped = bool(a.get("tapped"))
                self.perm(me, c, tapped=tapped, counters=a.get("counters"))
                self.log(f"{me.name} plays land: {c}" + (" (tapped)" if tapped else ""))
                self.apply_effects(i, a.get("effects"))
            else:
                self.log(f"  !! illegal/ignored land play by {me.name}: {c}")
        elif act == "cast":
            c = a.get("card")
            from_cz = self._pay_spell(i, a)
            if from_cz is None:
                self.log(f"  !! cast of {c} by {me.name} ignored (not in hand/CZ)")
            else:
                self.check_standing("cast", i)
                return self._resolve_spell(i, a, from_cz)
        elif act == "activate":
            perm = self._pay_ability(i, a)
            self._resolve_ability(i, a, perm)
        elif act == "correct":
            # bookkeeping repair: atoms apply directly, no stack, no windows
            effects = a.get("effects") or []
            changes = [e for e in effects if isinstance(e, dict) and set(e) != {"note"}]
            self.idle_corrections[i] = 0 if changes else self.idle_corrections[i] + 1
            self.log(f"{me.name} corrects the board — {a.get('narration') or '(no explanation given)'}")
            self.apply_effects(i, effects)
            if self.idle_corrections[i] >= self.IDLE_CORRECTIONS:
                self.idle_corrections[i] = 0
                self.log(f"  !! {me.name} has corrected the board {self.IDLE_CORRECTIONS} times "
                         f"running without changing anything. If you believe the game has already "
                         f"ended, say so once and pass; the table and the judge decide, not "
                         f"repetition. Treating this as a pass.")
                return "pass"
        elif act == "pass":
            pass
        elif act == "claim_win":
            winner = self.resolve_player(i, a.get("player", "self")) or me
            if winner is me:
                self.log(f"**{me.name} claims they WIN THE GAME: {a.get('how')}"
                         + (f" — loop: {a.get('loop')}" if a.get("loop") else "") + "**")
            else:
                self.log(f"**{me.name} says {winner.name} HAS ALREADY WON: {a.get('how')}"
                         + (f" — loop: {a.get('loop')}" if a.get("loop") else "") + "**")
            for pl in [x for x in self.p if x.alive and x is not me and x is not winner]:
                verdict = self.ask(self.p.index(pl),
                    f"{me.name} claims a rules-based win for "
                    f"{'themselves' if winner is me else winner.name} (see table log). "
                    "Reply JSON {\"concede\": true/false, \"reason\": \"...\"}. Concede only if the claim is "
                    "genuinely sound and you have no answer available.",
                    schema_hint='{"concede": bool, "reason": str}')
                if verdict.get("concede"):
                    self.log(f"  ↳ {pl.name} CONCEDES: {verdict.get('reason','')}")
                    self.eliminate(pl, "conceded to claimed win")
                else:
                    self.log(f"  ↳ {pl.name} DISPUTES: {verdict.get('reason')} — play continues")
            if winner is not me:
                # naming someone else the winner concedes on your own behalf too
                left = [x for x in self.p if x.alive and x is not winner and x is not me]
                if not left:
                    self.log(f"  ↳ {me.name} concedes as well — {winner.name} has won.")
                    raise GameOver(self.p.index(winner),
                                   a.get("how") or "the table agreed they had already won")
        elif act == "claim_draw":
            self.log(f"**{me.name} claims the game is a DRAW: {a.get('how')}"
                     + (f" — loop: {a.get('loop')}" if a.get("loop") else "") + "**")
            agreed = []
            for pl in list(self.others(i)):
                verdict = self.ask(self.p.index(pl),
                    f"{me.name} claims the game is a draw (see table log) — a mandatory loop nobody "
                    "can break, or another state the game can't leave. Reply JSON "
                    '{"agree": true/false, "reason": "..."}. Agree only if the loop really is '
                    "compulsory and you genuinely have no way to interrupt it.",
                    schema_hint='{"agree": bool, "reason": str}')
                if verdict.get("agree"):
                    self.log(f"  ↳ {pl.name} AGREES: {verdict.get('reason','')}")
                    agreed.append(pl)
                else:
                    self.log(f"  ↳ {pl.name} DISPUTES: {verdict.get('reason')} — play continues")
                    return None
            if len(agreed) == len(list(self.others(i))):
                raise GameOver(None, a.get("how") or "table agreed the loop is compulsory")
        return None

    DEAD_SEAT_CALLS = 5
    IDLE_CORRECTIONS = 3

    def _watch_for_a_dead_seat(self, i):
        """A brain that keeps failing turns the seat into a very passive opponent
        and the game into fiction, so stop instead of playing it out."""
        if getattr(self.agents[i], "gave_up", False):
            self.dead_calls[i] += 1
            if self.dead_calls[i] >= self.DEAD_SEAT_CALLS:
                raise GameOver(None, f"{self.p[i].name}'s agent failed "
                                     f"{self.dead_calls[i]} calls in a row")
        else:
            self.dead_calls[i] = 0

    def _card_line(self, name, n):
        d = self.db.get(name, {})
        pt = f" {d['pt'][0]}/{d['pt'][1]}" if d.get("pt") else ""
        text = (d.get("text") or "").replace("\n", " ")
        return (f"  {name}{f' x{n}' if n > 1 else ''} — {d.get('cost') or 'Land'} — "
                f"{d.get('type','')}{pt}" + (f" — {text}" if text else ""))

    def _decklist_block(self, pl):
        """The seat's own 99 with rules text — you built this deck and you know what
        every card in it does, so tutoring and planning are not guesswork. Grouped by
        the builder's own tags when the decklist carries them, since the groups are
        what the deck is for."""
        tags = deck_tags(pl.deckname)
        if tags:
            out = []
            for tag, cards in tags.items():
                out.append(f"[{tag}]")
                out += [self._card_line(c, n) for c, n in cards]
            return "\n".join(out)
        counts = collections.Counter(pl.decklist)
        return "\n".join(self._card_line(n, c) for n, c in sorted(counts.items()))

    def digest(self, i, full_board=False):
        """Compact authoritative state: numbers and ids, no prose. Oracle text
        only for names this seat hasn't been shown yet this session. full_board
        adds graveyard contents, commander status and draw counts — the
        start-of-turn planning view."""
        me = self.p[i]
        seats = "; ".join(
            f"{pl.handle} life {pl.life}"
            + (f" [{', '.join(f'{k} {v}' for k, v in pl.counters.items())}]" if pl.counters else "")
            + f", hand {len(pl.hand)}, lib {len(pl.library)}, gy {len(pl.graveyard)}"
            + "".join(f", {c.split(',')[0]} in CZ" for c in pl.commanders if pl.command_zone[c])
            if pl.alive else f"{pl.handle} eliminated"
            for pl in self.p)
        def bf(pl):
            out = []
            for x in pl.battlefield:
                flags = "".join(("T" if x["tapped"] else "", "S" if x["sick"] else ""))
                pt = f" {x['pt'][0]+plus_counters(x)}/{x['pt'][1]+plus_counters(x)}" if x["pt"] else ""
                out.append(x["id"] + pt + (f"[{flags}]" if flags else "")
                           + "".join(f"[{k} {v}]" for k, v in _counters(x).items()))
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
                f"{pl.handle}: " + "; ".join(
                    f"{c} " + (f"in CZ (tax +{pl.commander_tax[c]})"
                               if pl.command_zone[c] else "on the move")
                    for c in pl.commanders)
                + f", drew {pl.drew_this_turn} this turn"
                for pl in self.p if pl.alive)
            gys = "\n".join(f"{pl.handle}: {', '.join(pl.graveyard) or '(empty)'}"
                             for pl in self.p if pl.alive)
            extra = f"\nCOMMANDERS:\n{cz}\nGRAVEYARDS:\n{gys}"
        stackline = f"\nSTACK (top first): {self._stack_line()}" if self.stack else ""
        if self.standing:
            live = [st for st in self.standing if self.find(st["source"])[1]]
            if live:
                stackline += "\nSTANDING EFFECTS (declared; honor them): " + "; ".join(
                    f"{st['source']} ({self.p[st['owner']].handle}) on {st['on']}: {st['question']}"
                    for st in live)
        return (f"TURN {self.turn}. Seats: {seats}\n"
                f"YOUR HAND ({len(me.hand)}): {'; '.join(me.hand)}\n"
                f"BATTLEFIELDS:\n{boards}\n"
                f"Lands you've played this turn: {me.lands_played}. "
                f"Spells cast this turn: {sum(pl.spells_this_turn for pl in self.p)} "
                f"({me.handle} {me.spells_this_turn}; copies you have made "
                f"{me.copies_this_turn}).{stackline}{extra}{texts}")

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
                      + "Reply per the established protocol: exactly one JSON object.\n"
                      + (f"Stay in voice — you are still method acting as: {me.personality} "
                         f"(a register sample, not a script: keep its diction, dialect, punctuation "
                         f"and tics — those are the character — but not its sentences, and don't "
                         f"reference cards from it that aren't on the board.) The voice holds up "
                         f"under pressure: a line that carries hard news or a rules point is still "
                         f"spoken in it. No winks: commit to the stance. "
                         f"The voice is genuine, never ironic. "
                         f"Speak only as yourself; do not pick up the phrasing, jokes or dialect of "
                         f"the other seats in the log above, don't narrate your own plays, and never "
                         f"dress a game "
                         f"action in an incongruous everyday domain (offices, paperwork, gyms, "
                         f"traffic, customer service) — say it in character instead.\n"
                         if me.personality else ""))
            self.log_sent[i] = len(self.table)
            raw = agent.ask(prompt)
            self._watch_for_a_dead_seat(i)
            return self._parse_reply(i, raw)
        prompt = (f"You are playing Magic: The Gathering (Commander pod, {n_alive} players alive, 40 life "
                  f"start, free-for-all, last seat standing wins). You are an expert player and a table "
                  f"politician: threat-assess privately, make deals, needle people — but talk like a player "
                  f"at a kitchen table, not a commentator narrating the game. You don't know who's piloting "
                  f"the other seats — refer to players as they/them. Follow the comprehensive rules "
                  f"yourself — the engine only does bookkeeping and trusts your legality, because it has no "
                  f"rules knowledge of its own. It logs doubts publicly and illegal plays get argued at the "
                  f"table, so rules-precision (summoning sickness, mana payment, timing) is what keeps your "
                  f"plays standing. A human judge watches the game: lines marked ⚖ in the table log are "
                  f"authoritative — when one flags an error or issues a ruling, address it in your next "
                  f"reply (correct state via effect atoms) before advancing your own plans. "
                  f"Eliminating someone is not automatically progress: a seat that can't threaten "
                  f"you absorbs other people's attacks and blocks for you, and killing it early "
                  f"makes you the archenemy one opponent sooner. Spend your clock on whoever is "
                  f"closest to winning, not whoever is easiest to finish. You are not obliged to "
                  f"be the one who answers it, though: three seats spending their turns on the "
                  f"same problem is three seats not developing, and whoever spent least is usually "
                  f"best placed afterward. Weigh what an answer costs you against what it buys "
                  f"everyone else. And keep the default in mind: this is a Commander game at this "
                  f"power level, not a tournament. Most of your turns should go on getting your own "
                  f"deck online, because that is what you built it to do. Interaction is scarce and "
                  f"every piece is a card you paid for — hold it for the thing that actually ends "
                  f"the game rather than spending it on the best target currently available. You do "
                  f"not need an opinion about every spell that resolves; a seat that answers "
                  f"everything develops nothing.\n"
                  + f"\nYOUR DECKLIST — the 99 behind your commander, with rules text. You "
                    f"built this deck and know every card in it. Anything not on this list is not "
                    f"in your library, so never search for it:\n{self._decklist_block(me)}\n"
                  + (f"\nYOUR DECK'S GAMEPLAN: {me.strategy}\n" if me.strategy else "")
                  + (f"\nHOW YOU TALK: {me.personality} That is a sample of the register, never a "
                     f"script: its diction is yours to keep — the dialect, the punctuation, the "
                     f"rhythm, the tics are what the character is made of and they belong in every "
                     f"line you speak, all game. What you don't reuse is its sentences, and don't "
                     f"mention cards or events from it that aren't happening in this game. The "
                     f"character works better the more "
                     f"closely you adhere to this voice rather than defaulting to your own register. "
                     f"Fully inhabit the voice, you are method acting as you play. No winks: commit to "
                     f"the stance. If you are excited you are not also self-deprecating or meta about "
                     f"your own deck; if you are pedantic you are not also ironically distant; if you "
                     f"are haughty you are not begging or playing for laughs. The character does not "
                     f"know it is a character. The voice is genuine, never ironic. One tic is banned "
                     f"outright: comic juxtaposition, where a game action is dressed in an incongruous "
                     f"everyday domain — offices, paperwork, unions, departments, HR, liability, "
                     f"industrial accidents, clearances, gym routines, traffic, school, customer "
                     f"service. \"Deeply illegal cardio\" and \"the dinosaur union is filing "
                     f"paperwork\" are both the same joke and both forbidden. Whatever nouns it wears, "
                     f"that is the default register you are replacing: say what you mean, in "
                     f"character, about the actual game. Politics happens in this voice too — when you "
                     f"cut a deal, deflect a threat or beg for a turn, do it as this character, not by "
                     f"switching into a neutral diplomat — a truce offer sounds like this character "
                     f"offering a truce. Your voice is yours alone: never "
                     f"echo another seat's phrasing, sentence shape, running joke or diction, however "
                     f"fresh it is in the log. If someone else just said \"little tree now\", that "
                     f"construction is now off limits to you, and another player's dialect is theirs "
                     f"alone — when the seat across the table says \"ain't\", you still don't. Don't narrate your own plays either — the log "
                     f"already shows what you did, and \"just a land, nothing yet\" is not worth "
                     f"saying. Talk to the people instead — react to what they just did, needle them, "
                     f"answer them, make offers, threaten. You are a player at a table, not a "
                     f"spectator: expect to say something most turns, and keep it short.\n" if me.personality else "")
                  + (("\nTHE TABLE (what everyone knows about these decks):\n"
                      + "\n".join(f"  {pl.handle} {pl.deckname}: {pl.scouting}"
                                  for pl in self.p if pl.alive and pl.scouting) + "\n")
                     if any(pl.scouting for pl in self.p if pl.alive) else "")
                  + f"\n{PROTOCOL}\n\n"
                  f"=== GAME STATE ===\n{self.view(i)}\n\n=== INSTRUCTION ===\n{instruction}\n"
                  + (f"Schema: {schema_hint}\n" if schema_hint else "")
                  + (f"Stay in voice — you are method acting as: {me.personality}\n"
                     if me.personality else ""))
        self.force_full[i] = False
        self.log_sent[i] = len(self.table)
        self.oracle_shown[i].update(
            n for n in self.p[i].hand + [x["name"] for pl in self.p for x in pl.battlefield]
            if n in self.db)
        raw = self.agents[i].ask(prompt)
        self._watch_for_a_dead_seat(i)
        return self._parse_reply(i, raw)

    JSON_TRIES = 3          # an unreadable reply goes back to the seat, not to the floor

    def _parse_reply(self, i, raw, tries=0):
        me = self.p[i]
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            obj = json.loads(m.group(0)) if m else None
            if obj is None:
                raise ValueError("no JSON object in reply")
        except Exception as e:
            if tries + 1 >= self.JSON_TRIES:
                self.log(f"  !!! {me.name}: {self.JSON_TRIES} unreadable replies in a row "
                         f"({e}) — the seat loses this decision")
                return {"action": "pass"}
            self.log(f"  !! {me.name}: unreadable reply ({e}) — sent back "
                     f"[{tries + 1}/{self.JSON_TRIES - 1}]")
            return self._parse_reply(i, self.agents[i].ask(
                f"Your last reply could not be read: {e}. Send that same decision again as "
                f"exactly one valid JSON object — same action, same effects, balanced braces "
                f"— and nothing outside the object."), tries + 1)
        thinking = obj.pop("thinking", None)
        if thinking:                       # spectator-visible, table-invisible
            self.log_private(f'{me.name} thinks: "{thinking}"', seat=me.handle)
        talk = obj.pop("table_talk", None)
        if talk:
            # speech lands AFTER the play it accompanies, like a real table
            self.pending_talk.append(f'{me.name} says: "{talk}"')
        if obj.get("action") in (None, "pass"):   # no play follows — speak now
            self._flush_talk()
        return obj

    def _fresh_narration(self, says):
        """The narration to print now: the announcement already told the table the plan,
        so resolution speaks only when it has something new to say."""
        if not says:
            return None
        return None if _norm_narration(says) in self.narrated else says

    def _flush_talk(self):
        while self.pending_talk:
            self.log(self.pending_talk.pop(0))

    def view(self, i):
        me = self.p[i]

        def bf(pl):
            out = []
            for x in pl.battlefield:
                pt = f" {x['pt'][0]+plus_counters(x)}/{x['pt'][1]+plus_counters(x)}" if x["pt"] else ""
                flags = []
                if x["tapped"]: flags.append("tapped")
                if x["sick"]: flags.append("summoning-sick")
                for k, v in _counters(x).items(): flags.append(f"{k} x{v}")
                if x.get("damage"): flags.append(f"{x['damage']} damage")
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
            cz = "; ".join(
                f"{c} (" + (f"in command zone (tax +{pl.commander_tax[c]})"
                            if pl.command_zone[c] else "*not* in command zone") + ")"
                for c in pl.commanders)
            seats.append(f"  {pl.handle} {pl.name}{' <- YOU' if pl is me else ''} — life {pl.life}, "
                         f"hand {len(pl.hand)}, drew {pl.drew_this_turn} this turn — "
                         f"commander{'s' if len(pl.commanders) > 1 else ''} {cz}")
        boards = "\n".join(
            f"{pl.handle} {pl.name}{' (YOU)' if pl is me else ''} BATTLEFIELD:\n{bf(pl)}"
            for pl in self.p if pl.alive)
        relevant = me.hand + [x["name"] for pl in self.p for x in pl.battlefield] \
                   + [c for pl in self.p if pl.alive for c in pl.commanders] + me.graveyard
        graves = "\n".join(f"  {pl.handle}: {', '.join(pl.graveyard) or '(empty)'}"
                           for pl in self.p if pl.alive)
        tail = "\n".join(self.table[-self.log_tail:])
        return f"""TURN {self.turn}. You are {me.name} (seat {me.handle}). SEATS in turn order:
{chr(10).join(seats)}
YOUR HAND ({len(me.hand)}): {'; '.join(me.hand)}
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
            if not self._can_respond(pl, j):
                self.log_private(f"({pl.name} holds nothing playable at instant speed — "
                                 f"no window)", seat=pl.handle)
                continue
            r = self.ask(j,
                f"RESPONSE WINDOW: {context}. You may cast an instant/flash or activate an "
                f"instant-speed ability (it goes on the stack and can be responded to), or pass.",
                schema_hint='{"action":"cast"|"activate"|"pass", "card":str, "source":str, '
                            '"tap":[ids], "targets":[ids], "effects":[...], "narration":str}')
            if r.get("action") in ("cast", "activate"):
                self.resolve_on_stack(j, r, kind="spell" if r.get("action") == "cast" else "ability")
            elif r.get("action") == "correct":
                self.do_action(j, r)

    def _can_respond(self, pl, j=None):
        """Could this seat conceivably act at instant speed? Errs open — the
        agent judges payability (alternative costs, phyrexian mana, rituals).
        No untapped-mana requirement: free spells exist. Battlefield counts if
        any permanent has a non-mana activated ability, tapped or not (sac
        outlets don't tap). A human seat is always asked: the call is free and
        they may want to talk or repair the board. A skip is noted in the
        private channel — the table never learns what a seat was holding."""
        if j is not None and type(self.agents[j]).__name__ == "HumanAgent":
            return True
        for c in pl.hand:
            d = self.db.get(c, {})
            if "Instant" in d.get("type", "") or "Flash" in d.get("text", ""):
                return True
        for x in pl.battlefield:
            text = self.db.get(x["name"], {}).get("text", "")
            parts = text.split(":")
            for k in range(1, len(parts)):
                cost, effect = parts[k - 1], parts[k].lstrip()
                if not effect.startswith("Add"):
                    return True        # an ability that does something
                if "acrifice" in cost:
                    return True        # mana, but the cost eats a permanent —
                                       # "in response I sac it to the Altar"
        return False

    def half_turn(self, i):
        me = self.p[i]
        self.active = i
        if not me.alive:
            return
        me.lands_played = 0
        self.board_full[i] = True                 # full board read at own turn start
        for pl in self.p:
            pl.drew_this_turn = 0
            pl.spells_this_turn = 0
            pl.copies_this_turn = 0
        for pl in self.p:                     # damage wears off at end of turn
            for x in pl.battlefield:
                x["damage"] = 0
        untapped = 0
        for x in me.battlefield:
            stun = _counters(x).get("stun", 0)
            skip = int(x.get("skip_untaps", 0))
            if x["tapped"] and (stun or skip):
                if stun:                       # a real counter on the board comes off
                    c = _counters(x); c["stun"] = stun - 1
                    x["counters"] = {k: v for k, v in c.items() if v}
                    why = "a stun counter comes off instead"
                else:                          # exerted, frozen, whatever the seat called it
                    x["skip_untaps"] = skip - 1
                    why = "it skips this untap step"
                left = (stun or skip) - 1
                self.log(f"  ({x['id']} stays tapped — {why}"
                         + (f", {left} more" if left else "") + ")")
            else:
                if x["tapped"]:
                    untapped += 1
                x["tapped"] = False
            x["sick"] = False
        if untapped:
            self.log(f"  ({me.name} untaps {untapped} permanent{'s' if untapped != 1 else ''}.)")
        self.narrated.clear()
        self.log(f"\n## Turn {self.turn} — {me.name} — life: " +
                 ", ".join(f"{pl.handle} {pl.life}" for pl in self.p if pl.alive))
        upkeep = self.ask(i, "UPKEEP — your turn has begun and you have not drawn yet. Declare "
                             "everything that triggers at the beginning of your upkeep or your draw "
                             "step (Braids, Howling Mine, Font of Mythos, Phyrexian Arena, cumulative "
                             "upkeep, vanishing, sagas, your commander's beginning-of-turn ability...) "
                             "as effect atoms, or pass. Your normal draw for the turn happens after "
                             "this and the engine takes it — declare only the extra ones.",
                          schema_hint='{"action":"pass"|"activate","effects":[...],"narration":str}')
        if upkeep.get("action") != "pass":
            self.apply_effects(i, upkeep.get("effects"))
        if not me.alive:
            return
        if not (self.turn == 1 and i == 0 and len(self.p) == 2):
            self.draw(me, 1)  # CR 103.8: only 2-player pods skip the first draw
            self.check_standing("draw", i, 1)
        # main phase: up to N sequential decisions
        for _step in range(self.max_actions):
            if not me.alive:
                return
            plan = self.ask(i,
                "It is your MAIN PHASE (pre- or post-combat as you prefer; the engine doesn't distinguish — "
                "sequence responsibly). Give one action per protocol: play_land, cast, activate, attack, "
                "claim_win, or pass (pass ends your turn). For cast/activate: name every permanent you tap "
                "for mana in \"tap\" and declare every consequence as effect atoms. "
                "For attack you may split attackers among players; only untapped, non-sick (or haste-granted, "
                "justify in narration) creatures; attacking taps them unless vigilance (use set to untap). "
                "One attack step per turn unless an effect grants extra combats (Aurelia, Aggravated "
                "Assault...) — untap your attackers via set atoms and justify in narration. Declare "
                "attack triggers (tokens from attacking, Karazikar draws...) in the attack action" + chr(39) + "s "
                "own effects — they apply at declare time.",
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
                    f'{{"action":"order","top":[...],"bottom":[...],"take":[...],"to":"hand"}} '
                    f"using those names. top becomes the new top order, bottom goes to the bottom "
                    f"in order, and take pulls cards out of the library into the zone named by to "
                    f"(hand, exile, graveyard or battlefield) — that is how "
                    f"\"put the revealed cards into your hand\" and hideaway are declared. "
                    f"Account for every card you were shown. Or pass to leave the library untouched.",
                    schema_hint='{"action":"order"|"pass","top":[names],"bottom":[names],'
                                '"take":[names],"to":str,"effects":[...]}')
                if r.get("action") == "order":
                    newtop = list(r.get("top") or [])
                    newbot = list(r.get("bottom") or [])
                    taken = list(r.get("take") or [])
                    dest = r.get("to", "hand")
                    pool, stray = list(top), []
                    for c in newtop + newbot + taken:
                        if c in pool:
                            pool.remove(c)
                        else:
                            stray.append(c)
                    if stray:
                        self.log(f"  !! order names cards you weren't shown "
                                 f"({', '.join(stray)}); library unchanged")
                    else:
                        del me.library[:n]
                        for c in taken:
                            self._zone_put(me, c, dest)
                        me.library[:0] = newtop + pool
                        me.library.extend(newbot)
                        if taken:
                            self.log(f"  ↳ {me.name} takes {len(taken)} to {dest}"
                                     + (f" ({', '.join(taken)})" if dest != "hand" else ""))
                        if newbot:
                            self.log(f"  ↳ {me.name} puts {len(newbot)} on the bottom.")
                        if pool:
                            self.log(f"  ↳ {len(pool)} unaccounted for, left on top.")
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
            if self.do_action(i, plan) == "pass":
                break
        else:
            self.log(f"  !! {me.name} hit the action cap ({self.max_actions}/turn) — declare any "
                     f"unresolved triggers at your end step or next window; the table should hold "
                     f"them to it. A repeated activation belongs in one action with a count, not "
                     f"one action per iteration.")
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

    SHAPE = ('{"action":"attack","attacks":{"P2":["Bear#3"],"P4":["Wolf#7"]}} '
             '— a defender per group')

    def combat(self, i, plan):
        me = self.p[i]
        attacks = plan.get("attacks")
        if isinstance(attacks, list):
            # a list of groups, each naming its own defender
            spread = {}
            for grp in attacks:
                if not isinstance(grp, dict):
                    spread = None
                    break
                who = grp.get("defender") or grp.get("player") or grp.get("target")
                ids = grp.get("with") or grp.get("attackers") or grp.get("ids") or []
                if not who:
                    spread = None
                    break
                spread.setdefault(str(who), []).extend(ids if isinstance(ids, list) else [ids])
            attacks = spread
            if attacks is None:
                self.log(f"  !! {me.name}: attacks must say who each group is hitting — {self.SHAPE}")
                return
        if attacks and not isinstance(attacks, dict):
            self.log(f"  !! {me.name}: attacks must say who each group is hitting — {self.SHAPE}")
            return
        if not attacks and plan.get("attackers"):
            others = [p for p in self.others(i) if p.alive]
            defender = plan.get("defender") or plan.get("target")
            if defender:
                attacks = {str(defender): plan["attackers"]}
            elif len(others) == 1:
                attacks = {others[0].handle: plan["attackers"]}
            else:
                self.log(f"  !! {me.name} attacked without naming a defender, and "
                         f"{len(others)} seats are alive — say who each attacker is hitting: "
                         f"{self.SHAPE}")
                return
        if not attacks:
            return
        assault = []  # (defender_player, [perm, ...])
        vig = plan.get("vigilance")
        vig_all = vig is True
        vig_ids = set() if vig_all else {str(v) for v in (vig or [])}
        for ref, ids in attacks.items():
            dfd = self.resolve_player(i, ref)
            if not dfd or dfd is me or not dfd.alive:
                self.log(f"  !! attack on unresolvable/dead seat {ref!r}; ignored")
                continue
            atk = []
            for ident in ids:
                _, perm = self.find(ident, prefer=i)
                if perm and not perm["tapped"]:
                    if not (vig_all or perm["id"] in vig_ids or str(ident) in vig_ids):
                        perm["tapped"] = True
                    atk.append(perm)
            if atk:
                assault.append((dfd, atk))
                self.log(f"{me.name} attacks {dfd.name} with: {[x['id'] for x in atk]}")
        if not assault:
            return
        # attack triggers (Squirrel Girl's token, Karazikar's goad-and-draw...)
        # live in the attack action's own effects — apply them at declare time
        if plan.get("effects"):
            self.apply_effects(i, plan.get("effects"))
        # declare-attackers priority: anyone (defender or bystander) may act
        # before blocks — pre-block removal, fogs, political rescues
        self.response_windows(i, f"{me.name} has declared attackers (see above) — "
                                 f"window before blocks are declared")
        self._trick_window(i, "your attackers are declared, responses (if any) have resolved, "
                              "and blocks are about to be chosen. Pre-block pumps and protection "
                              "change what dares to block.")
        for dfd, atk in assault:
            j = self.p.index(dfd)
            blocks = self.ask(j,
                f"COMBAT: {me.name} attacks YOU with {[x['id'] for x in atk]}. Declare blocks "
                f"(only your untapped creatures; respect any 'can't block' effects) or none.",
                schema_hint='{"action":"block","blocks":{attacker_id:[blocker_ids]},"narration":str}')
            self.log(f"{dfd.name} blocks: {blocks.get('blocks', {})} — {blocks.get('narration','')}")
        # one trick window each: defenders first (turn order), attacker last.
        # each opens the real stack — chain pumps by responding to your own spell
        defenders = [dfd for dfd, _ in assault]
        for pl in self.others(i):
            if pl in defenders:
                self._trick_window(self.p.index(pl), "blocks are declared; combat tricks/removal window.")
        self._trick_window(i, "blocks are declared — your combat trick window.")
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
        if not result.get("effects") and not result.get("narration"):
            self.log(f"  !! {me.name} declared no combat consequences and gave no explanation "
                     f"— if damage happened, repair via a correct action; the table should check")

    def _trick_window(self, i, context):
        """An instant-speed offer that opens the real stack. Anything cast here
        goes through resolve_on_stack, so the caster can respond to their own
        spell when priority comes back around (that's holding priority) and
        chain pumps — the stack grows, counter wars included."""
        if not self._can_respond(self.p[i]):
            return
        r = self.ask(i,
            f"RESPONSE WINDOW: {context} You may cast an instant/flash — it goes on the "
            f"stack, and to chain more spells (multiple pumps...), respond to your *own* "
            f"spell when the response window comes back around: that is holding priority "
            f"— or pass. If something of yours TRIGGERS off this rather than being cast — "
            f"Kambal, Blood Artist, a tax — pass and put the trigger in \"effects\".",
            schema_hint='{"action":"cast"|"pass", "card":str, "tap":[ids], "targets":[ids], "effects":[...]}')
        if r.get("action") == "cast":
            self.resolve_on_stack(i, r, kind="spell")
        elif r.get("action") in ("correct", "activate", "play_land"):
            self.do_action(i, r)
        elif r.get("effects"):
            # passing with atoms is how a trigger fires here — Kambal, Blood
            # Artist, anything that answers what just happened without casting
            self.apply_effects(i, r["effects"])

    def mulligans(self):
        """Opening hands. Commander house rules: first mulligan free, then
        London-style — draw 7, put one extra card on the bottom per mull."""
        for i, pl in enumerate(self.p):
            lands = sum(1 for c in pl.decklist if "Land" in self.db.get(c, {}).get("type", ""))
            producers = sum(1 for c in pl.decklist
                            if "Land" not in self.db.get(c, {}).get("type", "")
                            and makes_mana(self.db.get(c, {}).get("text", "")))
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

    def run(self, from_turn=1, from_seat=0):
        """Play from (from_turn, from_seat) to the end. The defaults play a
        fresh game, mulligans included; restore_game() passes the half-turn
        after its save point."""
        try:
            if from_turn == 1 and from_seat == 0:
                self.mulligans()
            for self.turn in range(from_turn, self.max_turns + 1):
                for i in range(len(self.p)):
                    if self.turn == from_turn and i < from_seat:
                        continue
                    self.half_turn(i)
            standings = sorted((pl for pl in self.p if pl.alive), key=lambda p: -p.life)
            self.log(f"\n**Turn cap {self.max_turns} reached. Standings: " +
                     ", ".join(f"{pl.name} {pl.life}" for pl in standings) +
                     ". Highest life is the moral victor.**")
        except GameOver as g:
            if g.winner is None:
                self.log(f"\n**GAME OVER: DRAW on turn {self.turn} — {g.how}. "
                         f"Nobody wins.**")
            else:
                self.log(f"\n**GAME OVER: {self.p[g.winner].name} WINS ({g.how}) on turn {self.turn}.**")
        finally:
            self.logf.close()
            self.eventsf.close()
