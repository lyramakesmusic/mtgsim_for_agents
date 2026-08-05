#!/usr/bin/env python3
"""Agent postmortems for finished games — deck performance, not plot summary.

  uv run scripts/postmortem.py games/20260804_000350.md            # one game
  uv run scripts/postmortem.py games/tourney_20260805_*/           # a whole tourney dir
  uv run scripts/postmortem.py games/tourney_.../ --combine-only   # just re-synthesize

Per game: a codex agent (default tier — this is fire-and-forget, parallel gives
the throughput) reads the log and deck memos itself and writes
<game>.postmortem.md: verdict, inflection points, per-deck audits (wincon,
doctrine vs memo, card plus-minus, interaction ledger, threat clock, politics,
one-change), sim health in a separate channel, and a JSON block for
aggregation. Then one synthesis call over all JSON blocks writes REPORT.md:
win rates, combo-fire rates, recurring failure modes, proposed memo diffs.

The prompt encodes rules earned by iterating on a real game:
evidence rule (grep before claiming a card was drawn; zero log hits =
"never surfaced", a different diagnosis from "drawn but dead"), counts must
be countable from the log, and the winner's one_change means residual risk +
cheapest hedge (winners have nothing to "flip").
"""
import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mtgsim.agents import CodexAgent  # noqa: E402

PROMPT = """You are a Magic: The Gathering game analyst producing a deck-performance postmortem from a game log. Work from these files in the repo at {root} (read them with your tools; grep for headers like "## Turn" and "GAME OVER" to navigate):

- {log} — the full game log. Lines prefixed [private] are seats' hidden thinking (visible to you, never to other players at the time); use them to explain WHY pilots acted. If the log ends in a Python traceback, the engine crashed: treat the game as decided-up-to-that-point and say so plainly in the verdict.
{deckfiles}

Seats named in the log header map to those deck files; each deck file's first line is its strategy memo ("// strategy:"). Any seat may be human-piloted via an LLM scribe — the log reads the same.

Context on the sim: LLM agents are the rules engine; a deliberately dumb bookkeeper tracks state. Rules errors get argued at the table and repaired socially, so expect bookkeeping scuffles — report them, but keep them strictly OUT of deck-performance judgments (they get their own section).

Evidence rules (non-negotiable):
- Any claim that a card was drawn, held, or "dead in hand" must be backed by a log line — a cast, a "(Px drew: ...)" line, or a [private] mention. Grep the log for every card you name before asserting its game presence. Cards with zero log hits may only be discussed as "never surfaced" — a distinct category from "drawn but dead" (variance vs. card quality; they imply different deck edits).
- Quantified claims must be countable from the log: count the lines and give the real number, or say "not reliably countable".
- For the WINNING deck, "one change" means the biggest residual risk it exposed and the cheapest hedge — winners have nothing to flip.

Produce the postmortem in EXACTLY this structure, ordered most-information-first:

## Verdict
One line: outcome (state crash-truncation plainly if it happened — who held what position), turns reached; then elimination order — for each elimination: turn, proximate cause (the card/action that did it), root cause (the strategic error or inevitability behind it).

## Inflection points (max 4)
Turn-stamped moments where the win actually moved. For each: what resolved, what it changed, and the road not taken (what the losing side could have done in that exact window). Skip texture; only moments that decided things.

## Deck audits (one per seat, all seats)
- wincon: which win line was attempted; the memo's plan or improvised; did it fire; what stopped it. Which combo pieces surfaced but were never assembled.
- doctrine: memo rules followed/violated (quote the memo phrase), and whether each violation was punished by the game. A memo rule nobody follows, or that reads ambiguous when you try to apply it, is a DOCS BUG — flag it explicitly.
- card plus-minus: MVP cards (with the moment that earned the title), dead cards (surfaced, never mattered), and notable never-surfaced suites (e.g. "the deck's entire wipe package never appeared").
- interaction ledger: answers faced / answers held / answers actually spent. Removal aimed at this deck vs. protection it presented in response.
- threat clock: the turn the table collectively decided this deck was the problem, and what triggered it (cite thinking lines).
- politics: deals made/broken; whether the social game moved the outcome more than the cards did.
- one change: exactly one sentence — the single edit (card swap, memo line, or play choice) most likely to flip this deck's result (or, for the winner, hedge its closest brush).

## Sim health (separate channel — never mixed into deck audits)
Rules errors, judge interventions, bookkeeping failures, disputed plays, any crash: what happened mechanically, roughly what each cost in table-time, engine bug vs agent error.

## JSON
A fenced json block exactly of this shape:
{{"winner": str|null, "win_how": str, "turns": int, "crashed": bool,
 "eliminations": [{{"seat","deck","turn","proximate","root"}}],
 "decks": {{seat: {{"deck","wincon_attempted","wincon_fired": bool,"memo_violations": [str],"mvp": [str],"dead": [str],"never_surfaced": [str],"threat_clock_turn": int,"one_change": str}}}},
 "sim_health": {{"judge_calls": int, "atom_errors": int, "disputes": [str]}}}}

Constraints: be concrete — name cards, turn-stamp everything, quote table talk sparingly but lethally. No plot summary; every sentence must carry a decision-relevant fact someone could act on (a deck edit, a memo edit, or an engine fix). If the log contradicts a deck memo's theory of the deck, say so bluntly. Your final answer must be the complete markdown report and nothing else."""

COMBINE = """You are synthesizing {n} single-game Magic deck-performance postmortems into one tournament report. Below are the per-game JSON summaries (same pod each game). Produce a markdown report:

## Standings
Win counts by deck (crashed games: note who held the winning position), median game length, elimination-order tendencies.

## Wincon reality check
Per deck: how often the memo's win line actually fired vs. improvised lines vs. nothing. Combo pieces that repeatedly surfaced without assembling.

## Recurring failure modes
Ranked by frequency across games. Root causes, not proximate ones. Which one_change suggestions recur — those are the real signal.

## Card verdicts
Cards appearing in multiple MVP lists; cards appearing in multiple dead lists (cut candidates); suites that repeatedly never surfaced (density questions).

## Proposed memo diffs
For each deck with recurring doctrine violations or misfires: concrete replacement sentences for its strategy memo. Quote current phrase -> proposed phrase.

## Engine watchlist
Sim-health issues appearing in 2+ games.

Be terse and decision-dense. Every line should change a deck, a memo, or the engine.

=== PER-GAME JSON ===
{blobs}"""


def game_logs(paths):
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(x for x in p.glob("*.md") if not x.name.endswith((".postmortem.md", ".pretty.md")) and x.name != "REPORT.md")
        else:
            out.append(p)
    return out


def deck_files(log):
    head = log.read_text(errors="replace").splitlines()[0]
    decks = set(re.findall(r"P\d\(([^)]+)\)", head))
    lines = []
    for d in sorted(decks):
        f = ROOT / "data" / "decks" / f"{d}.txt"
        if f.exists():
            lines.append(f"- {f.relative_to(ROOT)} — deck + strategy memo for the seat(s) named ({d})")
    return "\n".join(lines)


def analyze(log, args):
    out = log.with_suffix(".postmortem.md")
    if out.exists() and not args.force:
        print(f"{log.name}: postmortem exists (--force to redo)")
        return out
    agent = CodexAgent(f"analyst-{log.stem}", model=args.codex_model, resume=False,
                       service_tier=None, effort=args.codex_effort, timeout=1800)
    prompt = PROMPT.format(root=ROOT, log=log.resolve(), deckfiles=deck_files(log))
    print(f"{log.name}: analyzing...")
    report = agent.ask(prompt)
    out.write_text(report)
    print(f"{log.name}: -> {out.name} ({len(report)} chars, "
          f"{agent.tokens['in']}/{agent.tokens['out']} tok)")
    return out


def combine(reports, args):
    blobs = []
    for r in reports:
        m = re.search(r"```json\n(.*?)```", r.read_text(), re.S)
        if not m:
            print(f"  !! {r.name}: no JSON block; skipped from synthesis")
            continue
        try:
            blobs.append(json.dumps(json.loads(m.group(1))))
        except json.JSONDecodeError:
            print(f"  !! {r.name}: JSON block unparseable; skipped")
    if len(blobs) < 2:
        print("fewer than 2 parseable games — no synthesis")
        return
    agent = CodexAgent("synthesizer", model=args.codex_model, resume=False,
                       effort=args.codex_effort, timeout=1800)
    report = agent.ask(COMBINE.format(n=len(blobs), blobs="\n".join(blobs)))
    dest = reports[0].parent / "REPORT.md"
    dest.write_text(report)
    print(f"synthesis -> {dest} ({len(blobs)} games)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="game .md files and/or tourney dirs")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="redo existing postmortems")
    ap.add_argument("--combine-only", action="store_true")
    ap.add_argument("--codex-model", default="gpt-5.6-terra")
    ap.add_argument("--codex-effort", default=None,
                    choices=["minimal", "low", "medium", "high"])
    args = ap.parse_args()

    logs = game_logs(args.paths)
    if not logs:
        sys.exit("no game logs found")
    if args.combine_only:
        reports = [l.with_suffix(".postmortem.md") for l in logs if l.with_suffix(".postmortem.md").exists()]
    else:
        with ThreadPoolExecutor(max_workers=args.parallel) as ex:
            reports = list(ex.map(lambda l: analyze(l, args), logs))
    if len(reports) > 1:
        combine(reports, args)
