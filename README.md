# Wicked Memory for Hermes

Persistent, wallet-scoped long-term memory for [Hermes Agent](https://hermes-agent.nousresearch.com), backed by the
hosted [Wicked Memory](https://memory.wickedapi.com) service. Your agent can remember facts across sessions,
recall them by meaning, keep a versioned history of how facts changed, and permanently forget on request.

It is a normal tools plugin, so it works alongside Hermes' built-in memory and any memory provider you already use.

## Install

```
hermes plugins install WickedAPI/hermes-wicked-memory
hermes plugins enable wicked-memory
```

Start a new session. That's it: no signup and no key to paste. On first use the plugin creates a wallet for the
agent (this is the agent's identity) and uses the service's shared free search key.

## What it adds

| Tool | What it does |
|---|---|
| `wicked_memory_remember` | Save one durable fact, preference, decision or lesson (free) |
| `wicked_memory_recall` | Semantic search over the agent's memories, ranked by similarity |
| `wicked_memory_update` | Change a fact without losing history: new version, old one closed |
| `wicked_memory_history` | Every version of a memory and when each was valid |
| `wicked_memory_forget` | Permanent hard delete. **You are asked to approve every call.** |
| `wicked_memory_whoami` | Which wallet, service and plan the memories live under |
| `wicked_memory_backup` | Export a readable backup, restore one, or keep an opt-in offline mirror |
| `wicked_memory_link` | Wicked Memory Pro: link this wallet to your key, or recover the memories of a lost machine |

Two skills are bundled: `wicked-memory:wicked-memory` (when to recall, what to remember, how to update) and
`wicked-memory:wicked-suite-mcp` (how to connect the hosted Wicked MCP server for the rest of the suite:
`hermes mcp add wicked --url "https://mcp.wickedapi.com/mcp"`).

## How memory is kept private

- The agent's identity is a wallet. Every request is signed locally (EIP-191) over its method, path, body, a
  timestamp and a single-use nonce. The private key never leaves your machine.
- Every query on the service is scoped to that wallet, so another wallet can never read, search or even detect
  these memories. **Whoever holds the wallet key can read the memories, and losing it loses access to them.**
- Updates never overwrite (history is kept). Forgetting is a real hard delete; only a content-free audit row remains.

## Backups, the offline mirror, and getting memory back

Memories live on the service under the agent's wallet, so they survive Hermes being reinstalled **as long as the agent
has the same wallet**. Three ways to keep that true:

1. **Keep the wallet key.** Set `WICKED_MEMORY_PRIVATE_KEY` to a key you hold (or copy `wallet.key` from the plugin data
   directory). Any machine with the key sees the same memories.
2. **A Wicked Memory Pro key** (`WICKED_MEMORY_API_KEY=wm_pro_...`, [subscribe](https://syncr.wickedapi.com/memory)). The plugin
   links your wallet to the key on first use. On a new machine with a new wallet, ask the agent to run `wicked_memory_link`
   with `recover: true` (you approve it) and the old wallet's memories move to the new one.
3. **A backup file.** `wicked_memory_backup` with `action: export` writes every memory (with history) to the plugin's
   `backups` folder; `restore` puts it back into any wallet. Restoring twice does nothing the second time.

**Optional offline mirror.** `action: mirror_on` keeps a readable copy of the agent's memories on this computer and refreshes
it (only what changed) at the start of each session. It is **off by default and you are asked to approve turning it on,
because the copy is not encrypted**: anyone who can read your files can read it. The `mirror_dir` setting puts it in a
folder you choose (for example one your own backup tool already covers); it is yours to protect there. `mirror_off` stops
refreshing it, and deleting the folder removes it.

## Configuration (all optional)

Set in the Hermes plugin settings (or the environment variables shown):

| Setting | Env var | Purpose |
|---|---|---|
| `base_url` | | Service URL. Default `https://memory.wickedapi.com`. |
| `api_key` | `WICKED_MEMORY_API_KEY` | Your own key for search: a free key, or a Wicked Memory Pro key (`wm_pro_...`). Without it the shared community key is used. |
| `mirror_dir` | | Folder for the opt-in offline mirror. Default: inside the plugin data directory. |
| `private_key` | `WICKED_MEMORY_PRIVATE_KEY` | Bring your own Ethereum private key as the agent's identity (e.g. to use the same memories from another agent). |

Pricing: storing, updating and forgetting are free. Search is free with a key (the shared community key is rate
limited and shared by everyone) or 0.001 USDC per call via x402. This plugin uses keys; it does not spend funds.

## Disclosures

- **Network:** talks only to the configured Wicked Memory service (`memory.wickedapi.com` by default). Each tool
  call sends the memory text or search query you or the model provide, signed by the agent's wallet. On first use it
  also makes one GET to `/v1/status` on that service to read the published community key.
- **Stored credentials:** unless you supply `WICKED_MEMORY_PRIVATE_KEY`, the plugin generates an Ethereum private key
  on first use and stores it unencrypted (owner-only file permissions) at `<hermes home>/plugin-data/wicked-memory/wallet.key`.
  It reads no other credential files and no other tool's logins. Secrets you supply are read through Hermes'
  `agent.secret_scope`.
- **Files it writes:** `wallet.key` (above), backups you ask for in `<data dir>/backups/`, and, only if you approve it, the offline
  mirror in `<data dir>/mirror/` (or your `mirror_dir`). Backups and the mirror hold your memories in clear text, are created
  owner-only, and `restore` accepts only a backup name from that folder, never a path. If `WICKED_MEMORY_API_KEY` is a Pro
  key, one extra request per session links your wallet to it.
- **No other behavior:** no shell commands, no background processes, no reads outside its own data directory (and your `mirror_dir`),
  no telemetry. It registers tools, one `pre_tool_call` hook (approval for `wicked_memory_forget`, for a Pro `recover`, and for
  turning the mirror on) and two read-only skills.
- **Third-party data:** memory text is stored on the Wicked Memory service (hosted by the plugin author). Do not
  ask the agent to remember secrets. Treat anything you remember as stored with that service.

## Development

```
cd tests && python -m pytest -q      # needs: pip install eth-account pytest pyyaml
hermes plugins validate . --install-deps
```

License: MIT.
