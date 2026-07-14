"""OyHub configuration. Everything lives under ~/.oyhub by default;
the Obsidian vault is wherever the user's vault already is."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _home() -> Path:
    return Path(os.getenv("OYHUB_HOME", "~/.oyhub")).expanduser()


@dataclass
class HubConfig:
    home: Path = field(default_factory=_home)
    # Obsidian vault root (the folder Obsidian opens). Memory notes go into
    # a "OyHub" subfolder there. If unset, falls back to ~/.oyhub/vault
    # (still plain markdown — you can open it as a vault later).
    vault_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("OYHUB_VAULT", "")
        ).expanduser()
        if os.getenv("OYHUB_VAULT")
        else _home() / "vault"
    )
    memory_folder: str = "OyHub"
    # Curated-memory snapshot budget (chars, not tokens — model-independent).
    snapshot_char_limit: int = 8000
    # Curator policy.
    curator_interval_hours: float = 6.0
    skill_stale_days: int = 90

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    @property
    def archive_dir(self) -> Path:
        return self.home / "archive"

    @property
    def db_path(self) -> Path:
        return self.home / "sessions.db"

    @property
    def projects_file(self) -> Path:
        return self.home / "projects.json"

    @property
    def state_file(self) -> Path:
        return self.home / "state.json"

    def ensure_dirs(self) -> None:
        for p in (self.home, self.skills_dir, self.archive_dir,
                  self.vault_path / self.memory_folder):
            p.mkdir(parents=True, exist_ok=True)
