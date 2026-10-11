---
name: wicked-suite-mcp
description: "Connect Hermes to the hosted Wicked MCP server (75 tools: trading data, DeFi and token risk, brokerage sync, agent identity, reputation, tool registry, hallucination checks, paywalls) and know when to use it instead of the built-in memory tools."
version: 0.1.0
metadata:
  hermes:
    tags: [mcp, wicked, x402, trading, identity]
    category: tools
---

# Wicked MCP server

The Wicked suite is also available as one hosted MCP server: `https://mcp.wickedapi.com/mcp` (75 tools).
It covers trading data, DeFi protocol health, token scam checks, brokerage sync, agent identity, on-chain
reputation, a tool-reliability registry, hallucination checks and x402 paywalls.

## Connect it (once, by the user)

Add it to Hermes with the CLI:

```
hermes mcp add wicked --url "https://mcp.wickedapi.com/mcp"
```

or in `config.yaml`:

```yaml
mcp_servers:
  wicked:
    url: https://mcp.wickedapi.com/mcp
```

Start a new session afterwards. Priced tools return the real x402 payment challenge (price, network, pay-to)
when called without credentials; nothing is spent unless the user configures a payer wallet.

## Memory: use the native tools, not the MCP ones

The MCP server also exposes `memory_prepare`, `memory_store`, `memory_search` and friends, but every one of those
needs a wallet signature produced OUTSIDE the server (the MCP server never holds a key), which a Hermes session
cannot do on its own. This plugin's `wicked_memory_*` tools sign locally, so for memory ALWAYS prefer
`wicked_memory_remember` and `wicked_memory_recall`. Use the MCP server for the rest of the suite.

## Honest limits

Free tools are rate limited per IP. Anything priced costs real USDC on Base if the user has set up a payer
wallet: confirm with the user before any call that spends money.
