#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request

try:
    import readline  # noqa: F401  — enables arrow-key history & editing in input()
except ImportError:
    pass

CHAT_URL = os.environ.get("RHEA_CHAT_URL", "http://localhost:8000/chat")
MAX_DEPTH = 3

# ANSI escapes
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
BLINK = "\x1b[5m"
CYAN = "\x1b[36m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
MAGENTA = "\x1b[35m"
GREY = "\x1b[90m"


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"


COLOR = _supports_color()


def c(code: str) -> str:
    return code if COLOR else ""


def _write(s: str) -> None:
    sys.stdout.write(s)
    sys.stdout.flush()


class BlinkingCursor:
    """Prints a blinking glyph on stdout until stop() is called. Thread-safe one-shot."""

    def __init__(self, glyph: str = "▍"):
        self.glyph = glyph
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not COLOR:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        visible = True
        while not self._stop.is_set():
            if visible:
                _write(c(DIM) + c(GREEN) + self.glyph + c(RESET))
            else:
                _write("\b \b")
            visible = not visible
            if self._stop.wait(0.45):
                break
        # clean up last glyph if still drawn
        if visible:
            _write("\b \b")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None


def _print_banner(session_id: str) -> None:
    _write(
        f"\n{c(BOLD)}{c(MAGENTA)}rhea{c(RESET)} "
        f"{c(DIM)}session{c(RESET)} {c(CYAN)}{session_id}{c(RESET)} "
        f"{c(DIM)}depth {MAX_DEPTH} · /quit to exit · Ctrl+C to cancel a reply{c(RESET)}\n\n"
    )


def _print_tool_call(name: str, args) -> None:
    _write(f"\n{c(DIM)}{c(YELLOW)}• {name}{c(RESET)}{c(DIM)} {args}{c(RESET)}\n")


def _print_tool_result(result: str) -> None:
    snippet = result if len(result) <= 600 else result[:600] + "…"
    # indent each line with a dim bar
    for line in snippet.splitlines() or [""]:
        _write(f"{c(DIM)}│ {line}{c(RESET)}\n")


def _print_error(msg: str) -> None:
    _write(f"\n{c(BOLD)}{c(RED)}error{c(RESET)} {c(RED)}{msg}{c(RESET)}\n")


def _stream_assistant(prompt: str, session_id: str) -> None:
    payload = json.dumps({"prompt": prompt, "session_id": session_id}).encode("utf-8")
    req = urllib.request.Request(
        CHAT_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    _write(f"\n{c(BOLD)}{c(GREEN)}rhea{c(RESET)}\n")

    cursor = BlinkingCursor()
    cursor.start()
    first_token_seen = False

    def ensure_cursor_cleared() -> None:
        nonlocal first_token_seen
        if not first_token_seen:
            cursor.stop()
            first_token_seen = True

    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue

                try:
                    packet = json.loads(data)
                except json.JSONDecodeError:
                    continue

                if "token" in packet:
                    ensure_cursor_cleared()
                    _write(str(packet.get("token", "")))
                    continue

                if "tool_call" in packet:
                    ensure_cursor_cleared()
                    _print_tool_call(str(packet.get("tool_call", "")), packet.get("args", {}))
                    continue

                if "tool_result" in packet:
                    ensure_cursor_cleared()
                    _print_tool_result(str(packet.get("tool_result", "")))
                    continue

                if "error" in packet:
                    ensure_cursor_cleared()
                    _print_error(str(packet.get("error", "unknown error")))
                    return

                if packet.get("done") is True:
                    break

    except KeyboardInterrupt:
        ensure_cursor_cleared()
        _write(f"\n{c(DIM)}{c(RED)}[cancelled]{c(RESET)}\n")
        return
    except urllib.error.HTTPError as exc:
        ensure_cursor_cleared()
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        detail = f"HTTP {exc.code} {exc.reason}".strip()
        if body:
            detail = f"{detail}: {body}"
        _print_error(detail)
        return
    except urllib.error.URLError as exc:
        ensure_cursor_cleared()
        _print_error(f"connection failed: {exc.reason}")
        return
    except Exception as exc:
        ensure_cursor_cleared()
        _print_error(f"request failed: {exc}")
        return
    finally:
        cursor.stop()

    _write("\n")


def _read_prompt() -> str | None:
    # readline miscounts prompt width if ANSI escapes aren't bracketed with \001..\002
    if COLOR:
        prompt_marker = f"\001{BOLD}{MAGENTA}\002❯\001{RESET}\002 "
    else:
        prompt_marker = "> "
    try:
        return input(prompt_marker)
    except EOFError:
        return None


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Rhea chat REPL")
    parser.add_argument("prompt", nargs="*", help="Optional initial prompt to send immediately")
    args = parser.parse_args()

    session_id = os.environ.get("RHEA_SESSION_ID") or uuid.uuid4().hex[:12]
    _print_banner(session_id)

    initial = " ".join(args.prompt).strip()
    if initial:
        _write(f"{c(BOLD)}{c(CYAN)}you{c(RESET)} {c(CYAN)}{initial}{c(RESET)}\n")
        _stream_assistant(initial, session_id)

    while True:
        try:
            prompt = _read_prompt()
        except KeyboardInterrupt:
            _write("\n")
            continue

        if prompt is None:
            _write(f"{c(DIM)}bye.{c(RESET)}\n")
            return

        prompt = prompt.strip()
        if not prompt:
            continue
        if prompt in ("/quit", "/exit", ":q"):
            _write(f"{c(DIM)}bye.{c(RESET)}\n")
            return

        _stream_assistant(prompt, session_id)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _write(f"\n{c(DIM)}bye.{c(RESET)}\n")
        sys.exit(0)
