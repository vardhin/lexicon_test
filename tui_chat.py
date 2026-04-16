#!/usr/bin/env python3
from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
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
INV = "\x1b[7m"

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


def _term_width(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except Exception:
        return default


# ---------- URL detection + OSC 8 hyperlinks ----------

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def _hyperlink(url: str, label: str | None = None) -> str:
    if not COLOR:
        return label or url
    label = label or url
    return f"\x1b]8;;{url}\x1b\\{label}\x1b]8;;\x1b\\"


def _linkify(text: str) -> str:
    if not COLOR:
        return text

    def repl(m: re.Match) -> str:
        url = m.group(0)
        return f"{UNDL}{FG['br_blue']}{_hyperlink(url)}{RESET}"

    return URL_RE.sub(repl, text)


# ---------- markdown-lite inline rendering ----------

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")
_ITAL_RE = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)")


def _mdlite(text: str) -> str:
    if not COLOR:
        return text
    text = _CODE_RE.sub(lambda m: f"{FG['br_cyan']}{m.group(1)}{RESET}", text)
    text = _BOLD_RE.sub(lambda m: f"{BOLD}{m.group(1)}{RESET}", text)
    text = _ITAL_RE.sub(lambda m: f"{ITAL}{m.group(1)}{RESET}", text)
    return text


def _format_inline(text: str) -> str:
    return _linkify(_mdlite(text))


# ---------- clipboard ----------

def _copy_to_clipboard(text: str) -> tuple[bool, str]:
    candidates = []
    if os.environ.get("WAYLAND_DISPLAY"):
        candidates.append(["wl-copy"])
    if os.environ.get("DISPLAY"):
        candidates.append(["xclip", "-selection", "clipboard"])
        candidates.append(["xsel", "--clipboard", "--input"])
    # fallback (covers systems where only one is installed regardless of env)
    for bin_ in ("wl-copy", "xclip", "xsel"):
        if any(bin_ == c[0] for c in candidates):
            continue
        if shutil.which(bin_):
            if bin_ == "xclip":
                candidates.append(["xclip", "-selection", "clipboard"])
            elif bin_ == "xsel":
                candidates.append(["xsel", "--clipboard", "--input"])
            else:
                candidates.append([bin_])

    for cmd in candidates:
        if not shutil.which(cmd[0]):
            continue
        try:
            proc = subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=3)
            if proc.returncode == 0:
                return True, cmd[0]
        except Exception:
            continue
    return False, "no clipboard tool found (install wl-copy / xclip / xsel)"


# ---------- animated spinner with mutable label ----------

class Spinner:
    BRAILLE = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    PULSE_COLORS = ["br_green", "green", "cyan", "br_cyan", "cyan", "green"]

    def __init__(self, label: str = "thinking", prefix: str = "") -> None:
        self._label = label
        self._prefix = prefix  # rendered before the spinner glyph (e.g. the rhea label)
        self._start_time = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def set_label(self, label: str) -> None:
        with self._lock:
            self._label = label

    def elapsed(self) -> float:
        return time.monotonic() - self._start_time

    def start(self) -> None:
        if not COLOR:
            _write(f"{self._prefix}{self._label}…\n")
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
            with self._lock:
                label = self._label
            # truncate label to terminal width to avoid wrap jitter
            max_label = max(10, _term_width() - 20)
            if len(label) > max_label:
                label = label[: max_label - 1] + "…"
            line = f"{CR}{CLEAR_LINE}{self._prefix}{col}{g}{RESET} {DIM}{label}{dots}{RESET}"
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
            _write(f"{CR}{CLEAR_LINE}{SHOW_CURSOR}")


# ---------- typewriter for special lines ----------

def _typewriter(text: str, delay: float = 0.008, style: str = "") -> None:
    if not COLOR or delay <= 0:
        _write(f"{style}{text}{RESET}\n")
        return
    _write(style)
    for ch in text:
        _write(ch)
        time.sleep(delay)
    _write(f"{RESET}\n")


# ---------- banner + separators ----------

def _print_banner(session_id: str) -> None:
    bar = "─" * 3
    _write("\n")
    _write(
        f"  {c(BOLD)}{c(FG['br_magenta'])}✦ rhea{c(RESET)}"
        f"  {c(DIM)}{bar}{c(RESET)}  "
        f"{c(DIM)}session{c(RESET)} {c(FG['cyan'])}{session_id}{c(RESET)}  "
        f"{c(DIM)}depth {MAX_DEPTH} · /help for commands{c(RESET)}\n\n"
    )


def _print_turn_separator() -> None:
    if not COLOR:
        _write("\n" + "-" * 40 + "\n\n")
        return
    w = max(20, _term_width() - 4)
    _write(f"\n{c(DIM)}{c(FG['grey'])}{'─' * w}{c(RESET)}\n\n")


# ---------- role labels & special lines ----------

def _print_user_echo(text: str) -> None:
    _write(f"{c(BOLD)}{c(FG['br_cyan'])}❯ you{c(RESET)} {c(FG['cyan'])}{text}{c(RESET)}\n")


RHEA_PREFIX = ""  # set once; used as spinner prefix so spinner sits next to the label


def _rhea_label_text() -> str:
    return f"{c(BOLD)}{c(FG['br_green'])}✧ rhea{c(RESET)} "


def _print_think_line_inline(text: str) -> None:
    """
    Print a single think line below the current streaming area, typewritered,
    italic grey. Used when we detect a 'THINK:' line on the wire.
    """
    body = text
    for prefix in ("THINK:", "Think:", "think:"):
        if body.startswith(prefix):
            body = body[len(prefix):].strip()
            break
    _write(f"{c(DIM)}{c(FG['br_yellow'])}◆ think{c(RESET)} ")
    _typewriter(body, delay=0.005, style=f"{c(DIM)}{c(ITAL)}{c(FG['grey'])}")


def _pretty_args(args) -> str:
    if isinstance(args, dict):
        parts = []
        for k, v in args.items():
            if isinstance(v, str):
                val = v if len(v) <= 60 else v[:60] + "…"
                parts.append(f"{k}={val!r}")
            else:
                parts.append(f"{k}={v!r}")
        return ", ".join(parts)
    return str(args)


def _print_tool_call(name: str, args, step: int | None = None) -> None:
    args_str = _pretty_args(args)
    max_w = max(40, _term_width() - 20)
    if len(args_str) > max_w:
        args_str = args_str[: max_w - 1] + "…"
    step_badge = f"{c(DIM)}[{step + 1}]{c(RESET)} " if step is not None else ""
    _write(
        f"\n{step_badge}{c(BOLD)}{c(FG['br_yellow'])}▸ tool{c(RESET)} "
        f"{c(FG['yellow'])}{name}{c(RESET)}"
        f"{c(DIM)}({args_str}){c(RESET)}\n"
    )


def _print_tool_result(result: str, elapsed: float | None = None) -> None:
    timing = f"  {c(DIM)}({elapsed:.2f}s){c(RESET)}" if elapsed is not None else ""
    # if short and single-line, collapse to one line
    single = result.strip()
    if "\n" not in single and len(single) <= 200:
        body = _format_inline(single)
        _write(f"  {c(DIM)}{c(FG['grey'])}←{c(RESET)} {c(DIM)}{body}{c(RESET)}{timing}\n")
        return
    snippet = result if len(result) <= 600 else result[:600] + "…"
    lines = snippet.splitlines() or [""]
    for i, line in enumerate(lines):
        body = _format_inline(line)
        tail = timing if (i == len(lines) - 1 and elapsed is not None) else ""
        _write(f"  {c(DIM)}{c(FG['grey'])}│{c(RESET)} {c(DIM)}{body}{c(RESET)}{tail}\n")


def _print_error(msg: str) -> None:
    _write(f"\n{c(BOLD)}{c(FG['br_red'])}✗ error{c(RESET)} {c(FG['red'])}{msg}{c(RESET)}\n")


# ---------- streaming token handler ----------

class TokenBuffer:
    """
    - Strips leading whitespace on the first token (and after any tool step resumes).
    - Detects a leading 'THINK:' line and suppresses it from the output stream,
      surfacing it instead as the spinner's label — on subsequent THINK lines
      within the same reply, they update the spinner label silently rather than
      being re-rendered.
    - Applies inline markdown-lite + hyperlink formatting line-by-line.
    """

    def __init__(self, spinner: Spinner | None = None) -> None:
        self.spinner = spinner
        self.reset_segment()
        self.think_lines_seen = 0

    def reset_segment(self) -> None:
        self.started = False
        self.pre_buffer = ""
        # per-line buffer for inline formatting
        self.line_buffer = ""

    def _flush_line(self, line: str, newline: bool) -> None:
        _write(_format_inline(line))
        if newline:
            _write("\n")

    def _feed_formatted(self, text: str) -> None:
        # accumulate until newline; format whole line then emit
        self.line_buffer += text
        while "\n" in self.line_buffer:
            idx = self.line_buffer.find("\n")
            line = self.line_buffer[:idx]
            self.line_buffer = self.line_buffer[idx + 1:]
            self._flush_line(line, newline=True)
        # also emit what we have so the user sees tokens stream,
        # but without markdown formatting applied yet — easier UX:
        # write raw trailing (formatting will re-wrap on next newline).
        # To keep feel of streaming, we flush the tail raw.
        tail = self.line_buffer
        if tail:
            _write(tail)
            self.line_buffer = ""

    def flush_tail(self) -> None:
        if self.line_buffer:
            self._flush_line(self.line_buffer, newline=False)
            self.line_buffer = ""

    def feed(self, tok: str) -> None:
        if not tok:
            return

        if not self.started:
            self.pre_buffer += tok
            stripped = self.pre_buffer.lstrip()
            if not stripped:
                return
            self.pre_buffer = stripped
            self.started = True

            low = self.pre_buffer.lower()
            if low.startswith("think:"):
                nl = self.pre_buffer.find("\n")
                if nl == -1:
                    return
                think_body = self.pre_buffer[len("think:") : nl].strip()
                rest = self.pre_buffer[nl + 1:]
                self.think_lines_seen += 1
                if self.think_lines_seen == 1:
                    # first THINK: render inline on its own line (preserves user-visible reasoning)
                    _print_think_line_inline(f"THINK: {think_body}")
                else:
                    # subsequent: update spinner label instead
                    if self.spinner is not None:
                        self.spinner.set_label(f"thinking — {think_body}")
                self.pre_buffer = ""
                if rest:
                    self._feed_formatted(rest.lstrip())
                return

            self._feed_formatted(self.pre_buffer)
            self.pre_buffer = ""
            return

        # if we were collecting a THINK line, keep collecting
        if self.pre_buffer:
            self.pre_buffer += tok
            nl = self.pre_buffer.find("\n")
            if nl == -1:
                return
            think_body = self.pre_buffer[len("think:") : nl].strip()
            rest = self.pre_buffer[nl + 1:]
            self.think_lines_seen += 1
            if self.think_lines_seen == 1:
                _print_think_line_inline(f"THINK: {think_body}")
            else:
                if self.spinner is not None:
                    self.spinner.set_label(f"thinking — {think_body}")
            self.pre_buffer = ""
            if rest:
                self._feed_formatted(rest.lstrip())
            return

        self._feed_formatted(tok)


# ---------- session state ----------

class Session:
    def __init__(self) -> None:
        self.session_id = os.environ.get("RHEA_SESSION_ID") or uuid.uuid4().hex[:12]
        self.last_prompt: str = ""
        self.last_reply: str = ""

    def new(self) -> None:
        self.session_id = uuid.uuid4().hex[:12]
        self.last_prompt = ""
        self.last_reply = ""


# ---------- streaming ----------

def _stream_assistant(prompt: str, sess: Session) -> None:
    payload = json.dumps({"prompt": prompt, "session_id": sess.session_id}).encode("utf-8")
    req = urllib.request.Request(
        CHAT_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    turn_start = time.monotonic()

    # rhea label sits on its own line; spinner starts on the next line (prefix empty)
    _write(f"\n{_rhea_label_text()}\n")

    spinner = Spinner("thinking")
    spinner.start()
    buf = TokenBuffer(spinner)
    spinner_stopped = False
    reply_accum: list[str] = []
    tool_step_start: float | None = None

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
                    tok = str(packet.get("token", ""))
                    reply_accum.append(tok)
                    buf.feed(tok)
                    continue

                if "thought" in packet:
                    # server-emitted thought — treat like a THINK line
                    thought = str(packet.get("thought", ""))
                    buf.think_lines_seen += 1
                    if buf.think_lines_seen == 1:
                        kill_spinner()
                        _print_think_line_inline(f"THINK: {thought}")
                    else:
                        spinner.set_label(f"thinking — {thought}")
                    continue

                if "tool_call" in packet:
                    kill_spinner()
                    buf.flush_tail()
                    step = packet.get("step")
                    _print_tool_call(
                        str(packet.get("tool_call", "")),
                        packet.get("args", {}),
                        step if isinstance(step, int) else None,
                    )
                    tool_step_start = time.monotonic()
                    spinner = Spinner("running tool")
                    spinner.start()
                    buf.spinner = spinner
                    spinner_stopped = False
                    continue

                if "tool_result" in packet:
                    kill_spinner()
                    elapsed = (time.monotonic() - tool_step_start) if tool_step_start else None
                    _print_tool_result(str(packet.get("tool_result", "")), elapsed)
                    tool_step_start = None
                    spinner = Spinner("thinking")
                    spinner.start()
                    spinner_stopped = False
                    # reset per-segment state so leading whitespace after a tool
                    # result gets trimmed too
                    buf.reset_segment()
                    buf.spinner = spinner
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
        buf.flush_tail()

    total = time.monotonic() - turn_start
    _write(f"\n{c(DIM)}{c(FG['grey'])}· {total:.1f}s{c(RESET)}\n")
    sess.last_reply = "".join(reply_accum).strip()


# ---------- input ----------

def _read_prompt_line(continuation: bool = False) -> str | None:
    if COLOR:
        glyph = "…" if continuation else "❯"
        color = FG["grey"] if continuation else FG["br_magenta"]
        prompt_marker = f"\001{BOLD}{color}\002{glyph}\001{RESET}\002 "
    else:
        prompt_marker = "... " if continuation else "> "
    try:
        return input(prompt_marker)
    except EOFError:
        return None


def _read_prompt() -> str | None:
    """Supports multi-line input via trailing '\\' continuation."""
    line = _read_prompt_line(False)
    if line is None:
        return None
    parts = [line]
    while parts[-1].endswith("\\"):
        parts[-1] = parts[-1][:-1]
        nxt = _read_prompt_line(True)
        if nxt is None:
            break
        parts.append(nxt)
    return "\n".join(parts)


# ---------- slash commands ----------

def _print_help() -> None:
    rows = [
        ("/help", "show this help"),
        ("/new", "start a new session (clears server memory for this client)"),
        ("/clear", "clear the screen"),
        ("/retry", "re-send the last prompt"),
        ("/history", "show readline history"),
        ("/yank", "copy the last assistant reply to clipboard"),
        ("/quit, /exit, :q", "exit"),
        ("end line with \\", "multi-line prompt"),
    ]
    _write("\n")
    for cmd, desc in rows:
        _write(f"  {c(BOLD)}{c(FG['br_magenta'])}{cmd:<22}{c(RESET)}  {c(DIM)}{desc}{c(RESET)}\n")
    _write("\n")


def _handle_slash(cmd: str, sess: Session) -> bool:
    """Return True if handled (caller should skip sending to model)."""
    cmd_lower = cmd.lower().strip()
    if cmd_lower in ("/quit", "/exit", ":q"):
        _write(f"{c(DIM)}bye.{c(RESET)}\n")
        sys.exit(0)
    if cmd_lower == "/help":
        _print_help()
        return True
    if cmd_lower == "/new":
        sess.new()
        _write(f"{c(DIM)}new session: {c(FG['cyan'])}{sess.session_id}{c(RESET)}\n")
        return True
    if cmd_lower == "/clear":
        if COLOR:
            _write("\x1b[2J\x1b[H")
        return True
    if cmd_lower == "/retry":
        if not sess.last_prompt:
            _write(f"{c(DIM)}nothing to retry.{c(RESET)}\n")
            return True
        _write(f"{c(DIM)}↻ retrying: {sess.last_prompt}{c(RESET)}\n")
        _print_user_echo(sess.last_prompt)
        _stream_assistant(sess.last_prompt, sess)
        _print_turn_separator()
        return True
    if cmd_lower == "/history":
        try:
            import readline as _rl
            n = _rl.get_current_history_length()
            _write("\n")
            for i in range(max(1, n - 20), n + 1):
                item = _rl.get_history_item(i)
                if item:
                    _write(f"  {c(DIM)}{i:>3}{c(RESET)}  {item}\n")
            _write("\n")
        except Exception:
            _write(f"{c(DIM)}history unavailable.{c(RESET)}\n")
        return True
    if cmd_lower == "/yank":
        if not sess.last_reply:
            _write(f"{c(DIM)}no reply to copy.{c(RESET)}\n")
            return True
        ok, info = _copy_to_clipboard(sess.last_reply)
        if ok:
            _write(f"{c(DIM)}{c(FG['green'])}✓ copied via {info}{c(RESET)}\n")
        else:
            _write(f"{c(DIM)}{c(FG['red'])}✗ {info}{c(RESET)}\n")
        return True
    return False


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Rhea chat REPL")
    parser.add_argument("prompt", nargs="*", help="Optional initial prompt to send immediately")
    args = parser.parse_args()

    sess = Session()
    _print_banner(sess.session_id)

    initial = " ".join(args.prompt).strip()
    if initial:
        sess.last_prompt = initial
        _print_user_echo(initial)
        _stream_assistant(initial, sess)
        _print_turn_separator()

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

        if prompt.startswith("/") or prompt == ":q":
            if _handle_slash(prompt, sess):
                continue

        sess.last_prompt = prompt
        _stream_assistant(prompt, sess)
        _print_turn_separator()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _write(f"\n{c(DIM)}bye.{c(RESET)}\n")
        sys.exit(0)
    finally:
        if COLOR:
            _write(SHOW_CURSOR)
