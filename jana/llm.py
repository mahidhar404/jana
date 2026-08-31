"""Model routing.

Three tiers, chosen by what a failure would cost:

  code    — scheduling, matching, validation. ~90% of operations. Free, instant,
            identical every run. Anything that can live here, does.
  local   — Ollama. English explanation, conversation scaffolding, hints,
            second-opinion grading. A wrong answer here is visible to the
            learner as bad English, which he can detect.
  remote  — DeepSeek. German the learner is expected to learn *from*, and
            Schreiben grading against exam rubrics. A wrong answer here is
            invisible until roughly B1, i.e. exactly too late.

That last line is the whole reason the tier split exists. It is not about
capability, it is about *who can detect the error*.

Every call is timed and its outcome recorded, because a model is a dependency
with a latency distribution and a failure rate like any other, and the day one
starts degrading is the day that telemetry stops being optional.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

def _load_dotenv() -> None:
    """Read .env into the environment, without a dependency.

    Keys live in a gitignored file rather than in the shell, so a fresh
    terminal, a cron job and the test suite all see the same configuration and
    nothing secret reaches the repository.
    """
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


_load_dotenv()

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
LOCAL_MODEL = os.environ.get("JANA_LOCAL_MODEL", "gemma4:12b-mlx")

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = os.environ.get("JANA_DEEPSEEK_MODEL", "deepseek-chat")

LOCAL_TIMEOUT_S = 90
REMOTE_TIMEOUT_S = 60


@dataclass
class Reply:
    text: str
    model: str
    tier: str
    latency_ms: int
    ok: bool = True
    error: str = ""


@dataclass
class Telemetry:
    """In-process call log. Cheap, and the only way to notice slow degradation."""
    calls: list[dict[str, Any]] = field(default_factory=list)

    def record(self, reply: Reply) -> None:
        self.calls.append({
            "tier": reply.tier, "model": reply.model,
            "latency_ms": reply.latency_ms, "ok": reply.ok,
            "error": reply.error, "at": time.time(),
        })
        del self.calls[:-500]

    def summary(self) -> dict[str, Any]:
        by_tier: dict[str, dict[str, Any]] = {}
        for call in self.calls:
            bucket = by_tier.setdefault(call["tier"], {"n": 0, "fail": 0, "ms": []})
            bucket["n"] += 1
            bucket["fail"] += 0 if call["ok"] else 1
            bucket["ms"].append(call["latency_ms"])
        for bucket in by_tier.values():
            times = sorted(bucket.pop("ms"))
            bucket["p50_ms"] = times[len(times) // 2] if times else 0
            bucket["p95_ms"] = times[int(len(times) * 0.95)] if times else 0
        return by_tier


TELEMETRY = Telemetry()


def deepseek_keys() -> list[str]:
    """Every configured DeepSeek key, in preference order.

    More than one is not redundancy theatre: the overnight batch jobs and the
    interactive loop compete for the same rate limit, and a 429 during a study
    session is a worse failure than a slightly slower batch. The second key is
    tried when the first is rate-limited or unauthorised — not on ordinary
    errors, which would just double the damage.
    """
    keys = [os.environ.get("DEEPSEEK_API_KEY", ""),
            os.environ.get("DEEPSEEK_API_KEY_2", "")]
    return [k for k in keys if k]


def deepseek_available() -> bool:
    return bool(deepseek_keys())


def _post(url: str, payload: dict, timeout: int,
          headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def local(messages: list[dict[str, str]], *, temperature: float = 0.6,
          max_tokens: int = 400, model: str = LOCAL_MODEL) -> Reply:
    started = time.perf_counter()
    try:
        # `think: false` matters — the model otherwise spends its whole token
        # budget in a reasoning block and returns empty content.
        body = _post(f"{OLLAMA_URL}/api/chat", {
            "model": model, "messages": messages, "stream": False, "think": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }, LOCAL_TIMEOUT_S)
        text = (body.get("message") or {}).get("content", "").strip()
        reply = Reply(text, model, "local",
                      int((time.perf_counter() - started) * 1000), bool(text))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        reply = Reply("", model, "local",
                      int((time.perf_counter() - started) * 1000), False, str(exc))
    TELEMETRY.record(reply)
    return reply


# HTTP statuses where trying the other key is the right move: the request was
# fine, this particular credential was not.
KEY_FAILOVER_STATUSES = {401, 402, 429}


def remote(messages: list[dict[str, str]], *, temperature: float = 0.4,
           max_tokens: int = 600, model: str | None = None) -> Reply:
    keys = deepseek_keys()
    model = model or DEEPSEEK_MODEL
    if not keys:
        reply = Reply("", model, "remote", 0, False, "no DEEPSEEK_API_KEY")
        TELEMETRY.record(reply)
        return reply

    started = time.perf_counter()
    last_error = ""
    for index, key in enumerate(keys):
        try:
            body = _post(DEEPSEEK_URL, {
                "model": model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens,
                "stream": False,
            }, REMOTE_TIMEOUT_S, {"Authorization": f"Bearer {key}"})
            text = body["choices"][0]["message"]["content"].strip()
            reply = Reply(text, model, "remote",
                          int((time.perf_counter() - started) * 1000), bool(text))
            TELEMETRY.record(reply)
            return reply
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code in KEY_FAILOVER_STATUSES and index + 1 < len(keys):
                continue          # this credential is the problem; try the next
            break
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError,
                json.JSONDecodeError, OSError) as exc:
            last_error = str(exc)
            break

    reply = Reply("", model, "remote",
                  int((time.perf_counter() - started) * 1000), False, last_error)
    TELEMETRY.record(reply)
    return reply


def authored(messages: list[dict[str, str]], **kwargs: Any) -> Reply:
    """German the learner will learn from. Prefers the tier he cannot audit least."""
    if deepseek_available():
        reply = remote(messages, **kwargs)
        if reply.ok:
            return reply
    return local(messages, **kwargs)


def health() -> dict[str, Any]:
    return {
        "ollama": _ollama_up(),
        "local_model": LOCAL_MODEL,
        "deepseek": deepseek_available(),
        "deepseek_keys": len(deepseek_keys()),
        "remote_model": DEEPSEEK_MODEL,
        "telemetry": TELEMETRY.summary(),
    }


def _ollama_up() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False
