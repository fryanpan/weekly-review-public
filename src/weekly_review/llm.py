"""Anthropic SDK wrapper with prompt caching + on-disk response cache."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from anthropic import Anthropic


CACHE_MIN_SYSTEM_CHARS = 4096


class LLMClient:
    def __init__(self, api_key: str | None = None):
        self._client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        system_blocks: list[dict[str, Any]]
        if len(system) >= CACHE_MIN_SYSTEM_CHARS:
            system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        else:
            system_blocks = [{"type": "text", "text": system}]

        resp = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_blocks,
            messages=[{"role": "user", "content": user}],
        )
        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text += block.text
        usage = resp.usage
        return {
            "text": text,
            "usage": {
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
            },
            "stop_reason": resp.stop_reason,
            "model": model,
        }


class CachedLLMClient:
    """Disk-cached wrapper. Idempotent on (model, system, user, max_tokens, temperature)."""

    def __init__(self, inner: LLMClient, cache_dir: Path):
        self._inner = inner
        self._dir = cache_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _key(self, model: str, system: str, user: str, max_tokens: int, temperature: float) -> str:
        h = hashlib.sha256()
        h.update(model.encode())
        h.update(b"\n--\n")
        h.update(system.encode())
        h.update(b"\n--\n")
        h.update(user.encode())
        h.update(f"\n--\n{max_tokens}:{temperature}".encode())
        return h.hexdigest()

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        key = self._key(model, system, user, max_tokens, temperature)
        cache_path = self._dir / f"{key}.json"
        if cache_path.exists():
            return json.loads(cache_path.read_text())
        result = self._inner.complete(
            system=system, user=user, model=model,
            max_tokens=max_tokens, temperature=temperature,
        )
        cache_path.write_text(json.dumps(result))
        return result
