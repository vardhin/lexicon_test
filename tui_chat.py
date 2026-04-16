#!/usr/bin/env python3
from __future__ import annotations

import itertools
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

# ----- ANSI escapes -----
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
ITAL = "\x1b[3m"
UNDL = "\x1b[4m"
BLINK = "\x1b[5m"
INV = "\x1b[7m"

# foreground
FG = {
    "black": "\x1b[30m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue": "\x1b[34m",
    "magenta": "\x1b[35m",
    "cyan": "\x1b[36m",
    "white": "\x1b[37m",
    "grey": "\x1b[90m",
    "br_red": "\x1b[91m",
    "br_green": "\x1b[92m",
    "br_yellow": "\x1b[93m",
    "br_blue": "\x1b[94m",
    "br_magenta": "\x1b[95m",
    "br_cyan": "\x1b[96m",
}

# terminal control
CLEAR_LINE = "\x1b[2K"
CR = "\r"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"


COLOR = _supports_color()


def c(code: str) -> str:
    return code if COLOR else ""


def _write(s: str) -> None:
    sys.stdout.write(s)
    sys.stdout.flush()


# ---------- animated spinner / "thinking" indicator ----------

class Spinner:
    """
    Inline animated indicator printed on its own line. Cycles glyphs and colors.
    Call stop() before printing normal text; stop() wipes the line so output is clean.
    """

    BRAILLE = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    PULSE_COLORS = ["br_green", "green", "cyan", "br_cyan", "cyan", "green"]

    def __init__(self, label: str = "thinking"):
        self.label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not COLOR:
            _write(f"{self.label}…\n")
            return
        _write(HIDE_CURSOR)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        glyphs = itertools.cycle(self.BRAILLE)
        colors = itertools.cycle(self.PULSE_COLORS)
        dots_cycle = itertools.cycle(["", ".", "..", "..."])
        tick = 0
        while not self._stop.is_set():
            g = next(glyphs)
            col = FG[next(colors)]
            dots = next(dots_cycle) if tick % 3 == 0 else ""
            line = f"{CR}{CLEAR_LINE}{col}{g}{RESET} {DIM}{self.label}{dots}{RESET}"
            _write(line)
            tick += 1
            if self._stop.wait(0.08):
                break

    def stop(self) -> None:
        if self._thread is None and not COLOR:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if COLOR:
            # wipe the spinner line and restore cursor
            _write(f"{CR}{CLEAR_LINE}{SHOW_CURSOR}")


# ---------- typewriter effect for special lines ----------

def _typewriter(text: str, delay: float = 0.012, style: str = "") -> None:
    """Print text char-by-char with a small delay and optional ANSI style."""
    if not COLOR or delay <= 0:
        _write(f"{style}{text}{RESET}\n")
        return
    _write(style)
    for ch in text:
        _write(ch)
        time.sleep(delay)
    _write(f"{RESET}\n")


# ---------- banner ----------

def _print_banner(session_id: str) -> None:
    bar = "─" * 3
    _write("\n")
    _write(
        f"  {c(BOLD)}{c(FG['br_magenta'])}✦ rhea{c(RESET)}"
        f"  {c(DIM)}{bar}{c(RESET)}  "
        f"{c(DIM)}session{c(RESET)} {c(FG['cyan'])}{session_id}{c(RESET)}  "
        f"{c(DIM)}depth {MAX_DEPTH} · /quit to exit · Ctrl+C cancels{c(RESET)}\n\n"
    )


# ---------- role labels & special lines ----------

def _print_user_echo(text: str) -> None:
    _write(f"{c(BOLD)}{c(FG['br_cyan'])}❯ you{c(RESET)} {c(FG['cyan'])}{text}{c(RESET)}\n")


def _print_rhea_label() -> None:
    # a sparkle + name on its own line, so the reply starts cleanly below
    _write(f"\n{c(BOLD)}{c(FG['br_green'])}✧ rhea{c(RESET)}\n")


def _print_think_line(text: str) -> None:
    """
    Render a 'THINK: ...' line with italic grey + a lightbulb glyph and typewriter fade.
    Strips the THINK: prefix if present.
    """
    body = text
    for prefix in ("THINK:", "Think:", "think:"):
        if body.startswith(prefix):
            body = body[len(prefix):].strip()
            break
    label = f"{c(DIM)}{c(FG['br_yellow'])}◆ think{c(RESET)} "
    _write(label)
    _typewriter(body, delay=0.006, style=f"{c(DIM)}{c(ITAL)}{c(FG['grey'])}")


def _print_tool_call(name: str, args) -> None:
    args_str = str(args)
    if len(args_str) > 120:
        args_str = args_str[:120] + "…"
    _write(
        f"\n{c(BOLD)}{c(FG['br_yellow'])}▸ tool{c(RESET)} "
        f"{c(FG['yellow'])}{name}{c(RESET)}"
        f"{c(DIM)} {args_str}{c(RESET)}\n"
    )


def _print_tool_result(result: str) -> None:
    snippet = result if len(result) <= 600 else result[:600] + "…"
    for line in snippet.splitlines() or [""]:
        _write(f"  {c(DIM)}{c(FG['grey'])}│{c(RESET)} {c(DIM)}{line}{c(RESET)}\n")


def _print_error(msg: str) -> None:
    _write(f"\n{c(BOLD)}{c(FG['br_red'])}✗ error{c(RESET)} {c(FG['red'])}{msg}{c(RESET)}\n")


# ---------- streaming ----------

class TokenBuffer:
    """
    Accepts streamed tokens and writes them to stdout with two behaviors:
    - Strips any leading whitespace/newlines from the very first token (model quirk).
    - Detects a leading 'THINK: ...' line and renders it with a typewriter style on
      its own line before switching to normal streaming.
    """

    def __init__(self) -> None:
        self.started = False
        self.pre_buffer = ""
        self.think_done = False

    def feed(self, tok: str) -> None:
        if not tok:
            return

        if not self.started:
            # collapse any leading whitespace / newlines on the first real content
            self.pre_buffer += tok
            stripped = self.pre_buffer.lstrip()
            if not stripped:
                return  # still all whitespace — wait
            self.pre_buffer = stripped
            self.started = True

            # detect a THINK: line before any real newline
            if not self.think_done:
                low = self.pre_buffer.lower()
                if low.startswith("think:"):
                    # wait until we have a full line (newline seen)
                    nl = self.pre_buffer.find("\n")
                    if nl == -1:
                        return
                    think_line = self.pre_buffer[:nl]
                    rest = self.pre_buffer[nl + 1:]
                    _print_think_line(think_line)
                    self.think_done = True
                    self.pre_buffer = ""
                    if rest:
                        _write(rest)
                    return
                self.think_done = True

            _write(self.pre_buffer)
            self.pre_buffer = ""
            return

        # after started: if we were still collecting a THINK line, keep collecting
        if self.pre_buffer:
            self.pre_buffer += tok
            nl = self.pre_buffer.find("\n")
            if nl == -1:
                return
            think_line = self.pre_buffer[:nl]
            rest = self.pre_buffer[nl + 1:]
            _print_think_line(think_line)
            self.think_done = True
            self.pre_buffer = ""
            if rest:
                _write(rest)
            return

        _write(tok)


def _stream_assistant(prompt: str, session_id: str) -> None:
    payload = json.dumps({"prompt": prompt, "session_id": session_id}).encode("utf-8")
    req = urllib.request.Request(
        CHAT_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    _print_rhea_label()

    spinner = Spinner("thinking")
    spinner.start()
    buf = TokenBuffer()
    spinner_stopped = False

    def kill_spinner() -> None:
        nonlocal spinner_stopped
        if not spinner_stopped:
            spinner.stop()
            spinner_stopped = True

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
                    kill_spinner()
                    buf.feed(str(packet.get("token", "")))
                    continue

                if "thought" in packet:
                    kill_spinner()
                    _print_think_line(str(packet.get("thought", "")))
                    continue

                if "tool_call" in packet:
                    kill_spinner()
                    _print_tool_call(str(packet.get("tool_call", "")), packet.get("args", {}))
                    # re-spin while tool runs
                    spinner = Spinner("running tool")
                    spinner.start()
                    spinner_stopped = False
                    continue

                if "tool_result" in packet:
                    kill_spinner()
                    _print_tool_result(str(packet.get("tool_result", "")))
                    # re-spin for the next LLM step
                    spinner = Spinner("thinking")
                    spinner.start()
                    spinner_stopped = False
                    buf = TokenBuffer()
                    continue

                if "error" in packet:
                    kill_spinner()
                    _print_error(str(packet.get("error", "unknown error")))
                    return

                if packet.get("done") is True:
                    break

    except KeyboardInterrupt:
        kill_spinner()
        _write(f"\n{c(DIM)}{c(FG['red'])}[cancelled]{c(RESET)}\n")
        return
    except urllib.error.HTTPError as exc:
        kill_spinner()
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
        kill_spinner()
        _print_error(f"connection failed: {exc.reason}")
        return
    except Exception as exc:
        kill_spinner()
        _print_error(f"request failed: {exc}")
        return
    finally:
        kill_spinner()

    _write("\n")


# ---------- input ----------

def _read_prompt() -> str | None:
    if COLOR:
        # readline needs \001..\002 around non-printing sequences
        prompt_marker = f"\001{BOLD}{FG['br_magenta']}\002❯\001{RESET}\002 "
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
        _print_user_echo(initial)
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
    finally:
        if COLOR:
            _write(SHOW_CURSOR)
