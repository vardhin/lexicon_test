from __future__ import annotations

from typing import Any, Protocol

from tools import ToolSpec


class ToolStrategy(Protocol):
    name: str

    def build_system_prompt(self, tools: dict[str, ToolSpec]) -> str: ...

    def parse_response(self, raw: str, tools: dict[str, ToolSpec]) -> tuple[str, dict[str, Any]] | None: ...
