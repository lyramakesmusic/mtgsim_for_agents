# mtgsim for agents

llm kitchen-table commander. the agents are the rules engine - claude and codex read the actual card text, argue about legality, bluff, make deals, etc. the sim is basically just a scorekeeper: it shuffles, draws, hides hands and libraries, and applies whatever the agents say happened.

![playing](playing.png)

## how it works

nobody writes a magic rules engine here. the models already know the rules - theyve read every judge forum in existence - so each seat is just a persistent `claude -p` or `codex exec` session (uses whatever your CLI is logged in as, no api keys). agents reply with json actions plus "effect atoms" - move, life, create, draw, yada yada. the engine applies bookkeeping exactly as declared, and handles anything involving hidden info itself: draws, tutors (it checks the card is actually in your library, no lying), shuffles, scry peeks, coin flips.

everything else - triggers, combat math, "wait can you even do that" - is on the agents. when someone gets it wrong the table notices, argues about it in the open, and fixes the state. kitchen table rules, not the pro tour.

agents talk on two channels: `table_talk` is public ("should i attack you or you"), `thinking` is private commentary only you see. so you get an agent privately going "i have no counterspells and never did, the bluff is free" while publicly saying "my blue is mostly decorative". theres also a judge channel - type into the terminal mid-game and it posts to the table as a ⚖ ruling. they treat these as binding and will cite them turns later.

## running it

you need [claude code](https://claude.com/claude-code) and/or the codex cli, logged in, plus [uv](https://docs.astral.sh/uv/). then:

```bash
uv run play.py --pod claude:squirrels,codex:snakes,claude:meren,codex:aurelia
uv run play.py --pod squirrels,snakes --mock     # plumbing test, no llms
```

2-4 seats, each `agent:deck`. the game streams color-coded to your terminal and saves to `games/` as markdown + a jsonl event stream (full state snapshot per event, if you want to build on top of it). claude seats default to opus. heads up: a real 4-seat game takes a few hours and a lot of tokens.

## decks

six included: squirrels (mana -> squirrels -> pain), snakes ("here have cards" until they die), meren (graveyard grind), aurelia (boros fliers), karazikar (goad politics), isperia (wraths and paperwork).

adding yours: paste any decklist export (moxfield/arena/deckstats formats all parse) into `data/decks/whatever.txt`, then

```bash
uv run scripts/fetch_oracle.py data/decks/whatever.txt
```

grabs oracle text from scryfall for anything missing. optionally put a `// strategy: ...` comment at the top - the agents actually read it and play to it.

## known jank

- a wrong ruling stands if all four agents miss it. they self-correct a lot, but its not guaranteed
- mana is honor-based. agents declare what they tap, enforcement is three opponents reading the same log (they do read it)
- no extra turns or weird turn order, the turn loop is a fixed rotation
- no shared zones (knowledge pool type stuff) - cards live in exactly one player's zones
- long games can drift on subtle state. theres an authoritative digest every prompt to re-anchor, but the real failure mode isnt crashes, its arguments. which is kinda true to the tabletop experience tbh
