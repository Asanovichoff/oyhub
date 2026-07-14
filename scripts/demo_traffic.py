#!/usr/bin/env python3
"""Generate realistic demo traffic against your local OyHub state.

Fires a few hundred real tool calls through the Hub (same code path the MCP
clients use) so the dashboard and Grafana panels have live data to show.
Run it, then screenshot Grafana.

Usage:
    python3 scripts/demo_traffic.py [--calls 300]
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oyhub.config import HubConfig
from oyhub.server import Hub

SKILLS = [
    ("git-hygiene", "Commit message and branch conventions.", ["git", "workflow"]),
    ("fastapi-patterns", "FastAPI routing and dependency patterns.", ["python", "backend"]),
    ("react-conventions", "React component and hook conventions.", ["react", "frontend"]),
    ("docker-debugging", "Diagnose failing containers and builds.", ["docker", "devops"]),
    ("sql-review", "Review queries for indexes and N+1 issues.", ["sql", "backend"]),
]

MEMORIES = [
    "Staging database is read-only for app users",
    "We deploy on Fridays only with a feature flag",
    "API rate limit is 100 req/min per key",
    "Frontend uses pnpm, not npm",
    "Postgres 16 in prod, 15 in CI",
]

LOGS = [
    ("user", "How did we fix the CORS issue on the admin panel?"),
    ("assistant", "Added the origin to the allowlist and enabled credentials."),
    ("user", "Remind me why we chose SQLite FTS5 over embeddings?"),
    ("assistant", "Zero LLM cost, no infra, BM25 is enough at this scale."),
    ("user", "What was the fix for the flaky docker build?"),
    ("assistant", "Pinned the base image digest and cleared buildkit cache."),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--calls", type=int, default=300)
    args = p.parse_args()

    hub = Hub(HubConfig())
    hub.call("project_activate", {"project": "demo", "tags": ["python", "backend"]})
    for name, desc, tags in SKILLS:
        try:
            hub.call("skill_add", {"name": name, "description": desc,
                                   "body": f"# {name}\n\nDemo skill body.",
                                   "tags": tags})
        except Exception:
            pass  # already exists
    for m in MEMORIES:
        try:
            hub.call("memory_add", {"entry": m})
        except Exception:
            pass
    for role, content in LOGS:
        hub.call("session_log", {"role": role, "content": content})

    weighted = (
        ["skill_list"] * 30 + ["memory_snapshot"] * 20 + ["skill_view"] * 15
        + ["session_search"] * 15 + ["skill_search"] * 10 + ["session_recent"] * 5
        + ["memory_add"] * 3 + ["session_log"] * 2
    )
    queries = ["CORS", "docker", "SQLite", "rate limit", "deploy"]
    made = 0
    for _ in range(args.calls):
        tool = random.choice(weighted)
        try:
            if tool == "skill_view":
                hub.call(tool, {"name": random.choice(SKILLS)[0]})
            elif tool in ("session_search", "skill_search"):
                hub.call(tool, {"query": random.choice(queries)})
            elif tool == "memory_add":
                hub.call(tool, {"entry": f"demo fact {random.randint(0, 10**6)}"})
            elif tool == "session_log":
                hub.call(tool, {"role": "user",
                                "content": f"demo message {random.randint(0, 10**6)}"})
            else:
                hub.call(tool, {})
            made += 1
        except Exception:
            pass
        time.sleep(random.uniform(0.005, 0.03))
    print(f"done: {made} tool calls recorded — dashboards are now populated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
