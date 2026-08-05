#!/usr/bin/env python3
"""Generate games/demo.md(.events.jsonl) — a scripted mini-game exercising
every renderer animation: land drops, casts, tokens, taps, combat, life
swings, a wheel, thinking lines, table talk, judge, elimination."""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgsim.cards import load_db, load_deck  # noqa: E402
from mtgsim.engine import Game, GameOver     # noqa: E402


class Silent:
    calls, cost_usd, tokens = 0, 0.0, {"in": 0, "out": 0}
    def ask(self, prompt):
        return '{"action":"pass"}'


db = load_db()
rng = random.Random(7)
decks = [(n, *load_deck(n, db)) for n in ("snakes", "squirrels", "braids", "lifedrain")]
(ROOT / "games").mkdir(exist_ok=True)
g = Game(db, decks, [Silent() for _ in range(4)], 7, str(ROOT / "games" / "demo.md"), 9, rng)

# hand-pick opening hands so the screenplay works
g.p[0].hand = ["Forest", "Island", "Mountain", "Sol Ring", "Xyris, the Writhing Storm", "Lightning Bolt", "Counterspell"]
g.p[1].hand = ["Forest", "Forest", "Elvish Mystic", "Squirrel Nest", "Chatterstorm", "Overrun", "Second Harvest"]
g.p[2].hand = ["Island", "Island", "Sol Ring", "Kira, Great Glass-Spinner", "Blightsteel Colossus", "Temple Bell", "Howling Mine"]
g.p[3].hand = ["Plains", "Swamp", "City of Brass", "Kambal, Consul of Allocation", "Kokusho, the Evening Star", "Exsanguinate", "Blood Artist"]

try:
    # --- turn 1: lands + talk
    g.turn = 1
    g.log("\n## Turn 1 — P1(snakes) — life: P1 40, P2 40, P3 40, P4 40")
    g.log_private('P1(snakes) thinks: "Everyone looks friendly. Nobody is friendly."')
    g.log('P1(snakes) says: "Good luck all. May your draws be mediocre."')
    g.do_action(0, {"action": "play_land", "card": "Forest"})
    g.log("\n## Turn 1 — P2(squirrels) — life: P1 40, P2 40, P3 40, P4 40")
    g.do_action(1, {"action": "play_land", "card": "Forest"})
    g.log('P2(squirrels) says: "One forest. The squirrels are patient."')
    g.log("\n## Turn 1 — P3(braids) — life: P1 40, P2 40, P3 40, P4 40")
    g.do_action(2, {"action": "play_land", "card": "Island"})
    g.log_private('P3(braids) thinks: "Twelve-drops in hand. Braids is the only way they ever see play."')
    g.log("\n## Turn 1 — P4(lifedrain) — life: P1 40, P2 40, P3 40, P4 40")
    g.do_action(3, {"action": "play_land", "card": "Plains"})
    g.log('P4(lifedrain) says: "The Plains remains innocent until proven otherwise."')

    # --- turn 2: rocks, dorks, first taps
    g.turn = 2
    g.log("\n## Turn 2 — P1(snakes) — life: P1 40, P2 40, P3 40, P4 40")
    g.draw(g.p[0], 1)
    g.do_action(0, {"action": "play_land", "card": "Island"})
    g.log("P1(snakes) announces Sol Ring...")
    g.do_action(0, {"action": "cast", "card": "Sol Ring", "tap": ["Forest#1"],
                    "narration": "Turn-two Ring. Apologies to the table."})
    g.log("\n## Turn 2 — P2(squirrels) — life: P1 40, P2 40, P3 40, P4 40")
    g.draw(g.p[1], 1)
    g.do_action(1, {"action": "play_land", "card": "Forest"})
    g.do_action(1, {"action": "cast", "card": "Elvish Mystic", "tap": ["Forest#2"]})
    g.log_private('P2(squirrels) thinks: "Mystic now, Nest next, then the storm."')
    g.log("\n## Turn 2 — P3(braids) — life: P1 40, P2 40, P3 40, P4 40")
    g.draw(g.p[2], 1)
    g.do_action(2, {"action": "play_land", "card": "Island"})
    g.do_action(2, {"action": "cast", "card": "Sol Ring", "tap": ["Island#5"]})
    g.log("\n## Turn 2 — P4(lifedrain) — life: P1 40, P2 40, P3 40, P4 40")
    g.draw(g.p[3], 1)
    g.do_action(3, {"action": "play_land", "card": "Swamp"})
    g.log('P4(lifedrain) says: "Infrastructure. Deeply boring."')

    # --- turn 3: commanders land
    g.turn = 3
    g.log("\n## Turn 3 — P2(squirrels) — life: P1 40, P2 40, P3 40, P4 40")
    g.draw(g.p[1], 1)
    g.do_action(1, {"action": "cast", "card": "Squirrel Nest", "tap": ["Forest#6", "Elvish Mystic#7"],
                    "effects": [{"note": "Nest enchants Forest#2: it gains 'T: create a 1/1 Squirrel.'"}]})
    g.log("\n## Turn 3 — P4(lifedrain) — life: P1 40, P2 40, P3 40, P4 40")
    g.draw(g.p[3], 1)
    g.do_action(3, {"action": "play_land", "card": "City of Brass"})
    g.log("P4(lifedrain) announces Kambal, Consul of Allocation...")
    g.do_action(3, {"action": "cast", "card": "Kambal, Consul of Allocation",
                    "tap": ["Plains#4", "Swamp#9", "City of Brass#10"],
                    "narration": "The auditor takes office."})
    g.log('P4(lifedrain) says: "Kambal is seated. Noncreature spells now carry a two-life filing fee."')
    g.log_private('P1(snakes) thinks: "There goes my whole deck\'s margin."')

    # --- turn 4: xyris + tax + tokens
    g.turn = 4
    g.log("\n## Turn 4 — P1(snakes) — life: P1 40, P2 40, P3 40, P4 40")
    g.draw(g.p[0], 1)
    g.do_action(0, {"action": "play_land", "card": "Mountain"})
    g.log("P1(snakes) announces Xyris, the Writhing Storm...")
    g.do_action(0, {"action": "cast", "card": "Xyris, the Writhing Storm",
                    "tap": ["Island#3", "Mountain#12", "Forest#1", "Sol Ring#4"],
                    "narration": "The storm arrives."})
    g.log('P1(snakes) says: "Xyris online. Every extra card you draw is a snake I keep."')
    # squirrel activation + chatterstorm with kambal tax
    g.log("\n## Turn 4 — P2(squirrels) — life: P1 40, P2 40, P3 40, P4 40")
    g.draw(g.p[1], 1)
    g.do_action(1, {"action": "activate", "source": "Forest#2", "tap_source": True,
                    "effects": [{"create": {"player": "self", "name": "Squirrel", "n": 1, "pt": [1, 1]}}],
                    "narration": "Nest makes its first squirrel."})
    g.do_action(1, {"action": "cast", "card": "Chatterstorm", "tap": ["Forest#6", "Elvish Mystic#7"],
                    "effects": [{"create": {"player": "self", "name": "Squirrel", "n": 2, "pt": [1, 1]}},
                                {"life": {"player": "self", "delta": -2}},
                                {"life": {"player": "P4", "delta": 2}},
                                {"note": "Kambal taxes the sorcery; storm count 1 makes two squirrels total."}]})
    g.log('P2(squirrels) says: "Two life to Kambal. The union absorbs the fee."')

    # --- turn 5: the wheel — snakes everywhere
    g.turn = 5
    g.log("\n## Turn 5 — P1(snakes) — life: P1 40, P2 38, P3 40, P4 42")
    g.draw(g.p[0], 1)
    g.log("P1(snakes) announces Winds of Change...")
    g.log_private('P1(snakes) thinks: "Wheel with Xyris out. This is the whole plan."')
    g.do_action(0, {"action": "cast", "card": "Lightning Bolt", "tap": ["Mountain#12"],
                    "targets": ["Elvish Mystic#7"],
                    "effects": [{"move": {"id": "Elvish Mystic#7", "to": "graveyard"}}],
                    "narration": "Bolt the dork first."})
    g.apply_effects(0, [{"life": {"player": "self", "delta": -2}}, {"life": {"player": "P4", "delta": 2}},
                        {"note": "Kambal taxes the Bolt."}])
    g.apply_effects(0, [
        {"move": {"player": "P1", "from": "hand", "all": True, "to": "library_top"}},
        {"shuffle": {"player": "P1"}}, {"draw": {"player": "P1", "n": 3}},
        {"move": {"player": "P2", "from": "hand", "all": True, "to": "library_top"}},
        {"shuffle": {"player": "P2"}}, {"draw": {"player": "P2", "n": 4}},
        {"create": {"player": "self", "name": "Snake", "n": 4, "pt": [1, 1]}},
        {"note": "Xyris: four snakes from the wheel's extra draws."}])
    g.log('P1(snakes) says: "Everyone gets new cards. I get new friends."')
    g.log('⚖ JUDGE: nice wheel. play nice with those snakes.')

    # --- turn 6: combat + kokusho drain + elimination drama
    g.turn = 6
    g.log("\n## Turn 6 — P2(squirrels) — life: P1 38, P2 38, P3 40, P4 44")
    g.draw(g.p[1], 1)
    g.do_action(1, {"action": "attack", "attacks": {"P4": ["Squirrel#13", "Squirrel#14", "Squirrel#15"]}})
    g.log('P4(lifedrain) says: "The audit found three squirrels. Approved, reluctantly."')
    g.apply_effects(1, [{"life": {"player": "P4", "delta": -3}},
                        {"note": "Three unblocked squirrels connect."}])
    g.log("\n## Turn 6 — P4(lifedrain) — life: P1 38, P2 38, P3 40, P4 41")
    g.draw(g.p[3], 1)
    g.log("P4(lifedrain) announces Kokusho, the Evening Star...")
    g.do_action(3, {"action": "cast", "card": "Kokusho, the Evening Star",
                    "tap": ["Swamp#9", "Plains#4", "City of Brass#10", "Kambal, Consul of Allocation#11"],
                    "narration": "The dragon clocks in."})
    g.log_private('P4(lifedrain) thinks: "Nobody at this table can afford to kill this dragon. Or ignore it."')
    g.log('P1(snakes) says: "That dragon is a five-point drain with wings. Noted."')

    # --- turn 7: exsanguinate finisher; P2 eliminated
    g.turn = 7
    g.log("\n## Turn 7 — P4(lifedrain) — life: P1 38, P2 38, P3 40, P4 41")
    g.draw(g.p[3], 1)
    g.log("P4(lifedrain) announces Exsanguinate for X=13...")
    g.log_private('P4(lifedrain) thinks: "The books close today."')
    g.do_action(3, {"action": "cast", "card": "Exsanguinate",
                    "tap": ["Swamp#9", "Plains#4", "City of Brass#10"],
                    "effects": [{"life": {"player": "P1", "delta": -13}},
                                {"life": {"player": "P2", "delta": -38}},
                                {"life": {"player": "P3", "delta": -13}},
                                {"life": {"player": "self", "delta": 25}}],
                    "narration": "Each opponent loses thirteen; the accountant collects."})
    g.log('P4(lifedrain) says: "Final invoice delivered."')
except GameOver as go:
    g.log(f"\n**GAME OVER: {g.p[go.winner].name} WINS ({go.how}) on turn {g.turn}.**")

g.logf.close()
g.eventsf.close()
import json
n = sum(1 for _ in open(str(ROOT / "games" / "demo.md") + ".events.jsonl"))
print(f"demo game written: games/demo.md ({n} events)")
