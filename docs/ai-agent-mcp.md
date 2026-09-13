---
title: "Run an AI Agent / MCP Server on Your Own LINE Account — OkLine"
description: "Run an AI agent or MCP server on your own LINE account: Claude reads your chats and replies via OkLine's bot framework — no bot channel, no webhook."
---

# Run an AI agent / MCP server on your own LINE account

[← docs home](./index.md)

> ✅ Verified against LINE Chrome extension 3.7.2 — September 2026 (v2.9.2)

The question "can Claude / an AI agent read and reply to my LINE chats?" is
getting common, and until now every answer started with "create a bot channel,
set up a webhook…" — a public server just to let a model see your own messages.

OkLine changes the shape of that answer. Because it automates **your own
personal account** — polling messages, decrypting E2EE, replying in the same
chat — wiring an LLM into LINE is a plain script on your laptop: no bot
channel, no webhook, no ngrok, no developer registration. This page shows two
patterns: a direct Claude auto-replier, and a minimal **MCP server** that
exposes your chats as tools to any MCP client.

> **Read this first:** personal-account automation violates LINE's ToS — the
> candid risk framing is in the
> [FAQ](./faq.md#8-will-my-line-account-get-banned-for-using-an-unofficial-api).
> Use your own account, keep volumes human-like, and think hard before you let
> an LLM reply on an account people rely on.

## The bot framework in 30 seconds

Everything below builds on [Bot](./bots.md): it streams your incoming
operations, **auto-decrypts Letter-Sealed messages**, and hands each one to
your handler with a one-line `ctx.reply(...)`:

```python
from okline import OkLine, Bot

api = OkLine.from_tokens_file("tokens.json")  # made once by `okline login`
bot = Bot(api)


@bot.on_message
def echo(ctx):
    if ctx.text:
        ctx.reply(f"you said: {ctx.text}")


bot.run()  # blocks; Ctrl-C to stop
```

`ctx` carries `ctx.text` (decrypted), `ctx.sender` (mid), `ctx.to` (the
conversation), `ctx.is_group`, and `ctx.reply(...)` picks the right destination
— DM sender or the group — automatically.

## Pattern 1: a Claude auto-replier

Message in → Claude → reply out. The whole agent is the handler:

```python
import anthropic
from okline import OkLine, Bot

llm = anthropic.Anthropic()  # pip install anthropic
api = OkLine.from_tokens_file("tokens.json")
bot = Bot(api)


@bot.on_message
def reply(ctx):
    if not ctx.text:
        return  # skip stickers, images, …
    resp = llm.messages.create(
        model="claude-opus-5",
        max_tokens=1024,
        system="You are a helpful assistant replying on LINE. Be brief.",
        messages=[{"role": "user", "content": ctx.text}],
    )
    answer = "".join(b.text for b in resp.content if b.type == "text")
    if answer:
        ctx.reply(answer)


bot.run(keepalive=True)
```

Notes:

- Each message is answered statelessly here. For memory, keep a
  `dict[chat_mid, list_of_turns]` and pass the conversation history in
  `messages` — the Messages API is stateless, your loop owns the history.
- `bot.run(keepalive=True)` adds the ~20 s heartbeat that keeps long-lived
  streams healthy (see [receiving events](./receiving-events.md)).
- Guard rails are yours to add: an allow-list of chats (`if ctx.to not in
  ALLOWED: return`), a `/ai` command prefix instead of every message, a
  human-approval step for sends. An agent that can message your contacts is a
  loaded footgun — start narrow.

The same loop works with any LLM SDK; only the `llm.messages.create` call
changes.

## Pattern 2: a minimal MCP server

The [Model Context Protocol](https://modelcontextprotocol.io) (MCP) is the
standard way to hand tools to an AI client: your process speaks JSON-RPC over
stdio, the client (Claude Desktop, Claude Code, …) calls your tools. OkLine is
a natural backend — two tools cover the loop: **read recent messages** and
**send a reply**.

Below is a **working sketch, not a product** — a hand-rolled stdio JSON-RPC
loop implementing just enough MCP for those two tools. It is meant to show how
thin the layer is. For anything real, build on the official
[`mcp` Python SDK](https://github.com/modelcontextprotocol/python-sdk) instead
of maintaining your own protocol loop.

```python
#!/usr/bin/env python3
"""line_mcp.py — a minimal MCP server exposing your LINE chats over stdio.

Sketch/example: hand-rolled newline-delimited JSON-RPC 2.0, two tools
(`recent_messages`, `send_reply`). For production use the `mcp` SDK.

    python line_mcp.py     # run under an MCP client (see config below)
"""

from __future__ import annotations

import json
import sys

from okline import OkLine

api = OkLine.from_tokens_file("tokens.json")  # made once by `okline login`

TOOLS = [
    {
        "name": "recent_messages",
        "description": "Read the most recent messages of one of your LINE "
        "chats (group, room or DM), oldest first, decrypted where possible.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat_mid": {
                    "type": "string",
                    "description": "the chat/user/group mid to read",
                },
                "count": {
                    "type": "integer",
                    "default": 20,
                    "description": "how many messages (default 20)",
                },
            },
            "required": ["chat_mid"],
        },
    },
    {
        "name": "send_reply",
        "description": "Send a text message to one of your LINE chats, as yourself.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat_mid": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["chat_mid", "text"],
        },
    },
]


def reply(request_id, result) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)


def call_tool(name: str, args: dict) -> str:
    if name == "recent_messages":
        msgs = api.get_recent_messages(args["chat_mid"], args.get("count", 20)) or []
        lines = []
        for m in reversed(msgs):  # oldest first
            if m.get("chunks"):  # Letter-Sealed
                m = api.decrypt_message(m)
            lines.append(f"{m.get('from')}: {m.get('text') or '<non-text>'}")
        return "\n".join(lines) or "(no messages)"
    if name == "send_reply":
        api.send_text(args["chat_mid"], args["text"])  # auto-seals if needed
        return "sent"
    raise ValueError(f"unknown tool: {name}")


for line in sys.stdin:  # the MCP stdio loop
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    method, rid = req.get("method"), req.get("id")

    if method == "initialize":
        reply(
            rid,
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "okline", "version": "0.1.0"},
            },
        )
    elif method == "tools/list":
        reply(rid, {"tools": TOOLS})
    elif method == "tools/call":
        params = req.get("params", {})
        try:
            text = call_tool(params["name"], params.get("arguments", {}))
            reply(rid, {"content": [{"type": "text", "text": text}]})
        except Exception as e:  # surface errors to the model
            reply(rid, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
    # notifications (e.g. "notifications/initialized") get no response
```

Register it with your MCP client — for Claude Desktop
(`claude_desktop_config.json`) or Claude Code (`claude mcp add`):

```json
{
  "mcpServers": {
    "okline": {
      "command": "python",
      "args": ["/absolute/path/to/line_mcp.py"]
    }
  }
}
```

Now the model can answer "what's new in my LINE chats?" by calling
`recent_messages`, and draft replies through `send_reply`. Natural extensions
to the sketch: a `list_chats` tool (`api.get_all_chat_mids()` +
`api.get_chats(...)` for names), a `send_image` tool, per-tool allow-lists, and
human confirmation before any send. And again — this is an example to
copy-adapt, not a supported server.

**Security notes, seriously:** this server gives whatever process runs it
full access to your LINE account — read *and* send as you. Keep
`tokens.json` and the script on the same trusted machine, prefer read-mostly
tools, and think twice before granting it to an agent with autonomy over
sends. See [SECURITY.md](https://github.com/NiceDayZc/OkLine/blob/main/SECURITY.md).

## Related pages

- [Building bots](./bots.md) — the `Bot` framework this page builds on
- [Receiving events](./receiving-events.md) — the raw SSE operation stream
- [E2EE / Letter Sealing](./e2ee.md) — how the decryption in `ctx.text` works
- [Authentication](./authentication.md) — sessions, refresh, renewal schedule
- [FAQ](./faq.md) — including the AI-agent question (#9)

---

**Next:** [Bots](./bots.md) · [Receiving events](./receiving-events.md) ·
[FAQ](./faq.md)
