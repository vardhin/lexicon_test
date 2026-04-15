import subprocess
from pathlib import Path

from tools import register_tool
from tools.models import OpenFileArgs

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".svg"}
_TEXT_EXTS = {".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml", ".csv", ".log", ".sh"}


@register_tool(OpenFileArgs, return_type="str")
def open_file(path: str) -> str:
    """Open a file. Images are displayed with feh. PDFs are read with pdftotext. Text files return their content."""
    p = Path(path)
    if not p.exists():
        return f"File not found: {path}"

    ext = p.suffix.lower()

    if ext in _IMAGE_EXTS:
        subprocess.Popen(["feh", "--auto-zoom", "--title", p.name, str(p)])
        return f"Opened image: {path}"

    if ext == ".pdf":
        result = subprocess.run(["pdftotext", str(p), "-"], capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return f"pdftotext error: {result.stderr.strip()}"
        text = result.stdout.strip()
        return text[:4000] + ("\n[truncated]" if len(text) > 4000 else "")

    if ext in _TEXT_EXTS or p.stat().st_size < 1_000_000:
        try:
            text = p.read_text(errors="replace")
            return text[:4000] + ("\n[truncated]" if len(text) > 4000 else "")
        except Exception as e:
            return f"Could not read file: {e}"

    return f"Unsupported file type: {ext}"
