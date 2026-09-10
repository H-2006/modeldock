"""Shared CLI construction helpers.

Keeps the ``--backend`` option honored in exactly one place so every command
resolves it the same way. See Architecture.md §6.
"""

from __future__ import annotations

from typing import Optional

from modeldock.cli.console import print_warning
from modeldock.common.errors import ConfigError
from modeldock.core.manager import ModelManager
from modeldock.domain.model import RuntimeBackend


def resolve_backend(backend: Optional[str]) -> Optional[RuntimeBackend]:
    """Resolve a ``--backend`` string, raising a friendly error when unknown."""
    if backend is None:
        return None
    try:
        return RuntimeBackend.from_value(backend)
    except ValueError as exc:
        supported = ", ".join(member.value for member in RuntimeBackend)
        raise ConfigError(f"Unknown backend {backend!r}; supported backends: {supported}") from exc


def manager_for(backend: Optional[str] = None) -> ModelManager:
    """Build a ``ModelManager`` for the requested backend (config default if None).

    Supplies the CLI's warning channel: the execution policy itself is decided
    in ``core``, but only an interactive front end should print to the user, so
    the library stays silent unless a caller opts in like this.
    """
    resolved = resolve_backend(backend)
    if resolved is None:
        return ModelManager(notify=print_warning)
    return ModelManager(backend=resolved, notify=print_warning)


__all__ = ["manager_for", "resolve_backend"]
