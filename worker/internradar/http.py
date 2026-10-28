"""A polite HTTP client: clear User-Agent, 1 request/second per domain,
retries with exponential backoff on 429/5xx, 15 s timeouts, robots.txt checks.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

BOT_NAME = "InternRadar"
MAX_RETRY_AFTER = 60.0  # never sleep longer than this for one retry


class FetchError(Exception):
    """A request failed for good (after retries, or with a non-retryable status)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def user_agent(contact_email: str | None) -> str:
    if contact_email:
        return f"{BOT_NAME}/1.0 (personal job alerts; contact: {contact_email})"
    return f"{BOT_NAME}/1.0 (personal job alerts)"


class PoliteClient:
    def __init__(
        self,
        contact_email: str | None = None,
        *,
        min_interval: float = 1.0,
        timeout: float = 15.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.user_agent = user_agent(contact_email)
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept": "application/json, */*;q=0.8"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, str | None] = {}  # base URL -> robots.txt (None: unreadable)
        self.request_count = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- rate limiting ------------------------------------------------------

    def throttle(self, url: str) -> None:
        """Wait until at least min_interval has passed since the last request to this host."""
        host = urlsplit(url).netloc.lower()
        last = self._last_request.get(host)
        if last is not None:
            wait = last + self._min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.strip().isdigit():
                return min(float(retry_after), MAX_RETRY_AFTER)
        return self._backoff_base * (2**attempt)  # 1s, 2s, 4s, ...

    # -- requests -----------------------------------------------------------

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        last_problem = ""
        for attempt in range(self._max_retries + 1):
            self.throttle(url)
            self.request_count += 1
            try:
                response = self._client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_problem = f"{type(exc).__name__}: {exc}"
                response = None
            else:
                status = response.status_code
                if status < 400:
                    return response
                if status != 429 and status < 500:
                    raise FetchError(f"HTTP {status} from {url}", status=status)
                last_problem = f"HTTP {status}"
            if attempt < self._max_retries:
                delay = self._retry_delay(attempt, response)
                log.info("retrying %s in %.1fs (%s)", url, delay, last_problem)
                self._sleep(delay)
        raise FetchError(f"gave up on {url} after {self._max_retries + 1} tries: {last_problem}")

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self._json(self.request("GET", url, **kwargs), url)

    def post_json(self, url: str, body: Any, **kwargs: Any) -> Any:
        return self._json(self.request("POST", url, json=body, **kwargs), url)

    def get_text(self, url: str, **kwargs: Any) -> str:
        return self.request("GET", url, **kwargs).text

    @staticmethod
    def _json(response: httpx.Response, url: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise FetchError(f"invalid JSON from {url}") from exc

    # -- robots.txt ---------------------------------------------------------

    def allowed_by_robots(self, url: str) -> bool:
        """Check robots.txt before scraping an HTML page.

        Missing robots.txt (4xx) means everything is allowed. If robots.txt
        cannot be fetched at all (5xx / network error) we stay on the safe side
        and treat the site as disallowed for this run.
        """
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            text: str | None
            try:
                text = self.get_text(f"{base}/robots.txt")
            except FetchError as exc:
                if exc.status is not None and 400 <= exc.status < 500:
                    text = ""  # no robots.txt: allow all
                else:
                    log.warning("could not read %s/robots.txt (%s); skipping", base, exc)
                    text = None
            self._robots[base] = text
        text = self._robots[base]
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        return text is not None and robots_allows(text, path)


def robots_allows(robots_txt: str, path: str, agent: str = BOT_NAME) -> bool:
    """RFC 9309: use the group for our agent (else "*"); the longest matching rule
    wins, and Allow wins a tie. Python's urllib.robotparser instead takes the first
    matching rule, so "Disallow: /" then "Allow: /careers" would wrongly block /careers.
    Same logic as robotsAllows() in server/lib/ats.ts.
    """
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    last_was_agent = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        field, sep, value = line.partition(":")
        if not sep:
            continue
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            if not groups or not last_was_agent:
                groups.append(([], []))
            groups[-1][0].append(value.lower())
            last_was_agent = True
        elif field in ("allow", "disallow") and groups:
            if value:
                groups[-1][1].append((field == "allow", value))
            last_was_agent = False

    me = agent.lower()
    mine = [g for g in groups if any(a != "*" and a in me for a in g[0])]
    applicable = mine or [g for g in groups if "*" in g[0]]
    verdict, longest = True, -1
    for _, rules in applicable:
        for allow, rule in rules:
            prefix = rule.split("*", 1)[0].removesuffix("$")
            if path.startswith(prefix) and (
                len(prefix) > longest or (len(prefix) == longest and allow)
            ):
                verdict, longest = allow, len(prefix)
    return verdict
