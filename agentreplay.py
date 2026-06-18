"""Tiny Python SDK for the Agent Replay MVP.

The intended MVP integration is:

    from agentreplay import init
    replay = init()
    client = replay.wrap_client(OpenAI())

    with replay.session("Support agent", input={"user_id": "u_123"}):
        client.chat.completions.create(model="gpt-4.1-mini", messages=[...])

No external dependency is required by this SDK. If OpenAI or Anthropic clients are
installed in the host app, pass their client object to wrap_client().
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import json
import os
import subprocess
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib import request

_current_session = contextvars.ContextVar("agentreplay_session", default=None)
_current_parent = contextvars.ContextVar("agentreplay_parent", default=None)
_session_start = contextvars.ContextVar("agentreplay_start", default=0.0)
_default_client: "ReplayClient | None" = None


def init(endpoint: str | None = None, enabled: bool | None = None) -> "ReplayClient":
    """Create and remember the default replay client."""
    global _default_client
    _default_client = ReplayClient(endpoint=endpoint, enabled=enabled)
    return _default_client


def get_client() -> "ReplayClient":
    global _default_client
    if _default_client is None:
        _default_client = ReplayClient()
    return _default_client


def _safe_json(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _safe_json(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_safe_json(item) for item in value]
        return repr(value)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _usage_from_response(response: Any) -> tuple[int | None, float | None]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return None, None
    total = None
    for name in ("total_tokens", "input_tokens", "output_tokens"):
        value = getattr(usage, name, None) if not isinstance(usage, dict) else usage.get(name)
        if value is not None:
            if name == "total_tokens":
                total = int(value)
                break
            total = (total or 0) + int(value)
    return total, None


def _response_preview(response: Any) -> Any:
    if isinstance(response, (dict, list, str, int, float, bool)) or response is None:
        return response
    if hasattr(response, "model_dump"):
        try:
            return response.model_dump()
        except Exception:
            pass
    if hasattr(response, "dict"):
        try:
            return response.dict()
        except Exception:
            pass
    return repr(response)


@dataclass
class Span:
    client: "ReplayClient"
    type: str
    title: str
    subtitle: str = ""
    input: Any = field(default_factory=dict)
    output: Any = field(default_factory=dict)
    tokens: int | None = None
    cost: float | None = None
    status: str = "ok"
    id: str = field(default_factory=lambda: f"span_{uuid.uuid4().hex}")
    parent_id: str | None = None
    start_wall_ms: int = 0
    start_offset_ms: int = 0
    _parent_token: Any = None

    def __enter__(self):
        self.parent_id = _current_parent.get()
        self.start_wall_ms = _now_ms()
        session_start = _session_start.get() or time.perf_counter()
        self.start_offset_ms = int((time.perf_counter() - session_start) * 1000)
        self._parent_token = _current_parent.set(self.id)
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self.status = "failed"
            self.output = {
                "error": exc.__class__.__name__,
                "message": str(exc),
                "traceback": "".join(traceback.format_exception(exc_type, exc, tb))[-4000:],
            }
        duration_ms = max(0, _now_ms() - self.start_wall_ms)
        _current_parent.reset(self._parent_token)
        self.client.emit_span(
            {
                "id": self.id,
                "session_id": _current_session.get(),
                "parent_id": self.parent_id,
                "type": self.type,
                "title": self.title,
                "subtitle": self.subtitle,
                "status": self.status,
                "start_ms": self.start_offset_ms,
                "duration_ms": duration_ms,
                "tokens": self.tokens,
                "cost": self.cost,
                "input": _safe_json(self.input),
                "output": _safe_json(self.output),
                "created_at": self.start_wall_ms,
            }
        )
        return False


class ReplayClient:
    def __init__(self, endpoint: str | None = None, enabled: bool | None = None):
        self.endpoint = endpoint or os.getenv("AGENT_REPLAY_URL", "http://127.0.0.1:8787/api/spans")
        self.enabled = enabled if enabled is not None else os.getenv("AGENT_REPLAY_DISABLED") != "1"
        self._wrapped: set[tuple[int, str]] = set()

    def session(self, title: str, input: Any | None = None, session_id: str | None = None, user_id: str | None = None):
        return Session(self, title=title, input=input or {}, session_id=session_id, user_id=user_id)

    def span(self, type: str, title: str, input: Any | None = None, subtitle: str = "") -> Span:
        return Span(self, type=type, title=title, subtitle=subtitle, input=input or {})

    def traced_tool(self, title: str | None = None, type: str = "tool"):
        def decorate(fn: Callable):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                span_title = title or f"{fn.__module__}.{fn.__name__}"
                with self.span(type, span_title, input={"args": repr(args), "kwargs": kwargs}) as span:
                    result = fn(*args, **kwargs)
                    span.output = result
                    return result
            return wrapper
        return decorate

    def wrap_client(self, client: Any) -> Any:
        """Wrap common OpenAI/Anthropic client resources in place and return client.

        Supported shapes:
        - OpenAI: client.chat.completions.create(...)
        - OpenAI Responses: client.responses.create(...)
        - Anthropic: client.messages.create(...)
        """
        self._wrap_path(client, ["chat", "completions"], "create", "openai.chat.completions.create")
        self._wrap_path(client, ["responses"], "create", "openai.responses.create")
        self._wrap_path(client, ["messages"], "create", "anthropic.messages.create")
        return client

    def _wrap_path(self, client: Any, path: list[str], method_name: str, title: str):
        target = client
        for part in path:
            target = getattr(target, part, None)
            if target is None:
                return
        original = getattr(target, method_name, None)
        if original is None:
            return
        key = (id(target), method_name)
        if key in self._wrapped or getattr(original, "_agentreplay_wrapped", False):
            return

        if inspect.iscoroutinefunction(original):
            @functools.wraps(original)
            async def async_wrapper(*args, **kwargs):
                with self.span("llm", title, input={"args": repr(args), "kwargs": kwargs}) as span:
                    response = await original(*args, **kwargs)
                    tokens, cost = _usage_from_response(response)
                    span.tokens = tokens
                    span.cost = cost
                    span.output = _response_preview(response)
                    return response
            async_wrapper._agentreplay_wrapped = True
            setattr(target, method_name, async_wrapper)
        else:
            @functools.wraps(original)
            def wrapper(*args, **kwargs):
                with self.span("llm", title, input={"args": repr(args), "kwargs": kwargs}) as span:
                    response = original(*args, **kwargs)
                    tokens, cost = _usage_from_response(response)
                    span.tokens = tokens
                    span.cost = cost
                    span.output = _response_preview(response)
                    return response
            wrapper._agentreplay_wrapped = True
            setattr(target, method_name, wrapper)
        self._wrapped.add(key)

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        with self.span("file_read", "file.read", subtitle=path, input={"path": path}) as span:
            with open(path, "r", encoding=encoding) as f:
                data = f.read()
            span.output = {"bytes": len(data.encode(encoding)), "lines": data.count("\n") + 1, "preview": data[:500]}
            return data

    def file_write(self, path: str, content: str, encoding: str = "utf-8"):
        with self.span("file_write", "file.write", subtitle=path, input={"path": path}) as span:
            with open(path, "w", encoding=encoding) as f:
                f.write(content)
            span.output = {"bytes": len(content.encode(encoding)), "lines": content.count("\n") + 1, "preview": content[:500]}

    def file_create(self, path: str, content: str, encoding: str = "utf-8"):
        import os as _os
        if _os.path.exists(path):
            raise FileExistsError(f"{path} already exists")
        with self.span("file_create", "file.create", subtitle=path, input={"path": path}) as span:
            with open(path, "w", encoding=encoding) as f:
                f.write(content)
            span.output = {"bytes": len(content.encode(encoding)), "lines": content.count("\n") + 1, "preview": content[:500]}

    def web_search(self, query: str, results: list | None = None) -> list:
        with self.span("web_search", "web.search", subtitle=query[:120], input={"query": query}) as span:
            output = results or []
            span.output = {"query": query, "results": output, "count": len(output)}
            return output

    def bash(self, cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
        with self.span("bash", "bash.command", input={"cmd": cmd, "timeout": timeout}) as span:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            span.status = "ok" if result.returncode == 0 else "failed"
            span.output = {
                "returncode": result.returncode,
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-4000:],
            }
            return result

    def emit_span(self, span: dict):
        if not self.enabled or not span.get("session_id"):
            return
        body = json.dumps({"span": span}, default=str).encode("utf-8")
        req = request.Request(self.endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            request.urlopen(req, timeout=3).read()
        except Exception:
            if os.getenv("AGENT_REPLAY_DEBUG") == "1":
                raise


class Session:
    def __init__(self, client: ReplayClient, title: str, input: Any, session_id: str | None, user_id: str | None):
        self.client = client
        self.title = title
        self.input = dict(input or {}) if isinstance(input, dict) else {"value": input}
        if user_id is not None:
            self.input.setdefault("user_id", user_id)
        self.session_id = session_id or f"sess_{uuid.uuid4().hex[:12]}"
        self.root = Span(client, "agent", title, input=self.input)
        self._session_token = None
        self._start_token = None

    def __enter__(self):
        self._session_token = _current_session.set(self.session_id)
        self._start_token = _session_start.set(time.perf_counter())
        self.root.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.root.__exit__(exc_type, exc, tb)
        _current_session.reset(self._session_token)
        _session_start.reset(self._start_token)
        return False
