"""CLI: `python -m oyhub` runs the MCP server (stdio).
`python -m oyhub doctor` prints a health check.
`python -m oyhub dashboard [--host H] [--port P]` serves the web dashboard."""

from __future__ import annotations

import sys

from .config import HubConfig
from .server import Hub, serve


def doctor() -> int:
    cfg = HubConfig()
    cfg.ensure_dirs()
    hub = Hub(cfg)
    print(f"oyhub home : {cfg.home}")
    print(f"vault         : {cfg.vault_path}")
    print(f"skills        : {len(hub.skills.all_names())}")
    print(f"projects      : {', '.join(hub.skills.list_projects()) or '(none)'}")
    print(f"sessions db   : {cfg.db_path} "
          f"({'exists' if cfg.db_path.exists() else 'will be created'})")
    print(f"curator due   : {hub.curator.due()}")
    print("status        : ok")
    return 0


def dashboard(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="oyhub dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8400)
    args = p.parse_args(argv)
    try:
        from .dashboard import run
    except ImportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    run(host=args.host, port=args.port)
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        return doctor()
    if len(sys.argv) > 1 and sys.argv[1] == "dashboard":
        return dashboard(sys.argv[2:])
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
