"""Minimal Telegram Bot API client for sending messages.

- parse_mode HTML (MarkdownV2 escaping breaks constantly); callers escape with esc().
- At most ~1 message per second to a chat (Telegram's own guidance).
- On 429, waits for the retry_after Telegram returns, then tries again.
- The bot token is part of every API URL, so errors here never include the URL,
  and httpx's request logging must stay off (see __main__).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from html import escape
from typing import Any

import httpx

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
MAX_TEXT = 4096  # characters, after entity parsing
MIN_INTERVAL = 1.1  # seconds between messages to the same chat
MAX_RETRY_AFTER = 120.0


def esc(text: str) -> str:
    """Escape text for Telegram HTML: &, < and > become entities."""
    return escape(text, quote=False)


def esc_attr(text: str) -> str:
    """Escape a value for an HTML attribute such as href."""
    return escape(text, quote=True)


class TelegramError(Exception):
    pass


# One button: (label, callback_data). callback_data must be 1-64 bytes.
Button = tuple[str, str]


class TelegramClient:
    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        max_attempts: int = 4,
    ) -> None:
        self._base = f"{API}/bot{token}"
        self.chat_id = chat_id
        self._client = httpx.Client(timeout=15.0, transport=transport)
        self._sleep = sleep
        self._clock = clock
        self._last_send: float | None = None
        self._max_attempts = max_attempts

    def close(self) -> None:
        self._client.close()

    def call(self, method: str, payload: dict[str, Any]) -> Any:
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post(f"{self._base}/{method}", json=payload)
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                # str(exc) can contain the URL, and with it the token: keep only the type.
                problem = f"{type(exc).__name__} calling {method}"
                data = None
            else:
                if data.get("ok"):
                    return data.get("result")
                problem = f"{method} failed: {data.get('error_code')} {data.get('description')}"
                retry_after = (data.get("parameters") or {}).get("retry_after")
                if data.get("error_code") == 429 and retry_after is not None:
                    if attempt < self._max_attempts:
                        wait = min(float(retry_after), MAX_RETRY_AFTER)
                        log.info("Telegram rate limit: waiting %.0fs", wait)
                        self._sleep(wait)
                        continue
                elif data.get("error_code", 500) < 500:
                    raise TelegramError(problem)  # e.g. 400 bad HTML: retrying won't help
            if attempt < self._max_attempts:
                self._sleep(2.0**attempt)
        raise TelegramError(problem)

    def send(self, text: str, buttons: list[Button] | None = None) -> int:
        """Send one HTML message to the configured chat. Returns the message id."""
        if len(text) > MAX_TEXT:
            raise TelegramError(f"message is {len(text)} characters (max {MAX_TEXT})")
        if self._last_send is not None:
            wait = self._last_send + MIN_INTERVAL - self._clock()
            if wait > 0:
                self._sleep(wait)
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if buttons:
            for _, data in buttons:
                if not 1 <= len(data.encode("utf-8")) <= 64:
                    raise TelegramError(f"callback_data must be 1-64 bytes: {data!r}")
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in buttons]
                ]
            }
        try:
            result = self.call("sendMessage", payload)
        finally:
            self._last_send = self._clock()
        return int(result["message_id"])
