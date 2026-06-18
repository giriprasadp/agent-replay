#!/usr/bin/env python3
"""MCP server for Agent Replay — Claude Desktop integration.

Exposes 5 tools to Claude Desktop. Every tool call is logged as a span to the
local Agent Replay collector (http://127.0.0.1:8787) so you can replay what
Claude did in the UI.

Add to ~/Library/Application Support/Claude/claude_desktop_config.json (macOS)
or %APPDATA%\\Claude\\claude_desktop_config.json (Windows):

  {
    "mcpServers": {
      "agent-replay": {
        "command": "python3",
        "args": ["/FULL/PATH/TO/mcp_server.py"]
      }
    }
  }

Then restart Claude Desktop. Claude will use these tools for file and search
operations, and every call will appear in http://127.0.0.1:8787.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib import request, parse

# ── collector / span emission ─────────────────────────────────────────────────

COLLECTOR = os.getenv("AGENT_REPLAY_URL", "http://127.0.0.1:8787/api/spans")
STATE_FILE = Path.home() / ".agent_replay_mcp_state.json"
SESSION_TIMEOUT_MS = 10 * 60 * 1000  # 10 minutes


def _now_ms() -> int:
    return int(time.time() * 1000)


def _emit(span: dict):
    try:
        body = json.dumps({"span": span}, default=str).encode()
        req = request.Request(COLLECTOR, data=body, headers={"Content-Type": "application/json"}, method="POST")
        request.urlopen(req, timeout=3).read()
    except Exception:
        pass


# ── session management ────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state))
    except Exception:
        pass


def _get_or_create_session() -> tuple[str, str]:
    """Return (session_id, root_span_id), creating a new session if needed."""
    state = _load_state()
    now = _now_ms()
    if state and (now - state.get("last_call_ms", 0)) < SESSION_TIMEOUT_MS:
        state["last_call_ms"] = now
        _save_state(state)
        return state["session_id"], state["root_span_id"]

    session_id = "mcp_" + uuid.uuid4().hex[:12]
    root_id = session_id + "_root"
    _emit({
        "id": root_id,
        "session_id": session_id,
        "parent_id": None,
        "type": "agent",
        "title": "Claude Desktop session",
        "subtitle": "Recorded via MCP server",
        "status": "ok",
        "start_ms": 0,
        "duration_ms": 0,
        "input": {"source": "mcp_server.py"},
        "output": {},
        "created_at": now,
    })
    new_state = {"session_id": session_id, "root_span_id": root_id, "last_call_ms": now}
    _save_state(new_state)
    return session_id, root_id


def _emit_span(type_: str, title: str, input_: dict, output: dict, status: str = "ok", start_ms: int = 0, duration_ms: int = 0):
    session_id, root_id = _get_or_create_session()
    _emit({
        "id": "span_" + uuid.uuid4().hex,
        "session_id": session_id,
        "parent_id": root_id,
        "type": type_,
        "title": title,
        "subtitle": _subtitle(input_),
        "status": status,
        "start_ms": start_ms,
        "duration_ms": duration_ms,
        "tokens": None,
        "cost": None,
        "input": input_,
        "output": output,
        "created_at": _now_ms(),
    })


def _subtitle(input_: dict) -> str:
    return (input_.get("path") or input_.get("query") or input_.get("command") or "")[:120]


# ── tool implementations ──────────────────────────────────────────────────────

def tool_read_file(path: str) -> str:
    t0 = _now_ms()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        dur = _now_ms() - t0
        _emit_span("file_read", "file.read", {"path": path}, {
            "bytes": len(content.encode()),
            "lines": content.count("\n") + 1,
            "preview": content[:500],
        }, duration_ms=dur, start_ms=t0)
        return content
    except Exception as e:
        _emit_span("file_read", "file.read", {"path": path}, {"error": str(e)}, status="failed", duration_ms=_now_ms() - t0, start_ms=t0)
        raise


def tool_write_file(path: str, content: str) -> str:
    t0 = _now_ms()
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        dur = _now_ms() - t0
        _emit_span("file_write", "file.write", {"path": path}, {
            "bytes": len(content.encode()),
            "lines": content.count("\n") + 1,
        }, duration_ms=dur, start_ms=t0)
        return f"Written {len(content.encode())} bytes to {path}"
    except Exception as e:
        _emit_span("file_write", "file.write", {"path": path}, {"error": str(e)}, status="failed", duration_ms=_now_ms() - t0, start_ms=t0)
        raise


def tool_create_file(path: str, content: str) -> str:
    t0 = _now_ms()
    if Path(path).exists():
        raise FileExistsError(f"{path} already exists — use write_file to overwrite")
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        dur = _now_ms() - t0
        _emit_span("file_create", "file.create", {"path": path}, {
            "bytes": len(content.encode()),
            "lines": content.count("\n") + 1,
        }, duration_ms=dur, start_ms=t0)
        return f"Created {path} ({len(content.encode())} bytes)"
    except Exception as e:
        _emit_span("file_create", "file.create", {"path": path}, {"error": str(e)}, status="failed", duration_ms=_now_ms() - t0, start_ms=t0)
        raise


def tool_bash(command: str) -> str:
    t0 = _now_ms()
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60)
        dur = _now_ms() - t0
        status = "ok" if result.returncode == 0 else "failed"
        _emit_span("bash", "bash.run", {"command": command}, {
            "returncode": result.returncode,
            "stdout": result.stdout[-3000:],
            "stderr": result.stderr[-1000:],
        }, status=status, duration_ms=dur, start_ms=t0)
        out = result.stdout + (("\n" + result.stderr) if result.stderr else "")
        return out.strip() or f"(exit {result.returncode})"
    except subprocess.TimeoutExpired:
        _emit_span("bash", "bash.run", {"command": command}, {"error": "timeout"}, status="failed", duration_ms=_now_ms() - t0, start_ms=t0)
        raise


def tool_web_search(query: str) -> str:
    t0 = _now_ms()
    try:
        url = "https://api.duckduckgo.com/?q=" + parse.quote_plus(query) + "&format=json&no_html=1&skip_disambig=1"
        req = request.Request(url, headers={"User-Agent": "agent-replay/0.1"})
        raw = request.urlopen(req, timeout=8).read()
        data = json.loads(raw)
        results = []
        if data.get("AbstractText"):
            results.append({"title": data.get("Heading", query), "url": data.get("AbstractURL", ""), "snippet": data["AbstractText"][:300]})
        for topic in data.get("RelatedTopics", [])[:4]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({"title": topic.get("Text", "")[:80], "url": topic.get("FirstURL", ""), "snippet": topic.get("Text", "")[:200]})
        if not results:
            results.append({"title": "No instant results", "url": "", "snippet": "Try a more specific query or check manually."})
        dur = _now_ms() - t0
        _emit_span("web_search", "web.search", {"query": query}, {
            "query": query, "results": results, "count": len(results),
        }, duration_ms=dur, start_ms=t0)
        return json.dumps(results, indent=2)
    except Exception as e:
        _emit_span("web_search", "web.search", {"query": query}, {"error": str(e)}, status="failed", duration_ms=_now_ms() - t0, start_ms=t0)
        raise


# ── MCP tool definitions ──────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "read_file",
        "description": "Read the full text content of a file. Logs a file_read span to Agent Replay.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute or relative path to the file"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Overwrite a file with new content. Logs a file_write span to Agent Replay.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file"},
                "content": {"type": "string", "description": "New file content"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "create_file",
        "description": "Create a new file. Fails if the file already exists. Logs a file_create span to Agent Replay.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path for the new file"},
                "content": {"type": "string", "description": "Initial file content"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "bash",
        "description": "Run a shell command and return stdout + stderr. Logs a bash span to Agent Replay.",
        "inputSchema": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "Shell command to run"}},
            "required": ["command"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web using DuckDuckGo Instant Answers. Logs a web_search span to Agent Replay.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    },
]


# ── JSON-RPC 2.0 dispatch ─────────────────────────────────────────────────────

def _ok(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(id_, code, msg):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": msg}}


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


def handle(req: dict) -> dict | None:
    method = req.get("method", "")
    id_ = req.get("id")
    params = req.get("params", {})

    if method == "initialize":
        return _ok(id_, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "agent-replay", "version": "0.1.0"},
        })

    if method == "notifications/initialized":
        return None  # no response for notifications

    if method == "tools/list":
        return _ok(id_, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        try:
            if name == "read_file":
                return _ok(id_, _text(tool_read_file(args["path"])))
            if name == "write_file":
                return _ok(id_, _text(tool_write_file(args["path"], args["content"])))
            if name == "create_file":
                return _ok(id_, _text(tool_create_file(args["path"], args["content"])))
            if name == "bash":
                return _ok(id_, _text(tool_bash(args["command"])))
            if name == "web_search":
                return _ok(id_, _text(tool_web_search(args["query"])))
            return _err(id_, -32601, f"Unknown tool: {name}")
        except Exception as e:
            return _ok(id_, {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})

    # Unknown method — return method not found for requests (has id), ignore notifications
    if id_ is not None:
        return _err(id_, -32601, f"Method not found: {method}")
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            resp = _err(None, -32700, "Parse error")
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
