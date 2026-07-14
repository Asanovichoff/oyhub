"""OyHub dashboard — FastAPI app: REST API, web UI, Prometheus /metrics.

Optional feature: requires the ``dashboard`` extra (``pip install
"oyhub[dashboard]"``). The core MCP server stays dependency-free; this module
is only imported by the ``oyhub dashboard`` subcommand.

Run:  oyhub dashboard [--host 127.0.0.1] [--port 8400]
"""

from __future__ import annotations

from .config import HubConfig
from .curator import Curator
from .memory import SessionStore, VaultMemory
from .skills import SkillStore
from .telemetry import Telemetry, render_prometheus

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, PlainTextResponse
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "The dashboard requires the optional extra: pip install 'oyhub[dashboard]'"
    ) from e

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>OyHub</title>
<style>
 body{font-family:-apple-system,system-ui,sans-serif;margin:2rem auto;max-width:960px;
      padding:0 1rem;color:#1a1a2e}
 h1{font-size:1.5rem} h2{font-size:1.1rem;margin-top:2rem;border-bottom:1px solid #ddd;
      padding-bottom:.3rem}
 table{border-collapse:collapse;width:100%;font-size:.9rem}
 td,th{text-align:left;padding:.35rem .6rem;border-bottom:1px solid #eee}
 th{color:#666;font-weight:600} .num{text-align:right}
 input{padding:.4rem;width:60%;margin-right:.5rem}
 button{padding:.4rem .8rem} pre{white-space:pre-wrap;background:#f6f6f8;padding:.8rem;
      border-radius:6px;font-size:.85rem}
 .pill{display:inline-block;background:#eef;border-radius:10px;padding:0 .5rem;
      margin-right:.3rem;font-size:.8rem}
</style></head><body>
<h1>OyHub dashboard</h1>
<div id="stats"></div>
<h2>Skills</h2><div id="skills"></div>
<h2>Tool calls</h2><div id="tools"></div>
<h2>Search past sessions</h2>
<p><input id="q" placeholder="e.g. CORS fix"><button onclick="search()">Search</button></p>
<div id="hits"></div>
<h2>Curator log</h2><pre id="curator"></pre>
<script>
const j = u => fetch(u).then(r => r.json());
function table(rows, cols){
  if(!rows.length) return "<p>(empty)</p>";
  let h = "<table><tr>" + cols.map(c=>`<th>${c}</th>`).join("") + "</tr>";
  for(const r of rows) h += "<tr>" + cols.map(c=>`<td>${r[c] ?? ""}</td>`).join("") + "</tr>";
  return h + "</table>";
}
async function load(){
  const s = await j("/api/stats");
  document.getElementById("stats").innerHTML =
    `<span class="pill">skills: ${s.skills}</span>` +
    `<span class="pill">projects: ${s.projects}</span>` +
    `<span class="pill">logged messages: ${s.messages}</span>` +
    `<span class="pill">tool calls (24h): ${s.tool_calls_24h}</span>`;
  document.getElementById("skills").innerHTML =
    table(await j("/api/skills"), ["name","description","tags","use_count"]);
  document.getElementById("tools").innerHTML =
    table(await j("/api/tools"), ["tool","status","calls","avg_ms"]);
  document.getElementById("curator").textContent = (await j("/api/curator/log")).log;
}
async function search(){
  const q = document.getElementById("q").value;
  const hits = await j("/api/sessions/search?q=" + encodeURIComponent(q));
  document.getElementById("hits").innerHTML =
    table(hits.map(h=>({when:h.when, project:h.project, match:h.match.content.slice(0,140)})),
          ["when","project","match"]);
}
load();
</script></body></html>"""


def create_app(cfg: HubConfig | None = None) -> "FastAPI":
    cfg = cfg or HubConfig()
    cfg.ensure_dirs()
    skills = SkillStore(cfg)
    vault = VaultMemory(cfg)
    sessions = SessionStore(cfg)
    telemetry = Telemetry(cfg.db_path)
    curator = Curator(cfg, skills, vault)

    app = FastAPI(title="OyHub", version=cfg_version())

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _PAGE

    @app.get("/api/stats")
    def stats() -> dict:
        import sqlite3
        messages = 0
        if cfg.db_path.exists():
            with sqlite3.connect(cfg.db_path) as conn:
                try:
                    (messages,) = conn.execute(
                        "SELECT COUNT(*) FROM messages").fetchone()
                except sqlite3.Error:
                    messages = 0
        return {
            "skills": len(skills.all_names()),
            "projects": len(skills.list_projects()),
            "messages": messages,
            "tool_calls_24h": telemetry.calls_last_hours(24),
        }

    @app.get("/api/skills")
    def skill_index() -> list[dict]:
        usage = skills.usage()
        out = []
        for item in skills.index():
            item["tags"] = ", ".join(item["tags"])
            item["use_count"] = usage.get(item["name"], {}).get("use_count", 0)
            out.append(item)
        return out

    @app.get("/api/skills/{name}")
    def skill_body(name: str) -> dict:
        skill = skills.get(name, track_usage=False)
        if skill is None:
            raise HTTPException(404, f"skill not found: {name}")
        return {"name": skill.name, "description": skill.description,
                "tags": skill.tags, "body": skill.body}

    @app.get("/api/projects")
    def projects() -> dict:
        return skills.list_projects()

    @app.get("/api/memory")
    def memory(project: str = "") -> dict:
        snap = vault.snapshot(project)
        return {"global": snap.global_notes, "project": snap.project_notes}

    @app.get("/api/sessions/recent")
    def recent(limit: int = 10) -> list[dict]:
        return sessions.recent_sessions(limit)

    @app.get("/api/sessions/search")
    def search(q: str, limit: int = 5) -> list[dict]:
        return sessions.search(q, limit=limit)

    @app.get("/api/tools")
    def tools() -> list[dict]:
        return telemetry.tool_stats()

    @app.get("/api/curator/log")
    def curator_log() -> dict:
        path = vault.dir / "Curator Log.md"
        log = path.read_text(encoding="utf-8") if path.exists() else "(no runs yet)"
        return {"log": log, "due": curator.due()}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        s = stats()
        gauges = {
            "oyhub_skills_total": float(s["skills"]),
            "oyhub_projects_total": float(s["projects"]),
            "oyhub_logged_messages_total": float(s["messages"]),
        }
        return render_prometheus(telemetry.tool_stats(), gauges)

    return app


def cfg_version() -> str:
    from . import __version__
    return __version__


def run(host: str = "127.0.0.1", port: int = 8400) -> None:  # pragma: no cover
    import uvicorn
    uvicorn.run(create_app(), host=host, port=port)
