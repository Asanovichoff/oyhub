"""OyHub MCP server — stdio JSON-RPC, no dependencies.

Implements the Model Context Protocol surface that Claude Desktop,
Claude Code, and Cursor speak: initialize / tools/list / tools/call.
Each tool call also gives the curator a chance to run (idle-triggered
loop engineering — the Hermes pattern of maintenance without a daemon).
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from . import __version__
from .config import HubConfig
from .curator import Curator
from .memory import SessionStore, VaultMemory
from .skills import SkillStore

PROTOCOL_VERSION = "2024-11-05"


def _tool(name: str, description: str, properties: dict,
          required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
        },
    }


class Hub:
    """All tool handlers in one place — server transport stays dumb."""

    def __init__(self, cfg: HubConfig | None = None):
        self.cfg = cfg or HubConfig()
        self.cfg.ensure_dirs()
        self.skills = SkillStore(self.cfg)
        self.vault = VaultMemory(self.cfg)
        self.sessions = SessionStore(self.cfg)
        self.curator = Curator(self.cfg, self.skills, self.vault)
        self.active_project = ""

    # ---- tool registry -------------------------------------------------------

    def tool_schemas(self) -> list[dict]:
        return [
            _tool("project_activate",
                  "Activate a project so skill listing and memory are scoped to it. "
                  "Creates the project profile if new.",
                  {"project": {"type": "string"},
                   "tags": {"type": "array", "items": {"type": "string"},
                            "description": "Skill tags relevant to this project."}},
                  ["project"]),
            _tool("skill_add",
                  "Save a reusable skill to the central library. Description must "
                  "be <=60 chars — it's the routing index.",
                  {"name": {"type": "string"},
                   "description": {"type": "string"},
                   "body": {"type": "string"},
                   "tags": {"type": "array", "items": {"type": "string"}}},
                  ["name", "description", "body"]),
            _tool("skill_list",
                  "Compact index of skills (name + 60-char description), filtered "
                  "to the active project's tags.",
                  {"project": {"type": "string",
                               "description": "Override the active project."}}),
            _tool("skill_view",
                  "Load one skill's full body (marks it as used).",
                  {"name": {"type": "string"}}, ["name"]),
            _tool("skill_search",
                  "Keyword search across all skills regardless of project.",
                  {"query": {"type": "string"}}, ["query"]),
            _tool("memory_add",
                  "Persist a curated memory entry (fact, preference, convention) "
                  "to the Obsidian vault. Scoped to the active project unless "
                  "global=true.",
                  {"entry": {"type": "string"},
                   "global": {"type": "boolean"}},
                  ["entry"]),
            _tool("memory_snapshot",
                  "Read the frozen memory snapshot (global + active project). "
                  "Call once at session start.",
                  {}),
            _tool("session_log",
                  "Log an exchange into searchable history (SQLite FTS5). Call "
                  "with the important content of a turn worth remembering.",
                  {"role": {"type": "string", "enum": ["user", "assistant"]},
                   "content": {"type": "string"},
                   "session_id": {"type": "string"}},
                  ["role", "content"]),
            _tool("session_search",
                  "Full-text search past conversations. Returns matches with "
                  "surrounding context. Use instead of asking the user to repeat.",
                  {"query": {"type": "string"},
                   "limit": {"type": "integer"}},
                  ["query"]),
            _tool("session_recent",
                  "Browse recent logged sessions chronologically.",
                  {"limit": {"type": "integer"}}),
            _tool("curator_run",
                  "Run hub maintenance now: archive stale skills, dedupe memory.",
                  {}),
            _tool("curator_pin",
                  "Pin a skill so the curator never auto-archives it.",
                  {"name": {"type": "string"}}, ["name"]),
        ]

    def call(self, name: str, args: dict) -> Any:
        handlers: dict[str, Callable[[dict], Any]] = {
            "project_activate": self._project_activate,
            "skill_add": lambda a: self.skills.add(
                a["name"], a["description"], a["body"], a.get("tags")).name
                + " saved",
            "skill_list": lambda a: self.skills.index(
                a.get("project") or self.active_project or None),
            "skill_view": self._skill_view,
            "skill_search": lambda a: self.skills.search(a["query"]),
            "memory_add": lambda a: self.vault.add(
                a["entry"],
                project=None if a.get("global") else (self.active_project or None)),
            "memory_snapshot": lambda a: self.vault.snapshot(
                self.active_project).as_text(),
            "session_log": lambda a: {"session_id": self.sessions.log(
                a["role"], a["content"], project=self.active_project,
                session_id=a.get("session_id", ""))},
            "session_search": lambda a: self.sessions.search(
                a["query"], limit=int(a.get("limit", 5))),
            "session_recent": lambda a: self.sessions.recent_sessions(
                int(a.get("limit", 10))),
            "curator_run": lambda a: self.curator.run().summary(),
            "curator_pin": self._curator_pin,
        }
        if name not in handlers:
            raise ValueError(f"unknown tool: {name}")
        result = handlers[name](args)
        # Idle-triggered background loop: every tool call is a chance for the
        # curator to catch up (cheap due-check, full run only when interval hit).
        if name != "curator_run":
            self.curator.maybe_run()
        return result

    def _project_activate(self, a: dict) -> str:
        project = a["project"].strip()
        if a.get("tags"):
            self.skills.set_project(project, a["tags"])
        elif self.skills.get_project(project) is None:
            self.skills.set_project(project, [])
        self.active_project = project
        n = len(self.skills.index(project))
        return f"project '{project}' active — {n} skill(s) in scope"

    def _skill_view(self, a: dict) -> str:
        skill = self.skills.get(a["name"])
        if skill is None:
            return f"skill not found: {a['name']}"
        return skill.to_markdown()

    def _curator_pin(self, a: dict) -> str:
        self.curator.pin(a["name"])
        return f"pinned: {a['name']}"


# ---------------------------------------------------------------------------
# JSON-RPC stdio transport
# ---------------------------------------------------------------------------

def handle_request(hub: Hub, req: dict) -> dict | None:
    """Pure request handler — testable without stdio."""
    rid = req.get("id")
    method = req.get("method", "")

    if method == "initialize":
        return _result(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "oyhub", "version": __version__},
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None  # notifications get no response
    if method == "tools/list":
        return _result(rid, {"tools": hub.tool_schemas()})
    if method == "tools/call":
        params = req.get("params", {})
        try:
            out = hub.call(params.get("name", ""), params.get("arguments", {}) or {})
            text = out if isinstance(out, str) else json.dumps(out, indent=2)
            return _result(rid, {"content": [{"type": "text", "text": text}]})
        except Exception as e:  # tool errors are results, not protocol errors
            return _result(rid, {
                "content": [{"type": "text", "text": f"error: {e}"}],
                "isError": True,
            })
    if method == "ping":
        return _result(rid, {})
    return _error(rid, -32601, f"method not found: {method}")


def _result(rid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def serve() -> None:
    """Blocking stdio loop: one JSON-RPC message per line."""
    hub = Hub()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_request(hub, req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
