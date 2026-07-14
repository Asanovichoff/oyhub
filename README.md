# OyHub

**A local, always-on agent harness that fixes skill sprawl and context exhaustion.**

Engineers using AI assistants hit two walls: skills scattered across projects with nothing organized in one place, and context windows that fill up and forget everything between sessions. OyHub is an MCP server you install once on your machine — Claude Desktop, Claude Code, and Cursor connect to it and gain:

- **One central skill library** with per-project activation. Skills live in `~/.oyhub/skills` (agentskills.io-compatible `SKILL.md` format). Activate a project and only its relevant skills appear — your Kubernetes skills stay out of your React sessions.
- **Persistent memory that survives any context window.** Curated facts go to your **Obsidian vault** as plain markdown (human-readable, editable, graph-linked). Full conversation history goes to **SQLite + FTS5** — full-text searchable recall at zero LLM cost. Instead of context running out, the assistant *retrieves* the five messages that matter.
- **Loop engineering in the background.** A curator loop runs on idle: archives stale skills (never deletes — archive is recoverable), dedupes memory entries, pins protect anything you care about, and every action is logged to a `Curator Log.md` note in your vault so the loop's work stays visible.
- **CI/CD + Docker from day one.** GitHub Actions runs lint + tests on a 6-way OS/Python matrix, builds the Docker image, and cuts a release on every version tag.

Zero runtime dependencies — Python stdlib only. No API key: the connected assistant is the intelligence; OyHub is the harness.

## Install

```bash
git clone https://github.com/YOURNAME/oyhub && cd oyhub
python3 -m oyhub doctor   # health check
```

Set where memory lives (your existing Obsidian vault):

```bash
export OYHUB_VAULT=~/Documents/MyVault
```

### Connect Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "oyhub": {
      "command": "python3",
      "args": ["-m", "oyhub"],
      "cwd": "/path/to/oyhub",
      "env": { "OYHUB_VAULT": "/Users/you/Documents/MyVault" }
    }
  }
}
```

### Connect Claude Code

```bash
claude mcp add oyhub -- python3 -m oyhub
```

### Docker

```bash
docker compose run --rm oyhub          # stdio MCP server
docker compose run --rm --entrypoint python oyhub -m oyhub doctor
```

## The tools it exposes

| Tool | What it does |
|---|---|
| `project_activate` | Scope skills + memory to a project (creates profile if new) |
| `skill_add` / `skill_view` / `skill_list` / `skill_search` | Central library; list shows the compact index (60-char descriptions), bodies load on demand |
| `memory_add` / `memory_snapshot` | Curated memory in your Obsidian vault, project-scoped or global |
| `session_log` / `session_search` / `session_recent` | FTS5-indexed history: log important turns, search them later with context windows |
| `curator_run` / `curator_pin` | Run maintenance now; pin skills the curator must never touch |

## A typical day

1. Start a session: Claude calls `project_activate("webapp")` and `memory_snapshot` — it now knows your conventions and this project's facts, without you repeating them.
2. Work normally. When something worth keeping comes up, Claude calls `memory_add` ("staging DB is read-only") or `skill_add` (a deploy procedure it just worked out).
3. Next week, in a *fresh* session: "how did we fix that CORS issue?" → `session_search("CORS")` returns the exact exchange. Nothing was lost when the old context window died.
4. Meanwhile the curator has deduped memory and archived skills untouched for 90 days — check `Curator Log.md` in Obsidian to see what it did.

## Architecture

```
Claude / Cursor  ──MCP (stdio JSON-RPC)──►  oyhub/server.py
                                              │
                    ┌─────────────────────────┼─────────────────────┐
                    ▼                         ▼                     ▼
             skills.py                 memory.py               curator.py
      central library + index    Obsidian vault (curated)   idle-triggered loop:
      per-project activation     SQLite FTS5 (raw recall)   archive stale, dedupe,
      usage tracking             frozen-snapshot reads      log actions to vault
```

Design lineage: the frozen-snapshot memory, 60-char skill index, archive-never-delete curator, and idle-triggered maintenance are patterns from [Hermes Agent](https://github.com/NousResearch/hermes-agent) (Nous Research, MIT), adapted for a keyless MCP harness.

## Development

```bash
pip install -e ".[dev]"
ruff check oyhub tests && pytest tests/ -q   # what CI runs
```

15 tests, all offline. CI matrix: Ubuntu + macOS × Python 3.10–3.12, plus a Docker build + smoke test. Tag `v*` to cut a GitHub release.

## Roadmap

- LLM-assisted curation (summarize/consolidate memory notes, improve skill descriptions)
- Skill import from agentskills.io and Claude skill bundles
- Session auto-logging middleware
- Vector search as an optional complement to FTS5

MIT license.
