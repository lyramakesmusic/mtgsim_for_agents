"""Branching: every event is a save point, minds are cloneable files."""
import json
import random
from pathlib import Path

from mtgsim.branching import (_rebuild_library, clone_claude_session, load_events,
                              pick_event, restore_game, resume_point)
from mtgsim.cards import load_db, load_deck


def _played_game(make_game, tmp_path):
    """A mock game with some texture: draws, a land, a token, life changes."""
    g = make_game()
    g.turn = 3
    g.log("\n## Turn 3 — P2(meren) — life: P1 40, P2 40, P3 40, P4 40")
    g.draw(g.p[1], 2)
    g.apply_effects(0, [{"create": {"player": "self", "name": "Snake", "n": 3, "pt": [1, 1]}},
                        {"life": {"player": "P3", "delta": -7}}])
    land = next(c for c in g.p[1].hand if "Land" in g.db.get(c, {}).get("type", ""))
    g.do_action(1, {"action": "play_land", "card": land})
    g.logf.close(); g.eventsf.close()
    return g


def _restore(g, tmp_path, db, at=None, edits=None):
    decks = [(pl.name.split("(")[1][:-1], pl.decklist, pl.commanders) for pl in g.p]
    agents = [type("A", (), {"ask": lambda self, p: '{"action":"pass"}',
                             "calls": 0, "cost_usd": 0.0, "tokens": {"in": 0, "out": 0}})()
              for _ in g.p]
    return restore_game(db, decks, agents, f"{g.logf.name}.events.jsonl",
                        str(tmp_path / "branch.md"), 9, random.Random(1),
                        at=at, edits=edits)


def test_restore_roundtrip_exact(make_game, tmp_path, db):
    g = _played_game(make_game, tmp_path)
    g2, ft, fs = _restore(g, tmp_path, db)
    for a, b in zip(g.p, g2.p):
        assert a.hand == b.hand
        assert a.library == b.library          # exact order, from library_cards
        assert a.life == b.life
        assert [x["id"] for x in a.battlefield] == [x["id"] for x in b.battlefield]
    assert g2.turn == 3
    assert g2.next_id == g.next_id
    assert (ft, fs) == (3, 2)                  # resumes with the seat after P2


def test_restore_applies_edits(make_game, tmp_path, db):
    g = _played_game(make_game, tmp_path)
    g2, _, _ = _restore(g, tmp_path, db, edits={"P4": {"hand": ["Fog", "Fog"], "life": 12}})
    p4 = g2.p[3]
    assert p4.hand == ["Fog", "Fog"] and p4.life == 12
    assert any("branch edit" in l for l in g2.table)


def test_old_logs_rebuild_library(make_game, tmp_path, db):
    g = _played_game(make_game, tmp_path)
    ev = f"{g.logf.name}.events.jsonl"
    events = [json.loads(l) for l in open(ev)]
    for e in events:                            # simulate a pre-feature log
        for ps in e["state"]["players"]:
            ps.pop("library_cards", None)
    Path(ev).write_text("\n".join(json.dumps(e) for e in events) + "\n")
    g2, _, _ = _restore(g, tmp_path, db)
    for a, b in zip(g.p, g2.p):
        assert sorted(a.library) == sorted(b.library)   # multiset right, order unknown


def test_pick_event_snaps_to_clean_stack(tmp_path):
    events = [{"line": "a", "state": {"stack_empty": True}},
              {"line": "b", "state": {"stack_empty": False}},
              {"line": "c", "state": {"stack_empty": False}}]
    assert pick_event(events, 2) == 0
    assert pick_event(events, 0) == 0


def test_resume_point_reads_last_turn_header():
    table = ["# Pod: ...", "## Turn 4 — P3(squirrels) — life: ...", "P3 does things"]
    assert resume_point(table) == (4, 3)        # P3's turn was in progress; P4 is next


def test_claude_session_truncation(tmp_path, monkeypatch):
    sid = "aaaaaaaa-0000-0000-0000-000000000000"
    lines = []
    for turn in range(3):
        lines += [json.dumps({"type": "user", "sessionId": sid, "n": turn}),
                  json.dumps({"type": "assistant", "sessionId": sid, "n": turn}),
                  json.dumps({"type": "last-prompt", "sessionId": sid, "n": turn})]
    sdir = tmp_path / "sessions"
    sdir.mkdir()
    (sdir / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")
    monkeypatch.setattr("mtgsim.branching._claude_dir", lambda: sdir)
    nid = clone_claude_session(sid, calls=2)
    cloned = (sdir / f"{nid}.jsonl").read_text().splitlines()
    assert len(cloned) == 6                     # two exchanges survive, the third is unlived future
    assert all(nid in l for l in cloned)
    assert sid not in "".join(cloned)


def test_rebuild_library_multiset():
    deck = ["Forest"] * 3 + ["Fog", "Sol Ring", "Toski, Bearer of Secrets"]
    ps = {"hand": ["Fog"], "graveyard": ["Forest"], "exile": [],
          "battlefield": [{"name": "Sol Ring", "token": False},
                          {"name": "Squirrel", "token": True}]}
    assert sorted(_rebuild_library(deck, ps)) == ["Forest", "Forest", "Toski, Bearer of Secrets"]
