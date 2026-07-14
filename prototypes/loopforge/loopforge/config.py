"""Configuration for LoopForge.

Provider-agnostic: any OpenAI-compatible endpoint works (OpenAI, OpenRouter,
Nous Portal, local vLLM/Ollama). Set via env vars or CLI flags.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LoopConfig:
    """Stop rules for the refinement loop (the 'loop engineering' knobs)."""

    # Finalize as soon as the critic's overall score reaches this (0-10).
    score_threshold: float = 8.5
    # Hard cap on iterations — the iteration budget (Hermes pattern).
    max_iterations: int = 5
    # Plateau rule: stop if improvement over the previous iteration is below
    # this epsilon. Prevents burning budget on diminishing returns.
    plateau_epsilon: float = 0.25
    # How many consecutive sub-epsilon improvements count as a plateau.
    plateau_patience: int = 2


@dataclass
class ModelConfig:
    base_url: str = field(
        default_factory=lambda: os.getenv(
            "LOOPFORGE_BASE_URL", "https://openrouter.ai/api/v1"
        )
    )
    api_key: str = field(default_factory=lambda: os.getenv("LOOPFORGE_API_KEY", ""))
    # Generator/reviser model — your strong model.
    model: str = field(
        default_factory=lambda: os.getenv("LOOPFORGE_MODEL", "openai/gpt-4o")
    )
    # Critic model — can be the same model; a *different* model reduces
    # self-preference bias (a model grading its own prose scores it higher).
    critic_model: str = field(
        default_factory=lambda: os.getenv("LOOPFORGE_CRITIC_MODEL", "")
    )
    temperature: float = 0.7
    critic_temperature: float = 0.0  # judges should be deterministic

    def resolved_critic_model(self) -> str:
        return self.critic_model or self.model


@dataclass
class VaultConfig:
    """Where memory lives inside your Obsidian vault."""

    # Path to the vault root (the folder Obsidian opens).
    vault_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("LOOPFORGE_VAULT", "~/Obsidian/LoopForge")
        ).expanduser()
    )
    # Subfolder for LoopForge's memory notes.
    memory_folder: str = "LoopForge Memory"
    # Curated memory notes injected as a frozen snapshot each run.
    style_note: str = "Style.md"      # your voice/preferences (like Hermes USER.md)
    lessons_note: str = "Lessons.md"  # what the critic keeps flagging (like MEMORY.md)
    # Folder where per-run session notes (with iteration history) are written.
    sessions_folder: str = "Sessions"
    # Character budget for the injected snapshot — bounded memory, chars not
    # tokens (model-independent), same reasoning as Hermes.
    snapshot_char_limit: int = 6000


@dataclass
class ForgeConfig:
    loop: LoopConfig = field(default_factory=LoopConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    vault: VaultConfig = field(default_factory=VaultConfig)
