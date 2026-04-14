from tools import register_tool
from tools.models import StringUtilsArgs


@register_tool(StringUtilsArgs, return_type="str")
def string_utils(text: str, operation: str) -> str:
    """Perform a string operation. Supported operations: upper, lower, reverse, count_words."""
    match operation:
        case "upper":
            return text.upper()
        case "lower":
            return text.lower()
        case "reverse":
            return text[::-1]
        case "count_words":
            return str(len(text.split()))
        case _:
            raise ValueError(f"Unknown operation: {operation}")
