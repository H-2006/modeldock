"""CatalogProviderRegistry — resolves a RuntimeBackend to its own live catalog.

Mirrors ``adapters/runtimes/registry.py``'s ``RuntimeRegistry``: built-in live
catalog sources (Hugging Face for LM Studio/llama.cpp) plus third-party
plugins discovered via the ``modeldock.catalog_providers`` entry-point group,
so a package can add live discovery for a new runtime (vLLM, Jan AI,
GPT4All, ...) without any change to ModelDock itself — the same extension
model runtimes already get. See Architecture.md §9/§14.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from modeldock.common.logging import get_logger
from modeldock.domain.model import RuntimeBackend
from modeldock.ports.registry import RegistryPort

# Preferred group name is ``modeldock.model_sources`` (a source *is* a model
# source); ``modeldock.catalog_providers`` is the original name, kept so
# existing plugins keep working. Both are scanned; a backend registered under
# either group is discovered.
_ENTRY_POINT_GROUPS = ("modeldock.model_sources", "modeldock.catalog_providers")

#: Built-in factories: backend -> (cache_dir) -> RegistryPort.
_BUILTIN: Dict[RuntimeBackend, Callable[[Path], RegistryPort]] = {}


def _register_builtins() -> None:
    from modeldock.adapters.registry.huggingface_catalog import HuggingFaceCatalogProvider

    _BUILTIN[RuntimeBackend.LM_STUDIO] = lambda cache_dir: HuggingFaceCatalogProvider(
        cache_dir, RuntimeBackend.LM_STUDIO
    )
    _BUILTIN[RuntimeBackend.LLAMACPP] = lambda cache_dir: HuggingFaceCatalogProvider(
        cache_dir, RuntimeBackend.LLAMACPP
    )


class CatalogProviderRegistry:
    """Resolves a ``RuntimeBackend`` to its own live catalog source, if any.

    Third-party packages register a new backend's live catalog via a
    ``modeldock.catalog_providers`` entry point, named after the backend
    (e.g. ``vllm``), pointing at a callable ``(cache_dir: Path) ->
    RegistryPort`` — a plain function, or a class whose constructor takes
    only ``cache_dir``. Entry points take priority over built-ins, so a
    plugin can also replace the shipped Hugging Face provider for LM
    Studio/llama.cpp if it wants to.

    Security Note
    -------------
    ``ep.load()`` imports third-party code into ModelDock's own process at
    construction time, so installing a catalog-provider plugin grants it
    arbitrary code execution. Pass ``allow_plugins=False`` (what
    ``execution_policy="strict"`` does) to skip discovery entirely.
    """

    def __init__(self, allow_plugins: bool = True) -> None:
        self._logger = get_logger("registry.catalog_provider_registry")
        _register_builtins()
        self._entry_points: Dict[RuntimeBackend, Callable[[Path], RegistryPort]] = {}
        if allow_plugins:
            self._discover_entry_points()
        else:
            self._logger.debug("Catalog provider plugin discovery disabled by execution policy")

    def _discover_entry_points(self) -> None:
        try:
            eps = entry_points()
            discovered: List[Any] = []
            for group_name in _ENTRY_POINT_GROUPS:
                if hasattr(eps, "select"):
                    discovered.extend(eps.select(group=group_name))
                else:
                    discovered.extend(eps.get(group_name, []))
            for ep in discovered:
                try:
                    backend = RuntimeBackend.from_value(ep.name)
                    factory = ep.load()
                    # First group wins on a duplicate registration, so the
                    # preferred ``modeldock.model_sources`` name takes priority.
                    self._entry_points.setdefault(backend, factory)
                except Exception as exc:  # skip bad plugins
                    self._logger.warning("Skipping catalog provider plugin %s: %s", ep.name, exc)
        except Exception as exc:
            self._logger.debug("Entry-point discovery unavailable: %s", exc)

    def get(self, backend: RuntimeBackend, cache_dir: Path) -> Optional[RegistryPort]:
        """Return a live catalog for ``backend``, or None when it has none.

        Building the catalog can fail (no network, no cache yet, a broken
        plugin); that degrades to None rather than raising, so callers can
        treat "no live catalog" and "live catalog unavailable right now" the
        same way — exactly how ``ModelManager._resolve_auto_registry``
        already degrades the general Ollama catalog to bundled.
        """
        factory = self._entry_points.get(backend) or _BUILTIN.get(backend)
        if factory is None:
            return None
        try:
            return factory(cache_dir)
        except Exception as exc:
            self._logger.debug("Catalog provider for %s failed to build (%s)", backend.value, exc)
            return None

    def available_backends(self) -> List[RuntimeBackend]:
        """Return every backend with a registered live catalog (built-in + plugins)."""
        return list(set(_BUILTIN) | set(self._entry_points))


__all__ = ["CatalogProviderRegistry"]
