# mtgsim for agents

llm kitchen-table commander. the agents *are* the rules engine — claude and codex read actual oracle text, argue legality with each other, bluff, cut deals, and adjudicate disputes socially. the sim is a deliberately dumb state tracker: it shuffles, deals, guards the hidden zones, and applies whatever effects the agents declare.

![four llms playing commander](playing.png)

## the idea

magic's comprehensive rules are ~300 pages and the models have basically read every judge forum in existence, so nobody writes a rules engine here. each seat is a persistent `claude -p` or `codex exec` session (no api keys — it uses whatever your CLIs are logged in as). agents reply with json actions plus *effect atoms* — a dozen verbs like `move`, `life`, `create`, `draw`. the engine applies bookkeeping verbatim, and personally executes anything touching hidden information: draws, tutors (verified against the real library — no lying), shuffles, private scry looks, coin flips.

everything else — triggers, combat math, layers, whether your line is even legal — is on the agents. when someone gets it wrong, the table notices, argues about it in the open, and applies corrections. kitchen table, not the pro tour.

agents talk on two channels: `table_talk` is public — banter, threats, deals — and `thinking` is private commentary only you see, so you can watch someone bluff a counterspell they don't have while saying "my blue is mostly decorative" out loud. there's also a judge channel: type into the terminal mid-game and it posts to the table as a ⚖ ruling. the agents treat these as binding and will cite them later.

## running it

you need [claude code](https://claude.com/claude-code) and/or the codex cli installed and logged in, plus [uv](https://docs.astral.sh/uv/).

```bash
uv run play.py --pod claude:squirrels,codex:snakes,claude:meren,codex:aurelia
uv run play.py --pod squirrels,snakes --mock     # plumbing test, no llms
```

2–4 seats, each `agent:deck`. the game streams color-coded to your terminal and saves to `games/` as markdown + a jsonl event stream (every line paired with a full state snapshot). claude seats default to opus; codex uses your config default. fair warning: a real 4-seat game runs a few hours and burns a meaningful chunk of a subscription's daily tokens.

## decks

six included — squirrels (token combo), snakes (xyris group-hug with a knife), meren (graveyard grind), aurelia (boros fliers), karazikar (goad politics), isperia (wraths and paperwork).

adding your own: paste any decklist export (moxfield / arena / deckstats formats all parse) into `data/decks/yourdeck.txt`, then

```bash
uv run scripts/fetch_oracle.py data/decks/yourdeck.txt
```

to pull oracle text from scryfall for anything missing. optionally start the file with a `// strategy: ...` comment — the agents genuinely read it and play to it.

## what it's not robust to

- **rules mistakes nobody catches.** the table self-corrects impressively often, but a wrong ruling stands if all four agents miss it
- **mana is honor-based.** agents declare what they tap; enforcement is three opponents reading the same log (they do read it)
- **extra turns and weird turn order** — the turn loop is a fixed rotation
- **shared zones** (knowledge pool style effects) — cards live in exactly one player's zones, period
- long games can drift on subtle state; there's a digest every prompt to re-anchor, but the deep failure mode isn't crashes, it's *arguments* — which is honestly true to the tabletop experience

## outputs

every game leaves `games/<timestamp>.md` (raw log), `.pretty.md` (styled — plays bold, private thinking in italics), and `.events.jsonl` (full state snapshot per event, if you want to build anything on top of the games).
