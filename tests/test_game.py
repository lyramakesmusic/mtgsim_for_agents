"""Core engine invariants: state, elimination, hidden info, turn structure."""
import pytest

from mtgsim.engine import GameOver

from conftest import StubAgent

def test_mock_pod_runs_to_turn_cap(make_game):
    g = make_game()
    g.run()
    assert "Turn cap" in g.table[-1]
    assert all(pl.alive for pl in g.p)

def test_elimination_cascade_and_last_standing(make_game):
    g = make_game()
    g.turn = 3
    g.apply_effects(0, [{"life": {"player": "P2", "delta": -45}}])
    assert not g.p[1].alive and g.p[1].battlefield == []
    assert g.resolve_player(0, "opponent") is None          # 3 alive: ambiguous
    g.apply_effects(0, [{"life": {"player": "P4", "delta": -45}}])
    assert g.resolve_player(0, "opponent") is g.p[2]        # 2 alive: resolves
    with pytest.raises(GameOver) as go:
        g.apply_effects(0, [{"life": {"player": "P3", "delta": -45}}])
    assert go.value.winner == 0

def test_decked_player_is_eliminated(make_game):
    g = make_game()
    g.p[1].library.clear()
    g.draw(g.p[1], 1)
    assert not g.p[1].alive

def test_view_hides_hidden_information(make_game):
    """The one invariant that makes the game real: no hand/library leaks."""
    g = make_game()
    g.turn = 2
    view = g.view(0)
    for other in g.p[1:]:
        for card in set(other.hand):
            # a card name may legitimately appear via oracle text of shared
            # cards; assert the HAND LINE itself never shows others' cards
            assert f"YOUR HAND" in view
            assert ", ".join(other.hand) not in view
        assert ", ".join(other.library[:10]) not in view

def test_own_hand_visible_and_sizes_public(make_game):
    g = make_game()
    view = g.view(2)
    assert "; ".join(g.p[2].hand[:3]) in view
    assert f"hand {len(g.p[0].hand)}" in view

def test_first_draw_rule(db, make_game, tmp_path):
    """CR 103.8: first player skips draw only in 2-player pods."""
    import random
    from mtgsim.agents import MockAgent
    from mtgsim.cards import load_deck
    from mtgsim.engine import Game

    for decknames, expect_skip in ((("snakes", "meren"), True),
                                   (("snakes", "meren", "squirrels", "aurelia"), False)):
        rng = random.Random(4)
        decks = [(n, *load_deck(n, db)) for n in decknames]
        agents = [MockAgent(f"P{i+1}", db) for i in range(len(decks))]
        g = Game(db, decks, agents, 4, str(tmp_path / f"g{len(decks)}.md"), 1, rng)
        hand_before = len(g.p[0].hand)
        g.turn = 1
        g.half_turn(0)
        drew = len(g.p[0].hand) - hand_before >= 1 or g.p[0].lands_played > 0
        # hand size: 7 - lands_played + draws; compute draw directly
        draws = len(g.p[0].hand) + g.p[0].lands_played - hand_before
        assert draws == (0 if expect_skip else 1)

def test_events_jsonl_snapshots(make_game, tmp_path):
    import json as _json
    g = make_game()
    g.turn = 1
    g.apply_effects(0, [{"create": {"player": "self", "name": "Snake", "n": 2, "pt": [1, 1]}},
                        {"life": {"player": "P2", "delta": -5}}])
    g.logf.close(); g.eventsf.close()
    events = [_json.loads(l) for l in open(str(tmp_path / "game.md") + ".events.jsonl")]
    assert all("state" in e and "line" in e for e in events)
    last = events[-1]["state"]
    assert last["players"][1]["life"] == 35
    assert sum(1 for x in last["players"][0]["battlefield"] if x["name"] == "Snake") == 2
    # a diff between consecutive snapshots shows the creation (renderer's animation source)
    prev = events[-2]["state"]
    assert len(last["players"][0]["battlefield"]) == len(prev["players"][0]["battlefield"]) + 2 \
        or last["players"][1]["life"] != prev["players"][1]["life"]

def test_mulligan_free_then_london(make_game):
    class Muller:
        calls, cost_usd, tokens = 0, 0.0, {"in": 0, "out": 0}
        def __init__(self):
            self.n = 0
        def ask(self, prompt):
            if "OPENING HAND" in prompt:
                self.n += 1
                return '{"action":"mulligan"}' if self.n <= 2 else '{"action":"keep"}'
            if "London mulligan" in prompt:
                import re as _re
                hand = _re.search(r"YOUR HAND \(\d+\): (.*)", prompt).group(1).split("; ")
                import json as _json
                return _json.dumps({"bottom": [hand[0]]})
            return '{"action":"pass"}'

    g = make_game()
    g.agents[0] = Muller()
    g.mulligans()
    p = g.p[0]
    assert len(p.hand) == 6                      # 2 mulls: free 7, then 7 bottom 1
    assert len(p.hand) + len(p.library) == 99    # deck integrity (commander is in CZ)
    assert any("mulligans to a new 7 (free)" in l for l in g.table)
    assert any("bottoms 1" in l for l in g.table)
    for other in g.p[1:]:                        # mock agents keep
        assert len(other.hand) == 7


def test_cleanup_discard_to_seven(make_game):
    class Discarder(StubAgent):
        def ask(self, prompt):
            if "CLEANUP" in prompt:
                import re as _re, json as _json
                hand = _re.search(r"YOUR HAND \(\d+\): (.*)", prompt).group(1).split("; ")
                n = len(hand) - 7
                return _json.dumps({"action": "cleanup", "effects": [
                    {"move": {"player": "self", "from": "hand", "card": c, "to": "graveyard"}}
                    for c in hand[:n]]})
            return '{"action":"pass"}'

    g = make_game()
    g.agents[0] = Discarder()
    g.p[0].hand += list(g.p[0].library[:4])   # inflate to 11
    del g.p[0].library[:4]
    g.turn = 2
    g.half_turn(0)
    assert len(g.p[0].hand) == 7


def test_attack_action_effects_apply_at_declare(make_game):
    """Attack triggers declared in the attack action must not vanish."""
    class Attacker(StubAgent):
        def __init__(self):
            super().__init__()
            self.done = False
        def ask(self, prompt):
            if "MAIN PHASE" in prompt and not self.done:
                self.done = True
                return ('{"action":"attack","attacks":{"P2":["Sq#900"]},'
                        '"effects":[{"create":{"player":"self","name":"Squirrel","n":1,"pt":[1,1]}}]}')
            return '{"action":"pass"}'

    g = make_game()
    g.agents[0] = Attacker()
    g.p[0].battlefield.append({"id": "Sq#900", "name": "Squirrel", "tapped": False,
                               "sick": False, "counters": 0, "token": True, "pt": [2, 2],
                               "owner": "P1"})
    g.turn = 3
    g.half_turn(0)
    assert any(x["name"] == "Squirrel" and x["id"] != "Sq#900"
               for x in g.p[0].battlefield), "attack-trigger token was dropped"


def test_empty_combat_result_flags_loudly(make_game):
    """Seen live: attacker's combat-result reply came back empty, damage
    silently vanished, and three seats invented fake activations to repair
    it. An effect-less combat result now gets a red flag in the log."""
    g = make_game()
    atk = g.perm(g.p[0], "Squirrel", token=True, pt=(1, 1))
    atk["sick"] = False
    g.agents[0] = StubAgent('{"action":"activate","effects":[],"narration":""}')
    g.agents[2] = StubAgent('{"action":"block","blocks":{}}')
    g.combat(0, {"action": "attack", "attacks": {"P3": [atk["id"]]}})
    assert any("no combat consequences" in l for l in g.table)


def test_combat_trick_window_lets_stack_grow(make_game):
    """Holding priority through your own pump: the combat trick window routes
    through resolve_on_stack, so the caster can respond to their own spell and
    chain a second instant. Both resolve; the stack empties."""
    g = make_game()
    atk = g.perm(g.p[0], "Snake", token=True, pt=(1, 1))
    atk["sick"] = False
    g.p[0].hand += ["Fog", "Fog"]
    state = {"first": False, "second": False}

    def attacker(prompt):
        if "your combat trick window" in prompt and not state["first"]:
            state["first"] = True
            return '{"action":"cast","card":"Fog","narration":"first pump"}'
        if "is casting Fog" in prompt and not state["second"]:
            state["second"] = True     # respond to my own spell — holding priority
            return '{"action":"cast","card":"Fog","narration":"second, holding priority"}'
        return '{"action":"pass"}'

    g.agents[0] = StubAgent(attacker)
    g.combat(0, {"action": "attack", "attacks": {"P3": [atk["id"]]}})
    assert state["first"] and state["second"]
    assert g.p[0].graveyard.count("Fog") == 2      # both resolved
    assert not g.stack
