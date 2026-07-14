"""Obsidian vault memory adapter — the harness's memory layer.

Implements the Hermes frozen-snapshot pattern on top of an Obsidian vault:

  * Two curated notes (Style.md, Lessons.md) are read ONCE per run and
    injected into the generator's system prompt as a frozen snapshot.
    Writes during the run hit disk immediately but never change the
    injected snapshot — the prompt prefix stays stable (cache-safe).
  * Every run writes a session note into Sessions/ with wiki-links back
    to the memory notes, so Obsidian's graph view shows which lessons
    connect to which pieces of writing.
  * Lessons are appended as `- ` bullets; the snapshot is char-bounded and
    keeps the NEWEST lessons when trimming (recency wins under a budget).

An Obsidian vault is just a folder of markdown — no plugin or API needed.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from pathlib import Path

from .config import VaultConfig


@dataclass(frozen=True)
class MemorySnapshot:
    """Immutable snapshot taken at run start. Never mutated mid-run."""

    style: str
    lessons: str

    def as_prompt_block(self) -> str:
        parts = []
        if self.style.strip():
            parts.append(f"## Writer's voice & preferences\n{self.style.strip()}")
        if self.lessons.strip():
            parts.append(
                "## Lessons from past critiques (avoid repeating these)\n"
                + self.lessons.strip()
            )
        return "\n\n".join(parts)


class ObsidianVault:
    def __init__(self, cfg: VaultConfig):
        self.cfg = cfg
        self.memory_dir = cfg.vault_path / cfg.memory_folder
        self.sessions_dir = self.memory_dir / cfg.sessions_folder

    # -- snapshot (read) ----------------------------------------------------

    def snapshot(self) -> MemorySnapshot:
        style = self._read_note(self.cfg.style_note)
        lessons = self._read_note(self.cfg.lessons_note)
        budget = self.cfg.snapshot_char_limit
        # Style gets priority; lessons get whatever budget remains,
        # trimmed to the newest entries.
        style = style[:budget]
        lessons = _keep_newest_bullets(lessons, budget - len(style))
        return MemorySnapshot(style=style, lessons=lessons)

    def _read_note(self, name: str) -> str:
        path = self.memory_dir / name
        if not path.exists():
            return ""
        return _strip_frontmatter(path.read_text(encoding="utf-8"))

    # -- learning write-back ------------------------------------------------

    def append_lesson(self, lesson: str) -> None:
        """Durable immediately; visible to the *next* run's snapshot."""
        lesson = " ".join(lesson.split())
        if not lesson:
            return
        path = self.memory_dir / self.cfg.lessons_note
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if lesson.lower() in existing.lower():
            return  # dedupe: don't relearn the same lesson
        stamp = _dt.date.today().isoformat()
        with path.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(f"- {lesson} *({stamp})*\n")

    def write_session_note(
        self, title: str, prompt: str, final: str, history_md: str
    ) -> Path:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H%M")
        safe = re.sub(r"[^\w\s-]", "", title)[:60].strip() or "untitled"
        path = self.sessions_dir / f"{stamp} {safe}.md"
        style_link = f"[[{self.cfg.style_note.removesuffix('.md')}]]"
        lessons_link = f"[[{self.cfg.lessons_note.removesuffix('.md')}]]"
        path.write_text(
            f"---\ntags: [loopforge/session]\n---\n"
            f"# {title}\n\n"
            f"Memory used: {style_link}, {lessons_link}\n\n"
            f"## Prompt\n{prompt}\n\n"
            f"## Final output\n{final}\n\n"
            f"## Loop history\n{history_md}\n",
            encoding="utf-8",
        )
        return path


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip("\n")
    return text


def _keep_newest_bullets(text: str, budget: int) -> str:
    """Trim to the newest `- ` bullets that fit the char budget."""
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    lines = [l for l in text.splitlines() if l.strip()]
    kept: list[str] = []
    used = 0
    for line in reversed(lines):  # newest last -> walk backwards
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(reversed(kept))
