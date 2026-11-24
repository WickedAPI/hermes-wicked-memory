"""Tool handlers. Every handler returns a JSON string and never raises (Hermes plugin contract)."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Optional

from . import client as wm
from . import portable

logger = logging.getLogger(__name__)

PLUGIN_ID = "wicked-memory"
SECRET_PRIVATE_KEY = "WICKED_MEMORY_PRIVATE_KEY"
SECRET_API_KEY = "WICKED_MEMORY_API_KEY"


def _get_secret(name: str) -> Optional[str]:
    try:
        from agent.secret_scope import get_secret  # Hermes' credential route: secrets are never read from the process environment or files directly

        return get_secret(name)
    except ImportError:
        return None


def _data_dir():
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_ID)


class Runtime:
    """Builds the client lazily (first tool call), so loading the plugin never touches disk or network."""

    def __init__(self, ctx: Any):
        self._ctx = ctx
        self._client: Optional[wm.WickedMemoryClient] = None
        self._lock = threading.Lock()
        self.key_source = "unknown"
        self.search_mode = "unknown"
        self.plan = "free"
        self.link_note = ""
        self.mirror_note = ""

    def base_url(self) -> str:
        value = self._ctx.get_config("base_url", wm.DEFAULT_BASE_URL)
        return str(value or wm.DEFAULT_BASE_URL).rstrip("/")

    def client(self) -> wm.WickedMemoryClient:
        with self._lock:
            if self._client is None:
                supplied = _get_secret(SECRET_PRIVATE_KEY)
                self.key_source = "WICKED_MEMORY_PRIVATE_KEY" if supplied else "generated (plugin data dir)"
                account = wm.load_or_create_wallet(_data_dir(), supplied)
                base = self.base_url()
                api_key = _get_secret(SECRET_API_KEY)
                if api_key:
                    self.search_mode = "your API key (WICKED_MEMORY_API_KEY)"
                else:
                    api_key = wm.discover_community_key(base)
                    self.search_mode = "shared community key (rate limited, free)" if api_key else "none: search needs an API key"
                self._client = wm.WickedMemoryClient(account, base, api_key)
                self._on_first_use(self._client, api_key or "")
            return self._client

    def mirror_dir(self) -> Optional[str]:
        value = self._ctx.get_config("mirror_dir", "")
        return str(value).strip() or None

    def mirror(self, c: wm.WickedMemoryClient) -> portable.Mirror:
        return portable.Mirror(_data_dir(), self.mirror_dir(), c.address)

    def _on_first_use(self, c: wm.WickedMemoryClient, api_key: str) -> None:
        """Once per process: link a Pro key to this wallet (a no-op when already linked) and, if the user switched
        the mirror on, bring it up to date. Neither may ever make a tool call fail."""
        if api_key.startswith(wm.PRO_KEY_PREFIX):
            try:
                res = c.link_pro(False)
                self.plan = "pro"
                self.link_note = "linked this wallet to your Pro key" if res.get("linked") else ""
            except wm.WickedMemoryError as exc:
                self.plan = "pro (not linked to this wallet)" if exc.status == 409 else "pro key problem"
                self.link_note = exc.message
            except Exception as exc:  # never fail a tool call over bookkeeping
                self.link_note = f"{type(exc).__name__}: {exc}"
        try:
            mirror = self.mirror(c)
            if mirror.enabled():
                mirror.sync(c)
        except Exception as exc:
            self.mirror_note = f"mirror sync skipped: {type(exc).__name__}: {exc}"


def _ok(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _fail(exc: Exception) -> str:
    if isinstance(exc, wm.WickedMemoryError):
        out: dict = {"error": exc.message, "status": exc.status}
        if exc.status == 402 and "Pro key" not in exc.message:
            out["hint"] = ("Search is metered. Set WICKED_MEMORY_API_KEY (free key) in your Hermes env; the shared "
                           "community key may be unavailable or this service has none configured.")
        elif exc.status == 429:
            out["hint"] = f"Rate limited; retry in {exc.retry_after or 'a minute'} seconds."
        elif exc.status == 409 and "Pro key" in exc.message:
            out["hint"] = ("This Pro key is linked to another wallet. If this is the same agent on a new machine, ask the user to confirm, "
                           "then call wicked_memory_link with recover=true to move the memories here.")
        elif exc.status == 402 and "Pro key" in exc.message:
            out["hint"] = "The Pro subscription is not active. The user can renew or recover the key from the Wicked Memory Pro page."
        elif exc.status == 404:
            out["hint"] = "No such memory for this wallet (ids are private to the wallet that wrote them)."
        elif exc.status == 0:
            out["hint"] = "Network problem reaching the memory service; try again shortly."
        return _ok(out)
    logger.warning("wicked-memory tool failed: %s", exc)
    return _ok({"error": f"{type(exc).__name__}: {exc}"})


def _compact(memory: dict) -> dict:
    keep = {k: memory.get(k) for k in ("id", "content", "tags", "score", "valid_from", "valid_until") if k in memory}
    return keep


def build_handlers(ctx: Any) -> dict[str, Callable[..., str]]:
    rt = Runtime(ctx)

    def remember(args: dict, **kwargs: Any) -> str:
        try:
            text = str(args.get("text") or "").strip()
            if not text:
                return _ok({"error": "text is required"})
            res = rt.client().store(text, tags=args.get("tags") or None, source="hermes")
            return _ok({"saved": True, "id": res["data"]["id"]})
        except Exception as exc:
            return _fail(exc)

    def recall(args: dict, **kwargs: Any) -> str:
        try:
            query = str(args.get("query") or "").strip()
            if not query:
                return _ok({"error": "query is required"})
            limit = max(1, min(int(args.get("limit") or 5), 50))
            res = rt.client().search(query, limit=limit, tags=args.get("tags") or None, as_of=args.get("as_of") or None,
                                     include_superseded=args.get("include_superseded"))
            hits = [_compact(m) for m in res.get("data", [])]
            return _ok({"count": len(hits), "memories": hits} if hits else {"count": 0, "memories": [], "note": "Nothing relevant remembered."})
        except Exception as exc:
            return _fail(exc)

    def update(args: dict, **kwargs: Any) -> str:
        try:
            if not args.get("memory_id"):
                return _ok({"error": "memory_id is required"})
            if args.get("text") is None and not args.get("tags"):
                return _ok({"error": "provide text and/or tags to change"})
            res = rt.client().update(str(args["memory_id"]), content=args.get("text"), tags=args.get("tags") or None)
            data = res["data"]
            return _ok({"updated": True, "previous_id": data["previous"]["id"], "current_id": data["current"]["id"]})
        except Exception as exc:
            return _fail(exc)

    def history(args: dict, **kwargs: Any) -> str:
        try:
            if not args.get("memory_id"):
                return _ok({"error": "memory_id is required"})
            res = rt.client().history(str(args["memory_id"]))
            return _ok({"versions": [_compact(m) for m in res.get("data", [])]})
        except Exception as exc:
            return _fail(exc)

    def forget(args: dict, **kwargs: Any) -> str:
        try:
            if not args.get("memory_id"):
                return _ok({"error": "memory_id is required"})
            scope = args.get("scope") if args.get("scope") in ("chain", "version") else "chain"
            res = rt.client().delete(str(args["memory_id"]), scope=scope, reason=args.get("reason") or None)
            return _ok({"deleted": res["data"]["deleted"], "scope": scope})
        except Exception as exc:
            return _fail(exc)

    def whoami(args: dict, **kwargs: Any) -> str:
        try:
            c = rt.client()
            out = {"wallet": c.address, "service": c.base_url, "wallet_source": rt.key_source, "search_access": rt.search_mode,
                   "plan": rt.plan, "note": "Memories are private to this wallet. Back up the wallet key, or use wicked_memory_backup, if you want to keep access."}
            if rt.link_note:
                out["pro_key"] = rt.link_note
            if rt.mirror_note:
                out["mirror"] = rt.mirror_note
            return _ok(out)
        except Exception as exc:
            return _fail(exc)

    def backup(args: dict, **kwargs: Any) -> str:
        try:
            action = str(args.get("action") or "")
            if action not in ("export", "restore", "mirror_on", "mirror_sync", "mirror_off", "status"):
                return _ok({"error": "action must be one of: export, restore, mirror_on, mirror_sync, mirror_off, status"})
            c = rt.client()
            data_dir = _data_dir()
            mirror = rt.mirror(c)
            if action == "export":
                return _ok({"exported": True, **portable.write_backup(c, data_dir),
                            "note": "Readable, unencrypted file. Keep it somewhere safe; restore it with action restore."})
            if action == "restore":
                path = portable.resolve_backup(data_dir, args.get("file") or None)
                return _ok({"restored": True, "file": path.name, **portable.restore(c, portable.read_backup(path))})
            if action == "mirror_on":
                mirror.set_enabled(True)
                return _ok({"mirror": "on", **mirror.sync(c)})
            if action == "mirror_off":
                mirror.set_enabled(False)
                return _ok({"mirror": "off", "note": f"Existing copy left in {mirror.root}. Delete that folder to remove it."})
            if action == "mirror_sync":
                if not mirror.enabled():
                    return _ok({"error": "The mirror is off. Use action mirror_on (the user is asked to approve)."})
                return _ok({"mirror": "synced", **mirror.sync(c)})
            return _ok({"backups": [p.name for p in portable.list_backups(data_dir)], "backup_folder": str(portable.backups_dir(data_dir)),
                        "mirror": mirror.status()})
        except ValueError as exc:
            return _ok({"error": str(exc)})
        except Exception as exc:
            return _fail(exc)

    def link(args: dict, **kwargs: Any) -> str:
        try:
            res = rt.client().link_pro(bool(args.get("recover")))
            return _ok({"linked": res.get("linked"), "memories_moved": res.get("moved"), "plan": "pro", "wallet": res.get("wallet"),
                        "searches": res.get("searches")})
        except Exception as exc:
            return _fail(exc)

    return {
        "wicked_memory_backup": backup, "wicked_memory_link": link,
        "wicked_memory_remember": remember, "wicked_memory_recall": recall, "wicked_memory_update": update,
        "wicked_memory_history": history, "wicked_memory_forget": forget, "wicked_memory_whoami": whoami,
    }


def forget_approval(tool_name: str, args: dict, task_id: str = "", **kwargs: Any):
    """pre_tool_call hook: the irreversible or privacy-relevant actions need the human's approval.
    Hard delete is irreversible; a recover link moves a whole wallet's memories; the mirror writes them out in clear."""
    args = args or {}
    if tool_name == "wicked_memory_forget":
        memory_id = str(args.get("memory_id") or "?")
        what = "every version of" if (args.get("scope") or "chain") == "chain" else "one version of"
        return {
            "action": "approve",
            "message": f"Permanently delete {what} memory {memory_id} from Wicked Memory? This cannot be undone.",
            "rule_key": f"wicked-memory:forget:{memory_id}",
        }
    if tool_name == "wicked_memory_link" and args.get("recover"):
        return {
            "action": "approve",
            "message": "Move all memories from the wallet your Wicked Memory Pro key was linked to into this agent's wallet?",
            "rule_key": "wicked-memory:link-recover",
        }
    if tool_name == "wicked_memory_backup" and args.get("action") == "mirror_on":
        return {
            "action": "approve",
            "message": "Keep a readable, UNENCRYPTED copy of all this agent's memories on this computer and refresh it from Wicked Memory? "
                       "Anyone with access to this machine's files could read it.",
            "rule_key": "wicked-memory:mirror-on",
        }
    return None
