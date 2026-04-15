from tools import register_tool, get_all_tools
from tools.models import ListToolsArgs


@register_tool(ListToolsArgs, return_type="str")
def list_tools() -> str:
    """List all available tools with their names, descriptions, and parameters."""
    tools = get_all_tools()
    lines = []
    for name, spec in tools.items():
        fields = spec.param_model.model_fields
        params = ", ".join(
            f"{k}: {v.annotation.__name__ if hasattr(v.annotation, '__name__') else v.annotation}"
            for k, v in fields.items()
        ) if fields else "none"
        lines.append(f"- {name}({params}): {spec.description}")
    return "\n".join(lines)
