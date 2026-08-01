import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mtgsim.agents import MockAgent          # noqa: E402
from mtgsim.cards import load_db, load_deck  # noqa: E402
from mtgsim.engine import Game               # noqa: E402


@pytest.fixture(scope="session")
def db():
    return load_db()


@pytest.fixture
def make_game(db, tmp_path):
    """4-seat mock game factory; agents overridable per-seat."""
    def _make(decknames=("snakes", "meren", "squirrels", "aurelia"), agents=None, seed=1):
        rng = random.Random(seed)
        decks = [(n, *load_deck(n, db)) for n in decknames]
        agents = agents or [MockAgent(f"P{i+1}", db) for i in range(len(decks))]
        return Game(db, decks, agents, seed, str(tmp_path / "game.md"), 5, rng)
    return _make


class StubAgent:
    """Scriptable agent stub: pass a string reply or a callable(prompt)->str."""
    resume = False
    session_id = None

    def __init__(self, reply='{"action":"pass"}'):
        self.reply = reply
        self.calls = 0
        self.cost_usd = 0.0
        self.tokens = {"in": 0, "out": 0}

    def ask(self, prompt):
        self.calls += 1
        return self.reply(prompt) if callable(self.reply) else self.reply


@pytest.fixture
def stub():
    return StubAgent
