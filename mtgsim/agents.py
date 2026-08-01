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
        for attempt in range(self.retries + 1):
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
        body = json.dumps({"model": self.model, "messages": self.messages,
                           "usage": {"include": True}}).encode()
        req = urllib.request.Request(self.URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/lyramakesmusic/mtgsim_for_agents",
            "X-Title": "mtgsim for agents"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            resp = json.load(r)
        if resp.get("error"):
            raise RuntimeError(f"openrouter: {str(resp['error'])[:200]}")
        msg = (resp.get("choices") or [{}])[0].get("message", {})
        text = (msg.get("content") or "").strip() or (msg.get("reasoning") or "")
        u = resp.get("usage") or {}
        self.tokens["in"] += u.get("prompt_tokens", 0)
        self.tokens["out"] += u.get("completion_tokens", 0)
        self.cost_usd += u.get("cost") or 0.0
        self.messages.append({"role": "assistant", "content": text})
        self.session_id = "local"                 # engine may now send deltas
        return text


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
        hand = [h.strip() for h in m.group(1).split(",")] if m else []
        lands = [h for h in hand if "Land" in self.db.get(h, {}).get("type", "")]
        if "Lands you've played this turn: 0" in prompt and lands:
            return json.dumps({"action": "play_land", "card": lands[0], "narration": "mock land"})
        return FALLBACK


AGENT_TYPES = {"claude": ClaudeAgent, "codex": CodexAgent, "openrouter": OpenRouterAgent}
