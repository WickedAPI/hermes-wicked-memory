from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


class FakeCtx:
    def __init__(self, base_url: str = "https://memory.wickedapi.com"):
        self.tools: dict[str, dict] = {}
        self.hooks: list[tuple[str, object]] = []
        self.skills: dict[str, Path] = {}
        self.config = {"base_url": base_url}

    def register_tool(self, name, toolset, schema, handler, check_fn=None, **kw):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, "check_fn": check_fn, **kw}

    def register_hook(self, hook_name, callback):
        self.hooks.append((hook_name, callback))

    def register_skill(self, name, path, **kw):
        assert (Path(path)).exists()
        self.skills[name] = Path(path)

    def get_config(self, key, default=None):
        return self.config.get(key, default)


@pytest.fixture()
def wired(plugin, service, tmp_path, monkeypatch):
    tools = plugin.tools
    monkeypatch.setattr(tools, "_data_dir", lambda: tmp_path)
    secrets: dict[str, str] = {}
    monkeypatch.setattr(tools, "_get_secret", lambda name: secrets.get(name))
    ctx = FakeCtx(service.url)
    plugin.register(ctx)
    return ctx, secrets, tmp_path


def call(ctx: FakeCtx, name: str, **args):
    return json.loads(ctx.tools[name]["handler"](args))


def test_manifest_matches_registration(plugin):
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    ctx = FakeCtx()
    plugin.register(ctx)
    assert sorted(ctx.tools) == sorted(manifest["provides_tools"])
    assert [h for h, _ in ctx.hooks] == manifest["provides_hooks"] == ["pre_tool_call"]
    assert sorted(ctx.skills) == ["wicked-memory", "wicked-suite-mcp"]
    for name, entry in ctx.tools.items():
        assert entry["toolset"] == "wicked_memory"
        assert entry["schema"]["name"] == name and entry["schema"]["parameters"]["type"] == "object"
        assert callable(entry["check_fn"])


def test_skill_frontmatter_is_valid():
    for skill in (ROOT / "skills").glob("*/SKILL.md"):
        _, front, body = skill.read_text(encoding="utf-8").split("---", 2)
        meta = yaml.safe_load(front)
        assert meta["name"] == skill.parent.name and meta["description"] and meta["version"]
        assert meta["metadata"]["hermes"]["category"] and body.strip()


def test_round_trip_is_signed_and_uses_community_key(wired, service):
    ctx, _, _ = wired
    saved = call(ctx, "wicked_memory_remember", text="The user prefers dark mode.", tags=["preferences"])
    assert saved == {"saved": True, "id": "11111111-1111-4111-8111-111111111111"}
    found = call(ctx, "wicked_memory_recall", query="what UI does the user like?", limit=3)
    assert found["count"] == 1 and found["memories"][0]["score"] == 0.83 and "metadata" not in found["memories"][0]
    search_req = [r for r in service.requests if r["path"].startswith("/memories/search")][0]
    assert {k.lower(): v for k, v in search_req["headers"].items()}["x-api-key"] == "wm_community_test"
    assert "q=what%20UI%20does%20the%20user%20like%3F" in search_req["path"]  # signed path == sent path
    assert all("wallet" in r for r in service.requests if r["path"] != "/v1/status")  # every call passed signature checks
    assert len({({k.lower(): v for k, v in r["headers"].items()})["x-wallet-nonce"] for r in service.requests if "wallet" in r}) == 2  # nonces never reused


def test_update_history_forget(wired):
    ctx, _, _ = wired
    up = call(ctx, "wicked_memory_update", memory_id="old", text="v2")
    assert up == {"updated": True, "previous_id": "old", "current_id": "new"}
    hist = call(ctx, "wicked_memory_history", memory_id="new")
    assert [v["id"] for v in hist["versions"]] == ["old", "new"]
    gone = call(ctx, "wicked_memory_forget", memory_id="new", reason="user asked")
    assert gone == {"deleted": ["old", "new"], "scope": "chain"}


def test_wallet_is_generated_once_and_persisted(wired, plugin, tmp_path):
    ctx, _, data_dir = wired
    first = call(ctx, "wicked_memory_whoami")
    assert re.fullmatch(r"0x[0-9a-fA-F]{40}", first["wallet"]) and "generated" in first["wallet_source"]
    key_file = data_dir / "wallet.key"
    assert key_file.exists() and key_file.read_text().startswith("0x")
    ctx2 = FakeCtx(ctx.config["base_url"])
    plugin.register(ctx2)  # new process-like runtime reads the same file
    assert call(ctx2, "wicked_memory_whoami")["wallet"] == first["wallet"]


def test_supplied_private_key_and_api_key_win(wired):
    ctx, secrets, data_dir = wired
    from eth_account import Account

    mine = Account.create()
    secrets["WICKED_MEMORY_PRIVATE_KEY"] = "0x" + mine.key.hex().removeprefix("0x")
    secrets["WICKED_MEMORY_API_KEY"] = "wm_mine"
    who = call(ctx, "wicked_memory_whoami")
    assert who["wallet"] == mine.address and who["wallet_source"] == "WICKED_MEMORY_PRIVATE_KEY"
    assert "WICKED_MEMORY_API_KEY" in who["search_access"]
    assert not (data_dir / "wallet.key").exists()  # nothing generated when the user brings a key


@pytest.mark.parametrize("status,needle", [(402, "WICKED_MEMORY_API_KEY"), (429, "retry in 7"), (404, "private to the wallet")])
def test_errors_are_json_with_hints(wired, service, status, needle):
    ctx, _, _ = wired
    service.force_status = status
    out = call(ctx, "wicked_memory_recall", query="x")
    assert out["status"] == status and needle in out["hint"]


def test_network_failure_never_raises(plugin, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin.tools, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin.tools, "_get_secret", lambda name: None)
    ctx = FakeCtx("http://127.0.0.1:9")  # nothing listens here
    plugin.register(ctx)
    out = call(ctx, "wicked_memory_remember", text="hello")
    assert out["status"] == 0 and "hint" in out


def test_input_validation(wired):
    ctx, _, _ = wired
    assert "error" in call(ctx, "wicked_memory_remember", text="   ")
    assert "error" in call(ctx, "wicked_memory_recall", query="")
    assert "error" in call(ctx, "wicked_memory_update", memory_id="x")
    assert "error" in call(ctx, "wicked_memory_forget")


def test_forget_requires_approval_and_nothing_else_does(plugin):
    gate = plugin.tools.forget_approval
    res = gate("wicked_memory_forget", {"memory_id": "abc", "scope": "chain"})
    assert res["action"] == "approve" and "abc" in res["message"] and res["rule_key"] == "wicked-memory:forget:abc"
    assert gate("wicked_memory_recall", {"query": "x"}) is None
    assert gate("terminal", {"command": "ls"}) is None


def test_no_credential_or_core_patching_patterns():
    """Hermes review rules: secrets only via agent.secret_scope; no os.environ/.env reads; no core patching."""
    forbidden = [r"os\.environ", r"\.env\b", r"auth\.json", r"sys\.modules\[", r"setattr\(", r"subprocess", r"\beval\(", r"\bexec\("]
    for path in ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert not re.search(pattern, text), f"{path.name} matches {pattern}"
