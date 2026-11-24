"""Backups and the optional local mirror.

Everything here reads and writes inside the plugin's own data directory, except the mirror when the user has
pointed the `mirror_dir` setting somewhere else on purpose. The files hold memory text in clear (that is the
point of a backup you can read and re-import), so they are created owner-only and the mirror is strictly opt-in.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from . import client as wm

BACKUP_DIR = "backups"
MIRROR_FLAG = "mirror.json"
FORMAT = "wicked-memory-export"
IMPORT_BATCH = 25
PAGE = 200
FULL_RESYNC_SECONDS = 7 * 24 * 3600
MAX_PAGES = 500  # 100k rows: far above any plan, so a runaway server answer cannot loop forever
_NAME_RE = re.compile(r"^wicked-memory-[0-9a-f]{8}-\d{8}-\d{6}\.jsonl$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _short(address: str) -> str:
    return address.lower().removeprefix("0x")[:8]


def _private_write(path: Path, text: str) -> None:
    """Atomic, owner-only write (temp file, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(str(tmp), str(path))


def _jsonl(rows: list) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)


def fetch_all(client: Any, include_superseded: bool = True, cursor: Optional[str] = None,
              on_page: Optional[Callable[[list], None]] = None) -> tuple[list, Optional[str]]:
    """Walk the export pages from `cursor`. Returns (rows, cursor to resume from next time)."""
    rows: list = []
    for _ in range(MAX_PAGES):
        page, more, nxt = client.export_page(cursor, PAGE, include_superseded)
        rows.extend(page)
        if on_page:
            on_page(page)
        cursor = nxt or cursor
        if not more:
            return rows, cursor
    return rows, cursor


# ---- backups -------------------------------------------------------------------------------------

def backups_dir(data_dir: Path) -> Path:
    return Path(data_dir) / BACKUP_DIR


def write_backup(client: Any, data_dir: Path) -> dict:
    rows, _ = fetch_all(client)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = backups_dir(data_dir) / f"wicked-memory-{_short(client.address)}-{stamp}.jsonl"
    header = {"format": FORMAT, "version": 1, "wallet": client.address.lower(), "exported_at": _now_iso(), "count": len(rows)}
    _private_write(path, _jsonl([header] + rows))
    return {"file": path.name, "path": str(path), "memories": len(rows), "current": sum(1 for r in rows if not r.get("valid_until"))}


def list_backups(data_dir: Path) -> list:
    d = backups_dir(data_dir)
    return sorted((p for p in d.glob("wicked-memory-*.jsonl") if _NAME_RE.match(p.name)), key=lambda p: p.name) if d.is_dir() else []


def resolve_backup(data_dir: Path, name: Optional[str]) -> Path:
    """A backup file by bare name (no directories), or the newest one. Never a path the model supplies."""
    if name:
        if not _NAME_RE.match(name):
            raise ValueError("file must be a backup name from wicked_memory_backup status (no folders).")
        path = backups_dir(data_dir) / name
        if not path.is_file():
            raise ValueError(f"no backup named {name}.")
        return path
    found = list_backups(data_dir)
    if not found:
        raise ValueError("no backup file found; run wicked_memory_backup with action export first, or place a backup in the backups folder.")
    return found[-1]


def read_backup(path: Path) -> list:
    rows: list = []
    with open(path, "r", encoding="utf-8") as handle:
        for n, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if n == 0 and obj.get("format") == FORMAT:
                continue
            rows.append(obj)
    return rows


def restore(client: Any, rows: list) -> dict:
    """Send rows to the service in small batches, successors first. Safe to repeat."""
    ordered = wm.sort_successors_first(rows)
    totals = {"received": 0, "imported": 0, "skipped_existing": 0, "unlinked_versions": 0}
    for i in range(0, len(ordered), IMPORT_BATCH):
        res = client.import_batch(ordered[i:i + IMPORT_BATCH])
        for k in totals:
            totals[k] += int(res.get(k) or 0)
    return totals


# ---- the opt-in local mirror ---------------------------------------------------------------------

class Mirror:
    """A readable local copy of the wallet's memories, kept current by pulling only what changed.

    Layout: <root>/memories.jsonl (one memory per line) and <root>/state.json (cursors). The on/off switch is
    a small file in the plugin data dir, so the model cannot turn it on without the approval hook."""

    def __init__(self, data_dir: Path, mirror_dir: Optional[str], address: str):
        self.flag = Path(data_dir) / MIRROR_FLAG
        base = Path(mirror_dir).expanduser() if mirror_dir else Path(data_dir) / "mirror"
        self.root = base / (f"wicked-memory-{_short(address)}" if mirror_dir else _short(address))
        self.address = address.lower()

    # -- switch
    def enabled(self) -> bool:
        try:
            return bool(json.loads(self.flag.read_text(encoding="utf-8")).get("enabled"))
        except (OSError, ValueError):
            return False

    def set_enabled(self, on: bool) -> None:
        _private_write(self.flag, json.dumps({"enabled": on, "changed_at": _now_iso()}))

    # -- state
    def _state(self) -> dict:
        try:
            state = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
            return state if state.get("wallet") == self.address else {}
        except (OSError, ValueError):
            return {}

    def _rows(self) -> dict:
        try:
            with open(self.root / "memories.jsonl", "r", encoding="utf-8") as handle:
                return {r["id"]: r for r in (json.loads(l) for l in handle if l.strip())}
        except (OSError, ValueError, KeyError):
            return {}

    def status(self) -> dict:
        state = self._state()
        return {"enabled": self.enabled(), "folder": str(self.root), "memories": state.get("count", 0), "last_sync": state.get("synced_at"),
                "note": "Readable, unencrypted copy of this agent's memories. Delete the folder to remove it."}

    def sync(self, client: Any) -> dict:
        """Pull changes since the last sync, apply removals, rewrite the files. Returns counts."""
        state = self._state()
        # A full pull now and then (and on first run) self-heals anything an incremental pull could have missed.
        full = not state or time.time() - float(state.get("full_at_epoch") or 0) > FULL_RESYNC_SECONDS
        if full:
            state = {}
        rows = self._rows() if state else {}
        started = datetime.fromtimestamp(time.time() - 5, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")  # small overlap: removals are idempotent
        pulled, cursor = fetch_all(client, True, state.get("cursor"))
        for r in pulled:
            rows[r["id"]] = r
        removed = 0
        if state.get("deletions_since"):
            for d in client.deletions_since(state["deletions_since"]):
                if rows.pop(d.get("memory_id"), None) is not None:
                    removed += 1
        ordered = sorted(rows.values(), key=lambda r: (r.get("updated_at") or "", r["id"]))
        _private_write(self.root / "memories.jsonl", _jsonl(ordered))
        _private_write(self.root / "state.json", json.dumps({
            "wallet": self.address, "cursor": cursor, "deletions_since": started, "synced_at": _now_iso(), "count": len(ordered),
            "full_at_epoch": time.time() if full else state.get("full_at_epoch", time.time())}))
        return {"pulled": len(pulled), "removed": removed, "memories": len(ordered), "folder": str(self.root)}
