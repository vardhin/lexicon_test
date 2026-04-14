from __future__ import annotations

import json
import re
from typing import Any

from tools import ToolSpec


class ParseError(Exception):
    pass


_JSON_TYPE_MAP = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
}


class JsonStyleStrategy:
    name = "json_style"

    def build_system_prompt(self, tools: dict[str, ToolSpec]) -> str:
        tool_schemas = []
        for spec in tools.values():
            tool_schemas.append(_build_tool_schema(spec))

        schema_text = json.dumps(tool_schemas, indent=2)

        return (
            "You have access to the following tools:\n\n"
            f"{schema_text}\n\n"
            "To call a tool, respond with a JSON object in this EXACT format:\n"
            '{"tool_call": {"name": "function_name", "arguments": {"param": "value"}}}\n\n'
            "Rules:\n"
            "- Output ONLY the JSON object when calling a tool, no other text.\n"
            "- If you do not need to call a tool, respond normally without JSON.\n"
        )

    def parse_response(self, raw: str, tools: dict[str, ToolSpec]) -> tuple[str, dict[str, Any]] | None:
        # Try the whole response first, then scan for embedded JSON blocks
        candidates = [raw.strip()]
        # Also extract top-level {...} blocks by scanning for balanced braces
        for start in range(len(raw)):
            if raw[start] == "{":
                depth = 0
                for end in range(start, len(raw)):
                    if raw[end] == "{":
                        depth += 1
                    elif raw[end] == "}":
                        depth -= 1
                        if depth == 0:
                            candidates.append(raw[start:end + 1])
                            break

        for candidate in candidates:
            try:
                obj = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "tool_call" in obj:
                tc = obj["tool_call"]
                name = tc.get("name")
                args = tc.get("arguments", {})
                if name is None:
                    raise ParseError("tool_call missing 'name'")
                if name not in tools:
                    raise ParseError(f"Unknown tool: {name}")
                return name, args
        return None


def _build_tool_schema(spec: ToolSpec) -> dict:
    properties = {}
    required = []
    for fname, finfo in spec.param_model.model_fields.items():
        prop: dict[str, Any] = {}
        annotation = finfo.annotation
        origin = getattr(annotation, "__origin__", None)

        if origin is not None:
            # Handle Literal
            args = getattr(annotation, "__args__", ())
            if args and all(isinstance(a, str) for a in args):
                prop["type"] = "string"
                prop["enum"] = list(args)
        else:
            type_name = getattr(annotation, "__name__", "string")
            prop["type"] = _JSON_TYPE_MAP.get(type_name, "string")

        if finfo.description:
            prop["description"] = finfo.description
        if not finfo.is_required() and finfo.default is not None:
            prop["default"] = finfo.default

        properties[fname] = prop

        if finfo.is_required():
            required.append(fname)

    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }
