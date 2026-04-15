import json
import os
import urllib.parse
import urllib.request

from tools import register_tool
from tools.models import ImageSearchArgs

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8888")


@register_tool(ImageSearchArgs, return_type="str")
def image_search(query: str, max_results: int = 3) -> str:
    """Search for images and return direct downloadable image URLs (not page links)."""
    params = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "categories": "images",
        "number_of_results": max_results,
    })
    url = f"{SEARXNG_URL}/search?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read())

    urls = []
    for r in data.get("results", [])[:max_results]:
        img_url = r.get("img_src") or r.get("thumbnail_src")
        if img_url:
            urls.append(img_url)

    if not urls:
        return "No image results found."
    return "\n".join(urls)
