"""Shared test helpers. Adapters are tested against real ATS responses saved
in tests/fixtures (trimmed to 3 jobs), served through httpx.MockTransport,
so tests never touch the network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from internradar.http import PoliteClient

FIXTURES = Path(__file__).parent / "fixtures"

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
    def _load(name: str) -> Any:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return _load


@pytest.fixture
def client_for() -> Callable[[Handler], PoliteClient]:
    """Build a PoliteClient whose requests go to `handler` instead of the internet."""

    def _make(handler: Handler) -> PoliteClient:
        return PoliteClient(
            "test@example.com",
            min_interval=0,
            backoff_base=0,
            transport=httpx.MockTransport(handler),
            sleep=lambda _s: None,
        )

    return _make
