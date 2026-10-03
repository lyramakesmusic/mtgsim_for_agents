#!/usr/bin/env python3
"""Decklist import from a deckbuilding site link. Prints JSON: {"name", "text", "tags"}.

  uv run --with curl_cffi web/importer.py https://moxfield.com/decks/<id>
  uv run --with curl_cffi web/importer.py https://archidekt.com/decks/<id>/<slug>

text is a plain Commander/Deck decklist; tags is {tag: [card names]} from the author's
own tagging (Moxfield author tags, Archidekt categories).
"""
import json
import re
import sys

from curl_cffi import requests


def get(url):
    r = requests.get(url, impersonate="chrome", timeout=30)
    r.raise_for_status()
    return r.json()


def decklist(commanders, main):
    """commanders: [name]; main: [(qty, name)] -> decklist text"""
    return "\n".join(["Commander", *[f"1 {c}" for c in commanders], "", "Deck", *[f"{q} {n}" for q, n in main]]) + "\n"


def moxfield(pid):
    d = get(f"https://api2.moxfield.com/v3/decks/all/{pid}")
    b = d["boards"]
    cmdrs = [c["card"]["name"] for c in b["commanders"]["cards"].values()]
    main = sorted(((c["quantity"], c["card"]["name"]) for c in b["mainboard"]["cards"].values()), key=lambda x: x[1])
    tags = {}
    for card, ts in (d.get("authorTags") or {}).items():
        for t in ts:
            tags.setdefault(t, []).append(card)
    return {"name": d.get("name", ""), "text": decklist(cmdrs, main), "tags": {t: sorted(c) for t, c in tags.items()}}


def archidekt(did):
    d = get(f"https://archidekt.com/api/decks/{did}/")
    excluded = {c["name"] for c in d.get("categories", []) if not c.get("includedInDeck", True)}
    cmdrs, main, tags = [], [], {}
    for c in d["cards"]:
        name = c["card"]["oracleCard"]["name"]
        cats = c.get("categories") or []
        if any(x in excluded for x in cats):
            continue
        if "Commander" in cats:
            cmdrs.append(name)
            continue
        main.append((c.get("quantity", 1), name))
        for t in cats:
            tags.setdefault(t, []).append(name)
    return {"name": d.get("name", ""), "text": decklist(cmdrs, sorted(main, key=lambda x: x[1])),
            "tags": {t: sorted(c) for t, c in tags.items()}}


if __name__ == "__main__":
    url = sys.argv[1].strip()
    if m := re.search(r"moxfield\.com/decks/([\w-]+)", url):
        out = moxfield(m.group(1))
    elif m := re.search(r"archidekt\.com/(?:api/)?decks/(\d+)", url):
        out = archidekt(m.group(1))
    else:
        raise SystemExit(f"unrecognized deck link: {url} (moxfield and archidekt links work)")
    print(json.dumps(out))
