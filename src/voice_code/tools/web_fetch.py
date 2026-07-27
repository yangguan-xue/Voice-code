"""WebFetch tool — 抓取网页内容"""

from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request

from langchain_core.tools import tool

from voice_code import __version__

logger = logging.getLogger(__name__)
USER_AGENT = f"voice-code/{__version__}"

_MAX_RESPONSE_BYTES = 1_000_000
_MAX_REDIRECTS = 5
_ALLOWED_CONTENT_TYPES = ("text/", "application/json", "application/xml")


def _validate_public_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must use http or https and include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not allowed")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("Localhost URLs are not allowed")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443)}
    except socket.gaierror as exc:
        raise ValueError(f"Unable to resolve URL hostname: {host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError(f"URL resolves to a non-public address: {ip}")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__()
        self.redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        self.redirect_count += 1
        if self.redirect_count > _MAX_REDIRECTS:
            raise urllib.error.HTTPError(newurl, code, "Too many redirects", headers, fp)
        target = urllib.parse.urljoin(req.full_url, newurl)
        _validate_public_url(target)
        return super().redirect_request(req, fp, code, msg, headers, target)


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
    try:
        _validate_public_url(url)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT},
        )
        opener = urllib.request.build_opener(_SafeRedirectHandler())
        with opener.open(req, timeout=10) as resp:
            _validate_public_url(resp.geturl())
            content_type = resp.headers.get_content_type().lower()
            if not any(content_type.startswith(prefix) for prefix in _ALLOWED_CONTENT_TYPES):
                return (
                    "<tool_use_error>Error: Unsupported response content type: "
                    f"{content_type}</tool_use_error>"
                )
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                return "<tool_use_error>Error: Response exceeded 1 MB limit</tool_use_error>"
            charset = resp.headers.get_content_charset() or "utf-8"
            content = raw.decode(charset, errors="replace")
    except (ValueError, urllib.error.URLError, urllib.error.HTTPError) as e:
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
object.__setattr__(web_fetch, "USER_AGENT", USER_AGENT)
