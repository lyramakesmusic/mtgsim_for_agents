"""The stack: counters, fizzles, wheels, activated-ability windows."""
from conftest import StubAgent

def test_countered_commander_returns_to_cz(make_game, db):
    class Counterer:
        calls, cost_usd, tokens = 0, 0.0, {"in": 0, "out": 0}
        def __init__(self):
            self.fired = False
        def ask(self, prompt):
            if "RESPONSE WINDOW" in prompt and not self.fired:
                self.fired = True
                return ('{"action":"cast","card":"Counterspell","tap":[],'
                        '"effects":[{"counter_spell":true}]}')
            return '{"action":"pass"}'

    g = make_game()
    me = g.p[0]
    counterer = Counterer()
    g.agents[1] = counterer
    g.p[1].hand = ["Counterspell"] * 7
    g.p[1].battlefield.append({"id": "Island#999", "name": "Island", "tapped": False,
                               "sick": False, "counters": 0, "token": False, "pt": None})

    class Caster:
        calls, cost_usd, tokens = 0, 0.0, {"in": 0, "out": 0}
        def __init__(self):
            self.step = 0
        def ask(self, prompt):
            self.step += 1
            if "MAIN PHASE" in prompt and self.step == 1:
                return f'{{"action":"cast","card":"{me.commander}","tap":[]}}'
            return '{"action":"pass"}'

    g.agents[0] = Caster()
    g.turn = 2
    g.half_turn(0)
    assert me.command_zone and me.commander_tax == 2
    assert me.commander not in me.graveyard

def test_cast_fizzles_when_all_targets_gone(make_game):
    g = make_game()
    me = g.p[0]
    me.hand.append("Lightning Bolt")
    g.do_action(0, {"action": "cast", "card": "Lightning Bolt",
                    "targets": ["Ghost#999"],
                    "effects": [{"create": {"player": "self", "name": "ShouldNotExist", "n": 1, "pt": [1, 1]}}]})
    assert "Lightning Bolt" in me.graveyard
    assert not any(x["name"] == "ShouldNotExist" for x in me.battlefield)
    assert any("FIZZLES" in line for line in g.table)

def test_move_all_empties_hand(make_game):
    g = make_game()
    me = g.p[2]
    n_hand, n_lib = len(me.hand), len(me.library)
    g.apply_effects(2, [{"move": {"player": "self", "from": "hand", "all": True, "to": "library_top"}},
                        {"shuffle": {"player": "self"}},
                        {"draw": {"player": "self", "n": n_hand}}])
    assert len(me.hand) == n_hand and len(me.library) == n_lib

def test_substantive_activation_gets_response_window(make_game):
    seen = {}
    class Watcher:
        calls, cost_usd, tokens = 0, 0.0, {"in": 0, "out": 0}
        def ask(self, prompt):
            if "RESPONSE WINDOW" in prompt and "activating" in prompt:
                seen["window"] = True
            return '{"action":"pass"}'

    class Activator:
        calls, cost_usd, tokens = 0, 0.0, {"in": 0, "out": 0}
        def __init__(self):
            self.n = 0
        def ask(self, prompt):
            self.n += 1
            if "MAIN PHASE" in prompt and self.n == 1:
                return ('{"action":"activate","source":"X#1","tap_source":true,'
                        '"effects":[{"create":{"player":"self","name":"Squirrel","n":4,"pt":[1,1]}}]}')
            return '{"action":"pass"}'

    g = make_game()
    g.agents[0] = Activator()
    g.agents[1] = Watcher()
    g.p[1].hand = ["Counterspell"] * 3
    g.p[1].battlefield.append({"id": "Island#900", "name": "Island", "tapped": False,
                               "sick": False, "counters": 0, "token": False, "pt": None})
    g.turn = 3
    g.half_turn(0)
    assert seen.get("window"), "activation with token creation should open a response window"


def test_tapped_out_free_tricks_get_windows(make_game):
    """Mutagenic Growth costs 2 life, not mana — tapped out is not silenced."""
    g = make_game()
    pl = g.p[1]
    pl.hand = ["Mutagenic Growth"]
    pl.battlefield = [{"id": "Forest#900", "name": "Forest", "tapped": True,
                       "sick": False, "counters": 0, "token": False, "pt": None}]
    assert g._can_respond(pl)
    pl.hand = ["Forest"]                      # no instants, only a tapped basic
    assert not g._can_respond(pl)
    # sac outlet counts even while tapped; lone Sol Ring (mana-only) doesn't
    pl.battlefield = [{"id": "Viscera Seer#901", "name": "Viscera Seer", "tapped": True,
                       "sick": False, "counters": 0, "token": False, "pt": [1, 1]}]
    assert g._can_respond(pl)
    pl.battlefield = [{"id": "Sol Ring#902", "name": "Sol Ring", "tapped": False,
                       "sick": False, "counters": 0, "token": False, "pt": None}]
    assert not g._can_respond(pl)


def test_graveyard_targets_do_not_false_fizzle(make_game):
    """Reanimate-style: name-only targets aren't the engine's to fizzle."""
    g = make_game()
    me = g.p[0]
    me.graveyard.append("Mikaeus, the Unhallowed")
    me.hand.append("Reanimate")
    g.do_action(0, {"action": "cast", "card": "Reanimate",
                    "targets": ["Mikaeus, the Unhallowed"],
                    "effects": [{"move": {"player": "self", "from": "graveyard",
                                          "card": "Mikaeus, the Unhallowed", "to": "battlefield"}},
                                {"life": {"player": "self", "delta": -6}}]})
    assert any(x["name"] == "Mikaeus, the Unhallowed" for x in me.battlefield)
    assert not any("FIZZLES" in line for line in g.table)


def test_counter_war_two_deep(make_game):
    """A casts, B counters, A counter-counters: LIFO unwinds, original resolves."""
    class P1Agent(StubAgent):
        def __init__(self):
            super().__init__()
            self.cast_main = False
            self.countered_back = False
        def ask(self, prompt):
            if "MAIN PHASE" in prompt and not self.cast_main:
                self.cast_main = True
                return ('{"action":"cast","card":"Harmonize",'
                        '"effects":[{"draw":{"player":"self","n":3}}]}')
            if "RESPONSE WINDOW" in prompt and "stack#2" in prompt and not self.countered_back:
                self.countered_back = True
                return ('{"action":"cast","card":"Counterspell",'
                        '"effects":[{"counter":{"target":"stack#2"}}]}')
            if "final resolution" in prompt:
                return '{"action":"cast"}'
            return '{"action":"pass"}'

    class P2Agent(StubAgent):
        def __init__(self):
            super().__init__()
            self.fired = False
        def ask(self, prompt):
            if "RESPONSE WINDOW" in prompt and "casting Harmonize" in prompt and not self.fired:
                self.fired = True
                return ('{"action":"cast","card":"Counterspell",'
                        '"effects":[{"counter":{"target":"stack#1"}}]}')
            return '{"action":"pass"}'

    g = make_game()
    g.agents[0], g.agents[1] = P1Agent(), P2Agent()
    g.p[0].hand = ["Harmonize", "Counterspell"]
    g.p[1].hand = ["Counterspell"]
    hand_before = 0  # after casting both, P1 draws 3 from Harmonize
    g.turn = 2
    g.half_turn(0)
    joined = "\n".join(g.table)
    assert "stack#2 Counterspell is countered by Counterspell" in joined
    assert "Harmonize is COUNTERED" not in joined
    assert "Harmonize" in g.p[0].graveyard          # resolved, went to yard
    assert "Counterspell" in g.p[0].graveyard       # the counter-counter
    assert "Counterspell" in g.p[1].graveyard       # the countered counter
    assert len(g.p[0].hand) >= 3                    # Harmonize's draws happened


def test_split_second_skips_windows(make_game):
    windows = []
    class Watcher(StubAgent):
        def ask(self, prompt):
            if "RESPONSE WINDOW" in prompt:
                windows.append(prompt)
            return '{"action":"pass"}'

    class Caster(StubAgent):
        def __init__(self):
            super().__init__()
            self.done = False
        def ask(self, prompt):
            if "MAIN PHASE" in prompt and not self.done:
                self.done = True
                return '{"action":"cast","card":"Krosan Grip","split_second":true,"effects":[]}'
            return '{"action":"pass"}'

    g = make_game()
    g.agents[0] = Caster()
    g.agents[1] = Watcher()
    g.p[0].hand = ["Krosan Grip"]
    g.p[1].hand = ["Counterspell"]
    g.p[0].graveyard.append("Krosan Grip") if False else None
    # ensure Krosan Grip is in the db via any sidecar (sigarda has it locally);
    # if absent it still resolves as a nonpermanent to graveyard
    g.turn = 2
    g.half_turn(0)
    joined = "\n".join(g.table)
    assert "split second" in joined
    assert not windows                               # nobody was ever asked
    assert "Krosan Grip" in g.p[0].graveyard


def test_stack_target_never_battlefield_fizzles(make_game):
    """Seen in tourney: Counterspell targeting stack#48 got fizzled by the
    battlefield police — stack ids are spells, exempt from that check."""
    g = make_game()
    g.p[0].hand.append("Counterspell")
    g.do_action(0, {"action": "cast", "card": "Counterspell", "targets": ["stack#3"],
                    "narration": "countering a thing the engine can't see"})
    assert not any("FIZZLES" in l for l in g.table)
    assert "Counterspell" in g.p[0].graveyard      # instants still resolve to gy


def test_counter_atom_in_corrections(make_game):
    """Seen in tourney: two agents filed correct actions carrying counter
    atoms to repair a bad fizzle — 'unknown effect atom'. Now honored."""
    g = make_game()
    g.stack.append({"id": "stack#9", "caster": 1, "kind": "spell",
                    "name": "Blind Obedience", "countered": False})
    g.do_action(0, {"action": "correct",
                    "effects": [{"counter": {"target": "stack#9"}}],
                    "narration": "counterspell legally counters it"})
    assert g.stack[0]["countered"]
    assert any("is countered" in l for l in g.table)
    g.apply_effects(0, [{"counter": {"target": "stack#404"}}])
    assert any("not on the stack" in l for l in g.table)
