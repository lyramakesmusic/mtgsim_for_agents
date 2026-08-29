"""Effect atoms: bookkeeping applied verbatim, hidden-info services verified."""


def test_search_verified_and_shuffles(make_game):
    g = make_game()
    me = g.p[1]
    g.apply_effects(1, [{"search": {"player": "self", "card": "Swamp", "to": "battlefield", "tapped": True}}])
    assert any(x["name"] == "Swamp" and x["tapped"] for x in me.battlefield)
    # lying tutor: named card not in library
    g.apply_effects(1, [{"search": {"player": "self", "card": "Lightning Bolt", "to": "hand"}}])
    assert "Lightning Bolt" not in me.hand


def test_move_verified(make_game):
    g = make_game()
    me = g.p[1]
    card = me.hand[0]
    g.apply_effects(1, [{"move": {"player": "self", "from": "hand", "card": card, "to": "graveyard"}}])
    assert card in me.graveyard
    # phantom reanimate: not actually in graveyard
    g.apply_effects(1, [{"move": {"player": "self", "from": "graveyard", "card": "Island", "to": "hand"}}])
    assert "Island" not in me.hand
    # real reanimate
    g.apply_effects(1, [{"move": {"player": "self", "from": "graveyard", "card": card, "to": "battlefield"}}])
    assert any(x["name"] == card for x in me.battlefield)


def test_mill_and_library_moves(make_game):
    g = make_game()
    before = len(g.p[2].library)
    g.apply_effects(1, [{"move": {"player": "P3", "from": "library_top", "n": 5, "to": "graveyard"}}])
    assert len(g.p[2].library) == before - 5
    assert len(g.p[2].graveyard) == 5


def test_commander_zone_roundtrip(make_game):
    g = make_game()
    me = g.p[1]
    g.apply_effects(1, [{"move": {"player": "self", "from": "command",
                                  "card": me.commanders[0], "to": "battlefield"}}])
    assert not me.command_zone[me.commanders[0]]
    cid = next(x["id"] for x in me.battlefield if x["name"] == me.commanders[0])
    g.apply_effects(1, [{"move": {"id": cid, "to": "command"}}])
    assert me.command_zone[me.commanders[0]]


def test_tokens_cease_and_set_atom(make_game):
    g = make_game()
    me = g.p[1]
    g.apply_effects(1, [{"create": {"player": "self", "name": "Zombie", "n": 2, "pt": [2, 2], "tapped": True}}])
    zid = next(x["id"] for x in me.battlefield if x["name"] == "Zombie")
    g.apply_effects(1, [{"set": {"id": zid, "tapped": False, "counters": 3}}])
    z = next(x for x in me.battlefield if x["id"] == zid)
    assert not z["tapped"] and z["counters"] == {"+1/+1": 3}
    g.apply_effects(1, [{"move": {"id": zid, "to": "graveyard"}}])
    assert "Zombie" not in me.graveyard          # tokens cease, never hit zones


def test_life_and_random_deterministic(make_game):
    g1, g2 = make_game(seed=9), make_game(seed=9)
    for g in (g1, g2):
        g.apply_effects(0, [{"life": {"player": "P2", "delta": -7}},
                            {"life": {"player": "self", "delta": 5}},
                            {"random": {"die": 20}}])
        assert g.p[1].life == 33 and g.p[0].life == 45
    assert g1.table[-1] == g2.table[-1]           # same seed, same roll


def test_eliminate_dispute_blocks_and_accept_kills(make_game, db):
    class Acceptor:
        calls, cost_usd, tokens = 0, 0.0, {"in": 0, "out": 0}
        def ask(self, prompt):
            return '{"accept": true, "reason": "it is correct"}'
    g = make_game()
    g.apply_effects(0, [{"eliminate": {"player": "P4", "reason": "fake oracle"}}])
    assert g.p[3].alive                            # mock disputes -> survives
    g.agents[2] = Acceptor()
    g.apply_effects(0, [{"eliminate": {"player": "P3", "reason": "real oracle"}}])
    assert not g.p[2].alive                        # acceptance -> eliminated


def test_bare_number_references_resolve(make_game):
    """Agents shorthand 'Squirrel#22' as 22 (or '22'); find() must resolve it."""
    g = make_game()
    me = g.p[1]
    g.apply_effects(1, [{"create": {"player": "self", "name": "Squirrel", "n": 1, "pt": [1, 1]}}])
    num = next(x["id"] for x in me.battlefield if x["name"] == "Squirrel").split("#")[1]
    g.apply_effects(1, [{"set": {"id": int(num), "counters": 2}}])       # int reference
    sq = next(x for x in me.battlefield if x["name"] == "Squirrel")
    assert sq["counters"] == {"+1/+1": 2}
    g.apply_effects(1, [{"move": {"id": num, "to": "graveyard"}}])       # str reference
    assert not any(x["name"] == "Squirrel" for x in me.battlefield)


def test_duplicate_names_prefer_actor(make_game):
    """Two Skullclamps, two owners: bare-name refs resolve to the actor's copy."""
    g = make_game()
    a = g.perm(g.p[0], "Skullclamp")
    b = g.perm(g.p[2], "Skullclamp")
    g.apply_effects(2, [{"set": {"id": "Skullclamp", "tapped": True}}])
    assert b["tapped"] and not a["tapped"]          # P3's own clamp tapped
    assert any("ambiguous reference" in l for l in g.table)


def test_control_change_and_owner_routing(make_game):
    g = make_game()
    thief, victim = g.p[2], g.p[0]
    x = g.perm(victim, "Kokusho, the Evening Star")
    g.apply_effects(2, [{"move": {"id": x["id"], "to": "battlefield", "control": "P3"}}])
    assert x in thief.battlefield and x not in victim.battlefield
    assert x["sick"]                                   # stolen creatures don't swing yet
    g.apply_effects(2, [{"move": {"id": x["id"], "to": "graveyard"}}])
    assert "Kokusho, the Evening Star" in victim.graveyard   # dies to OWNER's yard
    assert "Kokusho, the Evening Star" not in thief.graveyard


def test_draw_from_bottom(make_game):
    g = make_game()
    pl = g.p[0]
    bottom = pl.library[-1]
    g.apply_effects(0, [{"draw": {"player": "self", "n": 1, "from": "bottom"}}])
    assert pl.hand[-1] == bottom


def test_stolen_permanents_revert_when_thief_eliminated(make_game):
    """CR 800.4a: control effects end when their controller leaves the game."""
    g = make_game()
    owner, thief = g.p[1], g.p[2]
    x = g.perm(owner, "The Unbeatable Squirrel Girl")
    g.apply_effects(2, [{"move": {"id": x["id"], "to": "battlefield", "control": "P3"}}])
    assert x in thief.battlefield
    g.apply_effects(0, [{"life": {"player": "P3", "delta": -45}}])
    assert not thief.alive
    assert x in owner.battlefield          # she walked home
    assert x["sick"]                        # and can't attack this cycle


def test_owned_permanents_leave_when_owner_eliminated(make_game):
    """CR 800.4a other direction: your property leaves even from a thief's board."""
    g = make_game()
    owner, thief = g.p[0], g.p[2]
    x = g.perm(owner, "Purphoros, God of the Forge")
    g.apply_effects(2, [{"move": {"id": x["id"], "to": "battlefield", "control": "P3"}}])
    assert x in thief.battlefield
    g.apply_effects(1, [{"life": {"player": "P1", "delta": -45}}])
    assert not owner.alive
    assert x not in thief.battlefield       # left the game with its owner


def test_ask_atom_yes_branch(make_game):
    """Smothering Tithe shape: the drawing player is asked, pays, no Treasure.
    The answer is logged publicly — binding, not assumed."""
    from conftest import StubAgent
    g = make_game()
    g.agents[1] = StubAgent('{"choice":"yes"}')
    g.apply_effects(2, [{"ask": {"player": "P2",
                                 "question": "Smothering Tithe: pay {2}?",
                                 "if_no": [{"create": {"player": "self", "name": "Treasure", "n": 1}}]}}])
    assert not any(x["name"] == "Treasure" for x in g.p[2].battlefield)
    assert any("answers: yes" in l for l in g.table)


def test_ask_atom_no_branch_applies_as_asker(make_game):
    """Decline (or a pass/non-answer) takes if_no, applied as the asker —
    the Treasure lands on the Tithe player's board, not the decliner's."""
    from conftest import StubAgent
    g = make_game()
    g.agents[1] = StubAgent('{"action":"pass"}')      # never engaged = no
    g.apply_effects(2, [{"ask": {"player": "P2",
                                 "question": "Smothering Tithe: pay {2}?",
                                 "if_no": [{"create": {"player": "self", "name": "Treasure", "n": 1}}]}}])
    assert any(x["name"] == "Treasure" for x in g.p[2].battlefield)
    assert not any(x["name"] == "Treasure" for x in g.p[1].battlefield)
    assert any("answers: " in l for l in g.table)


def test_standing_tithe_auto_fires_on_draw(make_game):
    """Registered once, fires on another seat's draw without the owner doing
    anything; payer answers; declined trigger mints the owner's Treasure."""
    from conftest import StubAgent
    g = make_game()
    tithe = g.perm(g.p[2], "Smothering Tithe")
    g.apply_effects(2, [{"standing": {"source": tithe["id"], "on": "draw",
                                      "question": "pay {2} for Smothering Tithe?",
                                      "if_no": [{"create": {"player": "self", "name": "Treasure", "n": 1}}]}}])
    g.agents[1] = StubAgent('{"choice":"no"}')
    g.apply_effects(1, [{"draw": {"player": "self", "n": 1}}])
    assert sum(1 for x in g.p[2].battlefield if x["name"] == "Treasure") == 1
    assert any("answers: " in l for l in g.table)


def test_standing_expires_with_source_and_multidraw_counts(make_game):
    from conftest import StubAgent
    g = make_game()
    tithe = g.perm(g.p[2], "Smothering Tithe")
    g.apply_effects(2, [{"standing": {"source": tithe["id"], "on": "draw",
                                      "question": "pay {2}?",
                                      "if_no": [{"create": {"player": "self", "name": "Treasure", "n": 1}}]}}])
    g.agents[1] = StubAgent('{"choice":"yes","n":1}')   # wheel of 3: pay for 1, decline 2
    g.apply_effects(1, [{"draw": {"player": "self", "n": 3}}])
    assert sum(1 for x in g.p[2].battlefield if x["name"] == "Treasure") == 2
    # kill the source; next draw asks nothing and the entry expires
    g.apply_effects(0, [{"move": {"id": tithe["id"], "to": "graveyard"}}])
    g.agents[1] = StubAgent('{"choice":"no"}')
    g.apply_effects(1, [{"draw": {"player": "self", "n": 1}}])
    assert sum(1 for x in g.p[2].battlefield if x["name"] == "Treasure") == 2   # unchanged
    assert not g.standing
    assert any("expired" in l for l in g.table)


def test_standing_arbitrary_condition_is_memory_not_hook(make_game):
    """Unknown conditions don't auto-fire — they live in the digest as
    reminders and get triggered by agents via ask atoms."""
    g = make_game()
    src = g.perm(g.p[2], "Mangara, the Diplomat")
    g.apply_effects(2, [{"standing": {"source": src["id"], "on": "attacks me with 2+ creatures",
                                      "question": "Mangara draw check",
                                      "if_yes": [{"draw": {"player": "self", "n": 1}}]}}])
    before = len(g.p[2].hand)
    g.apply_effects(1, [{"draw": {"player": "self", "n": 1}}])   # draw event: no auto-fire
    assert len(g.p[2].hand) == before
    assert "STANDING EFFECTS" in g.digest(0)
    assert "Mangara draw check" in g.digest(0)


def test_dead_permanent_id_move_falls_back_to_public_zones(make_game):
    """Seen live: Aurelia Pongified to graveyard, then moved to command zone
    by her old battlefield id — the id search failed and the zone move hung.
    Unique public-zone match by name now honors the intent."""
    g = make_game()
    p3 = g.p[2]
    x = g.perm(p3, p3.commanders[0])
    p3.command_zone[p3.commanders[0]] = False
    g.apply_effects(1, [{"move": {"id": x["id"], "to": "graveyard"}}])   # pongify-ish
    assert p3.commanders[0] in p3.graveyard
    g.apply_effects(2, [{"move": {"id": x["id"], "to": "command"}}])     # by the DEAD id
    assert p3.command_zone[p3.commanders[0]]
    assert p3.commanders[0] not in p3.graveyard
    assert not any("skipped" in l for l in g.table)


def test_dead_id_move_ambiguous_name_still_skips(make_game):
    g = make_game()
    g.p[0].graveyard.append("Forest")
    g.p[1].graveyard.append("Forest")
    g.apply_effects(0, [{"move": {"id": "Forest#99", "to": "exile"}}])
    assert any("ambiguous" in l for l in g.table)
    assert g.p[0].graveyard == ["Forest"] and g.p[1].graveyard == ["Forest"]


def test_life_change_on_eliminated_player_ignored(make_game):
    """Seen live: a stale combat correction re-applied -28 to a player already
    eliminated at -1. Dead seats take no further bookkeeping."""
    g = make_game()
    g.p[2].alive = False
    g.p[2].life = -1
    g.apply_effects(0, [{"life": {"player": "P3", "delta": -28}}])
    assert g.p[2].life == -1
    assert any("already eliminated" in l for l in g.table)


def test_draw_atom_defaults_to_one(make_game):
    """Seen live (fatal): Yawgmoth's 'draw a card' declared without "n" —
    KeyError crashed a three-hour game. One is the default draw."""
    g = make_game()
    before = len(g.p[0].hand)
    g.apply_effects(0, [{"draw": {"player": "self"}}])
    assert len(g.p[0].hand) == before + 1


def test_malformed_atom_cannot_crash_the_game(make_game):
    """Atom armor: garbage keys/types log a red skip and play continues;
    later atoms in the same list still apply."""
    g = make_game()
    before = g.p[1].life
    g.apply_effects(0, [
        {"life": {"player": "P2", "delta": "not a number"}},   # ValueError
        {"set": None},                                          # TypeError
        {"life": {"player": "P2", "delta": -3}},                # still applies
    ])
    assert g.p[1].life == before - 3
    assert sum("crashed the bookkeeper" in l for l in g.table) == 2


def test_named_counters_do_not_move_power(make_game):
    """Ingenuity, experience and growth counters share a permanent with +1/+1
    counters without inflating it — or each other."""
    g = make_game()
    me = g.p[0]
    x = g.perm(me, "Forest")
    x["pt"] = [2, 2]

    g.apply_effects(0, [{"set": {"id": x["id"], "counters": {"ingenuity": 3}}}])
    assert x["counters"] == {"ingenuity": 3}
    assert "Forest#1(2/2)" in g._true_state()       # ingenuity moves nothing

    g.apply_effects(0, [{"set": {"id": x["id"], "counters": 2}}])
    assert x["counters"] == {"ingenuity": 3, "+1/+1": 2}

    g.apply_effects(0, [{"set": {"id": x["id"], "counters": {"ingenuity": 1, "experience": 1}}}])
    assert x["counters"] == {"ingenuity": 4, "+1/+1": 2, "experience": 1}

    assert "ingenuity 4" in g.digest(0, full_board=True)
    assert "Forest#1(4/4)" in g._true_state()       # 2/2 base +2, ingenuity excluded

    g.apply_effects(0, [{"set": {"id": x["id"], "counters": {"experience": -1}}}])
    assert "experience" not in x["counters"]        # emptied kinds stop being listed


def test_attackers_with_vigilance_stay_untapped(make_game):
    """Attacking taps by default; the declaration says who keeps vigilance,
    so a vigilant attacker doesn't need a correction after every swing."""
    g = make_game()
    me = g.p[0]
    a = g.perm(me, "Forest"); a["sick"] = False
    b = g.perm(me, "Mountain"); b["sick"] = False

    g.combat(0, {"attacks": {"P2": [a["id"], b["id"]]}, "vigilance": [a["id"]]})
    assert not a["tapped"], "vigilant attacker was tapped anyway"
    assert b["tapped"], "ordinary attacker should tap"

    c = g.perm(me, "Island"); c["sick"] = False
    g.combat(0, {"attacks": {"P2": [c["id"]]}, "vigilance": True})
    assert not c["tapped"]


def test_set_follows_a_card_that_came_back_with_a_new_id(make_game):
    """Exile and return mints a new permanent id, so a set aimed at the old one
    lands on the sole permanent of that name instead of silently skipping."""
    g = make_game()
    me = g.p[0]
    old = g.perm(me, "Forest")
    old_id = old["id"]
    me.battlefield.remove(old)                 # left the battlefield
    new = g.perm(me, "Forest")                 # came back as a different object
    assert new["id"] != old_id

    g.apply_effects(0, [{"set": {"id": old_id, "tapped": True}}])
    assert new["tapped"], "set didn't follow the returned card"
    assert any("applying to" in l for l in g.table)

    g.perm(me, "Forest")                       # now two of them: ambiguous, so say so
    g.apply_effects(0, [{"set": {"id": old_id, "counters": 1}}])
    assert any("say which" in l for l in g.table)


def test_look_is_private_and_reveal_is_public(make_game):
    """Gitaxian Probe says "look at", not "reveals" — the table learns that you
    looked and nothing else."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    secret = g.p[1].hand[0]

    g.apply_effects(0, [{"look": {"player": "P2", "zone": "hand"}}])
    assert any("looks at" in l for l in g.table)
    assert not any(secret in l for l in g.table), "a private look leaked to the table"

    g.apply_effects(0, [{"reveal": {"player": "P2", "zone": "hand"}}])
    assert any(secret in l for l in g.table), "a genuine reveal should be public"


def test_a_compulsory_loop_can_end_the_game_as_a_draw(make_game):
    """Marauding Raptor plus Polyraptor is mandatory on both halves: the table
    can agree the loop is unbreakable and nobody wins."""
    import pytest
    from conftest import StubAgent
    from mtgsim.engine import GameOver

    g = make_game()
    g.agents = [StubAgent('{"agree": true, "reason": "no way to interrupt it"}') for _ in g.p]
    with pytest.raises(GameOver) as end:
        g.do_action(0, {"action": "claim_draw", "how": "forced Polyraptor loop", "loop": "ping, copy, repeat"})
    assert end.value.winner is None
    assert any("DRAW" in l for l in g.table)


def test_one_dispute_keeps_the_game_going(make_game):
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent('{"agree": false, "reason": "I can bounce the raptor"}') for _ in g.p]
    assert g.do_action(0, {"action": "claim_draw", "how": "forced loop"}) is None
    assert any("DISPUTES" in l for l in g.table)


def test_order_can_take_looked_at_cards_into_another_zone(make_game):
    """Lead the Stampede looks at five, puts the creatures in hand and bottoms
    the rest — the cards that leave the library are named in take."""
    from conftest import StubAgent
    import json

    g = make_game()
    me = g.p[0]
    me.library[:6] = ["Plains", "Forest", "Llanowar Elves", "Sol Ring",
                      "Island", "Birds of Paradise"]   # Plains is the turn's draw
    replies = iter([
        '{"action":"pass"}',   # upkeep
        json.dumps({"action": "peek", "n": 5}),
        json.dumps({"action": "order", "take": ["Llanowar Elves", "Birds of Paradise"], "to": "hand",
                    "top": ["Sol Ring"], "bottom": ["Forest", "Island"]}),
    ])
    g.agents = [StubAgent(lambda _p: next(replies, '{"action":"pass"}')) for _ in g.p]
    hand_before = len(me.hand)
    g.half_turn(0)

    assert "Llanowar Elves" in me.hand and "Birds of Paradise" in me.hand
    assert len(me.hand) >= hand_before + 2
    assert me.library[0] == "Sol Ring"
    assert me.library[-2:] == ["Forest", "Island"]
    assert not any("doesn't match" in l or "unchanged" in l for l in g.table)
    assert "Llanowar Elves" not in me.library


def test_move_finds_a_commander_in_the_command_zone(make_game):
    """A missed Braids trigger is corrected by naming the commander; it's in the
    command zone, not on the battlefield."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    cmdr = me.commanders[0]
    assert me.command_zone[cmdr]
    g.apply_effects(0, [{"move": {"id": cmdr, "to": "battlefield"}}])
    assert not me.command_zone[cmdr]
    assert any(x["name"] == cmdr for x in me.battlefield)
    assert not any("no permanent" in l for l in g.table)


def test_move_can_rewind_a_spell_off_the_stack(make_game):
    """An illegally targeted spell gets taken back; it's on the stack, and the
    stack is not the battlefield."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    card = me.hand[0]
    obj = {"id": "stack#7", "caster": 0, "kind": "spell", "name": card,
           "countered": False, "targets": []}
    g.stack.append(obj)
    g.apply_effects(0, [{"move": {"id": obj["id"], "to": "hand"}}])
    assert obj not in g.stack
    assert card in me.hand
    assert not any("no permanent" in l for l in g.table)


def test_a_manland_can_stop_being_a_creature(make_game):
    """Celestial Colonnade animates for a turn and reverts at end of it; a null
    pt is how the seat says that, and it used to crash the bookkeeper."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    g.apply_effects(0, [{"create": {"player": "self", "name": "Celestial Colonnade", "pt": [4, 4]}}])
    land = me.battlefield[-1]
    g.apply_effects(0, [{"set": {"id": land["id"], "pt": None}}])
    assert land["pt"] is None
    assert not any("crashed" in l for l in g.table)


def test_a_permanent_can_register_its_own_standing_trigger(make_game):
    """Mystic Remora sets up its tax as it resolves, before it has an id."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    g.apply_effects(0, [{"create": {"player": "self", "name": "Mystic Remora"}}])
    g.apply_effects(0, [{"standing": {"source": "Mystic Remora", "on": "cast",
                                      "question": "pay {4} or I draw"}}])
    assert g.standing and g.standing[-1]["source"].startswith("Mystic Remora#")
    assert not any("standing: needs" in l for l in g.table)


def test_a_land_can_enter_tapped(make_game):
    """Two thirds of taplands in the archive were never recorded as tapped,
    because saying so needed a second atom."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    land = next(c for c in me.hand if "Land" in g.db.get(c, {}).get("type", "")) \
        if any("Land" in g.db.get(c, {}).get("type", "") for c in me.hand) else None
    if land is None:
        me.hand.append("Island"); land = "Island"
    g.do_action(0, {"action": "play_land", "card": land, "tapped": True})
    assert me.battlefield[-1]["tapped"] is True
    assert any("(tapped)" in l for l in g.table)


def test_a_permanent_can_arrive_with_counters(make_game):
    """Blue, Loyal Raptor makes every other dinosaur enter with a counter, and
    Giada does it for angels — how it arrives is part of declaring it."""
    from conftest import StubAgent
    from mtgsim.engine import plus_counters

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    g.apply_effects(0, [{"create": {"player": "self", "name": "Angel", "pt": [3, 3],
                                    "counters": {"+1/+1": 1}}}])
    tok = me.battlefield[-1]
    assert plus_counters(tok) == 1

    card = me.hand[0]
    g.do_action(0, {"action": "cast", "card": card, "tapped": True,
                    "counters": {"+1/+1": 2}, "tap": []})
    made = [x for x in me.battlefield if x["name"] == card]
    if made:                       # only if that card is a permanent
        assert made[-1]["tapped"] is True
        assert plus_counters(made[-1]) == 2


def test_minus_counters_shrink_a_creature(make_game):
    """The engine reads the two counter names that say what they do to power.
    A creature carrying both doesn't need its pt hand-adjusted to compensate."""
    from mtgsim.engine import plus_counters, _counters

    g = make_game()
    p = g.perm(g.p[0], "Llanowar Elves")
    g.apply_effects(0, [{"set": {"id": p["id"], "counters": {"+1/+1": 3}}}])
    g.apply_effects(0, [{"set": {"id": p["id"], "counters": {"-1/-1": 1}}}])
    g.apply_effects(0, [{"set": {"id": p["id"], "counters": {"stun": 2}}}])
    assert plus_counters(p) == 2
    assert _counters(p) == {"+1/+1": 2, "stun": 2}, "the opposing pair cancelled"



def test_a_stun_counter_spends_itself_at_untap(make_game):
    """A stun counter is one-shot by construction — it comes off instead of the
    permanent untapping, so there's nothing left set for a seat to forget."""
    from conftest import StubAgent
    from mtgsim.engine import _counters

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    frozen = g.perm(me, "Sol Ring", tapped=True, counters={"stun": 2})
    normal = g.perm(me, "Llanowar Elves", tapped=True)

    g.half_turn(0)
    assert frozen["tapped"] is True and _counters(frozen)["stun"] == 1
    assert normal["tapped"] is False

    frozen["tapped"] = True
    g.half_turn(0)
    assert frozen["tapped"] is True and "stun" not in _counters(frozen)

    frozen["tapped"] = True
    g.half_turn(0)
    assert frozen["tapped"] is False, "with the counters gone it untaps normally"


def test_an_exerted_creature_sits_out_one_untap_step(make_game):
    """Exerting isn't a stun counter — the board shouldn't show one — but it
    expires the same way, so nothing is left set for a seat to forget."""
    from conftest import StubAgent
    from mtgsim.engine import _counters

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    champ = g.perm(g.p[0], "Llanowar Elves", tapped=True)
    g.apply_effects(0, [{"set": {"id": champ["id"], "skip_untaps": 1}}])
    assert _counters(champ) == {}, "no counter appears on the board"

    g.half_turn(0)
    assert champ["tapped"] is True
    champ["tapped"] = True
    g.half_turn(0)
    assert champ["tapped"] is False, "it expired on its own"


def test_a_player_has_counters_of_their_own(make_game):
    """Meren's experience counters belong to the player and survive her dying —
    a seat trying to keep them on the permanent loses them with it. Poison and
    energy live in the same place."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    g.apply_effects(0, [{"set": {"player": "self", "counters": {"experience": 1}}}])
    g.apply_effects(0, [{"set": {"player": "self", "counters": {"experience": 2}}}])
    g.apply_effects(0, [{"set": {"player": "P2", "counters": {"poison": 4}}}])
    assert me.counters == {"experience": 3}
    assert g.p[1].counters == {"poison": 4}

    # they show up in the state every seat is handed
    assert "experience 3" in g.digest(0)
    # and they outlive the permanent that granted them
    me.battlefield.clear()
    assert me.counters["experience"] == 3


def test_opposing_counters_cancel(make_game):
    """CR 704.5q: +1/+1 and -1/-1 counters annihilate in pairs, so the board
    shows what is actually on the creature."""
    from conftest import StubAgent
    from mtgsim.engine import _counters, plus_counters

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    p = g.perm(g.p[0], "Llanowar Elves")
    g.apply_effects(0, [{"set": {"id": p["id"], "counters": {"-1/-1": 1}}}])
    g.apply_effects(0, [{"set": {"id": p["id"], "counters": {"+1/+1": 3}}}])
    assert _counters(p) == {"+1/+1": 2}
    assert plus_counters(p) == 2


def test_a_creature_shrunk_to_nothing_is_pointed_out(make_game):
    """The engine knows printed pt and counters and nothing else, so a 0/0
    gets named rather than removed — an anthem it wasn't told about is the
    seat's to account for."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    p = g.perm(g.p[0], "Llanowar Elves")          # a 1/1
    g.apply_effects(0, [{"set": {"id": p["id"], "counters": {"-1/-1": 1}}}])
    assert any("belongs in the graveyard" in l for l in g.table)
    assert p in g.p[0].battlefield, "the engine says so, it doesn't act"


def test_a_land_drop_carries_its_effects(make_game):
    """Landfall, a tapland's scry, the upkeep triggers a seat attaches to its
    first action — play_land was the one action that threw its effects away."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    land = next((c for c in me.hand if "Land" in g.db.get(c, {}).get("type", "")), None)
    if land is None:
        me.hand.append("Island"); land = "Island"
    before = g.p[1].life
    g.do_action(0, {"action": "play_land", "card": land,
                    "effects": [{"life": {"player": "P2", "delta": -3}}]})
    assert any(x["name"] == land for x in me.battlefield)
    assert g.p[1].life == before - 3, "the landfall trigger resolved with the drop"


def test_a_forced_discard_is_the_owners_choice(make_game):
    """Plaguecrafter makes each opponent discard; the seat casting it can't see
    their hands, so it names no card and the owner picks."""
    from conftest import StubAgent
    import json

    g = make_game()
    victim = g.p[1]
    victim.hand = ["Forest", "Counterspell", "Sol Ring"]
    g.agents = [StubAgent() for _ in g.p]
    g.agents[1] = StubAgent(json.dumps({"cards": ["Counterspell"]}))

    g.apply_effects(0, [{"move": {"player": "P2", "from": "hand", "n": 1, "to": "graveyard"}}])
    assert "Counterspell" in victim.graveyard
    assert victim.hand == ["Forest", "Sol Ring"]
    assert not any("VERIFICATION FAILED" in l for l in g.table)


def test_duress_takes_a_restricted_card_of_the_casters_choosing(make_game):
    """Duress: they reveal, YOU pick, and only a noncreature nonland qualifies."""
    from conftest import StubAgent
    import json

    g = make_game()
    victim = g.p[1]
    victim.hand = ["Forest", "Llanowar Elves", "Counterspell"]
    g.agents = [StubAgent() for _ in g.p]
    # the caster reaches for the creature; only the counterspell qualifies
    g.agents[0] = StubAgent(json.dumps({"cards": ["Llanowar Elves"]}))

    g.apply_effects(0, [{"move": {"player": "P2", "from": "hand", "n": 1, "to": "graveyard",
                                  "chooser": "self", "not_types": ["Creature", "Land"]}}])
    assert "Counterspell" in victim.graveyard
    assert "Llanowar Elves" in victim.hand and "Forest" in victim.hand


def test_discard_at_random_is_the_engines_roll(make_game):
    """"At random" is nobody's choice — neither seat gets to steer it."""
    from conftest import StubAgent
    import json

    g = make_game()
    victim = g.p[1]
    victim.hand = ["Forest", "Llanowar Elves", "Counterspell"]
    g.agents = [StubAgent(json.dumps({"cards": ["Forest"]})) for _ in g.p]

    g.apply_effects(0, [{"move": {"player": "P2", "from": "hand", "n": 1,
                                  "to": "graveyard", "chooser": "random"}}])
    assert len(victim.hand) == 2 and len(victim.graveyard) == 1
    assert any("at random" in l for l in g.table)


def test_dig_walks_a_library_until_it_matches(make_game):
    """Cascade off a one-drop whiffs through the whole deck; Umbris exiles until
    a land. Both used to be a chain of peeks with the card list inlined."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    me.library[:5] = ["Forest", "Island", "Llanowar Elves", "Sol Ring", "Counterspell"]

    # Umbris: exile from the top until you exile a land — everything goes to exile
    g.apply_effects(0, [{"dig": {"player": "self", "until": {"types": ["Land"]},
                                 "found": "exile", "rest": "exile"}}])
    assert me.exile == ["Forest"], "it stopped on the very first card"

    # cascade off a 1-drop: nothing costs zero, so it whiffs the whole library
    lib = len(me.library)
    g.apply_effects(0, [{"dig": {"until": {"max_mv": 0, "not_types": ["Land"]},
                                 "found": "exile", "rest": "library_bottom"}}])
    assert len(me.library) == lib, "everything came back"
    assert any("no match in the whole library" in l for l in g.table)


def test_dig_finds_a_real_hit(make_game):
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    me.library[:3] = ["Forest", "Island", "Sol Ring"]
    g.apply_effects(0, [{"dig": {"until": {"max_mv": 1, "not_types": ["Land"]},
                                 "found": "hand", "rest": "graveyard", "max": 3}}])
    assert "Sol Ring" in me.hand
    assert set(me.graveyard[-2:]) == {"Forest", "Island"}


def test_a_card_can_come_back_off_the_library_bottom(make_game):
    """library_bottom was a destination and not a source, so a seat could put
    cards there and never address them again."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    me.library.append("Brood Sliver")

    g.apply_effects(0, [{"move": {"from": "library_bottom", "n": 1, "to": "hand"}}])
    assert "Brood Sliver" in me.hand

    # and by name, anywhere in the library, without the shuffle a search forces
    target = me.library[len(me.library) // 2]
    order = list(me.library)
    g.apply_effects(0, [{"move": {"from": "library", "card": target, "to": "graveyard"}}])
    assert target in me.graveyard
    order.remove(target)
    assert me.library == order, "the rest of the library kept its order"
    assert not any("bad source" in l for l in g.table)


def test_a_card_can_go_into_the_library_at_a_depth(make_game):
    """Approach of the Second Sun puts itself seventh from the top, and the
    library only had a top and a bottom."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    me.graveyard.append("Approach of the Second Sun")
    g.apply_effects(0, [{"move": {"from": "graveyard", "card": "Approach of the Second Sun",
                                  "to": "library_depth", "depth": 7}}])
    assert me.library[7] == "Approach of the Second Sun"


def test_search_accepts_a_description(make_game):
    """"Search your library for a basic land" is a category, not a card name."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    me = g.p[0]
    g.apply_effects(0, [{"search": {"player": "self", "card": "a basic land",
                                    "types": ["basic", "land"], "to": "battlefield",
                                    "tapped": True}}])
    got = me.battlefield[-1]
    assert "Land" in g.db.get(got["name"], {}).get("type", "")
    assert got["tapped"] is True
    assert not any("VERIFICATION FAILED" in l for l in g.table)


def test_creatures_carry_marked_damage_that_wears_off(make_game):
    """The engine had no damage concept at all — agents wrote "damage marked"
    into prose 188 times across the archive."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    bear = g.perm(g.p[0], "Llanowar Elves")     # 1/1
    bear["pt"] = (4, 4)

    g.apply_effects(0, [{"damage": {"id": bear["id"], "n": 3, "from": "a bolt"}}])
    assert bear["damage"] == 3
    assert not any("lethal damage" in l for l in g.table)

    g.apply_effects(0, [{"damage": {"id": bear["id"], "n": 1}}])
    assert bear["damage"] == 4
    assert any("lethal damage marked" in l for l in g.table), "the engine says so"
    assert bear in g.p[0].battlefield, "and doesn't act on it"

    g.half_turn(1)
    assert bear["damage"] == 0, "damage wears off at end of turn"


def test_fight_is_each_creature_dealing_its_power(make_game):
    """Prey Upon, Savage Stomp, Ram Through — 'fight' was an unknown atom."""
    from conftest import StubAgent

    g = make_game()
    g.agents = [StubAgent() for _ in g.p]
    mine = g.perm(g.p[0], "Llanowar Elves"); mine["pt"] = (5, 5)
    theirs = g.perm(g.p[1], "Llanowar Elves"); theirs["pt"] = (2, 3)
    g.apply_effects(0, [{"set": {"id": mine["id"], "counters": {"+1/+1": 1}}}])

    g.apply_effects(0, [{"fight": {"a": mine["id"], "b": theirs["id"]}}])
    assert theirs["damage"] == 6, "counters count toward the power it deals"
    assert mine["damage"] == 2
    assert not any("unknown effect atom" in l for l in g.table)
