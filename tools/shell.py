import re
import subprocess

from tools import register_tool
from tools.models import ShellArgs

_RM_PATTERN = re.compile(r"(?:^|[;&|`\s])rm\s", re.MULTILINE)


@register_tool(ShellArgs, return_type="str")
def shell(command: str) -> str:
    """Execute a zsh shell command and return its stdout. rm commands are not allowed."""
    if _RM_PATTERN.search(command):
        raise ValueError("rm commands are not allowed.")
    result = subprocess.run(
        ["zsh", "-c", command],
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout
    if result.returncode != 0:
        output += result.stderr
    return output
