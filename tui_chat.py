#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import uuid
import urllib.error
import urllib.request

try:
    import readline  # noqa: F401  — enables arrow-key history & editing in input()
except ImportError:
    pass

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

CHAT_URL = os.environ.get("RHEA_CHAT_URL", "http://localhost:8000/chat")
MAX_DEPTH = 3

console = Console()


def _print_banner(session_id: str) -> None:
    console.print()
    console.print(
        Panel.fit(
            Text.from_markup(
                f"[bold magenta]Rhea[/bold magenta]  "
                f"[dim]session[/dim] [cyan]{session_id}[/cyan]  "
                f"[dim]depth[/dim] [cyan]{MAX_DEPTH}[/cyan]\n"
                f"[dim]Enter to send · Ctrl+D or /quit to exit · Ctrl+C to cancel a reply[/dim]"
            ),
            border_style="magenta",
        )
    )
    console.print()


def _print_user(text: str) -> None:
    console.print(Text("you  ", style="bold cyan"), end="")
    console.print(Text(text, style="cyan"))


def _print_tool_call(name: str, args) -> None:
    console.print(
        Text.from_markup(f"[bold yellow]tool[/bold yellow] [dim]→[/dim] [yellow]{name}[/yellow] [dim]{args}[/dim]")
    )


def _print_tool_result(result: str) -> None:
    snippet = result if len(result) <= 400 else result[:400] + "…"
    console.print(
        Panel(
            Text(snippet, style="white"),
            title="tool result",
            title_align="left",
            border_style="yellow",
            padding=(0, 1),
        )
    )


def _print_error(msg: str) -> None:
    console.print(
        Panel(Text(msg, style="bold red"), title="error", title_align="left", border_style="red", padding=(0, 1))
    )


def _render_assistant(md_text: str) -> Markdown | Text:
    if not md_text.strip():
        return Text("…", style="dim")
    return Markdown(md_text, code_theme="monokai")


def _stream_assistant(prompt: str, session_id: str) -> str:
    """Stream the assistant reply, updating a single live region. Returns the full text."""
    payload = json.dumps({"prompt": prompt, "session_id": session_id}).encode("utf-8")
    req = urllib.request.Request(
        CHAT_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    console.print(Text("rhea", style="bold green"))
    buffer = ""
    had_tool_activity = False

    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            with Live(
                _render_assistant(buffer),
                console=console,
                refresh_per_second=20,
                transient=False,
                vertical_overflow="visible",
            ) as live:
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
                        buffer += str(packet.get("token", ""))
                        live.update(_render_assistant(buffer))
                        continue

                    if "tool_call" in packet:
                        had_tool_activity = True
                        # Pause live, print tool call into scrollback, then resume empty live
                        live.update(Text(""))
                        live.stop()
                        _print_tool_call(str(packet.get("tool_call", "")), packet.get("args", {}))
                        live.start()
                        continue

                    if "tool_result" in packet:
                        live.update(Text(""))
                        live.stop()
                        _print_tool_result(str(packet.get("tool_result", "")))
                        live.start()
                        continue

                    if "error" in packet:
                        live.update(Text(""))
                        live.stop()
                        _print_error(str(packet.get("error", "unknown error")))
                        return buffer

                    if packet.get("done") is True:
                        break

    except KeyboardInterrupt:
        console.print(Text("\n[cancelled]", style="dim red"))
        return buffer
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        detail = f"HTTP {exc.code} {exc.reason}".strip()
        if body:
            detail = f"{detail}: {body}"
        _print_error(detail)
    except urllib.error.URLError as exc:
        _print_error(f"connection failed: {exc.reason}")
    except Exception as exc:
        _print_error(f"request failed: {exc}")

    # If tool activity happened but no final text came through, emit a newline separator
    if had_tool_activity and not buffer.strip():
        console.print()

    return buffer


def _read_prompt() -> str | None:
    try:
        line = input("\x1b[1;35m❯\x1b[0m ")
    except EOFError:
        return None
    return line


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Rhea chat REPL")
    parser.add_argument("prompt", nargs="*", help="Optional initial prompt to send immediately")
    args = parser.parse_args()

    session_id = os.environ.get("RHEA_SESSION_ID") or uuid.uuid4().hex[:12]
    _print_banner(session_id)

    initial = " ".join(args.prompt).strip()
    if initial:
        _print_user(initial)
        _stream_assistant(initial, session_id)
        console.print(Rule(style="dim"))

    while True:
        try:
            prompt = _read_prompt()
        except KeyboardInterrupt:
            console.print()
            continue

        if prompt is None:
            console.print(Text("\nbye.", style="dim"))
            return

        prompt = prompt.strip()
        if not prompt:
            continue
        if prompt in ("/quit", "/exit", ":q"):
            console.print(Text("bye.", style="dim"))
            return

        _stream_assistant(prompt, session_id)
        console.print(Rule(style="dim"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print(Text("\nbye.", style="dim"))
        sys.exit(0)
