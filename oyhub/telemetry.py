"""Telemetry — tool-call events recorded in the shared SQLite DB.

Every MCP client spawns its own OyHub process, so in-process counters would
fragment across processes and die with them. Instead each tool call appends
one row to a ``tool_calls`` table in the shared database; the dashboard
process aggregates with SQL and renders Prometheus exposition format.

The exposition renderer is hand-written (stdlib only) — the format is plain
text, and keeping the core dependency-free is a design goal.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY,
    tool TEXT NOT NULL,
    ok INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool);
"""


class Telemetry:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.executescript(_SCHEMA)
        return conn

    # -- write path (called by the MCP server on every tool call) -----------

    def record(self, tool: str, ok: bool, duration_ms: float) -> None:
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO tool_calls (tool, ok, duration_ms, ts) "
                    "VALUES (?,?,?,?)",
                    (tool, 1 if ok else 0, duration_ms, time.time()),
                )
        except sqlite3.Error:
            pass  # telemetry must never break a tool call

    # -- read path (dashboard) ------------------------------------------------

    def tool_stats(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT tool, ok, COUNT(*), SUM(duration_ms), AVG(duration_ms) "
                "FROM tool_calls GROUP BY tool, ok ORDER BY tool"
            ).fetchall()
        return [
            {"tool": t, "status": "ok" if ok else "error", "calls": n,
             "total_ms": total or 0.0, "avg_ms": round(avg or 0.0, 2)}
            for t, ok, n, total, avg in rows
        ]

    def calls_last_hours(self, hours: int = 24) -> int:
        cutoff = time.time() - hours * 3600
        with self._conn() as conn:
            (n,) = conn.execute(
                "SELECT COUNT(*) FROM tool_calls WHERE ts >= ?", (cutoff,)
            ).fetchone()
        return n


def render_prometheus(tool_stats: list[dict], gauges: dict[str, float]) -> str:
    """Render Prometheus text exposition format (version 0.0.4)."""
    lines = [
        "# HELP oyhub_tool_calls_total Total tool calls by tool and status.",
        "# TYPE oyhub_tool_calls_total counter",
    ]
    for s in tool_stats:
        lines.append(
            f'oyhub_tool_calls_total{{tool="{s["tool"]}",status="{s["status"]}"}}'
            f' {s["calls"]}'
        )
    lines += [
        "# HELP oyhub_tool_call_duration_seconds_sum Cumulative tool call duration.",
        "# TYPE oyhub_tool_call_duration_seconds_sum counter",
    ]
    for s in tool_stats:
        lines.append(
            f'oyhub_tool_call_duration_seconds_sum{{tool="{s["tool"]}"'
            f',status="{s["status"]}"}} {s["total_ms"] / 1000.0:.6f}'
        )
    for name, value in sorted(gauges.items()):
        lines += [
            f"# HELP {name} OyHub state gauge.",
            f"# TYPE {name} gauge",
            f"{name} {value}",
        ]
    return "\n".join(lines) + "\n"
