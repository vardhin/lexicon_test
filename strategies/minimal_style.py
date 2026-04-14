from __future__ import annotations

from typing import Any

from tools import ToolSpec


class ParseError(Exception):
    pass


_TYPE_MAP = {
    "str": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
}


class MinimalStyleStrategy:
    name = "minimal_style"

    def build_system_prompt(self, tools: dict[str, ToolSpec]) -> str:
        lines = []
        for spec in tools.values():
            fields = spec.param_model.model_fields
            params = ", ".join(
                f"{fname}:{_get_type(finfo.annotation)}"
                for fname, finfo in fields.items()
            )
            lines.append(f"{spec.name}({params})  # {spec.description}")

        tool_block = "\n".join(lines)
        return (
            f"Tools:\n{tool_block}\n\n"
            "Always use a tool when the task requires computation, string manipulation, or search.\n"
            "To call a tool write: CALL name(\"arg1\", arg2)\n"
            "String args use double quotes. Numeric args are unquoted.\n"
            "If no tool is needed, reply normally."
        )

    def parse_response(self, raw: str, tools: dict[str, ToolSpec]) -> tuple[str, dict[str, Any]] | None:
        for line in raw.splitlines():
            stripped = line.strip()
            # Handle "CALL name(...)" and "CALL: name(...)"
            if stripped.upper().startswith("CALL"):
                call_str = stripped[4:].lstrip(":= ").strip()
                if call_str:
                    return _parse_call(call_str, tools)
            # Handle model-native "<|tool_call>call:name(...)"
            if "<|tool_call>" in stripped:
                after = stripped.split("<|tool_call>", 1)[1]
                call_str = after.lstrip("call:Call: ").strip()
                if call_str:
                    return _parse_call(call_str, tools)
        return None


def _get_type(annotation) -> str:
    if annotation is None:
        return "str"
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        return "str"
    name = getattr(annotation, "__name__", "")
    return _TYPE_MAP.get(name, "str")


def _parse_call(call_str: str, tools: dict[str, ToolSpec]) -> tuple[str, dict[str, Any]]:
    paren_idx = call_str.find("(")
    if paren_idx == -1:
        raise ParseError(f"No opening parenthesis: {call_str}")

    func_name = call_str[:paren_idx].strip()
    if func_name not in tools:
        raise ParseError(f"Unknown function: {func_name}")

    if not call_str.rstrip().endswith(")"):
        raise ParseError(f"No closing parenthesis: {call_str}")

    args_str = call_str[paren_idx + 1:-1].strip()
    tokens = _tokenize(args_str) if args_str else []

    spec = tools[func_name]
    field_names = list(spec.param_model.model_fields.keys())

    kwargs: dict[str, Any] = {}
    for i, token in enumerate(tokens):
        if i >= len(field_names):
            raise ParseError(f"Too many arguments for {func_name}")
        kwargs[field_names[i]] = token

    return func_name, kwargs


def _tokenize(args_str: str) -> list[Any]:
    tokens: list[Any] = []
    i = 0
    n = len(args_str)

    while i < n:
        while i < n and args_str[i] in (" ", "\t", ","):
            i += 1
        if i >= n:
            break

        if args_str[i] == '"':
            i += 1
            parts = []
            while i < n and args_str[i] != '"':
                if args_str[i] == "\\" and i + 1 < n:
                    parts.append(args_str[i + 1])
                    i += 2
                else:
                    parts.append(args_str[i])
                    i += 1
            if i < n:
                i += 1
            tokens.append("".join(parts))
        else:
            # Collect raw token up to next comma/close-paren
            start = i
            while i < n and args_str[i] not in (",", ")"):
                i += 1
            raw = args_str[start:i].strip()
            # Strip named-arg prefix: "key: value" or "key:value"
            colon = raw.find(":")
            if colon != -1:
                key = raw[:colon].strip()
                # Only strip if key looks like an identifier (no spaces)
                if key and " " not in key:
                    raw = raw[colon + 1:].strip().strip('"')
            tokens.append(_coerce(raw))

    return tokens


def _coerce(raw: str) -> Any:
    # Strip type prefixes the model may echo back (e.g. "s:foo", "i:5")
    if len(raw) >= 2 and raw[1] == ":" and raw[0] in "sifb":
        raw = raw[2:]
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    if raw.lower() == "null":
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw
