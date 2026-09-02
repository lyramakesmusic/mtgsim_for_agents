"""Social layer: private thinking, judge channel, session delta prompts."""
import json
from conftest import StubAgent

def test_private_thinking_never_reaches_table(make_game):
    class Schemer:
        calls, cost_usd, tokens = 0, 0.0, {"in": 0, "out": 0}
        def ask(self, prompt):
            return ('{"action":"pass","thinking":"SECRET_PLAN_XYZZY",'
                    '"table_talk":"nothing to see here"}')

    g = make_game()
    g.agents[0] = Schemer()
    g.turn = 1
    g.ask(0, "test instruction")
    assert not any("SECRET_PLAN_XYZZY" in line for line in g.table)
    assert any("nothing to see here" in line for line in g.table)
    for i in range(4):
        assert "SECRET_PLAN_XYZZY" not in g.view(i)

def test_judge_passthrough_and_summon(make_game):
    g = make_game()
    g.judge_factory = lambda: StubAgent("Ruling: the Hermit is dead. Move it to the graveyard.")
    g.judge_inbox.write_text("didnt the hermit die?\nJUDGE what happened here\n")
    g.agents[0] = StubAgent('{"action":"pass"}')
    g.ask(0, "test")
    joined = "\n".join(g.table)
    assert '⚖ JUDGE: didnt the hermit die?' in joined
    assert '⚖ JUDGE RULES: Ruling: the Hermit is dead' in joined
    assert "JUDGE what happened here" not in joined      # keyword intercepted, never posted

def test_judge_prompt_has_true_state_and_hides_nothing(make_game):
    captured = {}
    class Judge:
        calls, cost_usd, tokens = 0, 0.0, {"in": 0, "out": 0}
        def ask(self, prompt):
            captured["p"] = prompt
            return "ok"
    g = make_game()
    g.judge_factory = lambda: Judge()
    g.judge_inbox.write_text("JUDGE\n")
    g.agents[0] = StubAgent('{"action":"pass"}')
    g.ask(0, "test")
    assert g.p[1].hand[0] in captured["p"]               # judge sees hidden hands

def test_session_agents_get_delta_prompts(make_game):
    prompts = []
    class Sessioned:
        calls, cost_usd, tokens = 0, 0.0, {"in": 0, "out": 0}
        resume, session_id = True, None
        def ask(self, prompt):
            prompts.append(prompt)
            self.session_id = "sess-1"        # established after first call
            return '{"action":"pass"}'

    g = make_game()
    g.agents[0] = Sessioned()
    g.turn = 2
    g.force_full[0] = True
    g.ask(0, "first")
    g.log("P2(x) says: \"something happened\"")
    g.ask(0, "second")
    assert "PROTOCOL" in prompts[0]                      # full brief once
    assert "PROTOCOL" not in prompts[1]                  # delta after
    assert "TABLE LOG since your last decision" in prompts[1]
    assert "something happened" in prompts[1]
    assert "STATE DIGEST" in prompts[1]
    g.force_full[0] = True                               # own-turn resync
    g.ask(0, "third")
    assert "PROTOCOL" in prompts[2]


def test_draw_names_never_reach_table(make_game):
    g = make_game()
    top3 = list(g.p[0].library[:3])
    g.apply_effects(0, [{"draw": {"player": "self", "n": 3}}])
    joined = "\n".join(g.table)
    for c in top3:
        assert f"drew: {c}" not in joined and f"({c}" not in joined
    assert "draws 3" in joined


def test_peek_private_look_and_order(make_game):
    prompts = []
    class Peeker(StubAgent):
        def __init__(self):
            super().__init__()
            self.step = 0
        def ask(self, prompt):
            prompts.append(prompt)
            self.step += 1
            if "MAIN PHASE" in prompt and self.step <= 2:
                return '{"action":"peek","n":3}'
            if "PRIVATE LOOK" in prompt:
                import re as _re, json as _json
                cards = _re.search(r"in order: (.+?)\. Declare", prompt).group(1).split(", ")
                return _json.dumps({"action": "order", "top": [cards[2], cards[0]], "bottom": [cards[1]]})
            return '{"action":"pass"}'

    g = make_game()
    g.agents[0] = Peeker()
    orig = list(g.p[0].library[1:4])   # turn draw pops one first
    g.turn = 2
    g.half_turn(0)
    lib = g.p[0].library
    assert lib[0] == orig[2] and lib[1] == orig[0] and lib[-1] == orig[1]
    joined = "\n".join(g.table)
    assert "looks at the top 3" in joined
    for c in orig:                                   # names never public
        assert c not in joined or joined.count(c) == joined.replace(f"top 3", "").count(c) and c not in joined


def test_openrouter_context_trim_pins_brief(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from mtgsim.agents import OpenRouterAgent
    a = OpenRouterAgent("P1(test)", model="test/model", context_budget=2000)
    a.messages = [{"role": "user", "content": "BRIEF " + "x" * 500}]
    for n in range(30):
        a.messages.append({"role": "user", "content": f"turn {n} " + "y" * 200})
        a.messages.append({"role": "assistant", "content": "ok " + "z" * 100})
    a._trim()
    assert a._size() <= 2000 + 300                 # budget plus marker slack
    assert a.messages[0]["content"].startswith("BRIEF")   # opening brief pinned
    assert "trimmed to fit context" in a.messages[1]["content"]
    assert a.messages[-1]["role"] == "assistant"   # tail preserved in order


def test_deck_gameplans_stay_private(db, tmp_path):
    """A seat is briefed on its own gameplan and never sees another seat's."""
    import random

    from mtgsim.cards import load_deck
    from mtgsim.engine import Game

    names = ("snakes", "meren")
    decks = [(n, *load_deck(n, db), f"GAMEPLAN_OF_{n.upper()}") for n in names]
    seen = {0: [], 1: []}
    agents = [StubAgent(lambda p, i=i: seen[i].append(p) or '{"action":"pass"}')
              for i in range(2)]
    g = Game(db, decks, agents, 1, str(tmp_path / "game.md"), 5, random.Random(1))
    g.turn = 1
    g.ask(0, "act")
    g.ask(1, "act")

    assert "GAMEPLAN_OF_SNAKES" in seen[0][0]
    assert "GAMEPLAN_OF_MEREN" not in seen[0][0]
    assert "GAMEPLAN_OF_MEREN" in seen[1][0]
    assert "GAMEPLAN_OF_SNAKES" not in seen[1][0]
    for i in (0, 1):
        assert "GAMEPLAN_OF" not in g.view(i)
    assert not any("GAMEPLAN_OF" in line for line in g.table)


def test_unreadable_reply_goes_back_to_the_seat(make_game):
    """Malformed JSON is sent back for a resend, not scored as a pass."""
    replies = iter([
        # the real shape that broke: one unclosed brace inside a nested atom
        '{"action":"cast","card":"Winds of Rath","effects":[{"move":{"id":"x#1",'
        '"to":"graveyard"}]}}',
        '{"action":"cast","card":"Winds of Rath","effects":[]}',
    ])
    class Fumbler:
        calls, cost_usd, tokens = 0, 0.0, {"in": 0, "out": 0}
        def ask(self, prompt):
            self.calls += 1
            return next(replies, '{"action":"pass"}')

    g = make_game()
    g.agents[0] = Fumbler()
    g.turn = 1
    out = g.ask(0, "act")
    assert out["action"] == "cast" and out["card"] == "Winds of Rath"
    assert g.agents[0].calls == 2                       # asked again, not passed
    assert any("unreadable reply" in line for line in g.table)


def test_seat_loses_the_decision_after_repeated_garbage(make_game):
    """A seat that never returns JSON is called out loudly and stops the loop."""
    class Broken:
        calls, cost_usd, tokens = 0, 0.0, {"in": 0, "out": 0}
        def ask(self, prompt):
            self.calls += 1
            return "I would like to cast Winds of Rath please"

    g = make_game()
    g.agents[0] = Broken()
    g.turn = 1
    assert g.ask(0, "act")["action"] == "pass"
    assert g.agents[0].calls == g.JSON_TRIES
    assert any("unreadable replies in a row" in line for line in g.table)


def test_human_combat_damage_defaults_to_the_scribe(monkeypatch):
    """Enter at the damage step asks the scribe to compute it, never a bare pass."""
    from mtgsim.agents import HumanAgent

    asked = []

    class Scribe:
        calls, cost_usd, tokens, gave_up = 0, 0.0, {"in": 0, "out": 0}, False
        resume, session_id = False, None
        def ask(self, prompt):
            asked.append(prompt)
            return ('{"action":"resolve","effects":[{"life":{"player":"P2","delta":-14}}],'
                    '"narration":"Light-Paws deals 14; it is commander damage."}')

    h = HumanAgent("P1(aurafarming)", Scribe())
    monkeypatch.setattr("builtins.input", lambda *_: "")
    out = json.loads(h.ask(
        "=== GAME STATE ===\nYOUR HAND (2): Plains; Plains\n\n=== INSTRUCTION ===\n"
        "Blocks and tricks are final (see table log). Compute the combat damage honestly.\n"))
    assert out["action"] == "resolve"
    assert out["effects"][0]["life"]["delta"] == -14
    assert asked, "the scribe was never consulted"
    assert "resolve this combat from the board" in asked[0]


def test_every_seat_is_briefed_on_threat_order(make_game):
    """Kill-the-closest-to-winning is table doctrine, not one deck's memo."""
    seen = []

    class Watcher:
        calls, cost_usd, tokens = 0, 0.0, {"in": 0, "out": 0}
        def ask(self, prompt):
            seen.append(prompt)
            return '{"action":"pass"}'

    g = make_game()
    g.agents = [Watcher() for _ in g.p]
    g.turn = 1
    for i in range(len(g.p)):
        g.force_full[i] = True
        g.ask(i, "act")
    assert seen and all("closest to winning" in p for p in seen)


def test_scouting_is_public_and_gameplans_stay_private(db, tmp_path):
    """Everyone knows what everyone's deck does; nobody knows how it plans to."""
    import random

    from mtgsim.cards import load_deck
    from mtgsim.engine import Game

    names = ("snakes", "meren")
    decks = [(n, *load_deck(n, db), f"PLAN_OF_{n.upper()}", f"scouts as {n}") for n in names]
    seen = {0: [], 1: []}
    agents = [StubAgent(lambda p, i=i: seen[i].append(p) or '{"action":"pass"}')
              for i in range(2)]
    g = Game(db, decks, agents, 1, str(tmp_path / "game.md"), 5, random.Random(1))
    g.turn = 1
    g.ask(0, "act")
    g.ask(1, "act")

    for i in (0, 1):
        assert "scouts as snakes" in seen[i][0]        # both scouting lines
        assert "scouts as meren" in seen[i][0]
    assert "PLAN_OF_SNAKES" in seen[0][0] and "PLAN_OF_MEREN" not in seen[0][0]
    assert "PLAN_OF_MEREN" in seen[1][0] and "PLAN_OF_SNAKES" not in seen[1][0]


def test_personality_is_private_voice_direction(db, tmp_path):
    """A seat is told how it talks; nobody else is told how it talks."""
    import random

    from mtgsim.cards import load_deck
    from mtgsim.engine import Game

    names = ("snakes", "meren")
    decks = [(n, *load_deck(n, db), f"PLAN_{n.upper()}", f"scouts as {n}", f"VOICE_{n.upper()}")
             for n in names]
    seen = {0: [], 1: []}
    agents = [StubAgent(lambda p, i=i: seen[i].append(p) or '{"action":"pass"}')
              for i in range(2)]
    g = Game(db, decks, agents, 1, str(tmp_path / "game.md"), 5, random.Random(1))
    g.turn = 1
    g.ask(0, "act")
    g.ask(1, "act")

    assert "VOICE_SNAKES" in seen[0][0] and "VOICE_MEREN" not in seen[0][0]
    assert "VOICE_MEREN" in seen[1][0] and "VOICE_SNAKES" not in seen[1][0]
    assert not any("VOICE_" in line for line in g.table)


def test_voice_reminder_rides_every_prompt(make_game):
    """The brief is sent once a session; the voice has to travel with the deltas
    or the seat drifts back to its default register by turn three."""
    prompts = []

    class Sessioned:
        calls, cost_usd, tokens = 0, 0.0, {"in": 0, "out": 0}
        resume, session_id = True, None
        def ask(self, prompt):
            prompts.append(prompt)
            self.session_id = "sess-1"
            return '{"action":"pass"}'

    g = make_game()
    g.p[0].personality = "VOICE_UNDER_TEST"
    g.agents[0] = Sessioned()
    g.turn = 2
    g.force_full[0] = True
    g.ask(0, "first")            # full brief
    g.ask(0, "second")           # delta
    g.ask(0, "third")            # delta
    assert "PROTOCOL" in prompts[0] and "PROTOCOL" not in prompts[1]
    assert all("VOICE_UNDER_TEST" in p for p in prompts), "voice missing from a delta prompt"



def test_the_turn_has_an_upkeep_phase(make_game):
    """Upkeep triggers were 17% of every board correction in the archive: the
    turn went untap, draw, main, with no moment to declare them in."""
    from conftest import StubAgent

    seen = []
    g = make_game()
    g.agents = [StubAgent(lambda p: seen.append(p) or '{"action":"pass"}') for _ in g.p]
    g.half_turn(0)
    ups = [k for k, p in enumerate(seen) if "UPKEEP —" in p]
    mains = [k for k, p in enumerate(seen) if "MAIN PHASE" in p]
    assert ups, "the seat is asked for upkeep triggers"
    assert ups[0] < mains[0], "before the main phase"
    assert len(ups) == 1, "once a turn"


def test_a_seat_is_shown_its_own_deck_with_text_and_groups(make_game):
    """A player knows their own 99 and what each card does. Tutoring is the most
    information-hungry action in the game and seats were doing it blind."""
    g = make_game(decknames=("rats", "orvar", "meren", "stella"))
    block = g._decklist_block(g.p[0])

    assert "[protect the king!!]" in block, "the builder's own grouping"
    assert "Rat Colony x32" in block, "duplicates are counted, not repeated"
    assert "gets +1/+0 for each other Rat" in block, "rules text has to be there"
    assert "Hidden Strings" not in block, "a seat must not see another seat's list"


def test_a_deck_without_tags_lists_alphabetically(make_game, monkeypatch):
    """Tags are optional; an untagged deck reads as a plain sorted list."""
    import mtgsim.engine as engine

    monkeypatch.setattr(engine, "deck_tags", lambda name: {})
    g = make_game(decknames=("rats", "orvar", "meren", "stella"))
    block = g._decklist_block(g.p[0])
    assert not block.lstrip().startswith("[")
    assert "Rat Colony x32" in block


TAGGED = """Commander (1)
1 Stella Lee, Wild Card

untap loopers (3)
1 Hidden Strings
1 Refocus
1 Twiddle

magecraft payoffs (2)
1 Storm-Kiln Artist
1 Ashling, Flame Dancer

Untagged Lands (4)
3 Island
1 Mountain
"""


def test_a_tag_grouped_export_keeps_its_groups():
    """A moxfield export grouped by tag carries the builder's own taxonomy, and the
    groups say what the deck is for better than an alphabetical list does."""
    from mtgsim.cards import tags_from_text, parse_decklist

    tags = tags_from_text(TAGGED)
    assert list(tags) == ["Commander", "untap loopers", "magecraft payoffs",
                          "Untagged Lands"]
    assert sum(n for v in tags.values() for _, n in v) == 10

    main, cmd = parse_decklist(TAGGED)
    assert cmd == ["Stella Lee, Wild Card"], cmd
    assert len(main) == 9, "tag headers must not swallow cards into the commander"


def test_an_untagged_export_has_no_groups():
    from mtgsim.cards import tags_from_text

    assert tags_from_text("Deck\n1 Island\n1 Mountain\n") == {}


def test_a_seat_can_say_someone_else_has_already_won(make_game):
    """A seat worked out that an opponent had won, had no vocabulary for it, and
    corrected the board eighteen times instead. You can declare a loss for someone
    else; you should be able to declare a win for them too."""
    import json as _json
    import pytest
    from mtgsim.engine import GameOver
    from conftest import StubAgent

    seen = []

    def concede(prompt):
        seen.append(prompt)
        return _json.dumps({"concede": True, "reason": "the trigger resolved, it is over"})

    g = make_game()
    g.agents = [StubAgent(concede) for _ in g.p]

    with pytest.raises(GameOver) as caught:
        g.do_action(1, {"action": "claim_win", "player": "P3",
                        "how": "Thassa's Oracle with an empty library"})
    assert caught.value.winner == 2, caught.value.winner
    assert any("HAS ALREADY WON" in line for line in g.table)
    assert seen and "P3(squirrels)" in seen[0], seen[0][:160]
    assert "claims a rules-based win for P3(squirrels)" in seen[0]
