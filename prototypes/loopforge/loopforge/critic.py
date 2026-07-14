"""The critic — a deterministic judge that scores drafts and returns
actionable feedback. This is what makes the loop converge instead of drift.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .llm import ChatClient

CRITIC_SYSTEM = """\
You are a rigorous writing critic. Score the draft against the writing task.
Respond with ONLY a JSON object, no prose, matching exactly:

{
  "scores": {"clarity": 0-10, "accuracy": 0-10, "structure": 0-10, "style": 0-10},
  "overall": 0-10,
  "issues": ["specific, actionable issue", ...],
  "kept_strengths": ["what must NOT be lost in revision", ...],
  "recurring_pattern": "one short sentence naming a habitual weakness, or empty string"
}

Rules:
- "overall" must reflect the weakest dimension, not the average.
- Issues must be concrete enough that a reviser can act on each one.
- Do not raise scores out of politeness. 8.5+ means publishable as-is.
- "recurring_pattern" is for a weakness that looks habitual (e.g. "opens every
  paragraph with 'However'"), suitable to remember for future sessions.
"""


@dataclass
class Critique:
    overall: float
    scores: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    kept_strengths: list = field(default_factory=list)
    recurring_pattern: str = ""

    def feedback_block(self) -> str:
        issues = "\n".join(f"- {i}" for i in self.issues) or "- (none)"
        keep = "\n".join(f"- {s}" for s in self.kept_strengths) or "- (none)"
        return f"### Fix these issues\n{issues}\n\n### Preserve these strengths\n{keep}"


class Critic:
    def __init__(self, client: ChatClient, model: str, temperature: float = 0.0):
        self.client = client
        self.model = model
        self.temperature = temperature

    def review(self, task: str, draft: str) -> Critique:
        user = f"## Writing task\n{task}\n\n## Draft to score\n{draft}"
        raw = self.client.complete(
            model=self.model,
            system=CRITIC_SYSTEM,
            user=user,
            temperature=self.temperature,
        )
        data = _parse_json_object(raw)
        return Critique(
            overall=float(data.get("overall", 0.0)),
            scores={k: float(v) for k, v in dict(data.get("scores", {})).items()},
            issues=[str(i) for i in data.get("issues", [])],
            kept_strengths=[str(s) for s in data.get("kept_strengths", [])],
            recurring_pattern=str(data.get("recurring_pattern", "")).strip(),
        )


def _parse_json_object(raw: str) -> dict:
    """Tolerant JSON extraction — models wrap JSON in fences or prose."""
    raw = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", raw, re.DOTALL)
        if brace:
            raw = brace.group(0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"overall": 0.0, "issues": ["Critic returned unparseable output."]}
