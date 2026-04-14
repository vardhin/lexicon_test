import json
import os
import urllib.parse
import urllib.request

from tools import register_tool
from tools.models import SearchArgs

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8888")


@register_tool(SearchArgs, return_type="str")
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web using SearXNG and return results as text."""
    params = urllib.parse.urlencode({"q": query, "format": "json", "number_of_results": max_results})
    url = f"{SEARXNG_URL}/search?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        data = json.loads(response.read())

    results = []
    for r in data.get("results", [])[:max_results]:
        results.append(f"- {r.get('title', '')}: {r.get('content', '')} ({r.get('url', '')})")
    return "\n".join(results) if results else "No results found."
