"""Social layer: private thinking, judge channel, session delta prompts."""
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
            if "MAIN PHASE" in prompt and self.step == 1:
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
