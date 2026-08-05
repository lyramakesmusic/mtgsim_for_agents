#!/usr/bin/env python3
"""Cache scryfall card images for every card in cards.json → data/art/<slug>.jpg
Idempotent; skips existing files. ~0.12s/card (scryfall politeness delay)."""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "data" / "art"
ART.mkdir(parents=True, exist_ok=True)
HEADERS = {"User-Agent": "agents-mtg-sim/0.1 (art cache for game replays)", "Accept": "*/*"}


def slug(name):
    return re.sub(r"[^\w]+", "_", name).strip("_").lower()


def fetch_one(name):
    url = "https://api.scryfall.com/cards/named?exact=" + urllib.parse.quote(name)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        card = json.load(r)
    uris = card.get("image_uris") or (card.get("card_faces") or [{}])[0].get("image_uris") or {}
    img_url = uris.get("normal") or uris.get("large")
    if not img_url:
        return False
    req = urllib.request.Request(img_url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        (ART / f"{slug(name)}.jpg").write_bytes(r.read())
    return True


if __name__ == "__main__":
    import sys as _s
    _s.path.insert(0, str(ROOT))
    from mtgsim.cards import load_db
    db = load_db()
    names = sys.argv[1:] or sorted(db)
    missing, done = [], 0
    for n in names:
        if (ART / f"{slug(n)}.jpg").exists():
            continue
        try:
            if fetch_one(n):
                done += 1
            else:
                missing.append(n)
        except Exception as e:
            missing.append(f"{n} ({e})")
        time.sleep(0.12)
    print(f"fetched {done} images → {ART} ({len(list(ART.glob('*.jpg')))} total)"
          + (f"; no art: {missing}" if missing else ""))
