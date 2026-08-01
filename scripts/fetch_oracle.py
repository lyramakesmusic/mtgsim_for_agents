#!/usr/bin/env python3
"""Fetch ground-truth oracle data from Scryfall for every card a deck needs
that cards.json doesn't have. Usage:

  uv run scripts/fetch_oracle.py data/decks/braids.txt [more decks...]
  uv run scripts/fetch_oracle.py --all          # every deck in data/decks/

Uses the /cards/collection endpoint (batch 75, by exact name; set codes in
decklists are ignored — latest oracle text wins). Merges into data/cards.json
in the DB's terse format: {cost, type, text, pt}. Existing entries are NOT
overwritten unless --refresh."""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mtgsim.cards import DECK_DIR, parse_decklist  # noqa: E402

API = "https://api.scryfall.com/cards/collection"
HEADERS = {"User-Agent": "agents-mtg-sim/0.1 (kitchen-table LLM commander sim)",
           "Accept": "application/json", "Content-Type": "application/json"}


def compact_cost(mana_cost):
    """'{2}{U}{U}' -> '2UU', '{W/B}' -> '(W/B)', '' -> ''"""
    toks = [t for t in mana_cost.replace("{", " ").replace("}", " ").split() if t]
    return "".join(t if len(t) == 1 else f"({t})" for t in toks)


def to_entry(card):
    faces = card.get("card_faces") or []
    if faces and "oracle_text" not in card:
        text = " // ".join(f"{f['name']}: {f.get('oracle_text','')}" for f in faces).replace("\n", " ")
        cost = compact_cost(faces[0].get("mana_cost", ""))
        src_pt = faces[0]
    else:
        text = (card.get("oracle_text") or "").replace("\n", " ")
        cost = compact_cost(card.get("mana_cost", ""))
        src_pt = card
    pt = None
    if src_pt.get("power") is not None and src_pt.get("toughness") is not None:
        def num(v):
            try:
                return int(v)
            except ValueError:
                return 0          # '*' etc — base 0, text explains
        pt = (num(src_pt["power"]), num(src_pt["toughness"]))
    if card.get("loyalty"):
        text += f" [Loyalty {card['loyalty']} — track via counters+notes]"
    return {"cost": cost, "type": card.get("type_line", ""), "text": text, "pt": pt}


def fetch(names):
    got, missing = {}, []
    names = list(names)
    for b in range(0, len(names), 75):
        batch = names[b:b + 75]
        body = json.dumps({"identifiers": [{"name": n} for n in batch]}).encode()
        req = urllib.request.Request(API, data=body, headers=HEADERS, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.load(r)
        for card in resp.get("data", []):
            got[card["name"]] = card
        missing += [i.get("name", "?") for i in resp.get("not_found", [])]
        time.sleep(0.1)
    return got, missing


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("decks", nargs="*", help="deck .txt paths")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-fetch cards already in DB")
    args = ap.parse_args()

    paths = sorted(DECK_DIR.glob("*.txt")) if args.all else [Path(p) for p in args.decks]
    cards_path = ROOT / "data" / "cards.json"
    db = json.loads(cards_path.read_text())

    wanted = {}
    for p in paths:
        main, commander = parse_decklist(p.read_text())
        for n in set(main + ([commander] if commander else [])):
            if args.refresh or n not in db:
                wanted[n] = True
    if not wanted:
        print("nothing to fetch — DB already covers these decks")
        sys.exit(0)

    print(f"fetching {len(wanted)} cards from scryfall...")
    got, missing = fetch(wanted)
    for req_name in wanted:
        # store under the deck's requested name so decklists validate;
        # scryfall may canonicalize (accents, punctuation)
        card = got.get(req_name) or next(
            (c for n, c in got.items() if n.lower() == req_name.lower()), None)
        if card:
            db[req_name] = to_entry(card)
        else:
            print(f"  !! scryfall couldn't find: {req_name!r}")
    cards_path.write_text(json.dumps(db, indent=1))
    found = len(wanted) - len(missing)
    print(f"merged {found} entries → cards.json now {len(db)} cards"
          + (f"; NOT FOUND: {missing}" if missing else ""))
