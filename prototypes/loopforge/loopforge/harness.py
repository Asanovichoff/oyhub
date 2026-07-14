"""The harness — wires memory, loop, and learning write-back together.

Order of operations per run (this IS the closed learning loop):

  1. SNAPSHOT   — freeze Obsidian memory (style + lessons) at run start
  2. LOOP       — generate → critique → revise until a stop rule fires
  3. FINALIZE   — best-scoring draft wins
  4. LEARN      — if the critic flagged a recurring pattern, persist it as a
                  lesson in the vault (visible to the NEXT run's snapshot,
                  never this one — cache-safe frozen snapshot semantics)
  5. RECORD     — session note with wiki-links into the vault graph
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import ForgeConfig
from .critic import Critic
from .engine import LoopResult, RefinementLoop
from .llm import ChatClient, OpenAICompatClient
from .vault import ObsidianVault


@dataclass
class ForgeOutcome:
    result: LoopResult
    session_note: Optional[Path]
    lesson_learned: str


class Forge:
    def __init__(self, cfg: ForgeConfig, client: Optional[ChatClient] = None):
        self.cfg = cfg
        self.client = client or OpenAICompatClient(cfg.model)
        self.vault = ObsidianVault(cfg.vault)

    def run(self, task: str, title: str = "", write_session: bool = True) -> ForgeOutcome:
        # 1. snapshot (frozen for the whole run)
        memory = self.vault.snapshot()

        # 2-3. loop + finalize
        critic = Critic(
            self.client,
            model=self.cfg.model.resolved_critic_model(),
            temperature=self.cfg.model.critic_temperature,
        )
        loop = RefinementLoop(
            client=self.client,
            critic=critic,
            cfg=self.cfg.loop,
            model=self.cfg.model.model,
            temperature=self.cfg.model.temperature,
        )
        result = loop.run(task, memory)

        # 4. learn — persist recurring weaknesses for future runs
        lesson = ""
        patterns = [
            it.critique.recurring_pattern
            for it in result.iterations
            if it.critique.recurring_pattern
        ]
        if patterns:
            lesson = patterns[-1]  # the critic's most-informed observation
            self.vault.append_lesson(lesson)

        # 5. record
        note = None
        if write_session:
            note = self.vault.write_session_note(
                title=title or task[:60],
                prompt=task,
                final=result.final,
                history_md=result.history_md(),
            )

        return ForgeOutcome(result=result, session_note=note, lesson_learned=lesson)
