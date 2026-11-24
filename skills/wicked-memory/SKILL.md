---
name: wicked-memory
description: Use Wicked Memory to remember and recall facts, preferences and decisions across Hermes sessions, with versioned history and real deletion.
version: 0.1.0
metadata:
  hermes:
    tags: [memory, long-term-memory, recall]
    category: memory
---

# Wicked Memory

Long-term memory that survives restarts and new sessions. Memories are private to this agent's wallet and are
searched by meaning, so you do not need exact words.

## When to recall

Call `wicked_memory_recall` BEFORE answering when the question may depend on earlier sessions:

- user preferences ("how does the user like X?"), earlier decisions and their reasons
- project facts, names, environments, conventions
- mistakes made before and what fixed them

Ask in natural language ("what database does this project use?"). Start with the default limit; widen only if
nothing relevant came back. Treat low scores as weak matches and say so rather than presenting them as fact.

## When to remember

Call `wicked_memory_remember` for durable things worth knowing next time:

- a preference or constraint the user stated
- a decision and its reason
- a lesson from a failure

Write ONE self-contained statement per memory ("The user wants dark mode and compact tables in every dashboard"),
not a transcript. Add short tags for things you will filter by later.

Never store passwords, API keys, private keys, tokens or other secrets. Do not store what is cheap to look up.

## When facts change

Use `wicked_memory_update` with the id of the CURRENT version. It never overwrites: the old version is closed
and kept, so `wicked_memory_history` can show how the fact evolved, and `wicked_memory_recall` with `as_of` can
answer "what did we believe last month?". Use `wicked_memory_remember` only for genuinely new facts.

## Forgetting

`wicked_memory_forget` permanently deletes a memory (every version by default). Use it when the user asks you to
forget something or when a memory is wrong or sensitive. The user is asked to approve each deletion. Tell them it
cannot be undone.

## Troubleshooting

`wicked_memory_whoami` shows the wallet, the service and how search is paid for. A 429 means the shared
community key is busy: retry shortly or set `WICKED_MEMORY_API_KEY`. A 402 means no key is available.

## Backups and getting memory back

Only when the user asks (never as part of normal remembering):

- **Back up:** `wicked_memory_backup` with `action: export` writes a readable file of every memory, with history, to this plugin's backups folder. `action: restore` puts the newest (or a named) backup back, and is safe to repeat.
- **Offline copy:** `action: mirror_on` keeps an always-current readable copy on this computer. It is unencrypted, so the user is asked to approve it; do not suggest it unless they want an offline copy. `mirror_sync` refreshes it, `mirror_off` stops it.
- **New machine:** memories belong to the wallet. With the same wallet key (`WICKED_MEMORY_PRIVATE_KEY`) the memories are simply there. Without it, a Wicked Memory Pro key (`WICKED_MEMORY_API_KEY=wm_pro_...`) lets `wicked_memory_link` with `recover: true` move the old wallet's memories to this one (the user approves). Check with `wicked_memory_whoami`, which shows the plan.
