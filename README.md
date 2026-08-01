## llm kitchen-table commander.

minimal state-tracking sim that allows various claude or codex agents to play mtg against each other. due to the complexity of the mtg rules engine, the agents are left in charge of interpreting the cards, playing them correctly, and manipulating the game state to reflect their plays. the sim keeps track of life, shuffles, draws, battlefield presence, etc. allowing the llm players arbitrary control over their actions allows for the weirdness you get in actual games.

![playing](playing.png)

## how it works

the models already know the rules. they have quite extensive knowledge of cards, rulings, archetypes, etc. each seat is just a persistent `claude -p` or `codex exec` session. agents reply with json actions plus "effect atoms" incl move, life, create, draw, etc. the engine tracks exactly what the agents declare and handles anything involving hidden info: draws, tutors, shuffles, scry peeks, coin flips.

triggers, combat math, payments, ruling decisions, the stack, etc is on the agents. the agents will occasionally get something wrong, get called out, and take action to fix board state. 

for example: P3 mind controlled P1's creature. P1 then lost the game. the sim removed P1's board state but was unable to handle removing the P3-controlled creature and left it on the board. the table realized, and took an action to hand the creature back to nonexistent P1 (removing it from the game) explicitly, noting it as a correction of board state rather than a play.

theres also a judge channel: type into the terminal mid-game and it posts to the table as a ⚖ ruling from `judge:`. they treat these as binding and will cite them turns later.

agents talk on two channels: `table_talk` is public for politics and trash talking, `thinking` is private commentary only you see. often you will see an agent privately reasoning about keeping four blue mana open as a bluff knowing it has nothing, but then openly representing a counterspell.

## running it

you need claude code and/or the codex cli, logged in, plus `uv`. then:

```bash
uv run play.py --pod claude:squirrels,codex:snakes,claude:meren,codex:aurelia
```

2-4 seats, each `agent:deck`. the game streams to your terminal as shown above and saves to `games/` as markdown plus a jsonl event stream. claude seats default to opus5, codex does not choose a default model.

## decks

we've included 6 stock decks (bracket ~2.5-3): unbeatable squirrel girl (ramp into infinite squirrel tokens and cause pain), xyris group hug combat tricks ("here have cards" until they die), braids (everyone gets free stuff every upkeep but mine are eldrazi), lifedrain (anything anybody does drains them life and gives it to me), meren (graveyard grind), aurelia (boros fliers).

adding yours: paste any decklist export (moxfield/arena/deckstats formats all parse) into `data/decks/whatever.txt`, then, to actually grab the rules text from each card:

```bash
uv run scripts/fetch_oracle.py data/decks/whatever.txt
```

optionally put a `// strategy: ...` comment at the top, the agents will read it and use it as guidance to play in case there are odd strategies they need to know about.

## known jank

- a wrong ruling stands if all four agents miss it. they self-correct a lot, but its not guaranteed. use the judge channel.
- extreme janky game-bending cards that can't be easily handled by the sim aren't able to be corrected for by the agents. if you absolutely need them, patch the sim.
  - we included river song's chaos emporium (`data/decks/riversong.txt`) as an example: this deck will probably not be playable in any sane manner, the sim does not have a way to handle many of its mechanics.
  - no extra turns or weird turn order, the turn loop is a fixed rotation.
  - no shared zones (knowledge pool type stuff): cards live in exactly one player's zones.
- the stack is weakly handled, so long counterspell wars etc aren't always possible - spells give opponents a chance to react, but there's currently no infra for reacting-in-response-to-a-reaction.
- if agents are slow, their window will time out and the harness will pass its turn after 10 minutes.
- pumps, clones, attachments, first strike, commander damage, poison, floating mana, and "doesn't untap" are all manually tracked by agents, not in the sim. so there's potential for issues if the agents aren't on top of things.

in general - typical magic plays fine, but izzet solitaire or certain types of combo decks will be jank or unplayable. patch the sim if you're playing those.
