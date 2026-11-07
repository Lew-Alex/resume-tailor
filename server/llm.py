"""LLM providers. Everything goes through `complete_json(prompt) -> dict`.

Default provider shells out to the Claude Code CLI in headless mode
(`claude -p`), so it uses whatever account the CLI is logged into — no API key.

Env vars:
  LLM_PROVIDER   claude (default) | mock
  CLAUDE_BIN     path to the claude binary (default: "claude" on PATH)
  CLAUDE_MODEL   model alias passed as --model (default: sonnet)
  LLM_TIMEOUT    seconds (default 300)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (handles ``` fences / chatter)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            raise LLMError(f"Model reply was not valid JSON: {e}") from e
    raise LLMError("Model reply contained no JSON object.")


def claude_binary() -> str | None:
    """CLAUDE_BIN, else `claude` on PATH, else the native installer's location."""
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude"
    return str(fallback) if fallback.exists() else None


async def _claude_cli(prompt: str) -> str:
    binary = claude_binary() or "claude"
    # Sonnet by default: ~3x faster and far cheaper than the CLI's default model
    # for these structured edit tasks. Override with CLAUDE_MODEL=opus etc.
    model = os.environ.get("CLAUDE_MODEL", "sonnet")
    args = [binary, "-p", "--output-format", "json", "--model", model]
    timeout = float(os.environ.get("LLM_TIMEOUT", "300"))

    # Run in an empty temp dir so the CLI has no project files to wander into.
    with tempfile.TemporaryDirectory() as cwd:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise LLMError(
                f"Could not find '{binary}'. Install Claude Code "
                "(https://docs.claude.com/en/docs/claude-code) or set CLAUDE_BIN."
            ) from e
        try:
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout)
        except asyncio.TimeoutError as e:
            proc.kill()
            raise LLMError(f"claude CLI timed out after {timeout:.0f}s") from e

    if proc.returncode != 0:
        raise LLMError(f"claude CLI failed ({proc.returncode}): {err.decode()[-800:]}")
    raw = out.decode()
    # --output-format json wraps the reply: {"type":"result","result":"...", ...}
    try:
        wrapper = json.loads(raw)
        if isinstance(wrapper, dict) and "result" in wrapper:
            if wrapper.get("is_error"):
                raise LLMError(f"claude CLI error: {wrapper.get('result')}")
            return wrapper["result"]
    except json.JSONDecodeError:
        pass
    return raw


async def _mock(prompt: str) -> str:
    """Deterministic fake replies so the app can be exercised without an AI."""
    if "TASK: TAILOR" in prompt:
        ids = re.findall(r'"id": "([^"]+)"', prompt.split("ITEMS:", 1)[1])
        scores = {i: {"s": (len(i) * 7) % 10 + 1, "why": "mock score"} for i in ids}
        return json.dumps({
            "title": "Mock Role", "company": "MockCo",
            "keywords": ["python", "c++", "ros 2"],
            "scores": scores,
            "summary": "Mock tailoring result.",
        })
    msg = prompt.rsplit("USER MESSAGE:", 1)[-1].strip()
    first = re.search(r'"experience": \[\s*\{\s*"id": "([^"]+)"', prompt)
    return json.dumps({
        "reply": f"(mock) I added a draft bullet from: {msg[:60]}",
        "ops": [{
            "op": "add_bullet", "parent": first.group(1) if first else "missing",
            "bullet": {"text": msg[:140], "tags": ["mock"], "evidence": "implemented", "status": "draft"},
        }],
    })


async def complete_json(prompt: str, retries: int = 1) -> dict:
    provider = os.environ.get("LLM_PROVIDER", "claude").lower()
    call = _mock if provider == "mock" else _claude_cli
    last: Exception | None = None
    for attempt in range(retries + 1):
        text = await call(prompt if attempt == 0 else
                          prompt + "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object.")
        try:
            return extract_json(text)
        except LLMError as e:
            last = e
    raise last  # type: ignore[misc]
