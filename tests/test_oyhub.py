"""OyHub tests — all offline, isolated to a tmp home."""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oyhub.config import HubConfig
from oyhub.curator import Curator
from oyhub.memory import SessionStore, VaultMemory
from oyhub.server import Hub, handle_request
from oyhub.skills import SkillStore


@pytest.fixture
def cfg(tmp_path):
    c = HubConfig(home=tmp_path / "hub", vault_path=tmp_path / "vault")
    c.ensure_dirs()
    return c


# -- skills -------------------------------------------------------------------

def test_skill_add_index_and_project_filtering(cfg):
    store = SkillStore(cfg)
    store.add("react-conventions", "React component and hook conventions.",
              "Use function components...", tags=["react", "frontend"])
    store.add("k8s-deploy", "Deploy services to Kubernetes safely.",
              "kubectl apply ...", tags=["kubernetes", "devops"])

    assert len(store.index()) == 2  # no project -> everything
    store.set_project("webapp", ["react", "frontend"])
    filtered = store.index("webapp")
    assert [s["name"] for s in filtered] == ["react-conventions"]


def test_skill_description_hard_cap(cfg):
    store = SkillStore(cfg)
    with pytest.raises(ValueError, match="hard cap"):
        store.add("too-long", "x" * 61, "body")


def test_skill_usage_tracked_and_search(cfg):
    store = SkillStore(cfg)
    store.add("git-hygiene", "Commit message and branch conventions.", "...")
    store.get("git-hygiene")
    store.get("git-hygiene")
    assert store.usage()["git-hygiene"]["use_count"] == 2
    assert store.search("branch conventions")[0]["name"] == "git-hygiene"


# -- memory: vault ---------------------------------------------------------------

def test_vault_project_scoping_and_dedupe(cfg):
    vault = VaultMemory(cfg)
    vault.add("Prefers tabs over spaces", project=None)
    vault.add("API base url is api.example.com", project="webapp")
    assert "duplicate" in vault.add("api base url is API.example.com",
                                    project="webapp")
    snap = vault.snapshot("webapp")
    assert "api.example.com" in snap.project_notes
    assert "tabs over spaces" in snap.global_notes
    assert "Project memory: webapp" in snap.as_text()


def test_vault_frozen_snapshot(cfg):
    vault = VaultMemory(cfg)
    vault.add("first fact")
    snap = vault.snapshot()
    vault.add("second fact added mid-session")
    assert "second fact" not in snap.global_notes
    assert "second fact" in vault.snapshot().global_notes


# -- memory: sqlite fts5 ------------------------------------------------------------

def test_session_log_and_fts_search(cfg):
    store = SessionStore(cfg)
    sid = store.log("user", "How do we configure the Kafka consumer group?",
                    project="pipeline")
    store.log("assistant", "Set group.id in consumer.properties ...",
              project="pipeline", session_id=sid)
    hits = store.search("kafka consumer")
    assert len(hits) == 1
    assert hits[0]["session_id"] == sid
    assert len(hits[0]["context"]) == 2  # match + neighbor


def test_fts_query_escaping(cfg):
    store = SessionStore(cfg)
    store.log("user", "what about C++ templates?")
    # raw '++' would be an FTS5 syntax error if unescaped
    assert store.search('C++ "templates')[0]["match"]["content"]


def test_recent_sessions(cfg):
    store = SessionStore(cfg)
    store.log("user", "alpha")
    store.log("user", "beta")
    recents = store.recent_sessions()
    assert len(recents) == 2


# -- curator ---------------------------------------------------------------------

def test_curator_archives_stale_but_not_pinned(cfg):
    store = SkillStore(cfg)
    vault = VaultMemory(cfg)
    store.add("old-skill", "Something once useful.", "...")
    store.add("kept-skill", "Pinned forever.", "...")
    store.get("old-skill")  # create usage records
    store.get("kept-skill")

    usage_file = cfg.home / "skill_usage.json"
    usage = json.loads(usage_file.read_text())
    for rec in usage.values():
        rec["last_used"] = time.time() - 200 * 86400  # 200 days ago
    usage_file.write_text(json.dumps(usage))

    curator = Curator(cfg, store, vault)
    curator.pin("kept-skill")
    report = curator.run()
    assert report.archived_skills == ["old-skill"]
    assert store.all_names() == ["kept-skill"]
    # archived, not deleted — recoverable
    assert any(p.name.startswith("old-skill") for p in cfg.archive_dir.iterdir())
    # visible in the vault log
    assert "archived" in (vault.dir / "Curator Log.md").read_text()


def test_curator_dedupes_vault_notes(cfg):
    store, vault = SkillStore(cfg), VaultMemory(cfg)
    note = vault.dir / "Global.md"
    note.write_text("- same fact *(2026-01-01)*\n- same fact *(2026-02-02)*\n"
                    "- unique fact *(2026-03-03)*\n")
    report = Curator(cfg, store, vault).run()
    assert report.deduped_notes["Global.md"] == 1
    assert note.read_text().count("same fact") == 1


def test_curator_interval_gate(cfg):
    store, vault = SkillStore(cfg), VaultMemory(cfg)
    curator = Curator(cfg, store, vault)
    assert curator.due()
    curator.run()
    assert not curator.due()
    assert curator.maybe_run() is None


# -- MCP protocol ------------------------------------------------------------------

def _rpc(hub, method, params=None, rid=1):
    return handle_request(hub, {"jsonrpc": "2.0", "id": rid,
                                "method": method, "params": params or {}})


def test_mcp_initialize_and_tools_list(cfg):
    hub = Hub(cfg)
    init = _rpc(hub, "initialize")
    assert init["result"]["serverInfo"]["name"] == "oyhub"
    assert _rpc(hub, "notifications/initialized") is None
    tools = _rpc(hub, "tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"skill_add", "skill_list", "memory_add", "session_search",
            "project_activate", "curator_run"} <= names
    assert all("inputSchema" in t for t in tools)


def test_mcp_end_to_end_workflow(cfg):
    hub = Hub(cfg)

    def call(name, args):
        resp = _rpc(hub, "tools/call", {"name": name, "arguments": args})
        return resp["result"]["content"][0]["text"]

    call("skill_add", {"name": "fastapi-patterns",
                       "description": "FastAPI routing and dependency patterns.",
                       "body": "Use APIRouter per domain...",
                       "tags": ["python", "backend"]})
    assert "active" in call("project_activate",
                            {"project": "api", "tags": ["python", "backend"]})
    assert "fastapi-patterns" in call("skill_list", {})
    call("memory_add", {"entry": "We use Postgres 16 in prod"})
    assert "Postgres 16" in call("memory_snapshot", {})
    sid = json.loads(call("session_log",
                          {"role": "user", "content": "decided on Postgres 16"}))
    assert sid["session_id"]
    assert "Postgres 16" in call("session_search", {"query": "postgres"})


def test_mcp_tool_error_is_result_not_crash(cfg):
    hub = Hub(cfg)
    resp = _rpc(hub, "tools/call", {"name": "skill_add",
                                    "arguments": {"name": "bad name!!",
                                                  "description": "d", "body": "b"}})
    assert resp["result"]["isError"] is True
    assert "invalid skill name" in resp["result"]["content"][0]["text"]


def test_mcp_unknown_method(cfg):
    resp = _rpc(Hub(cfg), "bogus/method")
    assert resp["error"]["code"] == -32601
