"""Minimal OpenAI-compatible chat client. Stdlib only — no dependencies.

Any provider exposing /chat/completions works: OpenAI, OpenRouter,
Nous Portal, local vLLM or Ollama (with an empty api_key).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Protocol

from .config import ModelConfig


class ChatClient(Protocol):
    """The seam that lets tests inject a fake LLM."""

    def complete(
        self, model: str, system: str, user: str, temperature: float
    ) -> str: ...


class OpenAICompatClient:
    def __init__(self, cfg: ModelConfig, timeout: float = 120.0):
        self.cfg = cfg
        self.timeout = timeout

    def complete(
        self, model: str, system: str, user: str, temperature: float
    ) -> str:
        payload = {
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        req = urllib.request.Request(
            self.cfg.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.cfg.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"LLM call failed ({e.code}): {body}") from e
        return data["choices"][0]["message"]["content"]
