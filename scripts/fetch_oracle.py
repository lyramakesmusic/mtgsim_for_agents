#!/usr/bin/env python3
"""Fetch ground-truth oracle data from Scryfall for every card a deck needs
that cards.json doesn't have. Usage:

  uv run scripts/fetch_oracle.py data/decks/braids.txt [more decks...]
  uv run scripts/fetch_oracle.py --all          # every deck in data/decks/

Uses the /cards/collection endpoint (batch 75, by exact name; set codes in
decklists are ignored — latest oracle text wins). Each deck gets its own
sidecar: data/decks/<name>.cards.json — deck and card data travel together.
Existing entries are NOT overwritten unless --refresh."""
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
        # the endpoint matches front-face names ("Dread Linnorm") and answers with
        # full ones ("Dread Linnorm // Scale Deflection") — index both to match back
        body = json.dumps({"identifiers": [{"name": n.split(" // ")[0]} for n in batch]}).encode()
        req = urllib.request.Request(API, data=body, headers=HEADERS, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.load(r)
        for card in resp.get("data", []):
            got[card["name"]] = card
            got.setdefault(card["name"].split(" // ")[0], card)
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

    for p in paths:
        side = p.with_suffix("").with_suffix("")  # strip .txt
        side = p.parent / (p.stem + ".cards.json")
        db = json.loads(side.read_text()) if side.exists() else {}
        main, commanders = parse_decklist(p.read_text())
        needed = sorted(set(main + commanders))
        wanted = [n for n in needed if args.refresh or n not in db]
        # prune entries for cards no longer in the deck
        db = {k: v for k, v in db.items() if k in needed}
        if not wanted:
            side.write_text(json.dumps(db, indent=1))
            print(f"{p.stem}: sidecar up to date ({len(db)} cards)")
            continue
        print(f"{p.stem}: fetching {len(wanted)} cards from scryfall...")
        got, missing = fetch(wanted)
        for req_name in wanted:
            card = got.get(req_name) or next(
                (c for n, c in got.items() if n.lower() == req_name.lower()), None)
            if card:
                db[req_name] = to_entry(card)
            else:
                print(f"  !! scryfall couldn't find: {req_name!r}")
        side.write_text(json.dumps(db, indent=1))
        print(f"{p.stem}: sidecar now {len(db)} cards"
              + (f"; NOT FOUND: {missing}" if missing else ""))
