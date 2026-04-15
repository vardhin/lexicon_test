import json
from pathlib import Path

from tools import register_tool
from tools.models import MemoryGetArgs, MemorySetArgs

_MEMORY_FILE = Path.home() / ".config" / "rhea" / "memory.json"


def _load() -> dict:
    if _MEMORY_FILE.exists():
        try:
            return json.loads(_MEMORY_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save(data: dict) -> None:
    _MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _MEMORY_FILE.write_text(json.dumps(data, indent=2))


@register_tool(MemoryGetArgs, return_type="str")
def memory_get(key: str) -> str:
    """Retrieve a value from Rhea's persistent memory by key. Pass '*' to see all stored keys and values."""
    data = _load()
    if key == "*":
        if not data:
            return "Memory is empty."
        return "\n".join(f"{k}: {v}" for k, v in data.items())
    value = data.get(key)
    if value is None:
        return f"No memory entry for '{key}'."
    return value


@register_tool(MemorySetArgs, return_type="str")
def memory_set(key: str, value: str) -> str:
    """Store a key-value pair in Rhea's persistent memory (e.g. directory paths, preferences)."""
    data = _load()
    data[key] = value
    _save(data)
    return f"Remembered: {key} = {value}"
