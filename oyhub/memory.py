"""Memory layer — the fix for context exhaustion.

Two stores, two jobs:

  1. OBSIDIAN VAULT (curated memory) — bounded markdown notes per project plus
     a global note. Human-readable, editable, graph-linked. Snapshot is read
     at session start and injected once (frozen-snapshot pattern: mid-session
     writes hit disk but never mutate an already-taken snapshot).

  2. SQLITE + FTS5 (raw recall) — every exchange the client chooses to log is
     full-text indexed. Recall is a *retrieval* call, not resident context:
     instead of keeping 200k tokens alive, you search for the 5 messages that
     matter. Zero LLM cost (BM25), returns actual stored text.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import HubConfig

_GLOBAL_NOTE = "Global.md"


# ---------------------------------------------------------------------------
# Curated memory — Obsidian vault
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Snapshot:
    global_notes: str
    project_notes: str
    project: str

    def as_text(self) -> str:
        parts = []
        if self.global_notes.strip():
            parts.append(f"## Global memory\n{self.global_notes.strip()}")
        if self.project_notes.strip():
            parts.append(f"## Project memory: {self.project}\n"
                         f"{self.project_notes.strip()}")
        return "\n\n".join(parts) or "(no curated memory yet)"


class VaultMemory:
    def __init__(self, cfg: HubConfig):
        self.cfg = cfg
        self.dir = cfg.vault_path / cfg.memory_folder
        self.dir.mkdir(parents=True, exist_ok=True)

    def _note_path(self, project: Optional[str]) -> Path:
        if project:
            safe = "".join(c for c in project if c.isalnum() or c in "-_ ")
            return self.dir / f"Project - {safe}.md"
        return self.dir / _GLOBAL_NOTE

    def add(self, entry: str, project: Optional[str] = None) -> str:
        entry = " ".join(entry.split())
        path = self._note_path(project)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if entry.lower() in existing.lower():
            return "duplicate — already remembered"
        stamp = _dt.date.today().isoformat()
        with path.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(f"- {entry} *({stamp})*\n")
        return f"saved to {path.name}"

    def snapshot(self, project: str = "") -> Snapshot:
        budget = self.cfg.snapshot_char_limit
        glob = self._read(self._note_path(None))
        proj = self._read(self._note_path(project)) if project else ""
        # Project memory gets priority; global fills the remainder, newest-first.
        proj = _keep_newest(proj, budget)
        glob = _keep_newest(glob, budget - len(proj))
        return Snapshot(global_notes=glob, project_notes=proj, project=project)

    def _read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.exists() else ""


def _keep_newest(text: str, budget: int) -> str:
    if budget <= 0:
        return ""
    if len(text) <= budget:
        return text
    kept, used = [], 0
    for line in reversed([ln for ln in text.splitlines() if ln.strip()]):
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(reversed(kept))


# ---------------------------------------------------------------------------
# Raw recall — SQLite + FTS5
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, content='messages', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


class SessionStore:
    def __init__(self, cfg: HubConfig):
        cfg.ensure_dirs()
        self.db_path = cfg.db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.executescript(_SCHEMA)
        return conn

    def log(self, role: str, content: str, project: str = "",
            session_id: str = "") -> str:
        session_id = session_id or uuid.uuid4().hex[:12]
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO messages (session_id, project, role, content, ts)"
                " VALUES (?,?,?,?,?)",
                (session_id, project, role, content, time.time()),
            )
        return session_id

    def search(self, query: str, project: str = "", limit: int = 5,
               window: int = 2) -> list[dict]:
        """FTS5 discovery: top hits (BM25), each with a ±window message
        context from its session. Zero LLM cost."""
        sql = (
            "SELECT m.id, m.session_id, m.project, m.role, m.content, m.ts "
            "FROM messages_fts f JOIN messages m ON m.id = f.rowid "
            "WHERE messages_fts MATCH ? "
        )
        args: list = [_fts_escape(query)]
        if project:
            sql += "AND m.project = ? "
            args.append(project)
        sql += "ORDER BY rank LIMIT ?"
        args.append(limit)

        with self._conn() as conn:
            hits = conn.execute(sql, args).fetchall()
            out = []
            seen_sessions: set[str] = set()
            for mid, sid, proj, role, content, ts in hits:
                if sid in seen_sessions:  # dedupe by session lineage
                    continue
                seen_sessions.add(sid)
                ctx = conn.execute(
                    "SELECT role, content FROM messages WHERE session_id=? "
                    "AND id BETWEEN ? AND ? ORDER BY id",
                    (sid, mid - window, mid + window),
                ).fetchall()
                out.append({
                    "session_id": sid,
                    "project": proj,
                    "when": _dt.datetime.fromtimestamp(ts).isoformat(" ", "minutes"),
                    "match": {"role": role, "content": content},
                    "context": [{"role": r, "content": c} for r, c in ctx],
                })
        return out

    def recent_sessions(self, limit: int = 10) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT session_id, project, MIN(ts), COUNT(*), "
                "substr(MIN(content), 1, 120) "
                "FROM messages GROUP BY session_id "
                "ORDER BY MIN(ts) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"session_id": sid, "project": proj,
             "started": _dt.datetime.fromtimestamp(ts).isoformat(" ", "minutes"),
             "messages": n, "preview": prev}
            for sid, proj, ts, n, prev in rows
        ]


def _fts_escape(query: str) -> str:
    """Quote each term — protects against FTS5 syntax errors on user input."""
    terms = [t.replace('"', '""') for t in query.split() if t]
    return " ".join(f'"{t}"' for t in terms)
