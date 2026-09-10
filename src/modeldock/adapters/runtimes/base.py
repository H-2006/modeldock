"""BaseRuntime — shared logic for concrete runtime adapters.

Provides alias resolution, availability caching, and error normalization so
each concrete runtime only implements runtime-specific calls. See Architecture.md §4.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, List, Optional, Sequence

from modeldock.common.errors import (
    ModelNotInstalledError,
    RuntimeUnavailableError,
)
from modeldock.common.logging import get_logger
from modeldock.domain.model import (
    Capability,
    Category,
    Device,
    ModelRef,
    ModelSpec,
    RuntimeBackend,
    RuntimeStatus,
)
from modeldock.ports.runtime import PullResult, RunResult

_AVAILABILITY_TTL = 5.0
# Discovery probes are best-effort and run before any real work, so they are
# kept short: a refused connection returns immediately, and this only bounds
# the pathological case where packets are dropped rather than refused.
_DISCOVERY_TIMEOUT = 0.5


def normalize_host(host: str) -> str:
    """Return ``host`` as a bare scheme://authority base URL.

    Accepts what users actually type — a bare ``localhost:1234``, a trailing
    slash, or a URL that already carries the OpenAI-compatible ``/v1`` suffix —
    and normalizes it so callers can append their own path without producing
    ``//v1`` or ``/v1/v1``.
    """
    text = host.strip().rstrip("/")
    if not text:
        return text
    if "://" not in text:
        text = f"http://{text}"
    if text.endswith("/v1"):
        text = text[: -len("/v1")]
    return text


class BaseRuntime:
    """Mixin/base providing shared RuntimePort behavior.

    Concrete runtimes subclass this and implement the ``_``-prefixed hooks.

    Security Note
    -------------
    All data received from runtime APIs (HTTP responses, SDK return values)
    crosses a trust boundary and is **untrusted by definition**.  Concrete
    adapters must validate and sanitise responses before constructing domain
    objects.  Never pass raw runtime output to ``exec()``, ``eval()``,
    ``subprocess``, or any code-execution primitive.  See SECURITY.md for
    the full prompt-injection guidance.

    ``get_model_client`` and ``run`` are the *execution* boundary: past them a
    runtime loads model weights and executes them as native code with the
    invoking user's full privileges.  Adapters declare which side of that
    boundary they sit on via ``executes_in_process``; the policy decision
    itself lives in ``core/execution.py``, never in an adapter.
    """

    backend: RuntimeBackend = RuntimeBackend.OLLAMA

    #: True when this adapter loads model weights into ModelDock's own Python
    #: process (a native extension or ctypes binding) instead of talking to a
    #: separate runtime server. Consulted by ``ExecutionGuard``: once native
    #: code is mapped into this process, Python can no longer restrain it, so
    #: ``execution_policy="strict"`` refuses these adapters outright.
    executes_in_process: bool = False

    def __init__(self) -> None:
        self._logger = get_logger(f"runtime.{self.backend.value}")
        self._availability: Optional[bool] = None
        self._availability_checked_at: float = 0.0
        self._resolved_host: Optional[str] = None

    # --- host resolution ---------------------------------------------------

    def resolve_host(
        self,
        explicit: Optional[str] = None,
        env_vars: Sequence[str] = (),
        default: str = "",
        candidates: Sequence[str] = (),
        probe_path: str = "",
    ) -> str:
        """Resolve this runtime's base URL, discovering it when unconfigured.

        Precedence, highest first:

        1. ``explicit`` — a host passed to the adapter (from config or code)
        2. ``env_vars`` — the first environment variable that is set
        3. auto-discovery — the first reachable entry in ``candidates``
        4. ``default``

        Discovery only runs when nothing is configured, so a user who names a
        host never pays for a probe and never has their choice second-guessed.
        Every result is normalized, so callers can rely on a bare
        ``scheme://authority`` base URL. The resolved value is cached for the
        lifetime of the adapter.
        """
        if self._resolved_host is not None:
            return self._resolved_host

        resolved = self._first_configured(explicit, env_vars)
        if resolved is None and candidates and probe_path:
            resolved = self._discover_host(candidates, probe_path)
        if resolved is None:
            resolved = default

        self._resolved_host = normalize_host(resolved)
        return self._resolved_host

    @staticmethod
    def _first_configured(explicit: Optional[str], env_vars: Iterable[str]) -> Optional[str]:
        """Return the first explicitly configured host, or None."""
        if explicit:
            return explicit
        for name in env_vars:
            value = os.environ.get(name)
            if value:
                return value
        return None

    def _discover_host(self, candidates: Sequence[str], probe_path: str) -> Optional[str]:
        """Return the first candidate answering ``probe_path``, or None.

        Used for unconfigured setups where the server may not be on the
        conventional loopback address — a container reaching the host, or a
        remapped port. Probes are short so a fully offline machine is not held
        up for long.
        """
        import httpx

        for candidate in candidates:
            base = normalize_host(candidate)
            if not base:
                continue
            try:
                response = httpx.get(f"{base}{probe_path}", timeout=_DISCOVERY_TIMEOUT)
            except Exception:  # nosec B112 - unreachable candidate, try the next
                continue
            if response.status_code == 200:
                self._logger.debug("Discovered %s server at %s", self.backend.value, base)
                return base
        return None

    def clear_host_cache(self) -> None:
        """Forget the resolved host so the next call re-resolves it."""
        self._resolved_host = None

    # --- shared, final behavior -------------------------------------------

    def is_available(self) -> bool:
        """Return cached availability, refreshing if stale."""
        import time

        now = time.monotonic()
        if self._availability is None or (now - self._availability_checked_at) > _AVAILABILITY_TTL:
            self._availability = self._check_available()
            self._availability_checked_at = now
        return self._availability

    def is_installed(self, ref: ModelRef) -> bool:
        """Presence check via ``list_installed``."""
        return any(
            existing.name == ref.name and existing.tag == ref.tag
            for existing in self.list_installed()
        )

    def default_tag_for(self, spec: ModelSpec) -> str:
        """Resolve the default tag for a spec (runtime may override)."""
        return spec.default_tag

    def status(self) -> RuntimeStatus:
        """Report availability and execution device.

        The base default reports availability and an ``UNKNOWN`` device; concrete
        runtimes override ``_detect_device`` to populate the real device.
        """
        available = self.is_available()
        device = self._detect_device() if available else Device.UNKNOWN
        return RuntimeStatus(
            backend=self.backend,
            available=available,
            device=device,
        )

    def _detect_device(self) -> Device:
        """Best-effort device detection; ``UNKNOWN`` by default.

        Concrete runtimes override this to inspect loaded-model metadata.
        """
        return Device.UNKNOWN

    # --- backend-specific model suggestions --------------------------------

    def models_for_category(self, category: Category) -> List[ModelRef]:
        """Return backend-native models for ``category``.

        Empty by default, meaning "this runtime has no naming of its own, use
        the shared catalog". Runtimes whose model identifiers differ from the
        catalog's (LM Studio addresses models by Hugging Face coordinates, not
        Ollama tags) override this so category installs resolve to names the
        runtime can actually pull. See issue #19.
        """
        return []

    def models_for_capability(self, capability: Capability) -> List[ModelRef]:
        """Return backend-native models exposing ``capability`` (empty by default)."""
        return []

    def pull(self, ref: ModelRef, progress: Any = None) -> PullResult:
        """Normalized pull: checks availability, delegates to ``_do_pull``.

        Idempotent: if the model is already installed, return a successful
        ``PullResult`` without re-downloading. This keeps repeated
        ``install()``/``load()`` calls cheap and avoids redundant network
        fetches (core "package manager" behavior).
        """
        if not self.is_available():
            raise RuntimeUnavailableError(
                self.backend.value,
                hint="Install the runtime or choose another backend.",
            )
        if self.is_installed(ref):
            self._logger.info("Already installed, skipping pull: %s", ref.qualified_name())
            return PullResult(ref=ref, success=True, already_present=True)
        self._logger.info("Pulling %s", ref.qualified_name())
        try:
            return self._do_pull(ref, progress)
        except RuntimeUnavailableError:
            raise
        except Exception as exc:  # normalize unexpected failures
            self._logger.error("Pull failed: %s", exc)
            return PullResult(ref=ref, success=False, error=str(exc))

    def get_model_client(self, ref: ModelRef) -> Any:
        """Return a client, ensuring the model is installed."""
        if not self.is_installed(ref):
            raise ModelNotInstalledError(ref.qualified_name())
        return self._get_client(ref)

    # --- hooks for concrete runtimes --------------------------------------

    def _check_available(self) -> bool:
        """Return whether the runtime is installed/reachable."""
        raise NotImplementedError

    def list_installed(self) -> List[ModelRef]:
        """Return locally installed models."""
        raise NotImplementedError

    def _do_pull(self, ref: ModelRef, progress: Any) -> PullResult:
        """Perform the actual pull; return a ``PullResult``."""
        raise NotImplementedError

    def _get_client(self, ref: ModelRef) -> Any:
        """Return a runtime-native client for ``ref``."""
        raise NotImplementedError

    def remove(self, ref: ModelRef) -> None:
        """Uninstall ``ref``."""
        raise NotImplementedError

    def run(self, ref: ModelRef, prompt: Optional[str] = None, **opts: Any) -> RunResult:
        """Run an interactive session; unsupported by default.

        Concrete runtimes override this when they can drive an interactive
        session. The base default signals "not supported" so callers get a
        clear, actionable error rather than a silent no-op.
        """
        raise NotImplementedError(
            f"Runtime {self.backend.value!r} does not support interactive run sessions."
        )


__all__ = ["BaseRuntime"]
