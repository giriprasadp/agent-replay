# Agent Replay

See exactly what Claude Desktop does behind the scenes — which files it read, which files it wrote, what it searched for, and which shell commands it ran — replayed step by step in a local UI.

![Trace view showing file reads, web search, and bash spans]

---

## What it looks like

Every session shows a timeline of steps:

| Icon | Type | What you see |
|---|---|---|
| `READ` | File read | Path + content preview |
| `EDIT` | File write | Path + new content preview |
| `NEW` | File create | Path + content |
| `WEB` | Web search | Query + result titles and snippets |
| `BASH` | Shell command | Command + exit code + stdout |

---

## Prerequisites

- Python 3.10 or later (no pip installs needed — stdlib only)
- Claude Desktop installed and running
- Git

---

## Step 1 — Clone the repo

```bash
git clone https://github.com/giriprasadp/agent-replay.git
cd agent-replay
```

---

## Step 2 — Start the collector

The collector is the local server that receives spans from the MCP server and serves the UI.

```bash
python3 collector.py
```

You should see:

```
Agent Replay running at http://127.0.0.1:8787
SQLite database: /path/to/agent-replay/agent_replay.sqlite3
```

Leave this running in a terminal. Open **http://127.0.0.1:8787** — it will say "no sessions yet."

---

## Step 3 — Run the demo (no Claude Desktop needed)

This simulates a full coding session so you can see what the UI looks like before connecting Claude Desktop.

Open a second terminal:

```bash
python3 example_agent.py
```

Refresh **http://127.0.0.1:8787**. You will see a session called **"Fix JWT token expiry bug"** with:

- 2 file reads (`sample_project/auth.py`, `sample_project/config.py`)
- 1 web search with 3 results
- 1 file write (patched `auth.py`)
- 2 bash commands (running pytest)
- 1 file create (new regression test)

Click any step to see the full input and output in the right panel.

---

## Step 4 — Connect Claude Desktop

### Find your config file

| OS | Path |
|---|---|
| **macOS** | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| **Windows** | `%APPDATA%\Claude\claude_desktop_config.json` |
| **Linux** | `~/.config/Claude/claude_desktop_config.json` |

### Add the MCP server

Open the config file and add (or merge into) the `mcpServers` block:

```json
{
  "mcpServers": {
    "agent-replay": {
      "command": "python3",
      "args": ["/FULL/PATH/TO/agent-replay/mcp_server.py"]
    }
  }
}
```

**Replace `/FULL/PATH/TO/agent-replay/`** with the actual absolute path where you cloned the repo.

To find it:

```bash
# macOS / Linux
pwd
# e.g. /home/yourname/projects/agent-replay

# Windows (in PowerShell)
Get-Location
# e.g. C:\Users\yourname\projects\agent-replay
```

### Windows example

```json
{
  "mcpServers": {
    "agent-replay": {
      "command": "python3",
      "args": ["C:\\Users\\yourname\\projects\\agent-replay\\mcp_server.py"]
    }
  }
}
```

---

## Step 5 — Restart Claude Desktop

Fully quit and reopen Claude Desktop. The MCP server must be picked up on startup.

To verify it connected: in Claude Desktop, ask:

> "What tools do you have available?"

Claude should list `read_file`, `write_file`, `create_file`, `bash`, and `web_search`.

---

## Step 6 — Use Claude Desktop normally

Give Claude a real task that involves files, for example:

> "Read my README.md and suggest improvements"

> "Search for how to add dark mode to a CSS file, then apply it to styles.css"

> "Run the tests and fix any failures"

Every tool call Claude makes goes through `mcp_server.py` and is logged.

---

## Step 7 — Open the replay UI

Go to **http://127.0.0.1:8787**

- The left panel shows all sessions (one per conversation, grouped by 10-minute inactivity windows)
- The middle panel shows the trace — every step Claude took, in order
- The right panel shows the full input and output for the selected step

Use the filter tabs to focus:
- **Files** — only file reads, writes, and creates
- **Search** — only web searches
- **Bash** — only shell commands
- **Errors** — only steps that failed

---

## Session management

A new session is created automatically when there is more than **10 minutes** of inactivity between tool calls.

To force a new session immediately:

```bash
# macOS / Linux
rm ~/.agent_replay_mcp_state.json

# Windows (PowerShell)
Remove-Item "$env:USERPROFILE\.agent_replay_mcp_state.json"
```

---

## What you can and cannot see

| | Visible |
|---|---|
| Which files Claude read | ✅ |
| Which files Claude wrote or created | ✅ |
| Web search queries and results | ✅ |
| Shell commands and their output | ✅ |
| Timing for each step | ✅ |
| LLM token count / cost | ❌ (not exposed by Claude Desktop MCP) |
| Claude's internal reasoning | ❌ (stays on Anthropic's servers) |
| Tool calls via Claude Desktop's built-in native tools | ❌ (only calls through this MCP server are captured) |

---

## Other ways to record

### Record a Claude Code CLI session

Wraps the terminal and logs everything visible:

```bash
python3 record_cli.py -- claude
```

### Record claude.ai or ChatGPT in the browser

1. Open `chrome://extensions` in Chrome or Edge
2. Enable **Developer mode**
3. Click **Load unpacked** → select the `browser-extension/` folder
4. Make sure `python3 collector.py` is running
5. Open claude.ai or chatgpt.com and chat normally
6. Open http://127.0.0.1:8787 to see the captured messages

### Instrument your own Python agent

```python
from agentreplay import ReplayClient

replay = ReplayClient()

with replay.session("My task", input={"user": "dev-42"}):
    src  = replay.read_text("src/app.py")
    hits = replay.web_search("how to fix X", results=results)
    replay.file_write("src/app.py", fixed)
    replay.bash(["pytest", "tests/"])
    replay.file_create("tests/test_regression.py", test_code)
```

---

## Files

| File | What it does |
|---|---|
| `mcp_server.py` | MCP server — connects to Claude Desktop |
| `collector.py` | Local HTTP server + SQLite store + UI server |
| `index.html` | Replay UI (served by collector.py) |
| `agentreplay.py` | Python SDK for direct instrumentation |
| `record_cli.py` | Terminal session recorder |
| `example_agent.py` | Demo: simulates a coding session |
| `browser-extension/` | Chrome/Edge extension for claude.ai / ChatGPT |
| `sample_project/` | Sample files used by the demo |
