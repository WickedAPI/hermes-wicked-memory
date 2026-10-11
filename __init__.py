"""Wicked Memory for Hermes: persistent, wallet-scoped long-term memory for the agent.

Tools:  wicked_memory_remember / _recall / _update / _history / _forget / _whoami
Hook:   pre_tool_call asks the human to approve every wicked_memory_forget (a hard delete).
Skills: wicked-memory:wicked-memory (how to use memory well), wicked-memory:wicked-suite-mcp (the rest of the
        Wicked suite through the hosted MCP server).

The agent's identity is a wallet; every request is signed locally and the key is never sent. Nothing here
patches Hermes: only ctx.register_tool / register_hook / register_skill are used.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import client, schemas, tools

logger = logging.getLogger(__name__)

TOOLSET = "wicked_memory"


def _available() -> bool:
    """Hide the tools (instead of failing at call time) when the signing dependency is missing."""
    return client.dependency_available()


def register(ctx: Any) -> None:
    handlers = tools.build_handlers(ctx)
    for schema in schemas.ALL:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=schema,
            handler=handlers[schema["name"]],
            check_fn=_available,
            description=schema["description"].split(". ")[0],
            emoji="🧠",
        )
    ctx.register_hook("pre_tool_call", tools.forget_approval)

    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            if (child / "SKILL.md").exists():
                ctx.register_skill(child.name, child / "SKILL.md")
