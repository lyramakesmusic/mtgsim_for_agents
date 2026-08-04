"""HumanAgent: the REPL seat. A stub scribe + scripted stdin exercise the
whole surface — pass fast-paths (no scribe call), chat-then-action flow,
engine-prompt buffering, opening-hand shortcuts, sitrep, mull bottoming."""
import builtins
import json

import pytest

from mtgsim.agents import FALLBACK, HumanAgent


class StubScribe:
    def __init__(self, replies=()):
        self.replies = list(replies)
        self.received = []
        self.calls = 0
        self.cost_usd = 0.0
        self.tokens = {"in": 0, "out": 0}
        self.session_id = None
        self.resume = True

    def ask(self, prompt):
        self.calls += 1
        self.received.append(prompt)
        return self.replies.pop(0) if self.replies else '{"chat":"hm?"}'


def feed(monkeypatch, lines):
    it = iter(lines)
    monkeypatch.setattr(builtins, "input", lambda *_: next(it))


RESP_PROMPT = ("=== TABLE LOG since your last decision ===\nP3(x) casts Bolt\n\n"
               "=== STATE DIGEST (authoritative) ===\nTURN 3. Seats: stuff\n"
               "YOUR HAND (2): Counterspell; Island\n"
               "=== INSTRUCTION ===\nRESPONSE WINDOW: P3 casts Bolt. You may cast an instant or pass.\n")
MAIN_PROMPT = ("=== STATE DIGEST (authoritative) ===\nTURN 4. Seats: stuff\n"
               "YOUR HAND (3): Forest; Sol Ring; Xyris, the Writhing Storm\n"
               "=== INSTRUCTION ===\nIt is your MAIN PHASE blah. Give one action per protocol.\n")
OPEN_PROMPT = ("You are playing Magic blah\nYOUR HAND (7): a; b; c; d; e; f; g\n"
               "=== INSTRUCTION ===\nOPENING HAND decision (0 mulligans taken...). Keep or mulligan?\n")


def test_response_window_pass_never_wakes_scribe(monkeypatch):
    scribe = StubScribe()
    h = HumanAgent("P2(snakes)", scribe)
    feed(monkeypatch, [""])
    assert json.loads(h.ask(RESP_PROMPT)) == {"action": "pass"}
    feed(monkeypatch, ["nah"])
    assert json.loads(h.ask(RESP_PROMPT)) == {"action": "pass"}
    assert scribe.calls == 0
    assert h.session_id            # engine may switch to deltas immediately


def test_chat_then_action(monkeypatch):
    scribe = StubScribe([
        '{"chat":"yes, the dragon is untapped"}',
        '{"action":"cast","card":"Counterspell","tap":["Island#4"],"chat":"countering it"}',
    ])
    h = HumanAgent("P2(snakes)", scribe)
    feed(monkeypatch, ["is the dragon untapped?", "ok counter it with the island"])
    out = json.loads(h.ask(RESP_PROMPT))
    assert out == {"action": "cast", "card": "Counterspell", "tap": ["Island#4"]}
    assert scribe.calls == 2
    # first scribe call carries the brief and the engine prompt verbatim
    assert HumanAgent.BRIEF.splitlines()[0] in scribe.received[0]
    assert "RESPONSE WINDOW: P3 casts Bolt" in scribe.received[0]
    assert "is the dragon untapped?" in scribe.received[0]
    # second call is just the human line — no repeated brief, no repeated prompt
    assert "ENGINE" not in scribe.received[1]


def test_buffered_prompts_reach_scribe(monkeypatch):
    scribe = StubScribe(['{"action":"play_land","card":"Forest"}'])
    h = HumanAgent("P2(snakes)", scribe)
    feed(monkeypatch, [""])                      # pass a window without waking scribe
    h.ask(RESP_PROMPT)
    feed(monkeypatch, ["play a forest"])         # now talk — both prompts must arrive
    h.ask(MAIN_PROMPT)
    assert scribe.calls == 1
    assert "RESPONSE WINDOW: P3 casts Bolt" in scribe.received[0]
    assert "MAIN PHASE" in scribe.received[0]


def test_main_phase_empty_reprompts_done_passes(monkeypatch):
    scribe = StubScribe()
    h = HumanAgent("P2(snakes)", scribe)
    feed(monkeypatch, ["", "", "done"])          # accidental enters must not end the turn
    assert json.loads(h.ask(MAIN_PROMPT)) == {"action": "pass"}
    assert scribe.calls == 0


def test_opening_hand_shortcuts(monkeypatch):
    h = HumanAgent("P2(snakes)", StubScribe())
    feed(monkeypatch, ["keep"])
    assert json.loads(h.ask(OPEN_PROMPT)) == {"action": "keep"}
    h2 = HumanAgent("P2(snakes)", StubScribe())
    feed(monkeypatch, ["mull"])
    assert json.loads(h2.ask(OPEN_PROMPT)) == {"action": "mulligan"}


def test_sitrep_fires_at_turn_start(monkeypatch):
    scribe = StubScribe(['{"chat":"four forests, two dorks, six mana up; P3 dragon is the threat"}'])
    h = HumanAgent("P2(snakes)", scribe)
    prompt = MAIN_PROMPT.replace("STATE DIGEST", "FULL STATE (start of your turn)")
    feed(monkeypatch, ["done"])
    assert json.loads(h.ask(prompt)) == {"action": "pass"}
    assert scribe.calls == 1                     # sitrep call happened before any input
    assert "sitrep" in scribe.received[0]


def test_bottom_reply_counts_as_action(monkeypatch):
    scribe = StubScribe(['{"bottom":["a"],"chat":"bottoming the extra land"}'])
    h = HumanAgent("P2(snakes)", scribe)
    prompt = OPEN_PROMPT.replace("OPENING HAND decision (0 mulligans taken...). Keep or mulligan?",
                                 "London mulligan: choose 1 card to bottom.")
    feed(monkeypatch, ["bottom a land"])
    assert json.loads(h.ask(prompt)) == {"bottom": ["a"]}


def test_quoted_line_is_table_talk_on_pass(monkeypatch):
    scribe = StubScribe()
    h = HumanAgent("P2(snakes)", scribe)
    feed(monkeypatch, ['"B is way too strong"', "nah"])
    out = json.loads(h.ask(RESP_PROMPT))
    assert out == {"action": "pass", "table_talk": "B is way too strong"}
    assert scribe.calls == 0                     # banter costs zero LLM calls


def test_quoted_line_rides_next_action(monkeypatch):
    scribe = StubScribe(['{"action":"cast","card":"Counterspell","tap":["Island#4"]}'])
    h = HumanAgent("P2(snakes)", scribe)
    feed(monkeypatch, ['"not so fast"', "counter it"])
    out = json.loads(h.ask(RESP_PROMPT))
    assert out["action"] == "cast"
    assert out["table_talk"] == "not so fast"
    # the quoted line went to the table, not to the scribe
    assert "not so fast" not in scribe.received[0]


def test_local_state_commands(monkeypatch, capsys):
    scribe = StubScribe()
    h = HumanAgent("P2(snakes)", scribe)
    feed(monkeypatch, ["hand", "board", "nah"])
    h.ask(RESP_PROMPT)
    out = capsys.readouterr().out
    assert "Counterspell; Island" in out         # 'hand' reprints
    assert "TURN 3. Seats: stuff" in out         # 'board' dumps the state section
    assert scribe.calls == 0


def test_console_privacy_with_human_seated(make_game, capsys):
    """console_private as a handle set: other seats' private lines vanish from
    the console but still reach the log file; the human's own draws and
    public-knowledge card text (seat=None) still print."""
    g = make_game()
    g.console_private = {"P2"}                   # human at seat 2
    g.log_private('P1(snakes) thinks: "kill P2 first"', seat="P1")
    g.log_private("  (P2 drew: Sol Ring, Forest)", seat="P2")
    g.log_private("  [Skullclamp: equipped gets +1/-1...]")  # seat=None: public info
    out = capsys.readouterr().out
    assert "kill P2 first" not in out            # hidden from the seated human
    assert "P2 drew: Sol Ring" in out            # own draws still visible
    assert "Skullclamp" in out                   # card text is public knowledge
    logtext = open(g.logf.name).read()
    assert "kill P2 first" in logtext            # file keeps everything for export
    # spectator mode unchanged
    g.console_private = "all"
    g.log_private('P1(snakes) thinks: "still scheming"', seat="P1")
    assert "still scheming" in capsys.readouterr().out


def test_engine_integration_full_turn(make_game, monkeypatch):
    """A real Game with a human at seat 1: land drop via scribe, quoted banter,
    turn end, end step — and the engine flips to delta prompts after call one."""
    g = make_game()
    land = next(c for c in g.p[0].hand
                if "Land" in g.db.get(c, {}).get("type", ""))
    scribe = StubScribe([json.dumps({"action": "play_land", "card": land,
                                     "chat": f"dropping the {land}"})])
    h = HumanAgent("P1(snakes)", scribe)
    g.agents[0] = h
    g.turn = 1
    feed(monkeypatch, ["play a land", '"glhf everyone"', "done", ""])  # main, banter, end turn, end step
    monkeypatch.setattr(g, "check_judge", lambda: None)  # don't drain test-runner stdin
    g.half_turn(0)
    assert any(x["name"] == land for x in g.p[0].battlefield)
    assert any('says: "glhf everyone"' in l for l in g.table)
    # first prompt was the full brief; once session_id is set the engine sends deltas
    assert "=== PROTOCOL ===" in scribe.received[0]
    assert h.session_id and g.log_sent[0] == len(g.table)
