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
