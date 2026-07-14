# OyHub

**A local, always-on agent harness that fixes skill sprawl and context exhaustion.**

Engineers using AI assistants hit two walls: skills scattered across projects with nothing organized in one place, and context windows that fill up and forget everything between sessions. OyHub is an MCP server you install once on your machine — Claude Desktop, Claude Code, and Cursor connect to it and gain:

- **One central skill library** with per-project activation. Skills live in `~/.oyhub/skills` (agentskills.io-compatible `SKILL.md` format). Activate a project and only its relevant skills appear — your Kubernetes skills stay out of your React sessions.
- **Persistent memory that survives any context window.** Curated facts go to your **Obsidian vault** as plain markdown (human-readable, editable, graph-linked). Full conversation history goes to **SQLite + FTS5** — full-text searchable recall at zero LLM cost. Instead of context running out, the assistant *retrieves* the five messages that matter.
- **Loop engineering in the background.** A curator loop runs on idle: archives stale skills (never deletes — archive is recoverable), dedupes memory entries, pins protect anything you care about, and every action is logged to a `Curator Log.md` note in your vault so the loop's work stays visible.
- **Injection-scanned writes.** Memory and skills persist into every future session's context, so anything flowing into them is scanned at write time — instruction-override phrasing and credential-exfil link shapes are refused with a reason. Legitimate engineering content (curl commands, env var names) passes.
- **Safe under concurrent clients.** Multiple assistants can run OyHub against the same state simultaneously: SQLite runs in WAL mode with busy timeouts, and all shared JSON/markdown writes go through file-locked atomic read-modify-write.
- **Web dashboard + REST API.** `oyhub dashboard` serves a local **FastAPI** app: skill usage stats, memory browser, full-text session search, curator log — plus a JSON API for scripting against your hub.
- **Observability built in.** Every tool call is recorded (tool, latency, status) in the shared SQLite DB; the dashboard exposes a **Prometheus** `/metrics` endpoint, and `docker compose --profile observability up` starts a provisioned **Grafana** with a ready-made OyHub dashboard (call rates, latencies, error rates).
- **CI/CD + Docker from day one.** GitHub Actions runs lint + tests on a 6-way OS/Python matrix, builds the Docker image, cuts a GitHub release on every version tag, and publishes to PyPI via trusted publishing.

Zero runtime dependencies — Python stdlib only. No API key: the connected assistant is the intelligence; OyHub is the harness.

## Install

```bash
git clone https://github.com/Asanovichoff/oyhub && cd oyhub
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
      "env": {
        "PYTHONPATH": "/path/to/oyhub",
        "OYHUB_VAULT": "/Users/you/Documents/MyVault"
      }
    }
  }
}
```

> **Note:** Claude Desktop ignores a `cwd` field — `PYTHONPATH` is what makes
> `python3 -m oyhub` importable. Alternatively, `pip3 install -e /path/to/oyhub`
> once, then use `"command": "oyhub"` with no `args` or `PYTHONPATH`.

### Connect Claude Code

```bash
claude mcp add oyhub --env PYTHONPATH=/path/to/oyhub --env OYHUB_VAULT=~/Documents/MyVault -- python3 -m oyhub
```

Or after `pip3 install -e /path/to/oyhub`: `claude mcp add oyhub -- oyhub`

### Docker

```bash
docker compose run --rm oyhub          # stdio MCP server
docker compose run --rm --entrypoint python oyhub -m oyhub doctor
```

### Dashboard & observability

```bash
pip install "oyhub[dashboard]"
oyhub dashboard                        # web UI + API at http://127.0.0.1:8400
```

Full observability stack (dashboard + Prometheus + Grafana, pre-provisioned):

```bash
docker compose --profile observability up
# dashboard http://localhost:8400 · prometheus http://localhost:9090 · grafana http://localhost:3000
```

The core MCP server stays dependency-free — FastAPI/uvicorn are an optional extra used only by the dashboard process.

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

25 tests, all offline — including concurrency tests (parallel writers, no lost updates), injection-guard tests, and dashboard API tests. CI matrix: Ubuntu + macOS × Python 3.10–3.12, plus a Docker build + smoke test. Tag `v*` to cut a GitHub release and publish to PyPI (trusted publishing — configure once under PyPI → Publishing).

## Roadmap

- LLM-assisted curation (summarize/consolidate memory notes, improve skill descriptions)
- Skill import from agentskills.io and Claude skill bundles
- Session auto-logging middleware
- Vector search as an optional complement to FTS5

MIT license.
