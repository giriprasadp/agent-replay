#!/usr/bin/env python3
"""Record an existing terminal AI tool session into Agent Replay.

Examples:
    python3 record_cli.py -- claude
    python3 record_cli.py -- codex
    python3 record_cli.py -- bash

This runs the command inside a pseudo-terminal and logs the visible terminal
transcript to the local collector. It can record Claude Code/Codex-style CLI
sessions without modifying those tools.
"""

from __future__ import annotations

import argparse
import json
import os
import pty
import select
import re
import subprocess
import sys
import termios
import time
import tty
import uuid
from urllib import request


def now_ms() -> int:
    return int(time.time() * 1000)


ANSI_RE = re.compile(
    r"""
    \x1B\[[0-?]*[ -/]*[@-~] |
    \x1B\][^\x07]*(?:\x07|\x1B\\) |
    \x1B[P^_].*?\x1B\\ |
    \x1B.
    """,
    re.VERBOSE | re.DOTALL,
)


def normalize_terminal_text(value: str) -> str:
    """Turn a noisy terminal stream into readable text."""
    if not value:
        return ""

    text = ANSI_RE.sub("", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines: list[str] = []
    current = []
    for ch in text:
        if ch == "\b":
            if current:
                current.pop()
            continue
        if ch == "\x00":
            continue
        if ch == "\n":
            lines.append("".join(current))
            current = []
            continue
        if ch.isprintable() or ch in "\t ":
            current.append(ch)
    lines.append("".join(current))

    cleaned = "\n".join(line.rstrip() for line in lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


class Collector:
    def __init__(self, base_url: str, session_id: str, title: str):
        self.base_url = base_url.rstrip('/')
        self.session_id = session_id
        self.root_id = session_id + '_root'
        self.started_at = now_ms()
        self.sequence = 0
        self.post_span({
            'id': self.root_id,
            'session_id': session_id,
            'parent_id': None,
            'type': 'agent',
            'title': title,
            'subtitle': 'Recorded external CLI session',
            'status': 'ok',
            'start_ms': 0,
            'duration_ms': 0,
            'input': {'source': 'record_cli.py', 'command': title},
            'output': {},
            'created_at': self.started_at,
        })

    def post_span(self, span: dict):
        body = json.dumps({'span': span}, default=str).encode('utf-8')
        req = request.Request(self.base_url + '/api/spans', data=body, headers={'Content-Type': 'application/json'}, method='POST')
        try:
            request.urlopen(req, timeout=2).read()
        except Exception:
            pass

    def terminal_event(self, direction: str, content: str):
        content = normalize_terminal_text(content)
        if not content:
            return
        self.sequence += 1
        created_at = now_ms()
        self.post_span({
            'id': f'term_{uuid.uuid4().hex}',
            'session_id': self.session_id,
            'parent_id': self.root_id,
            'type': 'terminal',
            'title': f'terminal.{direction}',
            'subtitle': content.replace('\n', ' ')[:120],
            'status': 'ok',
            'start_ms': created_at - self.started_at,
            'duration_ms': 0,
            'input': {'direction': direction, 'content': content, 'sequence': self.sequence},
            'output': {},
            'created_at': created_at,
        })


def main():
    parser = argparse.ArgumentParser(description='Record an existing CLI AI tool session')
    parser.add_argument('--collector', default='http://127.0.0.1:8787')
    parser.add_argument('--session-id', default=None)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        raise SystemExit('Usage: python3 record_cli.py -- claude')

    session_id = args.session_id or 'cli_' + uuid.uuid4().hex[:12]
    recorder = Collector(args.collector, session_id, 'CLI: ' + ' '.join(command))
    print(f'\n[agent-replay] Recording session {session_id}')
    print(f'[agent-replay] Open {args.collector}/#{session_id}\n')

    if not sys.stdin.isatty():
        result = subprocess.run(command, capture_output=True, text=True)
        if result.stdout:
            recorder.terminal_event('output', result.stdout)
        if result.stderr:
            recorder.terminal_event('output', result.stderr)
        recorder.terminal_event('exit', f'process exited with code {result.returncode}')
        raise SystemExit(result.returncode or 0)

    master_fd, slave_fd = pty.openpty()
    old_stdin = termios.tcgetattr(sys.stdin.fileno())
    process = subprocess.Popen(command, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True)
    os.close(slave_fd)

    try:
        tty.setraw(sys.stdin.fileno())
        output_buffer = ''
        input_buffer = ''
        last_flush = time.time()
        while process.poll() is None:
            readable, _, _ = select.select([sys.stdin, master_fd], [], [], 0.1)
            for stream in readable:
                if stream is sys.stdin:
                    data = os.read(sys.stdin.fileno(), 4096)
                    if data:
                        os.write(master_fd, data)
                        text = data.decode(errors='replace')
                        input_buffer += text
                        if '\n' in input_buffer or '\r' in input_buffer:
                            recorder.terminal_event('input', input_buffer)
                            input_buffer = ''
                else:
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError:
                        data = b''
                    if data:
                        os.write(sys.stdout.fileno(), data)
                        output_buffer += data.decode(errors='replace')
            if output_buffer and (time.time() - last_flush > 0.75 or len(output_buffer) > 2000):
                recorder.terminal_event('output', output_buffer)
                output_buffer = ''
                last_flush = time.time()
        if input_buffer:
            recorder.terminal_event('input', input_buffer)
        if output_buffer:
            recorder.terminal_event('output', output_buffer)
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_stdin)
        try:
            os.close(master_fd)
        except OSError:
            pass

    recorder.terminal_event('exit', f'process exited with code {process.returncode}')
    raise SystemExit(process.returncode or 0)


if __name__ == '__main__':
    main()
