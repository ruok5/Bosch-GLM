"""Bosch GLM rangefinder companion app."""
from __future__ import annotations

import tomllib
from pathlib import Path


def _read_version() -> str:
    """Read version from pyproject.toml — single source of truth per
    .claude/versioning.md. Falls back to importlib.metadata if the source
    pyproject is missing (e.g. installed wheel only)."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if pyproject.is_file():
        with pyproject.open("rb") as f:
            return tomllib.load(f)["project"]["version"]
    try:
        from importlib.metadata import version
        return version("bosch-glm")
    except Exception:
        return "0.0.0"


__version__ = _read_version()
