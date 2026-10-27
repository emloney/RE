"""Loads companies.yaml (and later filters.yaml) from the repo root."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from internradar.models import Company


def repo_root() -> Path:
    """Repo root: INTERNRADAR_HOME if set, else two folders above this package."""
    env = os.environ.get("INTERNRADAR_HOME")
    return Path(env) if env else Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    pass


def load_env() -> None:
    load_dotenv(repo_root() / ".env")


def contact_email() -> str | None:
    return os.environ.get("CONTACT_EMAIL") or None


def load_companies(path: Path | None = None) -> list[Company]:
    path = path or repo_root() / "companies.yaml"
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except FileNotFoundError as exc:
        raise ConfigError(f"{path} not found") from exc
    if not isinstance(raw, list):
        raise ConfigError(f"{path} must be a list of companies")

    companies: list[Company] = []
    problems: list[str] = []
    for i, entry in enumerate(raw, start=1):
        label = entry.get("name", f"entry {i}") if isinstance(entry, dict) else f"entry {i}"
        try:
            companies.append(Company.model_validate(entry))
        except ValidationError as exc:
            problems.append(f"{label}: {exc.errors()[0]['msg']}")

    names = [c.name.lower() for c in companies]
    problems += [
        f"duplicate company name: {n}" for n in sorted({n for n in names if names.count(n) > 1})
    ]
    if problems:
        raise ConfigError(f"{path} has problems:\n  " + "\n  ".join(problems))
    return companies
