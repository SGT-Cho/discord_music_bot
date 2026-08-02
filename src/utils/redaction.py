"""Helpers for keeping user input and signed URLs out of logs."""

import re
from urllib.parse import urlsplit, urlunsplit


_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")


def redact_input(value: object, limit: int = 120) -> str:
    """Return a log-safe representation of a URL, query, or arbitrary value."""
    if value is None:
        return "None"

    text = str(value)
    def _redact_url(match: re.Match[str]) -> str:
        try:
            parts = urlsplit(match.group(0))
        except ValueError:
            return match.group(0)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    text = _URL_PATTERN.sub(_redact_url, text)

    if len(text) > limit:
        return f"{text[:limit]}..."
    return text
