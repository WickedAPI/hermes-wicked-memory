"""Tool schemas the model reads. The descriptions are what teach it when (and when not) to call each tool."""

REMEMBER = {
    "name": "wicked_memory_remember",
    "description": (
        "Save one durable fact, preference, decision or lesson to long-term memory so it is available in "
        "FUTURE sessions. Write a single self-contained statement a future you could act on without this "
        "conversation. Do NOT store passwords, API keys, private keys or other secrets. Returns the memory id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The fact to remember, as one self-contained statement (max ~16 KB)."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional short labels for filtering, e.g. ['preferences']."},
        },
        "required": ["text"],
    },
}

RECALL = {
    "name": "wicked_memory_recall",
    "description": (
        "Search long-term memory by MEANING (semantic search, not keywords). Use it before answering anything "
        "that may depend on earlier sessions: user preferences, past decisions, project facts, earlier mistakes. "
        "Returns the closest memories with a similarity score (higher is closer). Only currently-valid versions "
        "are searched unless as_of or include_superseded is given."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What you want to remember, in natural language."},
            "limit": {"type": "integer", "description": "Max results, 1-50 (default 5)."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Only memories having ALL of these tags."},
            "as_of": {"type": "string", "description": "ISO-8601 instant: search memory as it stood at that moment."},
            "include_superseded": {"type": "boolean", "description": "Also search old, replaced versions."},
        },
        "required": ["query"],
    },
}

UPDATE = {
    "name": "wicked_memory_update",
    "description": (
        "Change a remembered fact WITHOUT losing history: creates a new version and closes the old one. Use it "
        "when a fact changed (not to add a new fact: use wicked_memory_remember). Pass the id of the CURRENT "
        "version; omitted fields carry over."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "UUID of the current version to supersede."},
            "text": {"type": "string", "description": "The new text."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Replacement tags."},
        },
        "required": ["memory_id"],
    },
}

HISTORY = {
    "name": "wicked_memory_history",
    "description": "Show every version of a memory, oldest first, with when each was valid. Use it to see how a fact changed over time.",
    "parameters": {
        "type": "object",
        "properties": {"memory_id": {"type": "string", "description": "UUID of any version of the memory."}},
        "required": ["memory_id"],
    },
}

FORGET = {
    "name": "wicked_memory_forget",
    "description": (
        "PERMANENTLY delete a memory (hard delete, cannot be undone). By default removes every version. Only "
        "call this when the user asks to forget something or a memory is wrong or sensitive. The user is asked to approve."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "UUID of any version of the memory."},
            "scope": {"type": "string", "enum": ["chain", "version"], "description": "'chain' (default) deletes all versions; 'version' only this one."},
            "reason": {"type": "string", "description": "Optional short note kept in the content-free audit log."},
        },
        "required": ["memory_id"],
    },
}

WHOAMI = {
    "name": "wicked_memory_whoami",
    "description": (
        "Report which wallet this agent's memories belong to, which service it talks to, and how search is "
        "being paid for. Use it to troubleshoot or when the user asks where the memories live."
    ),
    "parameters": {"type": "object", "properties": {}},
}

ALL = [REMEMBER, RECALL, UPDATE, HISTORY, FORGET, WHOAMI]
