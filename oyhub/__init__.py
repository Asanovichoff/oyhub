"""OyHub — a local, always-on agent harness for engineers.

One central skill library with per-project activation, persistent memory
(Obsidian vault + SQLite FTS5 recall), and a background curator loop —
exposed to Claude Desktop / Claude Code / Cursor as an MCP server.

Fixes two problems:
  1. Skill sprawl  — skills scattered across projects, nothing organized.
  2. Context exhaustion — knowledge lost when the context window fills up.
"""

__version__ = "0.1.0"
