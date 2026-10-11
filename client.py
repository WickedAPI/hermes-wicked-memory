"""Wicked Memory HTTP client: wallet-signed requests over the standard library (plus eth-account to sign).

Every request carries four headers that prove the caller controls a wallet; the wallet decides WHOSE
memories are touched. The signed message binds method, path?query, body hash, timestamp and a single-use
nonce, so a captured signature cannot be replayed against another request or wallet.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

DEFAULT_BASE_URL = "https://memory.wickedapi.com"
TIMEOUT_SECONDS = 20
USER_AGENT = "hermes-wicked-memory/0.1"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class WickedMemoryError(Exception):
    """A non-2xx answer (or a network failure, status 0) from the service."""

    def __init__(self, status: int, message: str, body: Any = None, retry_after: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.body = body
        self.retry_after = retry_after


def _account_class():
    from eth_account import Account  # imported lazily so the plugin can load and explain a missing dependency

    return Account


def dependency_available() -> bool:
    try:
        _account_class()
        return True
    except Exception:
        return False


def load_or_create_wallet(data_dir: Path, supplied_key: Optional[str]):
    """The agent's wallet: the key the user supplied, else the one generated on first use and kept
    (owner-only permissions) in this plugin's own data directory."""
    account_cls = _account_class()
    if supplied_key and supplied_key.strip():
        return account_cls.from_key(supplied_key.strip())
    path = Path(data_dir) / "wallet.key"
    if not path.exists():
        account = account_cls.create()
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("0x" + account.key.hex().removeprefix("0x"))
        except FileExistsError:  # another process created it first: use theirs
            pass
    return account_cls.from_key(path.read_text(encoding="utf-8").strip())


def build_auth_message(wallet: str, method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    sha = hashlib.sha256(body).hexdigest() if body else _EMPTY_SHA256
    return "\n".join(
        [
            "Wicked Memory auth",
            f"wallet: {wallet.lower()}",
            f"method: {method.upper()}",
            f"path: {path}",
            f"timestamp: {timestamp}",
            f"nonce: {nonce}",
            f"body-sha256: {sha}",
        ]
    )


def discover_community_key(base_url: str) -> Optional[str]:
    """The service publishes a shared, rate-limited free-trial key in its public status document."""
    try:
        req = urllib.request.Request(base_url.rstrip("/") + "/v1/status", headers={"user-agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tier = (data.get("data") or {}).get("free_tier") or {}
        return tier.get("community_key") or None
    except Exception:
        return None


class WickedMemoryClient:
    def __init__(self, account, base_url: str = DEFAULT_BASE_URL, api_key: Optional[str] = None):
        self.account = account
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    @property
    def address(self) -> str:
        return self.account.address

    def _call(self, method: str, path: str, payload: Optional[dict] = None, extra: Optional[dict] = None) -> Any:
        from eth_account.messages import encode_defunct

        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8") if payload is not None else b""
        timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
        message = build_auth_message(self.account.address, method, path, timestamp, nonce, body)
        signature = self.account.sign_message(encode_defunct(text=message)).signature.hex()
        headers = {
            "x-wallet-address": self.account.address,
            "x-wallet-timestamp": timestamp,
            "x-wallet-nonce": nonce,
            "x-wallet-signature": signature if signature.startswith("0x") else "0x" + signature,
            "user-agent": USER_AGENT,
            "accept": "application/json",
            **(extra or {}),
        }
        if body:
            headers["content-type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=body or None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            message_text = (parsed or {}).get("error") if isinstance(parsed, dict) else None
            raise WickedMemoryError(exc.code, message_text or exc.reason, parsed, exc.headers.get("Retry-After")) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise WickedMemoryError(0, f"could not reach {self.base_url}: {exc}") from None

    # ---- API ---------------------------------------------------------------------------------

    def store(self, content: str, tags: Optional[list] = None, metadata: Optional[dict] = None, source: Optional[str] = None):
        payload = {"content": content, "tags": tags, "metadata": metadata, "source": source}
        return self._call("POST", "/memories", {k: v for k, v in payload.items() if v is not None})

    def search(self, q: str, limit: Optional[int] = None, tags: Optional[list] = None, as_of: Optional[str] = None,
               include_superseded: Optional[bool] = None):
        params: dict = {"q": q}
        if limit:
            params["limit"] = limit
        if tags:
            params["tags"] = ",".join(tags)
        if as_of:
            params["as_of"] = as_of
        if include_superseded is not None:
            params["include_superseded"] = "true" if include_superseded else "false"
        path = "/memories/search?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        return self._call("GET", path, extra={"x-api-key": self.api_key} if self.api_key else None)

    def get(self, memory_id: str):
        return self._call("GET", f"/memories/{urllib.parse.quote(memory_id, safe='')}")

    def update(self, memory_id: str, content: Optional[str] = None, tags: Optional[list] = None, metadata: Optional[dict] = None):
        patch = {"content": content, "tags": tags, "metadata": metadata}
        return self._call("PATCH", f"/memories/{urllib.parse.quote(memory_id, safe='')}", {k: v for k, v in patch.items() if v is not None})

    def history(self, memory_id: str):
        return self._call("GET", f"/memories/{urllib.parse.quote(memory_id, safe='')}/history")

    def delete(self, memory_id: str, scope: str = "chain", reason: Optional[str] = None):
        params = {"scope": scope, **({"reason": reason} if reason else {})}
        return self._call("DELETE", f"/memories/{urllib.parse.quote(memory_id, safe='')}?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote))
