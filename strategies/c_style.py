from __future__ import annotations

from typing import Any

from tools import ToolSpec


class ParseError(Exception):
    pass


_TYPE_MAP = {
    "str": "string",
    "int": "int",
    "float": "float",
    "bool": "bool",
}


class CStyleStrategy:
    name = "c_style"

    def build_system_prompt(self, tools: dict[str, ToolSpec]) -> str:
        declarations = []
        for spec in tools.values():
            declarations.append(_build_declaration(spec))
        header = "\n\n".join(declarations)

        return (
            "You have access to the following functions:\n\n"
            "---BEGIN FUNCTION DECLARATIONS---\n"
            f"{header}\n"
            "---END FUNCTION DECLARATIONS---\n\n"
            "To call a function, write EXACTLY one line in this format:\n"
            'CALL: function_name(arg1, "arg2", 3.14)\n\n'
            "Rules:\n"
            "- String arguments MUST be in double quotes.\n"
            "- Numeric arguments are unquoted.\n"
            "- Do not add any text on the CALL: line other than the function call.\n"
            "- If you do not need to call a function, respond normally without CALL:.\n"
        )

    def parse_response(self, raw: str, tools: dict[str, ToolSpec]) -> tuple[str, dict[str, Any]] | None:
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.startswith("CALL:"):
                call_str = stripped[len("CALL:"):].strip()
                return _parse_c_call(call_str, tools)
        return None


def _build_declaration(spec: ToolSpec) -> str:
    fields = spec.param_model.model_fields
    params = []
    param_docs = []
    for fname, finfo in fields.items():
        ftype = _get_c_type(finfo.annotation)
        params.append(f"{ftype} {fname}")
        desc = finfo.description or ""
        default = ""
        if finfo.default is not None and not finfo.is_required():
            default = f" (default: {finfo.default})"
        param_docs.append(f" * @param {fname}  ({ftype}) {desc}{default}")

    ret_type = _TYPE_MAP.get(spec.return_type, spec.return_type)
    doc = f"/*\n * {spec.description}\n *\n"
    doc += "\n".join(param_docs)
    doc += f"\n * @return {ret_type}\n */"
    sig = f"{ret_type} {spec.name}({', '.join(params)});"
    return f"{doc}\n{sig}"


def _get_c_type(annotation) -> str:
    if annotation is None:
        return "string"
    name = getattr(annotation, "__name__", str(annotation))
    # Handle Literal types
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        args = getattr(annotation, "__args__", ())
        if args and all(isinstance(a, str) for a in args):
            return "string"
    return _TYPE_MAP.get(name, "string")


def _parse_c_call(call_str: str, tools: dict[str, ToolSpec]) -> tuple[str, dict[str, Any]]:
    # Extract function name
    paren_idx = call_str.find("(")
    if paren_idx == -1:
        raise ParseError(f"No opening parenthesis: {call_str}")

    func_name = call_str[:paren_idx].strip()
    if func_name not in tools:
        raise ParseError(f"Unknown function: {func_name}")

    # Find matching closing paren
    if not call_str.rstrip().endswith(")"):
        raise ParseError(f"No closing parenthesis: {call_str}")

    args_str = call_str[paren_idx + 1 : -1].strip()
    if not args_str:
        tokens = []
    else:
        tokens = _tokenize_args(args_str)

    # Map positional args to param names
    spec = tools[func_name]
    field_names = list(spec.param_model.model_fields.keys())

    kwargs: dict[str, Any] = {}
    for i, token in enumerate(tokens):
        if i >= len(field_names):
            raise ParseError(f"Too many arguments for {func_name}: expected {len(field_names)}")
        kwargs[field_names[i]] = token

    return func_name, kwargs


def _tokenize_args(args_str: str) -> list[Any]:
    tokens: list[Any] = []
    i = 0
    n = len(args_str)

    while i < n:
        # Skip whitespace and commas
        while i < n and args_str[i] in (" ", "\t", ","):
            i += 1
        if i >= n:
            break

        if args_str[i] == '"':
            # Quoted string
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
                i += 1  # skip closing quote
            tokens.append("".join(parts))
        else:
            # Unquoted token (number, bool, null)
            start = i
            while i < n and args_str[i] not in (",", ")"):
                i += 1
            raw = args_str[start:i].strip()
            tokens.append(_coerce_value(raw))

    return tokens


def _coerce_value(raw: str) -> Any:
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
