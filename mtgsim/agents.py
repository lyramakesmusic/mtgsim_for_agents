"""Agent harness — how we talk to a brain.

The engine hands each agent a fully-built prompt and wants raw model text back.
Everything transport-shaped lives here: subprocess invocation, session
continuity (context tracking), retries, per-player transcripts, usage.

Implementations:
  ClaudeAgent — `claude -p` headless, session continuity via --resume.
  CodexAgent  — `codex exec` headless, session continuity via `codex exec resume`.
  MockAgent   — scripted dummy for plumbing tests. No subprocess.

With resume on (default) each player is one persistent session: the model
keeps its own memory of the game (its plans, what it's holding up, grudges)
across calls. The engine still serializes full authoritative state into every
prompt, so session memory is strategy, not ground truth — drift can't corrupt
the game.
"""
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

FALLBACK = '{"action":"pass"}'


class _SubprocessAgent:
    """Shared plumbing: transcripts, retry loop, fallback."""

    def __init__(self, label, model=None, resume=True, transcript_dir=None,
                 timeout=600, retries=2):
        self.label = label
        self.model = model
        self.resume = resume
        self.timeout = timeout
        self.retries = retries
        self.session_id = None
        self.calls = 0
        self.cost_usd = 0.0
        self.tokens = {"in": 0, "out": 0}
        self.transcript = None
        if transcript_dir:
            Path(transcript_dir).mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^\w.-]", "_", label)
            self.transcript = Path(transcript_dir) / f"{safe}.md"
            self.transcript.write_text(
                f"# transcript — {label} — {type(self).__name__} model={model or 'default'}\n")

    def _transcribe(self, prompt, reply):
        if self.transcript:
            with self.transcript.open("a") as f:
                f.write(f"\n---\n## call {self.calls}\n### prompt\n```\n{prompt}\n```\n"
                        f"### reply\n```\n{reply}\n```\n")

    def ask(self, prompt):
        self.calls += 1
        self.gave_up = False        # explicit failure flag — content can't signal it,
        for attempt in range(self.retries + 1):     # a real reply may equal FALLBACK
            try:
                reply = self._ask_once(prompt)
                if reply and reply.strip():
                    self._transcribe(prompt, reply)
                    return reply
                print(f"  !! {self.label}: empty reply (attempt {attempt})")
            except Exception as e:
                print(f"  !! {self.label}: {e} (attempt {attempt})")
            # a dead session can poison every retry — drop it and go fresh
            if self.resume and self.session_id and attempt >= 1:
                print(f"  !! {self.label}: dropping session {self.session_id}, starting fresh")
                self.session_id = None
            time.sleep(2)
        self._transcribe(prompt, "(harness gave up -> forced pass)")
        self.gave_up = True
        return FALLBACK

    def _ask_once(self, prompt):
        raise NotImplementedError


class ClaudeAgent(_SubprocessAgent):
    def __init__(self, label, model="opus", **kw):
        super().__init__(label, model=model, **kw)

    def _ask_once(self, prompt):
        cmd = ["claude", "-p", prompt, "--model", self.model, "--output-format", "json"]
        if self.resume and self.session_id:
            cmd += ["--resume", self.session_id]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        if r.returncode != 0 or not r.stdout.strip():
            raise RuntimeError(f"claude -p rc={r.returncode} stderr={r.stderr.strip()[:200]!r}")
        try:
            env = json.loads(r.stdout)
        except json.JSONDecodeError:
            return r.stdout            # not an envelope; treat stdout as the reply
        if self.resume:
            self.session_id = env.get("session_id", self.session_id)
        self.cost_usd += env.get("total_cost_usd") or 0.0
        u = env.get("usage") or {}
        self.tokens["in"] += u.get("input_tokens", 0)
        self.tokens["out"] += u.get("output_tokens", 0)
        if env.get("is_error"):
            raise RuntimeError(f"claude -p is_error: {str(env.get('result'))[:200]}")
        return env.get("result", "") or ""


class CodexAgent(_SubprocessAgent):
    """`codex exec` headless. Final message via -o file; session id + usage
    from the --json event stream. resume subcommand lacks --sandbox, so the
    sandbox is pinned via -c sandbox_mode config override on both paths.
    service_tier / reasoning effort ride along as -c overrides (what the
    interactive /fast toggle sets), so a run doesn't depend on ~/.codex."""

    def __init__(self, label, model=None, service_tier=None, effort=None, **kw):
        super().__init__(label, model=model, **kw)
        self.service_tier = service_tier
        self.effort = effort

    def _ask_once(self, prompt):
        with tempfile.NamedTemporaryFile(mode="r", suffix=".txt", delete=False) as tf:
            outfile = tf.name
        # NB: flag set must be the intersection of `exec` and `exec resume` —
        # resume rejects flags exec accepts (e.g. --color, --sandbox)
        common = ["-c", 'sandbox_mode="read-only"', "--skip-git-repo-check",
                  "--json", "-o", outfile]
        if self.service_tier:
            common += ["-c", f'service_tier="{self.service_tier}"']
        if self.effort:
            common += ["-c", f'model_reasoning_effort="{self.effort}"']
        if self.model:
            common += ["-m", self.model]
        if self.resume and self.session_id:
            cmd = ["codex", "exec", "resume", *common, self.session_id, prompt]
        else:
            cmd = ["codex", "exec", *common, prompt]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        if r.returncode != 0:
            raise RuntimeError(f"codex exec rc={r.returncode} stderr={r.stderr.strip()[:200]!r}")
        for line in r.stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "thread.started" and self.resume:
                self.session_id = ev.get("thread_id", self.session_id)
            elif ev.get("type") == "turn.completed":
                u = ev.get("usage") or {}
                self.tokens["in"] += u.get("input_tokens", 0)
                self.tokens["out"] += u.get("output_tokens", 0) + u.get("reasoning_output_tokens", 0)
        out = Path(outfile)
        reply = out.read_text() if out.exists() else ""
        out.unlink(missing_ok=True)
        return reply


class OpenRouterAgent(_SubprocessAgent):
    """OpenRouter chat-completions backend — seats OSS models (kimi, deepseek,
    qwen...) at the table. No server-side sessions, so we keep the message
    list ourselves. Context trims from the middle when it grows: safe by
    design, because every engine update carries an authoritative state digest
    and the opening brief (rules + protocol + gameplan) is pinned as message
    zero. Needs OPENROUTER_API_KEY in the environment."""

    URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, label, model=None, context_budget=240_000, **kw):
        if not model:
            raise SystemExit("openrouter seats need a model: openrouter@provider/slug:deck")
        super().__init__(label, model=model, **kw)
        self.api_key = os.environ.get("OPENROUTER_API_KEY")
        if not self.api_key:
            raise SystemExit("set OPENROUTER_API_KEY to seat openrouter agents")
        self.messages = []
        self.context_budget = context_budget      # chars, ~4 chars/token

    def _size(self):
        return sum(len(m.get("content") or "") for m in self.messages)

    def _trim(self):
        """Drop oldest turns after the pinned brief until under budget."""
        if self._size() <= self.context_budget:
            return
        marker = {"role": "user", "content":
                  "(earlier turns trimmed to fit context — your opening brief above still "
                  "applies, and the latest state digest below is authoritative)"}
        while self._size() > self.context_budget and len(self.messages) > 3:
            del self.messages[1]
        if marker["content"] not in (self.messages[1].get("content") or ""):
            self.messages.insert(1, marker)

    def _ask_once(self, prompt):
        if self.session_id is None:               # fresh session (first call or post-drop)
            self.messages = []
        self.messages.append({"role": "user", "content": prompt})
        self._trim()
        payload = {"messages": self.messages, "usage": {"include": True}}
        if self.model:                            # omit rather than send "model": null
            payload["model"] = self.model
        req = urllib.request.Request(self.URL, data=json.dumps(payload).encode(),
                                     method="POST", headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/lyramakesmusic/mtgsim_for_agents",
            "X-Title": "mtgsim for agents"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            resp = json.load(r)
        if resp.get("error"):
            raise RuntimeError(f"{type(self).__name__}: {str(resp['error'])[:200]}")
        msg = (resp.get("choices") or [{}])[0].get("message", {})
        text = (msg.get("content") or "").strip() or (msg.get("reasoning") or "")
        u = resp.get("usage") or {}
        self.tokens["in"] += u.get("prompt_tokens", 0)
        self.tokens["out"] += u.get("completion_tokens", 0)
        self.cost_usd += u.get("cost") or 0.0
        self.messages.append({"role": "assistant", "content": text})
        self.session_id = "local"                 # engine may now send deltas
        return text


class LocalAgent(OpenRouterAgent):
    """openai-compat server on localhost (LM Studio, llama.cpp, ollama...).
    Same message-list + pinned-brief trimming as OpenRouterAgent — one
    implementation, so the twins can't drift apart. No key needed; model
    optional (`local@name:deck` targets a specific loaded model, bare
    `local:deck` takes whatever the server default is). context_budget is
    chars, ~4 chars/token — 32k chars ≈ 8k tokens; size it to the server's
    loaded context, the opening brief alone runs >10k chars."""

    def __init__(self, label, model=None, port=1234, context_budget=32_000, **kw):
        _SubprocessAgent.__init__(self, label, model=model, **kw)
        self.URL = f"http://localhost:{port}/v1/chat/completions"
        self.api_key = "local"                    # servers ignore it; header needs a value
        self.messages = []
        self.context_budget = context_budget

_HBOLD, _HDIM, _HGREY, _HITAL, _HBANNER, _HRESET = \
    "\033[1m", "\033[2m", "\033[90m", "\033[3m", "\033[1;7m", "\033[0m"
_PASS_WORDS = {"", "pass", "done", "nah", "no", "n", "nothing", "nope", "skip"}


class HumanAgent:
    """A human seat. The engine has no idea: it hands over the same prompts it
    gives every agent and gets protocol JSON back. In between sits a REPL —
    the human types plain words at a `you>` prompt, and a scribe (a claude or
    codex session with full engine context) answers questions, translates
    committed intent into the action JSON, and does the bookkeeping (tap
    lists, effect atoms, triggers) the human shouldn't have to type.

    Cheap paths never wake the scribe: enter/'nah' passes a response window,
    'done' ends your turn, 'keep'/'mull' answer the opening hand, 'hand' and
    'board' reprint state locally. Engine prompts that arrive while the human
    isn't talking are buffered and delivered to the scribe on the next real
    exchange, so its picture of the game stays complete.

    One terminal caveat: lines typed while agents are thinking are drained by
    the judge channel — type at the `you>` prompt and you're the player; type
    between prompts and you're the spectator heckling from the booth."""

    BRIEF = """You are the scribe for the *human* piloting this seat — their interface to the game engine, not a player yourself. Engine prompts addressed to this seat are forwarded to you verbatim (marked ENGINE); the human's typed words arrive marked HUMAN. The human watches the public game log live and is shown their hand, but never sees the engine prompts — you are their eyes for everything else.

Your jobs:
- answer their questions ("is the dragon untapped?", "what does that do?") from the authoritative engine state, briefly — one or two sentences, this is table talk not a briefing
- when they commit to a play ("tap three forests and cast the nest"), translate it into exactly one protocol JSON action, filling in the bookkeeping they shouldn't have to type: tap lists with real permanent ids, effect atoms for every consequence, triggers, life deltas
- when a fix is needed (you got the bookkeeping wrong, a judge ruled, the table agreed something's off), use {"action":"correct","effects":[...],"narration":"what was wrong"} — it applies directly with no stack or announcement. Never dress a fix up as a cast or activation; the table reads that as a new illegal play and piles on
- when an ENGINE prompt marks the start of their turn (full state), open with a sitrep chat before they say anything: their battlefield summarized in plain words with repeats grouped ("four forests, two dorks, squirrel girl, five squirrel tokens"), how much mana is untapped, and one line on the biggest threat at the table
- warn once, briefly, if a committed play looks illegal or misses a cost — then their call stands
- never choose plays for them; if intent is ambiguous or a named card doesn't match the board, ask instead of guessing
- remember multi-play intents: if they said "cast X and Y" and only X has resolved, a bare "go"/"continue" (or the next window arriving with them saying so) means carry out Y — don't make them repeat themselves
- refer to the other players as they/them — you don't know who's piloting those seats

Reply with exactly one JSON object every time:
  {"chat": "..."} — private words to the human; these never reach the table
  or a protocol action object — only once the human has clearly committed. You may include "chat" alongside an action as a short note, and "table_talk" when the human wants to say something to the table.
Don't use a "thinking" field. When the human tells you to pass, decline, or end their turn in their own words ("pass turn", "nothing else", "that's it for me"), reply {"action":"pass","chat":"short ack"} — passing on their behalf is part of the job. What you must never do is pass on your *own* initiative when they haven't declined: an unprompted pass throws away their turn."""

    def __init__(self, label, scribe):
        self.label = label
        self.scribe = scribe
        self.resume = True
        self.session_id = None      # set on first ask so the engine switches to deltas
        self.pending = []           # engine prompts the scribe hasn't seen yet
        self.queued_talk = []       # "quoted lines" — table talk riding the next reply
        self.last_prompt = ""
        self.briefed = False

    # usage plumbing — play.py's report reads these off every seat
    @property
    def calls(self): return self.scribe.calls
    @property
    def cost_usd(self): return self.scribe.cost_usd
    @property
    def tokens(self): return self.scribe.tokens

    @staticmethod
    def _instruction(prompt):
        m = re.search(r"=== INSTRUCTION ===\n(.*?)(?:\nSchema:|\nReply per|\Z)", prompt, re.S)
        return (m.group(1) if m else prompt[-500:]).strip()

    @staticmethod
    def _line_starting(prompt, prefix):
        for ln in prompt.splitlines():
            if ln.startswith(prefix):
                return ln
        return None

    def _banner(self, text):
        print(f"\a\n{_HBANNER} --- {text} --- {_HRESET}")

    def _show(self, prompt, instr, mainphase):
        hand = self._line_starting(prompt, "YOUR HAND")
        stack = self._line_starting(prompt, "STACK (top first)")
        if instr.startswith("RESPONSE WINDOW"):
            self._banner("RESPOND?")
            print(f"{_HDIM}{instr.split('. You may')[0]}{_HRESET}")
            if stack:
                print(f"{_HDIM}{stack}{_HRESET}")
            print(f"{_HDIM}(enter/'nah' passes — or say what you want to do){_HRESET}")
            return
        if mainphase:
            self._banner("YOUR TURN")
            turnline = self._line_starting(prompt, "TURN ")
            if turnline:
                print(f"{_HDIM}{turnline}{_HRESET}")
            if hand:
                print(f"{_HBOLD}{hand}{_HRESET}")
            print(f"{_HDIM}(say plays in plain words — 'done' ends your turn, 'hand'/'board' reprint state){_HRESET}")
            return
        if instr.startswith("OPENING HAND"):
            self._banner("OPENING HAND")
            if hand:
                print(f"{_HBOLD}{hand}{_HRESET}")
            print(f"{_HDIM}(enter/'keep' keeps, 'mull' mulligans — or ask the scribe){_HRESET}")
            return
        if instr.startswith("COMBAT"):
            self._banner("BLOCK?")
        else:
            self._banner("YOUR CALL")
        print(f"{_HDIM}{instr}{_HRESET}")
        if hand and instr.startswith(("CLEANUP", "London mulligan")):
            print(f"{_HBOLD}{hand}{_HRESET}")

    def _print_state(self):
        m = re.search(r"===\s*(?:FULL STATE|STATE DIGEST|GAME STATE)[^\n]*===\n(.*?)"
                      r"(?:\n=== INSTRUCTION|\Z)", self.last_prompt, re.S)
        print(f"{_HDIM}{(m.group(1) if m else self.last_prompt).strip()}{_HRESET}")

    def _converse(self, text):
        """Buffered engine prompts + one human line → scribe. Returns a JSON
        action string to hand back to the engine, or None to keep talking."""
        parts = []
        if not self.briefed:
            parts.append(self.BRIEF)
            self.briefed = True
        parts += [f"=== ENGINE → THIS SEAT ===\n{p}" for p in self.pending]
        self.pending = []
        parts.append(f"=== HUMAN ===\n{text}")
        raw = self.scribe.ask("\n\n".join(parts))
        if getattr(self.scribe, "gave_up", False):
            print("  !! scribe error — try again")
            return None
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            obj = json.loads(m.group(0)) if m else None
        except Exception:
            obj = None
        if obj is None:
            print(f"{_HGREY}{_HITAL}scribe (unparsed): {raw.strip()[:400]}{_HRESET}")
            return None
        chat = obj.pop("chat", None)
        if chat:
            print(f"{_HGREY}{_HITAL}scribe: {chat}{_HRESET}")
        if not obj.get("action") and "bottom" not in obj:
            return None                    # chat-only reply — conversation continues
        return obj

    def _with_talk(self, payload):
        """Attach queued "quoted" table talk, trace, and serialize for the engine."""
        if self.queued_talk:
            talk = " ".join(self.queued_talk)
            self.queued_talk = []
            prior = payload.get("table_talk")
            payload["table_talk"] = f"{prior} {talk}".strip() if prior else talk
        if payload != {"action": "pass"}:
            print(f"{_HDIM}  → {json.dumps(payload)}{_HRESET}")
        return json.dumps(payload)

    def ask(self, prompt):
        self.pending.append(prompt)
        self.last_prompt = prompt
        self.session_id = self.session_id or "human"   # engine: send deltas now
        instr = self._instruction(prompt)
        mainphase = instr.startswith("It is your MAIN PHASE")
        opening = instr.startswith("OPENING HAND")
        self._show(prompt, instr, mainphase)
        if "FULL STATE (start of your turn)" in prompt:
            self._converse("(the human sits down for their turn — sitrep, please)")
        while True:
            try:
                line = input(f"{_HBOLD}you> {_HRESET}").strip()
            except EOFError:
                return FALLBACK
            low = line.lower()
            if low in _PASS_WORDS:
                if mainphase and low == "":
                    print(f"{_HDIM}  ('done' ends your turn — anything else, just say it){_HRESET}")
                    continue
                return self._with_talk({"action": "pass"})
            if len(line) >= 2 and line[0] == line[-1] == '"':
                self.queued_talk.append(line[1:-1].strip())
                print(f"{_HDIM}  (queued for the table — lands with your next play or pass){_HRESET}")
                continue
            if opening and low == "keep":
                return self._with_talk({"action": "keep"})
            if opening and low in ("mull", "mulligan"):
                return self._with_talk({"action": "mulligan"})
            if low == "hand":
                h = self._line_starting(self.last_prompt, "YOUR HAND")
                print(f"{_HBOLD}{h or '(hand not in the last engine prompt — ask the scribe)'}{_HRESET}")
                continue
            if low == "board":
                self._print_state()
                continue
            action = self._converse(line)
            if action is not None:
                return self._with_talk(action)


class MockAgent:
    """Dumb plumbing-test agent: plays a land, casts nothing, passes."""

    def __init__(self, label, db):
        self.label = label
        self.db = db
        self.calls = 0
        self.cost_usd = 0.0
        self.tokens = {"in": 0, "out": 0}

    def ask(self, prompt):
        self.calls += 1
        if "RESPONSE WINDOW" in prompt or "COMBAT" in prompt or '"concede"' in prompt or '"accept"' in prompt:
            return '{"action":"pass","concede":false,"accept":false,"reason":"mock never concedes"}'
        m = re.search(r"YOUR HAND \(\d+\): (.*)", prompt)
        hand = [h.strip() for h in m.group(1).split(";")] if m else []
        lands = [h for h in hand if "Land" in self.db.get(h, {}).get("type", "")]
        if "Lands you've played this turn: 0" in prompt and lands:
            return json.dumps({"action": "play_land", "card": lands[0], "narration": "mock land"})
        return FALLBACK


AGENT_TYPES = {"claude": ClaudeAgent, "codex": CodexAgent, "openrouter": OpenRouterAgent, "local": LocalAgent}
