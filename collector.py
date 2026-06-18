#!/usr/bin/env python3
"""Dependency-free collector and UI server for Agent Replay MVP."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
import uuid
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "agent_replay.sqlite3"


def now_ms() -> int:
    return int(time.time() * 1000)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS spans (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            parent_id TEXT,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            subtitle TEXT,
            status TEXT NOT NULL,
            start_ms INTEGER NOT NULL,
            duration_ms INTEGER NOT NULL,
            tokens INTEGER,
            cost REAL,
            input_json TEXT NOT NULL,
            output_json TEXT NOT NULL,
            error TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_session ON spans(session_id, start_ms)")
    return conn


def row_to_span(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "parentId": row["parent_id"],
        "type": row["type"],
        "title": row["title"],
        "subtitle": row["subtitle"] or "",
        "status": row["status"],
        "startMs": row["start_ms"],
        "durationMs": row["duration_ms"],
        "tokens": row["tokens"],
        "cost": row["cost"],
        "input": json.loads(row["input_json"]),
        "output": json.loads(row["output_json"]),
        "error": row["error"],
    }


def ordered_spans(rows: list[sqlite3.Row]) -> list[dict]:
    spans = [row_to_span(row) for row in rows]
    by_parent: dict[str | None, list[dict]] = {}
    for span in spans:
        by_parent.setdefault(span["parentId"], []).append(span)
    for siblings in by_parent.values():
        siblings.sort(key=lambda item: (item["startMs"], item["title"]))

    ordered: list[dict] = []

    def visit(parent_id: str | None):
        for span in by_parent.get(parent_id, []):
            ordered.append(span)
            visit(span["id"])

    visit(None)
    return ordered or spans


def session_summary(session_id: str, rows: list[sqlite3.Row]) -> dict:
    spans = ordered_spans(rows)
    root = next((span for span in spans if span["parentId"] is None), spans[0])
    status_rank = {"failed": 3, "warn": 2, "ok": 1}
    status = max((span["status"] for span in spans), key=lambda item: status_rank.get(item, 0))
    started = min(row["created_at"] for row in rows)
    ended = max(row["created_at"] + row["duration_ms"] for row in rows)
    return {
        "id": session_id,
        "title": root["title"],
        "user": root["input"].get("user_id") or root["input"].get("user") or "local-run",
        "startedAt": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started / 1000)),
        "status": status,
        "durationMs": max(root["durationMs"], ended - started),
        "cost": round(sum((span.get("cost") or 0) for span in spans), 6),
        "tokens": sum((span.get("tokens") or 0) for span in spans),
        "spanCount": len(spans),
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def send_json(self, status: int, payload: dict | list):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/spans":
            self.send_error(404)
            return
        payload = self.read_json()
        span = payload.get("span", payload)
        span_id = span.get("id") or f"span_{uuid.uuid4().hex}"
        session_id = span.get("session_id") or span.get("sessionId")
        if not session_id:
            self.send_json(400, {"error": "session_id is required"})
            return
        with connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO spans
                (id, session_id, parent_id, type, title, subtitle, status, start_ms, duration_ms,
                 tokens, cost, input_json, output_json, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    span_id,
                    session_id,
                    span.get("parent_id") or span.get("parentId"),
                    span.get("type", "custom"),
                    span.get("title", "Untitled span"),
                    span.get("subtitle", ""),
                    span.get("status", "ok"),
                    int(span.get("start_ms", span.get("startMs", 0))),
                    int(span.get("duration_ms", span.get("durationMs", 0))),
                    span.get("tokens"),
                    span.get("cost"),
                    json.dumps(span.get("input", {}), default=str),
                    json.dumps(span.get("output", {}), default=str),
                    span.get("error"),
                    int(span.get("created_at", now_ms())),
                ),
            )
        self.send_json(201, {"ok": True, "span_id": span_id, "session_id": session_id})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            self.send_json(200, {"ok": True})
            return
        if path == "/api/sessions":
            with connect() as conn:
                rows = conn.execute("SELECT * FROM spans ORDER BY created_at DESC").fetchall()
            grouped: dict[str, list[sqlite3.Row]] = {}
            for row in rows:
                grouped.setdefault(row["session_id"], []).append(row)
            sessions = [session_summary(sid, sorted(items, key=lambda r: r["start_ms"])) for sid, items in grouped.items()]
            sessions.sort(key=lambda item: item["startedAt"], reverse=True)
            self.send_json(200, sessions)
            return
        if path.startswith("/api/sessions/"):
            session_id = path.rsplit("/", 1)[-1]
            with connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM spans WHERE session_id = ? ORDER BY start_ms ASC",
                    (session_id,),
                ).fetchall()
            if not rows:
                self.send_json(404, {"error": "session not found"})
                return
            summary = session_summary(session_id, rows)
            summary["spans"] = ordered_spans(rows)
            self.send_json(200, summary)
            return
        return super().do_GET()


def main():
    parser = argparse.ArgumentParser(description="Run the Agent Replay local MVP collector")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    connect().close()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Agent Replay running at http://{args.host}:{args.port}")
    print(f"SQLite database: {DB_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
