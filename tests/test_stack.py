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
            if "MAIN PHASE" in prompt and not self.step:
                self.step = 1
                return f'{{"action":"cast","card":"{me.commanders[0]}","tap":[]}}'
            return '{"action":"pass"}'

    g.agents[0] = Caster()
    g.turn = 2
    g.half_turn(0)
    assert me.command_zone[me.commanders[0]] and me.commander_tax[me.commanders[0]] == 2
    assert me.commanders[0] not in me.graveyard

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
            if "MAIN PHASE" in prompt and self.n <= 2:
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


def test_sorcery_speed_cast_in_response_is_flagged(make_game, db):
    """The engine doesn't block bad timing, it notices it out loud."""
    from conftest import StubAgent

    g = make_game(decknames=("squirrels", "meren"))
    g.active = 1                                   # meren's turn
    me = g.p[0]
    sorc = next(c for c in me.decklist
                if "Sorcery" in db.get(c, {}).get("type", "")
                and "Flash" not in db.get(c, {}).get("text", ""))
    me.hand.append(sorc)
    g.stack.append({"id": "stack#99", "caster": 1, "kind": "spell",
                    "name": "Massacre Wurm", "countered": False})
    g.agents = [StubAgent() for _ in g.p]
    g.resolve_on_stack(0, {"card": sorc, "tap": []})
    assert any("timing dubious" in l for l in g.table), g.table[-4:]


def test_announce_and_stack_line_name_the_targets(make_game, db):
    """A response window is a blind choice unless the table can see the aim."""
    from conftest import StubAgent

    g = make_game(decknames=("squirrels", "meren"))
    g.active = 0
    me = g.p[0]
    inst = next(c for c in me.decklist if "Instant" in db.get(c, {}).get("type", ""))
    me.hand.append(inst)
    g.agents = [StubAgent() for _ in g.p]
    g.stack.append({"id": "stack#98", "caster": 1, "kind": "spell", "name": "Massacre Wurm",
                    "countered": False, "targets": []})
    g.resolve_on_stack(0, {"card": inst, "tap": [], "targets": ["Massacre Wurm#38"],
                           "narration": "Exile the Wurm."})
    assert any("targeting Massacre Wurm#38" in l for l in g.table), g.table[-3:]
    assert any("Exile the Wurm." in l for l in g.table)


def test_response_window_skip_is_visible_and_never_public(make_game, capsys):
    """A gated seat is noted for the spectator; the table learns nothing."""
    from conftest import StubAgent

    g = make_game(decknames=("squirrels", "meren"))
    g.agents = [StubAgent() for _ in g.p]
    g.p[1].hand.clear()
    g.p[1].battlefield.clear()
    g.active = 0
    me = g.p[0]
    me.hand.append(me.decklist[0])
    g.resolve_on_stack(0, {"card": me.decklist[0], "tap": []})
    assert "no window" in capsys.readouterr().out
    assert not any("no window" in l for l in g.table), "leaked to the table"


def test_human_seat_always_gets_its_window(make_game):
    """The gate saves API calls; a human costs nothing and may want to talk."""
    from mtgsim.agents import HumanAgent
    from conftest import StubAgent

    class Scribe(StubAgent):
        pass

    g = make_game(decknames=("squirrels", "meren"))
    g.p[1].hand.clear()
    g.p[1].battlefield.clear()
    g.agents[1] = HumanAgent("P2(meren)", Scribe())
    assert g._can_respond(g.p[1], 1) is True


def test_sac_for_mana_outlet_earns_a_response_window(make_game):
    """'In response I sac it to the Altar' — a mana ability that eats a
    creature is a real response, so the gate must not filter it out."""
    g = make_game(decknames=("meren", "squirrels"))
    pl = g.p[0]
    pl.hand.clear()
    pl.battlefield.clear()
    assert g._can_respond(pl, 0) is False
    g.perm(pl, "Ashnod's Altar")                 # Sacrifice a creature: Add {C}{C}.
    assert g._can_respond(pl, 0) is True


def test_a_board_of_only_mana_lands_still_gets_no_window(make_game):
    g = make_game(decknames=("meren", "squirrels"))
    pl = g.p[0]
    pl.hand.clear()
    pl.battlefield.clear()
    g.perm(pl, "Swamp")
    assert g._can_respond(pl, 0) is False


def test_response_window_carries_the_card_text(make_game, db):
    """A window is a decision about a card, so the card's text is in the prompt."""
    prompts = []

    class Watcher:
        calls, cost_usd, tokens = 0, 0.0, {"in": 0, "out": 0}
        def ask(self, prompt):
            prompts.append(prompt)
            return '{"action":"pass"}'

    g = make_game(decknames=("squirrels", "meren"))
    g.active = 0
    me = g.p[0]
    inst = next(c for c in me.decklist if "Instant" in db.get(c, {}).get("type", ""))
    me.hand.append(inst)
    g.agents = [Watcher() for _ in g.p]
    g.resolve_on_stack(0, {"card": inst, "tap": []})
    windows = [p for p in prompts if "RESPONSE WINDOW" in p]
    assert windows, "nobody was offered a window"
    text = db[inst]["text"][:40]
    assert any(text in p for p in windows), windows[0][:300]


def test_resolution_does_not_repeat_the_announcement(make_game):
    """The announcement tells the table the plan; resolution has nothing to add
    unless it says something new."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    plan = {"card": g.p[0].hand[0], "narration": "Tapping three lands, drawing two."}
    g.resolve_on_stack(0, dict(plan), kind="spell")
    announced = [l for l in g.table if "announces" in l]
    assert announced and "drawing two" in announced[0]

    g.table.clear()
    assert g._fresh_narration("Tapping three lands, drawing two.") is None
    assert g._fresh_narration("Actually I scry first.") == "Actually I scry first."


def test_an_illegal_cast_can_be_backed_up_off_the_stack(make_game):
    """The table catches a spell cast without the mana for it; the correction
    takes it back to hand, and the stack is a zone a card can come from."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    card = me.hand[0]
    g.stack.append({"id": "stack#3", "caster": 0, "kind": "spell", "name": card,
                    "countered": False, "targets": []})
    g.apply_effects(1, [{"move": {"from": "stack", "card": card, "to": "hand"}}])
    assert not g.stack
    assert card in me.hand
    assert not any("bad source" in l for l in g.table)


def test_a_spell_can_be_cast_from_exile(make_game):
    """The First Sliver cascades on every sliver spell: the hit is exiled and
    cast from there. Etali and discover do the same, and flashback casts from
    the graveyard."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    me.exile.append("Lightning Bolt")
    me.graveyard.append("Llanowar Elves")

    assert g._pay_spell(0, {"card": "Lightning Bolt", "from": "exile", "tap": []}) is False
    assert "Lightning Bolt" not in me.exile

    # the seat can also leave the zone unsaid when the card sits in exactly one
    assert g._pay_spell(0, {"card": "Llanowar Elves", "tap": []}) is False
    assert "Llanowar Elves" not in me.graveyard
    assert any("casting it from" in l for l in g.table)

    assert g._pay_spell(0, {"card": "Not In Any Zone", "tap": []}) is None


def test_combat_survives_an_attack_declared_as_a_list(make_game):
    """A list where a dict was expected crashed the whole game two hours in.
    The shapes agents actually reach for are accepted; anything else is
    refused with the shape spelled out."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    bear = g.perm(me, "Llanowar Elves")
    bear["sick"] = False

    # a list of groups, each naming its defender
    g.combat(0, {"attacks": [{"defender": "P2", "with": [bear["id"]]}]})
    assert any("attacks P2" in l for l in g.table)

    # a bare list of ids can't say who it's hitting — refused, not crashed
    g.table.clear()
    g.combat(0, {"attacks": [bear["id"]]})
    assert any("must say who each group is hitting" in l for l in g.table)

    # attackers plus a defender is the other shape they reach for
    g.table.clear()
    bear["tapped"] = False
    g.combat(0, {"attackers": [bear["id"]], "defender": "P3"})
    assert any("attacks P3" in l for l in g.table)


def test_a_spell_rewound_mid_resolution_does_not_resolve(make_game):
    """A seat casts something illegal, the table objects, and the caster takes it
    back — while that spell's own resolution is still live on the call stack.
    It must not resolve, and the cleanup must not crash."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    card = g.p[0].hand[0]

    def rewind(obj, depth):           # what a correction does mid-flight
        g.apply_effects(1, [{"move": {"from": "stack", "card": obj["id"], "to": "hand"}}])
        return False
    g._priority_rounds = rewind

    ok = g.resolve_on_stack(0, {"card": card, "narration": "an illegal cast"}, kind="spell")
    assert ok is False
    assert g.stack == []
    assert card in g.p[0].hand
    assert any("left the stack before it resolved" in l for l in g.table)


def test_a_trigger_declared_in_a_response_window_fires(make_game):
    """Kambal and Blood Artist answer what just happened without casting
    anything — passing with atoms is the shape, and the atoms were dropped."""
    from conftest import StubAgent
    import json

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    g.agents[1] = StubAgent(json.dumps({
        "action": "pass",
        "effects": [{"life": {"player": "P1", "delta": -2}}],
        "narration": "Kambal triggers off that noncreature spell",
    }))
    g.p[1].hand = ["Counterspell"]
    g.perm(g.p[1], "Island")
    before = g.p[0].life
    g._trick_window(1, "P1 cast something.")
    assert g.p[0].life == before - 2


def test_spells_cast_this_turn_is_counted(make_game):
    """Storm counts every spell cast before it this turn, whoever cast it, and a
    countered spell still counted — so the tally is kept when a spell is announced."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    me.hand.append("Counterspell")
    g.p[1].hand.append("Counterspell")
    g.resolve_on_stack(0, {"card": "Counterspell", "targets": []})
    g.resolve_on_stack(1, {"card": "Counterspell", "targets": []})
    assert (me.spells_this_turn, g.p[1].spells_this_turn) == (1, 1)
    assert "Spells cast this turn: 2 (P1 1; copies you have made 0)" in g.digest(0)



def test_a_seat_whose_brain_dies_ends_the_game(make_game):
    """800 failed calls produced twenty turns of nothing that looked like a game;
    a seat that keeps giving up stops it instead."""
    import pytest
    from mtgsim.engine import GameOver
    from conftest import StubAgent

    class Dead(StubAgent):
        gave_up = True

    g = make_game()
    g.agents = [Dead() for _ in g.p]
    with pytest.raises(GameOver) as caught:
        for _ in range(g.DEAD_SEAT_CALLS + 2):
            g.ask(0, "anything")
    assert "failed" in caught.value.how
    assert caught.value.winner is None


def test_a_recovered_seat_does_not_count_toward_the_limit(make_game):
    from conftest import StubAgent

    class Flaky(StubAgent):
        gave_up = False

    g = make_game()
    g.agents = [Flaky() for _ in g.p]
    for _ in range(g.DEAD_SEAT_CALLS * 3):
        g.ask(0, "anything")
    assert g.dead_calls[0] == 0


def test_a_seat_correcting_nothing_over_and_over_is_moved_along(make_game):
    """A seat that believes the game already ended corrected the board eighteen
    times in a row and would not act; the game could not proceed and could not
    conclude. Saying it once is enough."""
    g = make_game()
    me = g.p[0]

    for _ in range(g.IDLE_CORRECTIONS - 1):
        out = g.do_action(0, {"action": "correct", "narration": "the match is over",
                              "effects": [{"note": "game over"}]})
        assert out != "pass"

    out = g.do_action(0, {"action": "correct", "narration": "the match is over",
                          "effects": [{"note": "game over"}]})
    assert out == "pass"
    assert any("without changing anything" in line for line in g.table)


def test_a_correction_that_changes_something_resets_the_count(make_game):
    g = make_game()
    me = g.p[0]
    me.hand.append("Swamp")
    for _ in range(g.IDLE_CORRECTIONS + 2):
        g.do_action(0, {"action": "correct", "narration": "fixing a real thing",
                        "effects": [{"life": {"player": "self", "delta": -1}}]})
    assert g.idle_corrections[0] == 0
