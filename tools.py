"""Tool handlers. Every handler returns a JSON string and never raises (Hermes plugin contract)."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Optional

from . import client as wm

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
            return self._client


def _ok(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _fail(exc: Exception) -> str:
    if isinstance(exc, wm.WickedMemoryError):
        out: dict = {"error": exc.message, "status": exc.status}
        if exc.status == 402:
            out["hint"] = ("Search is metered. Set WICKED_MEMORY_API_KEY (free key) in your Hermes env; the shared "
                           "community key may be unavailable or this service has none configured.")
        elif exc.status == 429:
            out["hint"] = f"Rate limited; retry in {exc.retry_after or 'a minute'} seconds."
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
            return _ok({"wallet": c.address, "service": c.base_url, "wallet_source": rt.key_source, "search_access": rt.search_mode,
                        "note": "Memories are private to this wallet. Back up the wallet key if you want to keep access."})
        except Exception as exc:
            return _fail(exc)

    return {
        "wicked_memory_remember": remember, "wicked_memory_recall": recall, "wicked_memory_update": update,
        "wicked_memory_history": history, "wicked_memory_forget": forget, "wicked_memory_whoami": whoami,
    }


def forget_approval(tool_name: str, args: dict, task_id: str = "", **kwargs: Any):
    """pre_tool_call hook: hard delete is irreversible, so the human approves each one."""
    if tool_name != "wicked_memory_forget":
        return None
    memory_id = str((args or {}).get("memory_id") or "?")
    scope = (args or {}).get("scope") or "chain"
    what = "every version of" if scope == "chain" else "one version of"
    return {
        "action": "approve",
        "message": f"Permanently delete {what} memory {memory_id} from Wicked Memory? This cannot be undone.",
        "rule_key": f"wicked-memory:forget:{memory_id}",
    }
