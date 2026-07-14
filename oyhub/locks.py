"""Cross-process safety primitives.

Multiple MCP clients (Claude Desktop, Claude Code, Cowork) each spawn their
own OyHub process against the same on-disk state. Without locking, two
processes doing read-modify-write on the same JSON file silently lose
updates (last write wins). Every shared JSON file goes through
:func:`update_json` — an exclusive-lock + atomic-replace critical section.

POSIX uses ``fcntl.flock`` on a sidecar ``.lock`` file; on platforms without
fcntl (Windows) we degrade to lock-free atomic replace, which still prevents
torn files even if it can't prevent lost updates.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Exclusive cross-process lock scoped to *path* (sidecar lockfile)."""
    lock_path = path.parent / (path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    """Write via temp file + os.replace — readers never see a torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def update_json(path: Path, default: Any,
                fn: Callable[[Any], Any]) -> Any:
    """Atomic read-modify-write under an exclusive lock."""
    with locked(path):
        data = read_json(path, default)
        data = fn(data)
        write_json_atomic(path, data)
        return data
