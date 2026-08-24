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
    assert not z["tapped"] and z["counters"] == 3
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
    assert sq["counters"] == 2
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
