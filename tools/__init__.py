from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from pydantic import BaseModel


@dataclass
class ToolSpec:
    name: str
    description: str
    func: Callable
    param_model: type[BaseModel]
    return_type: str


_REGISTRY: dict[str, ToolSpec] = {}


def register_tool(param_model: type[BaseModel], return_type: str = "str"):
    def decorator(func: Callable) -> Callable:
        spec = ToolSpec(
            name=func.__name__,
            description=(func.__doc__ or "").strip(),
            func=func,
            param_model=param_model,
            return_type=return_type,
        )
        _REGISTRY[spec.name] = spec
        return func
    return decorator


def get_all_tools() -> dict[str, ToolSpec]:
    return dict(_REGISTRY)


def get_tool(name: str) -> ToolSpec:
    if name not in _REGISTRY:
        raise KeyError(f"Tool '{name}' not found. Available: {list(_REGISTRY.keys())}")
    return _REGISTRY[name]


def execute_tool(name: str, kwargs: dict[str, Any]) -> Any:
    spec = get_tool(name)
    validated = spec.param_model(**kwargs)
    return spec.func(**validated.model_dump())
