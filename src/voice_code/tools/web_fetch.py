"""WebFetch tool — 抓取网页内容"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def web_fetch(url: str) -> str:
    """Fetch content from a URL and return it as markdown.

    Use this tool to gather information from the web. The content is
    converted to markdown for readability.

    IMPORTANT: Only fetch URLs that are relevant to the user's programming
    task (documentation, API references, etc.).

    Args:
        url: The URL to fetch. Must be a fully-formed valid URL.
    """
    if not url.startswith(("http://", "https://")):
        return f"<tool_use_error>Error: Invalid URL: {url}</tool_use_error>"

    try:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "reasoning-agent/0.1"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return f"<tool_use_error>Error fetching URL: {e}</tool_use_error>"
    except Exception as e:
        return f"<tool_use_error>Error: {e}</tool_use_error>"

    # Strip HTML tags (simple approach)
    text = _strip_html(content)
    if len(text) > 10000:
        text = text[:10000] + "\n... (truncated)"

    return text


def _strip_html(html: str) -> str:
    """简单 HTML 标签剥离。"""
    import re
    # Remove scripts and styles
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    # Decode entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    return text.strip()


web_fetch.metadata = {
    "is_readonly": True,
    "is_concurrency_safe": True,
    "max_result_chars": 50_000,
}
