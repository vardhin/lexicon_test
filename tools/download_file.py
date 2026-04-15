import urllib.request
from pathlib import Path

from tools import register_tool
from tools.models import DownloadFileArgs


@register_tool(DownloadFileArgs, return_type="str")
def download_file(url: str, destination: str) -> str:
    """Download a file from a URL and save it to a local path. Returns the saved path."""
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)
    size = dest.stat().st_size
    return f"Downloaded {url} -> {dest} ({size} bytes)"
