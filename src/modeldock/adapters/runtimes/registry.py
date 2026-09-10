"""RuntimeRegistry - discovers and maps runtimes to factories.

Supports entry-point discovery (third-party plugins) and built-in adapters.
See Architecture.md S4/S14.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Callable, Dict, List, cast

from modeldock.common.logging import get_logger
from modeldock.domain.model import RuntimeBackend
from modeldock.ports.runtime import RuntimePort

# Built-in registry: first-party adapters shipped in-repo.
_BUILTIN: Dict[RuntimeBackend, Callable[[], RuntimePort]] = {}

#: Our own distribution name. ModelDock advertises its ``ollama`` adapter as a
#: ``modeldock.runtimes`` entry point, so discovery legitimately finds a
#: first-party entry point on every run.
_OWN_DISTRIBUTION = "modeldock"


def _distribution_name(ep: Any) -> str:
    """Return the distribution that provides ``ep``, or "" when unknown.

    ``EntryPoint.dist`` does not exist at all before Python 3.10, and even
    where it does it is only populated for entry points obtained from
    ``entry_points()``. This degrades to "" rather than asserting provenance it
    cannot establish.
    """
    return str(getattr(getattr(ep, "dist", None), "name", "") or "")


def _target_root_package(ep: Any) -> str:
    """Return the top-level package ``ep`` resolves into, or "".

    ``"modeldock.adapters.runtimes.ollama:OllamaRuntime"`` -> ``"modeldock"``.
    """
    module = str(getattr(ep, "value", "") or "").split(":", 1)[0]
    return module.strip().split(".", 1)[0]


def _is_first_party(ep: Any) -> bool:
    """Whether ``ep`` was advertised by ModelDock itself.

    The distribution name is exact, so it is preferred where available. On
    Python 3.9 ``EntryPoint`` carries no distribution at all, so fall back to
    where the entry point actually points: one resolving into the ``modeldock``
    package is our own registration, not a third-party override. Without this
    fallback every 3.9 invocation warns about ModelDock's own ``ollama`` entry
    point — exactly the false alarm this check exists to prevent.
    """
    distribution = _distribution_name(ep)
    if distribution:
        return distribution.replace("_", "-").lower() == _OWN_DISTRIBUTION
    return _target_root_package(ep) == _OWN_DISTRIBUTION


def _register_builtins() -> None:
    from modeldock.adapters.runtimes.gpt4all import Gpt4AllRuntime
    from modeldock.adapters.runtimes.jan import JanRuntime
    from modeldock.adapters.runtimes.llamacpp import LlamaCppRuntime
    from modeldock.adapters.runtimes.lmstudio import LMStudioRuntime
    from modeldock.adapters.runtimes.ollama import OllamaRuntime
    from modeldock.adapters.runtimes.vllm import VllmRuntime

    _BUILTIN[RuntimeBackend.OLLAMA] = lambda: OllamaRuntime()
    _BUILTIN[RuntimeBackend.LM_STUDIO] = lambda: LMStudioRuntime()
    _BUILTIN[RuntimeBackend.LLAMACPP] = lambda: LlamaCppRuntime()
    _BUILTIN[RuntimeBackend.JAN] = lambda: JanRuntime()
    _BUILTIN[RuntimeBackend.GPT4ALL] = lambda: Gpt4AllRuntime()
    _BUILTIN[RuntimeBackend.VLLM] = lambda: VllmRuntime()


class RuntimeRegistry:
    """Resolves a RuntimeBackend to a runtime instance.

    Security Note
    -------------
    Entry-point discovery imports and instantiates code from any installed
    distribution advertising ``modeldock.runtimes``, inside ModelDock's own
    process, at construction time. Installing such a package is equivalent to
    granting it arbitrary code execution. Pass ``allow_plugins=False`` (what
    ``execution_policy="strict"`` does) for a built-ins-only registry.
    """

    def __init__(self, allow_plugins: bool = True) -> None:
        self._logger = get_logger("runtime.registry")
        _register_builtins()
        self._entry_points: Dict[RuntimeBackend, Callable[[], RuntimePort]] = {}
        if allow_plugins:
            self._discover_entry_points()
        else:
            # Discovery imports and instantiates third-party code inside this
            # process. Skipping it entirely is the enforcement half of
            # ``execution_policy="strict"`` — see core/execution.py.
            self._logger.debug("Runtime plugin discovery disabled by execution policy")

    def _discover_entry_points(self) -> None:
        try:
            eps = entry_points()
            if hasattr(eps, "select"):
                group: Any = eps.select(group="modeldock.runtimes")
            else:
                group = list(eps.get("modeldock.runtimes", []))
            for ep in group:
                try:
                    backend = RuntimeBackend.from_value(ep.name)
                    runtime_cls = ep.load()
                    loaded = cast(RuntimePort, runtime_cls())
                    self._entry_points[backend] = self._make_factory(loaded)
                    # Loading a plugin runs third-party code in this process,
                    # and an entry point named after a built-in backend
                    # silently displaces the first-party adapter. Record the
                    # shadowing case loudly, because nothing else reveals that
                    # the shipped adapter is no longer the one in use.
                    #
                    # ModelDock registers its own ``ollama`` runtime this way
                    # (pyproject.toml), so provenance is checked before
                    # warning: a first-party entry point re-registering a
                    # first-party adapter is not a security event, and warning
                    # on every invocation would teach users to ignore the one
                    # that matters.
                    if _is_first_party(ep):
                        self._logger.debug("Registered first-party runtime entry point %r", ep.name)
                    elif backend in _BUILTIN:
                        self._logger.warning(
                            "Runtime plugin %r from %r replaces the built-in %s adapter",
                            ep.name,
                            _distribution_name(ep) or "an unknown distribution",
                            backend.value,
                        )
                    else:
                        self._logger.info(
                            "Loaded third-party runtime plugin %r for backend %s",
                            ep.name,
                            backend.value,
                        )
                except Exception as exc:  # skip bad plugins
                    self._logger.warning("Skipping runtime plugin %s: %s", ep.name, exc)
        except Exception as exc:
            self._logger.debug("Entry-point discovery unavailable: %s", exc)

    def get(
        self,
        backend: RuntimeBackend,
        host: str | None = None,
        gpu_layers: int | None = None,
        models_dir: Path | None = None,
    ) -> RuntimePort:
        """Return a runtime instance for backend (entry points win).

        ``host`` is forwarded to any adapter that supports a host override, so
        a configured host always applies (clients are built lazily per
        instance). ``gpu_layers`` is forwarded the same way to adapters that
        support it (currently llama.cpp). Adapters that do not take one are
        left untouched.
        """
        factory = self._entry_points.get(backend) or _BUILTIN.get(backend)
        if factory is None:
            raise KeyError(f"No runtime registered for backend {backend.value!r}")
        runtime = factory()
        if host is not None and hasattr(runtime, "_host"):
            runtime._host = host
            # A host set after construction must invalidate any host the
            # adapter already resolved and cached.
            clear = getattr(runtime, "clear_host_cache", None)
            if callable(clear):
                clear()
        if gpu_layers is not None and hasattr(runtime, "_gpu_layers"):
            runtime._gpu_layers = gpu_layers
        if models_dir is not None and hasattr(runtime, "_models_dir"):
            runtime._models_dir = models_dir
        return runtime

    @staticmethod
    def _make_factory(instance: RuntimePort) -> Callable[[], RuntimePort]:
        """Wrap an already-built instance in a no-arg factory."""
        return lambda: instance

    def available_backends(self) -> List[RuntimeBackend]:
        """Return all known backends (built-in + discovered)."""
        known = set(_BUILTIN) | set(self._entry_points)
        return list(known)

    def detect_available(self) -> List[RuntimeBackend]:
        """Return backends whose runtime is currently installed/reachable."""
        result: List[RuntimeBackend] = []
        for backend in self.available_backends():
            try:
                runtime = self.get(backend)
                if runtime.is_available():
                    result.append(backend)
            except Exception as exc:  # nosec B112 - one bad adapter must not hide the rest
                self._logger.warning("Backend %s failed to probe: %s", backend.value, exc)
                continue
        return result


__all__ = ["RuntimeRegistry"]
