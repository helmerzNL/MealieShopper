"""Prepared public prices, separate from the personal authentication database."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from mealieshopper import auth

from .protocol import validate_manifest
from .reader import Snapshot
from .store import GenerationStore

__all__: list[str] = ["GenerationStore", "Snapshot", "generation", "lease", "refresh_if_due", "reset", "validate_manifest"]


def _store() -> GenerationStore:
    return GenerationStore(auth.data_dir() / "shared-prices")


async def refresh_if_due(force: bool = False) -> dict[str, Any]:
    return await _store().refresh_if_due(force)


def generation(store: str = "ah") -> str | None:
    return _store().generation(store)


@contextmanager
def lease(store: str = "ah") -> Iterator[Snapshot | None]:
    with _store().lease(store) as snapshot:
        yield snapshot


def reset() -> None:
    """Selection is read from the atomic pointer, so no cached state needs invalidation."""
