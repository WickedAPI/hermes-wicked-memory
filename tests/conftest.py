"""Import the plugin directory as a package (its folder name has a hyphen) and provide a fake service that
verifies wallet signatures exactly like the real Wicked Memory server does."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = "wicked_memory_plugin"


def _load_package():
    if PKG in sys.modules:
        return sys.modules[PKG]
    spec = importlib.util.spec_from_file_location(PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PKG] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def plugin():
    return _load_package()


class FakeService:
    """Records requests; rejects bad signatures, stale timestamps and reused nonces (401), like the real service."""

    def __init__(self):
        self.requests: list[dict] = []
        self.nonces: set[str] = set()
        self.status_free_tier = {"community_key": "wm_community_test"}
        self.force_status: int | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def _handle(self):
                from eth_account import Account
                from eth_account.messages import encode_defunct

                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else b""
                rec = {"method": self.command, "path": self.path, "body": body, "headers": dict(self.headers.items())}
                outer.requests.append(rec)
                if self.path == "/v1/status":
                    return self._send(200, {"data": {"free_tier": outer.status_free_tier}})
                if outer.force_status:
                    return self._send(outer.force_status, {"error": "forced", "source": "test"}, {"Retry-After": "7"})
                wallet = self.headers.get("x-wallet-address", "")
                ts, nonce, sig = (self.headers.get(h, "") for h in ("x-wallet-timestamp", "x-wallet-nonce", "x-wallet-signature"))
                message = "\n".join([
                    "Wicked Memory auth", f"wallet: {wallet.lower()}", f"method: {self.command}", f"path: {self.path}",
                    f"timestamp: {ts}", f"nonce: {nonce}", f"body-sha256: {hashlib.sha256(body).hexdigest()}",
                ])
                try:
                    recovered = Account.recover_message(encode_defunct(text=message), signature=sig)
                except Exception:
                    return self._send(401, {"error": "Invalid signature", "source": "auth"})
                if recovered.lower() != wallet.lower() or abs(time.time() - int(ts)) > 300 or len(nonce) < 16 or nonce in outer.nonces:
                    return self._send(401, {"error": "Invalid signature for this wallet and request.", "source": "auth"})
                outer.nonces.add(nonce)
                rec["wallet"] = wallet
                self._route(rec, body)

            def _route(self, rec, body):
                path = self.path.split("?")[0]
                data = json.loads(body) if body else {}
                if self.command == "POST" and path == "/memories":
                    return self._send(201, {"data": {"id": "11111111-1111-4111-8111-111111111111", **data}})
                if self.command == "GET" and path == "/memories/search":
                    return self._send(200, {"data": [{"id": "11111111-1111-4111-8111-111111111111", "content": "The user prefers dark mode.",
                                                      "tags": ["preferences"], "score": 0.83, "valid_from": "2026-01-01T00:00:00Z", "valid_until": None,
                                                      "metadata": {}, "source": None}]})
                if self.command == "PATCH":
                    return self._send(200, {"data": {"previous": {"id": "old"}, "current": {"id": "new", **data}}})
                if self.command == "GET" and path.endswith("/history"):
                    return self._send(200, {"data": [{"id": "old", "content": "v1", "valid_until": "t"}, {"id": "new", "content": "v2", "valid_until": None}]})
                if self.command == "DELETE":
                    return self._send(200, {"data": {"deleted": ["old", "new"], "scope": "chain"}})
                return self._send(404, {"error": "Not found", "source": "server"})

            def _send(self, status, payload, extra=None):
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PATCH = do_DELETE = _handle

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture()
def service():
    svc = FakeService()
    yield svc
    svc.close()
