#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import queue
import textwrap
import threading
import time
import urllib.error
import urllib.request
import uuid
import curses
from dataclasses import dataclass

CHAT_URL = os.environ.get("RHEA_CHAT_URL", "http://localhost:8000/chat")
MAX_DEPTH = 3


@dataclass
class Entry:
    role: str
    text: str


def _stream_chat(prompt: str, session_id: str, event_q: queue.Queue, stop_event: threading.Event) -> None:
    payload = json.dumps({"prompt": prompt, "session_id": session_id}).encode("utf-8")
    req = urllib.request.Request(
        CHAT_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    event_q.put(("assistant_begin", ""))

    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            for raw_line in resp:
                if stop_event.is_set():
                    break

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
                    event_q.put(("token", str(packet.get("token", ""))))
                    continue

                if "tool_call" in packet:
                    tool_name = str(packet.get("tool_call", ""))
                    tool_args = packet.get("args", {})
                    event_q.put(("tool_call", f"{tool_name}({tool_args})"))
                    continue

                if "tool_result" in packet:
                    event_q.put(("tool_result", str(packet.get("tool_result", ""))))
                    continue

                if "error" in packet:
                    event_q.put(("error", str(packet.get("error", "Unknown error"))))
                    break

                if packet.get("done") is True:
                    break

    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        detail = f"HTTP {exc.code} {exc.reason}".strip()
        if body:
            detail = f"{detail}: {body}"
        event_q.put(("error", detail))
    except urllib.error.URLError as exc:
        event_q.put(("error", f"Connection failed: {exc.reason}"))
    except Exception as exc:
        event_q.put(("error", f"Request failed: {exc}"))
    finally:
        event_q.put(("assistant_end", ""))


class ChatTUI:
    def __init__(self, stdscr: "curses._CursesWindow", initial_prompt: str):
        self.stdscr = stdscr
        self.session_id = os.environ.get("RHEA_SESSION_ID") or uuid.uuid4().hex[:12]
        self.entries: list[Entry] = []
        self.input_buffer: list[str] = []
        self.event_q: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.busy = False
        self.pending_assistant_idx: int | None = None
        self.turn_count = 0

        self._setup_ui()
        self._add_entry("System", "Interactive session started. Prompt memory is auto-injected before each model run.")
        self._add_entry("System", "Depth policy: 3-turn sliding window per session.")

        if initial_prompt.strip():
            self._submit_prompt(initial_prompt)

    def _setup_ui(self) -> None:
        curses.noecho()
        curses.cbreak()
        self.stdscr.keypad(True)
        self.stdscr.nodelay(True)
        try:
            curses.curs_set(1)
        except Exception:
            pass

    def _add_entry(self, role: str, text: str) -> None:
        self.entries.append(Entry(role=role, text=text))

    def _start_assistant_entry(self) -> None:
        self.entries.append(Entry(role="Assistant", text=""))
        self.pending_assistant_idx = len(self.entries) - 1

    def _append_assistant_text(self, text: str) -> None:
        if self.pending_assistant_idx is None:
            self._start_assistant_entry()
        idx = self.pending_assistant_idx
        if idx is not None:
            self.entries[idx].text += text

    def _submit_prompt(self, prompt: str) -> None:
        message = prompt.strip()
        if not message:
            return
        if self.busy:
            self._add_entry("System", "Already processing a request. Wait for current response.")
            return

        self._add_entry("You", message)
        self.turn_count += 1
        self.busy = True
        self.pending_assistant_idx = None

        worker = threading.Thread(
            target=_stream_chat,
            args=(message, self.session_id, self.event_q, self.stop_event),
            daemon=True,
        )
        worker.start()

    def _drain_events(self) -> None:
        while True:
            try:
                event, payload = self.event_q.get_nowait()
            except queue.Empty:
                return

            if event == "assistant_begin":
                self._start_assistant_entry()
                continue

            if event == "token":
                self._append_assistant_text(str(payload))
                continue

            if event == "tool_call":
                self._add_entry("Tool", f"CALL {payload}")
                continue

            if event == "tool_result":
                self._add_entry("Tool", str(payload))
                continue

            if event == "error":
                self._add_entry("Error", str(payload))
                continue

            if event == "assistant_end":
                self.busy = False
                self.pending_assistant_idx = None
                continue

    def _handle_key(self, key) -> bool:
        if key in ("\x03",):
            return False

        if key in ("\n", "\r") or key == curses.KEY_ENTER:
            prompt = "".join(self.input_buffer)
            self.input_buffer.clear()
            self._submit_prompt(prompt)
            return True

        if key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            if self.input_buffer:
                self.input_buffer.pop()
            return True

        if key == curses.KEY_RESIZE:
            return True

        if isinstance(key, str) and key.isprintable():
            self.input_buffer.append(key)
            return True

        return True

    def _flatten_lines(self, width: int) -> list[str]:
        lines: list[str] = []
        content_width = max(10, width - 2)

        for entry in self.entries:
            prefix = f"{entry.role}: "
            text = entry.text if entry.text else ""
            wrapped = textwrap.wrap(text, width=max(10, content_width - len(prefix)))
            if not wrapped:
                lines.append(prefix)
                continue

            lines.append(prefix + wrapped[0])
            indent = " " * len(prefix)
            for chunk in wrapped[1:]:
                lines.append(indent + chunk)

        return lines

    def _draw(self) -> None:
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()

        status = "busy" if self.busy else "ready"
        header = (
            f"Rhea TUI | session={self.session_id} | depth={MAX_DEPTH} sliding window | "
            f"state={status} | Enter=send Ctrl+C=quit"
        )
        self.stdscr.addnstr(0, 0, header, max(0, w - 1), curses.A_REVERSE)

        msg_top = 1
        msg_bottom = max(msg_top, h - 3)
        msg_height = max(1, msg_bottom - msg_top + 1)

        lines = self._flatten_lines(w)
        visible = lines[-msg_height:]
        for i, line in enumerate(visible):
            self.stdscr.addnstr(msg_top + i, 0, line, max(0, w - 1))

        prompt_label = "> "
        input_text = "".join(self.input_buffer)
        self.stdscr.addnstr(h - 2, 0, "-" * max(0, w - 1), max(0, w - 1), curses.A_DIM)
        self.stdscr.addnstr(h - 1, 0, prompt_label + input_text, max(0, w - 1))

        cursor_x = min(w - 1, len(prompt_label) + len(input_text))
        self.stdscr.move(h - 1, max(0, cursor_x))
        self.stdscr.refresh()

    def run(self) -> None:
        try:
            while True:
                self._drain_events()
                self._draw()

                try:
                    key = self.stdscr.get_wch()
                except curses.error:
                    time.sleep(0.03)
                    continue

                if not self._handle_key(key):
                    break
        finally:
            self.stop_event.set()


def _main(stdscr: "curses._CursesWindow", initial_prompt: str) -> None:
    tui = ChatTUI(stdscr, initial_prompt)
    tui.run()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Interactive TUI client for Rhea chat")
    parser.add_argument("prompt", nargs="*", help="Optional initial prompt to send immediately")
    args = parser.parse_args()

    initial_prompt = " ".join(args.prompt).strip()
    curses.wrapper(lambda stdscr: _main(stdscr, initial_prompt))


if __name__ == "__main__":
    main()
