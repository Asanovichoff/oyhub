"""The curator — loop engineering in the background.

Runs periodically (and on server idle) to keep the hub healthy without any
LLM calls in the MVP — pure policy, borrowed from Hermes' curator invariants:

  * NEVER deletes — stale skills are archived (recoverable).
  * Pinned skills bypass all automation.
  * Dedupes curated-memory bullets (case-insensitive).
  * Records every action it takes in a curator log note in the vault,
    so the loop's work is visible to the user.
"""

from __future__ import annotations

import datetime as _dt
import json
import time
from dataclasses import dataclass, field

from .config import HubConfig
from .memory import VaultMemory
from .skills import SkillStore


@dataclass
class CuratorReport:
    archived_skills: list[str] = field(default_factory=list)
    deduped_notes: dict = field(default_factory=dict)  # note name -> removed count
    ran_at: str = ""

    def summary(self) -> str:
        bits = []
        if self.archived_skills:
            bits.append(f"archived {len(self.archived_skills)} stale skill(s): "
                        + ", ".join(self.archived_skills))
        removed = sum(self.deduped_notes.values())
        if removed:
            bits.append(f"removed {removed} duplicate memory entr(ies)")
        return "; ".join(bits) or "nothing to do — hub is healthy"


class Curator:
    def __init__(self, cfg: HubConfig, skills: SkillStore, vault: VaultMemory):
        self.cfg = cfg
        self.skills = skills
        self.vault = vault

    # -- scheduling ------------------------------------------------------------

    def due(self) -> bool:
        state = self._read_state()
        last = state.get("curator_last_run", 0.0)
        return time.time() - last >= self.cfg.curator_interval_hours * 3600

    def maybe_run(self) -> CuratorReport | None:
        return self.run() if self.due() else None

    # -- the loop body -----------------------------------------------------------

    def run(self) -> CuratorReport:
        report = CuratorReport(ran_at=_dt.datetime.now().isoformat(" ", "minutes"))
        report.archived_skills = self._archive_stale_skills()
        report.deduped_notes = self._dedupe_memory_notes()
        self._write_state()
        self._log_to_vault(report)
        return report

    def pin(self, skill_name: str) -> None:
        state = self._read_state()
        pinned = set(state.get("pinned", []))
        pinned.add(skill_name)
        state["pinned"] = sorted(pinned)
        self.cfg.state_file.write_text(json.dumps(state, indent=2))

    def pinned(self) -> set[str]:
        return set(self._read_state().get("pinned", []))

    # -- policies ---------------------------------------------------------------

    def _archive_stale_skills(self) -> list[str]:
        cutoff = time.time() - self.cfg.skill_stale_days * 86400
        usage = self.skills.usage()
        pinned = self.pinned()
        archived = []
        for name in self.skills.all_names():
            if name in pinned:
                continue
            rec = usage.get(name)
            if rec is None:
                continue  # never used since tracking began — grace period
            if rec.get("last_used", time.time()) < cutoff:
                if self.skills.remove(name):  # remove() archives, never deletes
                    archived.append(name)
        return archived

    def _dedupe_memory_notes(self) -> dict:
        out: dict[str, int] = {}
        for path in sorted(self.vault.dir.glob("*.md")):
            if path.name.startswith("Curator"):
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            seen: set[str] = set()
            kept: list[str] = []
            removed = 0
            for line in lines:
                key = _normalize_bullet(line)
                if key and key in seen:
                    removed += 1
                    continue
                if key:
                    seen.add(key)
                kept.append(line)
            if removed:
                path.write_text("\n".join(kept) + "\n", encoding="utf-8")
                out[path.name] = removed
        return out

    # -- bookkeeping --------------------------------------------------------------

    def _read_state(self) -> dict:
        f = self.cfg.state_file
        return json.loads(f.read_text()) if f.exists() else {}

    def _write_state(self) -> None:
        state = self._read_state()
        state["curator_last_run"] = time.time()
        self.cfg.state_file.write_text(json.dumps(state, indent=2))

    def _log_to_vault(self, report: CuratorReport) -> None:
        path = self.vault.dir / "Curator Log.md"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"- {report.ran_at}: {report.summary()}\n")


def _normalize_bullet(line: str) -> str:
    """Key for dedupe: bullet text, lowercased, date-stamp stripped."""
    s = line.strip()
    if not s.startswith("- "):
        return ""
    s = s[2:]
    if s.endswith(")*") and "*(" in s:
        s = s[: s.rfind("*(")]
    return " ".join(s.lower().split())
