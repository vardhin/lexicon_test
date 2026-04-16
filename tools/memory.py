from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tools import register_tool
from tools.graph_memory_daemon import SOCKET_PATH
from tools.models import MemoryClearArgs, MemoryGetArgs, MemoryQueryArgs, MemorySetArgs

_WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
_DAEMON_BOOT_TIMEOUT_SECONDS = 2.5


def _send_request(payload: dict[str, Any], ensure_daemon: bool = True) -> dict[str, Any]:
    attempts = 3
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2.0)
                client.connect(str(SOCKET_PATH))
                client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                raw = _read_socket_line(client)

            data = json.loads(raw)
            if not data.get("ok", False):
                raise RuntimeError(str(data.get("error", "unknown daemon error")))
            return data
        except Exception as exc:
            last_error = exc
            if not ensure_daemon or attempt == attempts - 1:
                break
            _ensure_daemon_running()
            time.sleep(0.15)

    raise RuntimeError(f"Graph memory daemon unavailable: {last_error}")


def _read_socket_line(client: socket.socket) -> str:
    chunks: list[bytes] = []
    while True:
        block = client.recv(4096)
        if not block:
            break
        chunks.append(block)
        if b"\n" in block:
            break
    if not chunks:
        return "{}"
    return b"".join(chunks).split(b"\n", 1)[0].decode("utf-8", errors="replace")


def _daemon_ping() -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.5)
            client.connect(str(SOCKET_PATH))
            client.sendall(b'{"action":"ping"}\n')
            raw = _read_socket_line(client)
        data = json.loads(raw)
        return bool(data.get("ok", False)) and bool(data.get("pong", False))
    except Exception:
        return False


def _ensure_daemon_running() -> None:
    if _daemon_ping():
        return

    subprocess.Popen(
        [sys.executable, "-m", "tools.graph_memory_daemon", "--daemon"],
        cwd=str(_WORKSPACE_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.time() + _DAEMON_BOOT_TIMEOUT_SECONDS
    while time.time() < deadline:
        if _daemon_ping():
            return
        time.sleep(0.1)

    raise RuntimeError("Failed to start graph memory daemon")


def ensure_memory_daemon() -> None:
    """Ensure the graph memory daemon is running."""
    _ensure_daemon_running()


def auto_memory_ingest(text: str) -> None:
    """Ingest free-form text into graph memory; best-effort and non-fatal."""
    if not text or not text.strip():
        return

    try:
        _send_request({"action": "ingest", "text": text})
    except Exception:
        # Ingestion should never break chat flow.
        return


def _clean_memory_item(text: Any, max_len: int = 120) -> str:
    cleaned = " ".join(str(text).split()).strip()
    if not cleaned:
        return ""
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3].rstrip() + "..."


def build_memory_context(query: str, top_k: int = 5, max_chars: int = 1400) -> str:
    """Fetch and compact memory for prompt grounding before LLM execution."""
    if not query or not query.strip():
        return ""

    try:
        result = _send_request({"action": "query", "query": query, "top_k": top_k}).get("result", {})
    except Exception:
        return ""

    words = []
    seen_words: set[str] = set()
    for row in result.get("top_words", []):
        item = _clean_memory_item(row.get("word", ""), max_len=40)
        if item and item not in seen_words:
            seen_words.add(item)
            words.append(item)

    phrases = []
    seen_phrases: set[str] = set()
    for row in result.get("top_phrases", []):
        item = _clean_memory_item(row.get("phrase", ""), max_len=100)
        if item and item not in seen_phrases:
            seen_phrases.add(item)
            phrases.append(item)

    sentences = []
    seen_sentences: set[str] = set()
    for row in result.get("top_sentences", []):
        item = _clean_memory_item(row.get("sentence", ""), max_len=180)
        if item and item not in seen_sentences:
            seen_sentences.add(item)
            sentences.append(item)

    if not words and not phrases and not sentences:
        return ""

    lines: list[str] = []
    if words:
        lines.append("words: " + ", ".join(words))
    if phrases:
        lines.append("phrases: " + " | ".join(phrases))
    if sentences:
        lines.append("sentences: " + " | ".join(sentences))

    context = "\n".join(lines)
    if len(context) > max_chars:
        context = context[: max_chars - 3].rstrip() + "..."
    return context


def _format_query_result(result: dict[str, Any]) -> str:
    words = result.get("top_words", [])
    phrases = result.get("top_phrases", [])
    sentences = result.get("top_sentences", [])

    if not words and not phrases and not sentences:
        return "No ranked memory matches found."

    lines: list[str] = []

    lines.append("Top words:")
    if words:
        for idx, row in enumerate(words, start=1):
            lines.append(
                f"{idx}. {row.get('word', '')} (weight={row.get('weight', 0)}, score={row.get('score', 0)})"
            )
    else:
        lines.append("1. (none)")

    lines.append("")
    lines.append("Top phrases:")
    if phrases:
        for idx, row in enumerate(phrases, start=1):
            lines.append(
                f"{idx}. {row.get('phrase', '')} (weight={row.get('weight', 0)}, score={row.get('score', 0)})"
            )
    else:
        lines.append("1. (none)")

    lines.append("")
    lines.append("Top sentences:")
    if sentences:
        for idx, row in enumerate(sentences, start=1):
            lines.append(
                f"{idx}. {row.get('sentence', '')} (weight={row.get('weight', 0)}, score={row.get('score', 0)})"
            )
    else:
        lines.append("1. (none)")

    return "\n".join(lines)


@register_tool(MemoryGetArgs, return_type="str")
def memory_get(key: str) -> str:
    """Retrieve key-value memory entries from the graph-memory daemon. Pass '*' to list all keys."""
    try:
        value = _send_request({"action": "recall", "key": key}).get("result")
    except Exception as exc:
        return f"Memory read failed: {exc}"

    if value is None:
        return f"No memory entry for '{key}'."
    return str(value)


@register_tool(MemorySetArgs, return_type="str")
def memory_set(key: str, value: str) -> str:
    """Store a key-value pair and link it into graph memory for weighted retrieval."""
    try:
        _send_request({"action": "remember", "key": key, "value": value})
    except Exception as exc:
        return f"Memory write failed: {exc}"

    return f"Remembered: {key} = {value}"


@register_tool(MemoryQueryArgs, return_type="str")
def memory_query(query: str, top_k: int = 5) -> str:
    """Search graph memory and return top-ranked words, phrases, and sentences related to the query."""
    try:
        result = _send_request({"action": "query", "query": query, "top_k": top_k}).get("result", {})
    except Exception as exc:
        return f"Memory query failed: {exc}"
    return _format_query_result(result)


@register_tool(MemoryClearArgs, return_type="str")
def memory_clear(scope: str = "all", confirm: str = "") -> str:
    """Clear memory data. scope='graph' clears weighted graph, scope='kv' clears key-value memory, scope='all' clears both."""
    if confirm != "CONFIRM":
        return (
            "Memory clear blocked: confirmation required. "
            "Call memory_clear(scope, \"CONFIRM\") to proceed."
        )

    try:
        result = _send_request({"action": "clear", "scope": scope}).get("result", {})
    except Exception as exc:
        return f"Memory clear failed: {exc}"

    cleared_graph = int(result.get("cleared_graph_rows", 0))
    cleared_kv = int(result.get("cleared_kv_rows", 0))
    scope_out = str(result.get("scope", scope))
    return f"Memory cleared (scope={scope_out}): graph_rows={cleared_graph}, kv_rows={cleared_kv}"
