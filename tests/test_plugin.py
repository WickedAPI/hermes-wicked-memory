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


# ---- backups, mirror, Pro -----------------------------------------------------------------------

def _rows():
    base = {"tags": [], "metadata": {}, "source": None, "created_at": "2026-01-01T00:00:00Z", "valid_from": "2026-01-01T00:00:00Z"}
    return [
        {**base, "id": "old", "content": "v1", "valid_until": "2026-02-01T00:00:00Z", "superseded_by": "new", "updated_at": "2026-02-01T00:00:00.000001Z"},
        {**base, "id": "new", "content": "v2", "valid_until": None, "superseded_by": None, "updated_at": "2026-02-01T00:00:00.000002Z"},
        {**base, "id": "solo", "content": "other fact", "valid_until": None, "superseded_by": None, "updated_at": "2026-03-01T00:00:00.000000Z"},
    ]


def test_sort_successors_first(plugin):
    rows = _rows()
    order = [r["id"] for r in plugin.client.sort_successors_first(rows)]
    assert order.index("new") < order.index("old") and sorted(order) == ["new", "old", "solo"]
    flipped = [r["id"] for r in plugin.client.sort_successors_first(list(reversed(rows)))]
    assert flipped.index("new") < flipped.index("old")  # whatever order the file is in


def test_export_then_restore_round_trip(wired, service):
    ctx, _, data_dir = wired
    service.export_rows = _rows()
    out = call(ctx, "wicked_memory_backup", action="export")
    assert out["exported"] and out["memories"] == 3 and out["current"] == 2
    path = data_dir / "backups" / out["file"]
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["format"] == "wicked-memory-export" and [l["id"] for l in lines[1:]] == ["old", "new", "solo"]
    assert call(ctx, "wicked_memory_backup", action="status")["backups"] == [out["file"]]

    res = call(ctx, "wicked_memory_backup", action="restore")
    assert res["restored"] and res["imported"] == 3 and res["file"] == out["file"]
    sent = [m["id"] for batch in service.imported for m in batch]
    assert sorted(sent) == ["new", "old", "solo"] and sent.index("new") < sent.index("old")  # successor first
    imp = [r for r in service.requests if r["path"] == "/memories/import"][0]
    assert "wallet" in imp  # signed like every other call


def test_restore_only_reads_named_backups_never_paths(wired):
    ctx, _, _ = wired
    for bad in ("../../etc/passwd", "wallet.key", "C:/x.jsonl", "backups/../wallet.key"):
        assert "error" in call(ctx, "wicked_memory_backup", action="restore", file=bad)
    assert "no backup file found" in call(ctx, "wicked_memory_backup", action="restore")["error"]
    assert "error" in call(ctx, "wicked_memory_backup", action="nonsense")


def test_mirror_is_off_until_enabled_and_syncs_incrementally(wired, service):
    ctx, _, data_dir = wired
    service.export_rows = _rows()
    assert call(ctx, "wicked_memory_backup", action="status")["mirror"]["enabled"] is False
    assert "error" in call(ctx, "wicked_memory_backup", action="mirror_sync")  # off: nothing written
    assert not (data_dir / "mirror").exists()

    on = call(ctx, "wicked_memory_backup", action="mirror_on")
    assert on["mirror"] == "on" and on["pulled"] == 3 and on["memories"] == 3
    folder = next((data_dir / "mirror").iterdir())
    kept = [json.loads(l) for l in (folder / "memories.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sorted(m["id"] for m in kept) == ["new", "old", "solo"]

    # a change on the service: one row gone, one new; the next sync pulls from the saved cursor only
    service.export_rows.append({**_rows()[2], "id": "fresh", "content": "newer fact", "updated_at": "2026-04-01T00:00:00.000000Z"})
    service.deletions = [{"memory_id": "solo", "deleted_at": "2026-04-02T00:00:00Z"}]
    before = len(service.requests)
    again = call(ctx, "wicked_memory_backup", action="mirror_sync")
    assert again["pulled"] == 1 and again["removed"] == 1 and again["memories"] == 3
    export_calls = [r["path"] for r in service.requests[before:] if r["path"].startswith("/memories/export")]
    assert export_calls and "cursor=c3" in export_calls[0]
    ids = sorted(json.loads(l)["id"] for l in (folder / "memories.jsonl").read_text(encoding="utf-8").splitlines())
    assert ids == ["fresh", "new", "old"]

    off = call(ctx, "wicked_memory_backup", action="mirror_off")
    assert off["mirror"] == "off" and call(ctx, "wicked_memory_backup", action="status")["mirror"]["enabled"] is False


def test_mirror_dir_setting_moves_the_copy(wired, service, tmp_path):
    ctx, _, _ = wired
    service.export_rows = _rows()
    target = tmp_path / "my-synced-folder"
    ctx.config["mirror_dir"] = str(target)
    call(ctx, "wicked_memory_backup", action="mirror_on")
    assert list(target.glob("wicked-memory-*/memories.jsonl"))


def test_enabled_mirror_refreshes_on_first_use_of_a_new_process(wired, plugin, service):
    ctx, _, data_dir = wired
    service.export_rows = _rows()
    call(ctx, "wicked_memory_backup", action="mirror_on")
    service.export_rows.append({**_rows()[2], "id": "later", "content": "later fact", "updated_at": "2026-05-01T00:00:00.000000Z"})
    ctx2 = FakeCtx(ctx.config["base_url"])
    plugin.register(ctx2)  # a new session: the first tool call brings the mirror up to date by itself
    call(ctx2, "wicked_memory_whoami")
    folder = next((data_dir / "mirror").iterdir())
    assert "later" in (folder / "memories.jsonl").read_text(encoding="utf-8")


def test_privacy_sensitive_actions_need_approval(plugin):
    gate = plugin.tools.forget_approval
    on = gate("wicked_memory_backup", {"action": "mirror_on"})
    assert on["action"] == "approve" and "UNENCRYPTED" in on["message"] and on["rule_key"] == "wicked-memory:mirror-on"
    assert gate("wicked_memory_link", {"recover": True})["rule_key"] == "wicked-memory:link-recover"
    for harmless in (("wicked_memory_backup", {"action": "export"}), ("wicked_memory_backup", {"action": "restore"}),
                     ("wicked_memory_backup", {"action": "mirror_sync"}), ("wicked_memory_link", {}), ("wicked_memory_link", {"recover": False})):
        assert gate(*harmless) is None


def test_pro_key_links_this_wallet_on_first_use(wired, service):
    ctx, secrets, _ = wired
    secrets["WICKED_MEMORY_API_KEY"] = "wm_pro_" + "a" * 40
    who = call(ctx, "wicked_memory_whoami")
    assert who["plan"] == "pro" and "linked" in who["pro_key"]
    link = [r for r in service.requests if r["path"] == "/v1/pro/link"][0]
    assert {k.lower(): v for k, v in link["headers"].items()}["x-api-key"] == secrets["WICKED_MEMORY_API_KEY"]
    assert json.loads(link["body"]) == {"move_from_previous": False} and "wallet" in link


def test_pro_key_for_another_wallet_points_the_agent_to_recover(wired, service):
    ctx, secrets, _ = wired
    secrets["WICKED_MEMORY_API_KEY"] = "wm_pro_" + "b" * 40
    service.link_response = (409, {"error": "This Pro key is linked to another wallet (0x1234...abcd) holding 12 memories. To restore them, repeat with move_from_previous.", "source": "pro"})
    who = call(ctx, "wicked_memory_whoami")
    assert "not linked" in who["plan"] and "holding 12 memories" in who["pro_key"]
    out = call(ctx, "wicked_memory_link")
    assert out["status"] == 409 and "recover=true" in out["hint"]
    service.link_response = (200, {"data": {"plan": "pro", "linked": True, "moved": 12, "wallet": "0xabc", "searches": {"used": 1}}})
    done = call(ctx, "wicked_memory_link", recover=True)
    assert done["linked"] and done["memories_moved"] == 12
    last = [r for r in service.requests if r["path"] == "/v1/pro/link"][-1]
    assert json.loads(last["body"]) == {"move_from_previous": True}


def test_link_without_a_pro_key_explains_itself(wired):
    ctx, _, _ = wired
    out = call(ctx, "wicked_memory_link")
    assert "wm_pro_" in out["error"]


def test_keys_never_follow_a_redirect_to_another_host(plugin):
    import http.server
    import threading

    seen: list = []

    class Sink(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("x-api-key"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        log_message = lambda *a: None

    sink = http.server.HTTPServer(("127.0.0.1", 0), Sink)

    class Redirector(Sink):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{sink.server_port}/steal")
            self.end_headers()

    origin = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
    for srv in (sink, origin):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        eth = pytest.importorskip("eth_account")
        client = plugin.client.WickedMemoryClient(
            eth.Account.create(), base_url=f"http://127.0.0.1:{origin.server_port}", api_key="SECRET")
        with pytest.raises(plugin.client.WickedMemoryError):
            client.search("x")
        assert plugin.client.discover_community_key(f"http://127.0.0.1:{origin.server_port}") is None
    finally:
        sink.shutdown()
        origin.shutdown()
    assert seen == []

