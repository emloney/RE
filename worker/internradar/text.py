"""Small text helpers: HTML stripping, normalising, date parsing."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from html import unescape

from bs4 import BeautifulSoup

_REMOTE = re.compile(r"\bremote\b|work from home|\bwfh\b", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9+#]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_BLOCK_TAGS = ["p", "div", "li", "ul", "ol", "tr", "h1", "h2", "h3", "h4", "h5", "h6"]


def html_to_text(html: str | None) -> str:
    """Turn job-description HTML into readable plain text.

    Greenhouse double-escapes its HTML (``&lt;div&gt;``), so unescape once
    first when the string has escaped tags but no real ones.
    """
    if not html:
        return ""
    if "<" not in html and "&lt;" in html:
        html = unescape(html)
    soup = BeautifulSoup(html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    # Line breaks after block elements only, so inline tags like <b> stay in the sentence.
    for block in soup.find_all(_BLOCK_TAGS):
        block.append("\n")
    text = soup.get_text()
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def normalise_title(title: str) -> str:
    """Lower-case, punctuation-free title for duplicate detection."""
    return " ".join(_NON_ALNUM.sub(" ", title.lower()).split())


def normalise_location(location: str) -> str:
    return " ".join(_NON_ALNUM.sub(" ", location.lower()).split())


def clean_location(location: str) -> str:
    """Tidy strings like 'Chennai, , India' into 'Chennai, India'."""
    parts = [p.strip() for p in location.split(",")]
    return ", ".join(p for p in parts if p)


def looks_remote(*parts: str | None) -> bool:
    return any(p and _REMOTE.search(p) for p in parts)


def parse_dt(value: str | int | float | None) -> datetime | None:
    """Parse ISO strings, plain dates or epoch milliseconds into aware datetimes."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    try:
        if len(value) == 10:  # "2026-09-18"
            return datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=UTC)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
