"""The refinement loop — the 'loop engineering' core.

    draft = generate(task, memory)
    loop:
        critique = critic.review(task, draft)
        if stop_rule(critique, history): break
        draft = revise(task, draft, critique, memory)
    finalize -> best-scoring draft (not necessarily the last one)

Stop rules (checked in order):
  1. THRESHOLD  — critic overall >= score_threshold          (success)
  2. PLATEAU    — improvement < epsilon for `patience` steps (diminishing returns)
  3. BUDGET     — max_iterations exhausted                   (hard cap, Hermes-style)

Finalization always returns the *best* iteration by score. A revision can
regress; the loop must not reward the last word over the best word.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from .config import LoopConfig
from .critic import Critic, Critique
from .llm import ChatClient
from .vault import MemorySnapshot

GENERATOR_SYSTEM = """\
You are a skilled writer. Produce only the requested text — no preamble,
no meta-commentary, no "Here's the draft".

{memory_block}
"""

REVISER_USER = """\
## Writing task
{task}

## Current draft (iteration {n})
{draft}

## Critique to address
{feedback}

Rewrite the draft. Fix every listed issue, preserve every listed strength,
and keep the writer's voice from the system prompt. Output only the
revised text.
"""


class StopReason(str, Enum):
    THRESHOLD = "threshold"  # critic score met the bar
    PLATEAU = "plateau"      # improvements went sub-epsilon
    BUDGET = "budget"        # iteration cap hit


@dataclass
class Iteration:
    n: int
    draft: str
    critique: Critique


@dataclass
class LoopResult:
    final: str
    final_score: float
    stop_reason: StopReason
    iterations: list[Iteration] = field(default_factory=list)
    best_iteration: int = 0

    def history_md(self) -> str:
        lines = []
        for it in self.iterations:
            marker = " ← **finalized**" if it.n == self.best_iteration else ""
            lines.append(
                f"- Iteration {it.n}: overall **{it.critique.overall:.1f}**"
                f" ({', '.join(f'{k} {v:.0f}' for k, v in it.critique.scores.items())})"
                f"{marker}"
            )
        lines.append(f"- Stopped: {self.stop_reason.value}")
        return "\n".join(lines)


class RefinementLoop:
    def __init__(
        self,
        client: ChatClient,
        critic: Critic,
        cfg: LoopConfig,
        model: str,
        temperature: float = 0.7,
        on_iteration: Optional[Callable[[Iteration], None]] = None,
    ):
        self.client = client
        self.critic = critic
        self.cfg = cfg
        self.model = model
        self.temperature = temperature
        self.on_iteration = on_iteration or (lambda it: None)

    # -- loop ----------------------------------------------------------------

    def run(self, task: str, memory: MemorySnapshot) -> LoopResult:
        system = GENERATOR_SYSTEM.format(
            memory_block=memory.as_prompt_block() or "(no saved writer profile yet)"
        )
        iterations: list[Iteration] = []
        stop = StopReason.BUDGET
        flat_steps = 0

        draft = self.client.complete(
            model=self.model, system=system, user=task, temperature=self.temperature
        )

        for n in range(1, self.cfg.max_iterations + 1):
            critique = self.critic.review(task, draft)
            it = Iteration(n=n, draft=draft, critique=critique)
            iterations.append(it)
            self.on_iteration(it)

            # 1. threshold
            if critique.overall >= self.cfg.score_threshold:
                stop = StopReason.THRESHOLD
                break

            # 2. plateau
            if len(iterations) >= 2:
                gain = critique.overall - iterations[-2].critique.overall
                flat_steps = flat_steps + 1 if gain < self.cfg.plateau_epsilon else 0
                if flat_steps >= self.cfg.plateau_patience:
                    stop = StopReason.PLATEAU
                    break

            # 3. budget — don't spend a revision we'll never score
            if n == self.cfg.max_iterations:
                stop = StopReason.BUDGET
                break

            draft = self.client.complete(
                model=self.model,
                system=system,
                user=REVISER_USER.format(
                    task=task, draft=draft, n=n, feedback=critique.feedback_block()
                ),
                temperature=self.temperature,
            )

        # -- finalize: best score wins, not last --
        best = max(iterations, key=lambda i: i.critique.overall)
        return LoopResult(
            final=best.draft,
            final_score=best.critique.overall,
            stop_reason=stop,
            iterations=iterations,
            best_iteration=best.n,
        )
