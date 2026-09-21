"""Paths for resources that live beside the source or inside a frozen app."""
from __future__ import annotations

import sys
from pathlib import Path


def project_root() -> Path:
    """Return the source root, or PyInstaller's extracted application root."""
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen)
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    """Resolve a bundled or source-tree resource by its repository-relative path."""
    return project_root().joinpath(*parts)
