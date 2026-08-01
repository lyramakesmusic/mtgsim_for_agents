"""Card library + decklist loading. Pure data, no game logic.

data/cards.json    — oracle DB: name -> {cost, type, text, pt}
data/decks/*.txt   — standard plain-text decklists (Moxfield/Arena export style):

    Commander
    1 Xyris, the Writhing Storm

    Deck
    8 Forest
    1 Sol Ring
    ...

Accepted per-line: "N Name" or "Nx Name". Section headers ("Commander",
"Deck", "Mainboard", with or without colon) are case-insensitive; "// x" and
"# x" lines are comments. A "1 Name *CMDR*" marker also works in lieu of a
Commander section. Sideboard sections are ignored.

Adding a deck = paste a decklist into data/decks/<name>.txt + add any missing
cards to cards.json. load_deck() validates every name against the DB and
fails loudly with the full missing list, so a typo'd card never silently
becomes a blank.
"""
import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
DECK_DIR = DATA / "decks"

_LINE = re.compile(r"^(\d+)x?\s+(.+?)\s*$")
_SETCODE = re.compile(r"\s*\(\w{2,6}\)(\s+[\dA-Za-z★-]+)?\s*$")   # "(m12)" / "(eld) 123"
_SECTIONS = {"commander": "commander", "deck": "main", "mainboard": "main",
             "main": "main", "sideboard": "side", "maybeboard": "side", "considering": "side"}


def load_db():
    db = json.loads((DATA / "cards.json").read_text())
    for d in db.values():             # json turns pt tuples into lists; normalize
        if d.get("pt"):
            d["pt"] = tuple(d["pt"])
    return db


def deck_names():
    return sorted(p.stem for p in DECK_DIR.glob("*.txt"))


def parse_decklist(text):
    """-> (main: [name]*N in listed order, commander: str)"""
    section, commander, main = "main", None, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("//"):        # deckstats-style section: //deck-1, //play-1, //sideboard
            tag = line.lstrip("/ ").rstrip(":").lower()
            if tag.startswith("play") or "commander" in tag or "cmdr" in tag:
                section = "commander"
            elif tag.startswith(("deck", "main")):
                section = "main"
            elif tag.startswith(("side", "maybe")):
                section = "side"
            continue
        if line.startswith("#"):
            continue
        header = _SECTIONS.get(line.rstrip(":").lower())
        if header:
            section = header
            continue
        m = _LINE.match(line)
        if not m:
            continue
        n, name = int(m.group(1)), _SETCODE.sub("", m.group(2)).strip()
        if name.endswith("*CMDR*"):
            commander = name[: -len("*CMDR*")].strip()
            continue
        if section == "commander":
            commander = name
        elif section == "main":
            main += [name] * n
    return main, commander


STRATEGY = re.compile(r"^(?://|#)\s*strategy:?\s*(.+)$", re.I)


def deck_strategy(name):
    """Optional gameplan blurb: '// strategy: ...' comment lines in the decklist."""
    path = DECK_DIR / f"{name}.txt"
    if not path.exists():
        return ""
    return " ".join(m.group(1).strip() for line in path.read_text().splitlines()
                    if (m := STRATEGY.match(line.strip())))


def load_deck(name, db):
    path = DECK_DIR / f"{name}.txt"
    if not path.exists():
        raise SystemExit(f"no deck {name!r} — available: {', '.join(deck_names())}")
    main, commander = parse_decklist(path.read_text())
    if not commander:
        raise SystemExit(f"deck {name!r}: no commander found (use a 'Commander' section or '*CMDR*' marker)")
    missing = sorted({c for c in main + [commander] if c not in db})
    if missing:
        raise SystemExit(f"deck {name!r} references {len(missing)} cards missing from cards.json:\n  "
                         + "\n  ".join(missing))
    return main, commander
