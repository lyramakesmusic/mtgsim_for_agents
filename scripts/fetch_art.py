#!/usr/bin/env python3
"""Cache scryfall card images for every card in cards.json → data/art/<slug>.jpg
Idempotent; skips existing files. ~0.12s/card (scryfall politeness delay)."""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "data" / "art"
ART.mkdir(parents=True, exist_ok=True)
HEADERS = {"User-Agent": "agents-mtg-sim/0.1 (art cache for game replays)", "Accept": "*/*"}


def slug(name):
    return re.sub(r"[^\w]+", "_", name).strip("_").lower()


def get(url, tries=6):
    """GET with backoff on 429 (Scryfall's rate limit): 1s, 2s, 4s..."""
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or k == tries - 1:
                raise
            time.sleep(2 ** k)


def fetch_one(name):
    url = "https://api.scryfall.com/cards/named?exact=" + urllib.parse.quote(name)
    card = json.loads(get(url))
    uris = card.get("image_uris") or (card.get("card_faces") or [{}])[0].get("image_uris") or {}
    img_url = uris.get("normal") or uris.get("large")
    if not img_url:
        return False
    (ART / f"{slug(name)}.jpg").write_bytes(get(img_url))
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
