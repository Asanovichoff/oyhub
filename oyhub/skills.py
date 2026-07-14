"""Central skill library with per-project activation.

The fix for skill sprawl:
  * ONE store on disk (~/.oyhub/skills), each skill a folder with SKILL.md
    (YAML-ish frontmatter + markdown body). Compatible with the agentskills.io
    shape used by Claude skills and Hermes.
  * A compact INDEX (name + description, description hard-capped at 60 chars)
    is what clients list — full bodies load on demand. This is the Hermes
    routing trick: cheap index always in context, bodies only when needed.
  * PROJECT PROFILES map a project to skill tags. Activating a project
    filters the index to relevant skills only — no more loading your
    Kubernetes skills while writing a React app.
  * Usage tracking (last_used, use_count) feeds the curator's staleness policy.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import HubConfig

DESCRIPTION_LIMIT = 60
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass
class Skill:
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    body: str = ""

    def to_markdown(self) -> str:
        tags = ", ".join(self.tags)
        return (
            f"---\nname: {self.name}\ndescription: {self.description}\n"
            f"tags: [{tags}]\n---\n\n{self.body.strip()}\n"
        )


def _parse_skill_md(text: str, fallback_name: str) -> Skill:
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = text[end + 4 :].lstrip("\n")
    tags = [
        t.strip()
        for t in meta.get("tags", "").strip("[]").split(",")
        if t.strip()
    ]
    return Skill(
        name=meta.get("name", fallback_name),
        description=meta.get("description", "")[:DESCRIPTION_LIMIT],
        tags=tags,
        body=body,
    )


class SkillStore:
    def __init__(self, cfg: HubConfig):
        self.cfg = cfg
        cfg.ensure_dirs()

    # -- CRUD -----------------------------------------------------------------

    def add(self, name: str, description: str, body: str,
            tags: Optional[list[str]] = None) -> Skill:
        name = name.strip().lower().replace(" ", "-")
        if not _NAME_RE.match(name):
            raise ValueError(
                f"invalid skill name {name!r}: lowercase-hyphenated, <=64 chars"
            )
        description = " ".join(description.split())
        if len(description) > DESCRIPTION_LIMIT:
            raise ValueError(
                f"description is {len(description)} chars; hard cap is "
                f"{DESCRIPTION_LIMIT}. Anything longer gets truncated in the "
                f"index and never routes — shorten it."
            )
        skill = Skill(name=name, description=description,
                      tags=sorted(set(tags or [])), body=body)
        d = self.cfg.skills_dir / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(skill.to_markdown(), encoding="utf-8")
        return skill

    def get(self, name: str, track_usage: bool = True) -> Optional[Skill]:
        path = self.cfg.skills_dir / name / "SKILL.md"
        if not path.exists():
            return None
        if track_usage:
            self._touch_usage(name)
        return _parse_skill_md(path.read_text(encoding="utf-8"), name)

    def remove(self, name: str) -> bool:
        """Archive, never delete (curator invariant: archive is recoverable)."""
        src = self.cfg.skills_dir / name
        if not src.exists():
            return False
        dst = self.cfg.archive_dir / f"{name}-{int(time.time())}"
        shutil.move(str(src), str(dst))
        return True

    def all_names(self) -> list[str]:
        return sorted(
            p.name for p in self.cfg.skills_dir.iterdir()
            if (p / "SKILL.md").exists()
        )

    # -- index & per-project activation ----------------------------------------

    def index(self, project: Optional[str] = None) -> list[dict]:
        """Compact index: name + description (+tags). Filtered to the active
        project's tags when a project is given. This is what goes in context."""
        wanted = None
        if project:
            profile = self.get_project(project)
            if profile and profile.get("tags"):
                wanted = set(profile["tags"])
        out = []
        for name in self.all_names():
            skill = self.get(name, track_usage=False)
            if skill is None:
                continue
            if wanted is not None and not (wanted & set(skill.tags)):
                continue
            out.append({"name": skill.name,
                        "description": skill.description[:DESCRIPTION_LIMIT],
                        "tags": skill.tags})
        return out

    def search(self, query: str) -> list[dict]:
        q = query.lower()
        terms = [t for t in q.split() if t]
        out = []
        for name in self.all_names():
            skill = self.get(name, track_usage=False)
            if skill is None:
                continue
            hay = " ".join([skill.name, skill.description,
                            " ".join(skill.tags), skill.body]).lower()
            if all(t in hay for t in terms):
                out.append({"name": skill.name, "description": skill.description,
                            "tags": skill.tags})
        return out

    # -- project profiles -------------------------------------------------------

    def set_project(self, project: str, tags: list[str],
                    description: str = "") -> dict:
        data = self._read_projects()
        data[project] = {"tags": sorted(set(tags)), "description": description}
        self.cfg.projects_file.write_text(json.dumps(data, indent=2))
        return data[project]

    def get_project(self, project: str) -> Optional[dict]:
        return self._read_projects().get(project)

    def list_projects(self) -> dict:
        return self._read_projects()

    def _read_projects(self) -> dict:
        if self.cfg.projects_file.exists():
            return json.loads(self.cfg.projects_file.read_text())
        return {}

    # -- usage tracking (feeds the curator) --------------------------------------

    def _usage_file(self) -> Path:
        return self.cfg.home / "skill_usage.json"

    def usage(self) -> dict:
        f = self._usage_file()
        return json.loads(f.read_text()) if f.exists() else {}

    def _touch_usage(self, name: str) -> None:
        data = self.usage()
        rec = data.get(name, {"use_count": 0})
        rec["use_count"] = rec.get("use_count", 0) + 1
        rec["last_used"] = time.time()
        data[name] = rec
        self._usage_file().write_text(json.dumps(data, indent=2))
