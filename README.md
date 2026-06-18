# Agent Replay — Claude Desktop Session Replay

See exactly what Claude Desktop did for each task — which files it read or wrote, what it searched for, and which shell commands it ran — in a local replay UI.

```
Claude Desktop
    └── MCP server (mcp_server.py)
            ├── read_file("src/auth.py")      → logged ✅
            ├── web_search("jwt expiry bug")   → logged ✅
            ├── write_file("src/auth.py", …)   → logged ✅
            └── bash("pytest tests/")          → logged ✅
                           ↓
              http://127.0.0.1:8787   (replay UI)
```

---

## Quick Start (demo, no Claude Desktop needed)

```bash
# Terminal 1 — start the collector + UI server
python3 collector.py

# Terminal 2 — run the demo (simulates a coding session)
python3 example_agent.py
```

Open **http://127.0.0.1:8787** — you will see a session called "Fix JWT token expiry bug" with file reads, a web search, a file write, bash commands, and a new test file being created.

---

## Connect Claude Desktop

**1. Find your config file**

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

**2. Add the MCP server block**

```json
{
  "mcpServers": {
    "agent-replay": {
      "command": "python3",
      "args": ["/FULL/PATH/TO/agent-session-replay-demo/mcp_server.py"]
    }
  }
}
```

Replace the path with the absolute path on your machine.

**3. Restart Claude Desktop**

The tools `read_file`, `write_file`, `create_file`, `bash`, and `web_search` are now available to Claude. Every call is logged and visible in the replay UI.

**4. Make sure the collector is running**

```bash
python3 collector.py
```

Open **http://127.0.0.1:8787**. Use Claude Desktop normally. Each new conversation that uses a tool will appear as a session after Claude makes its first tool call.

---

## What You Can See

| Span | Icon | What it shows |
|---|---|---|
| File read | `READ` | Path, file size, content preview |
| File write | `EDIT` | Path, bytes written, new content preview |
| File create | `NEW` | Path, bytes written, new content preview |
| Web search | `WEB` | Query, result titles + snippets |
| Bash command | `BASH` | Command, exit code, stdout, stderr |

**Filter tabs** in the trace panel: All · Files · Search · Bash · Errors

---

## What You Cannot See

Claude Desktop's MCP interface does not expose LLM internals. The following are **not** available from this tool:

- Token counts or context window usage
- Dollar cost per call
- Internal chain-of-thought or reasoning
- Tool calls made by Claude's built-in native tools (only calls through this MCP server are logged)

---

## Session Management

A new replay session is created when there is more than **10 minutes** of inactivity between tool calls. Session state is stored in `~/.agent_replay_mcp_state.json`.

To force a new session immediately:

```bash
rm ~/.agent_replay_mcp_state.json
```

---

## Other Recording Methods

**CLI wrapper** — records the visible terminal output of any CLI tool:

```bash
python3 record_cli.py -- claude
python3 record_cli.py -- bash
```

**Browser extension** — records visible messages from claude.ai or chatgpt.com:

1. Open `chrome://extensions`, enable Developer mode
2. Click "Load unpacked", select the `browser-extension/` folder
3. Keep `python3 collector.py` running
4. Chat on claude.ai or chatgpt.com — messages appear in the replay UI

**Python SDK** — instrument your own agent code with full visibility:

```python
from agentreplay import ReplayClient

replay = ReplayClient()

with replay.session("My task", input={"user": "dev-42"}):
    src = replay.read_text("src/app.py")
    results = replay.web_search("how to fix X", results=fetched_results)
    replay.file_write("src/app.py", fixed_src)
    replay.bash(["pytest", "tests/"])
    replay.file_create("tests/test_regression.py", test_code)
```

---

## Files

| File | Purpose |
|---|---|
| `mcp_server.py` | MCP server — plug into Claude Desktop |
| `collector.py` | Local HTTP server + SQLite store + UI server |
| `index.html` | Replay UI |
| `agentreplay.py` | Python SDK for direct instrumentation |
| `record_cli.py` | Terminal session recorder |
| `example_agent.py` | Demo: simulates a coding session |
| `browser-extension/` | Chrome/Edge extension for claude.ai / ChatGPT |
