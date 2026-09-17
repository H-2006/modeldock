"""RuntimePort — the contract every model runtime adapter must honor.

Pure interface (typing.Protocol). No implementation, no I/O here.
See Architecture.md §4 for the design rationale.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Protocol, runtime_checkable

from modeldock.domain.model import (
    Capability,
    Category,
    ModelRef,
    ModelSpec,
    RuntimeBackend,
    RuntimeStatus,
)


@runtime_checkable
class RuntimePort(Protocol):
    """Abstraction over a local model runtime (Ollama, LM Studio, ...).

    Security Note
    -------------
    Data flowing through this port originates from external runtime processes
    and HTTP APIs.  It is **untrusted by definition** and may contain
    prompt-injection payloads or adversarial strings.  Implementations must
    validate and sanitise all responses before constructing domain objects.
    Consumers must never execute, ``eval()``, or otherwise treat port output
    as trusted instructions.  See SECURITY.md for full guidance.

    ``get_model_client`` and ``run`` additionally cross an *execution*
    boundary: they cause a runtime to load a model artifact and execute it as
    native code with the invoking user's full privileges.  ModelDock cannot
    sandbox that; it can only warn or refuse, which ``core.execution`` does on
    every caller's behalf.  See SECURITY.md, "Model Execution & Native Code".
    """

    @property
    def backend(self) -> RuntimeBackend:
        """Identify the runtime backend."""
        ...

    def is_available(self) -> bool:
        """Return True if the runtime is installed and reachable."""
        ...

    def list_installed(self) -> List[ModelRef]:
        """Return models present locally in this runtime."""
        ...

    def is_installed(self, ref: ModelRef) -> bool:
        """Return True if ``ref`` is present locally."""
        ...

    def pull(self, ref: ModelRef, progress: Any = None) -> PullResult:
        """Download/install ``ref``, reporting via ``progress`` (ProgressPort)."""
        ...

    def remove(self, ref: ModelRef) -> None:
        """Uninstall ``ref`` from the runtime."""
        ...

    def get_model_client(self, ref: ModelRef) -> Any:
        """Return a ready-to-use, runtime-native client for ``ref``."""
        ...

    def default_tag_for(self, spec: ModelSpec) -> str:
        """Resolve the default variant tag for a model spec."""
        ...

    def models_for_category(self, category: Category) -> List[ModelRef]:
        """Return backend-native models for ``category``.

        Empty means "this runtime uses the shared catalog's names". Runtimes
        whose identifiers differ from the catalog's — LM Studio addresses models
        by Hugging Face coordinates rather than Ollama tags — return their own
        list so category installs resolve to names the runtime can pull.
        ``BaseRuntime`` supplies the empty default.
        """
        raise NotImplementedError

    def models_for_capability(self, capability: Capability) -> List[ModelRef]:
        """Return backend-native models exposing ``capability`` (empty by default)."""
        ...

    def status(self) -> RuntimeStatus:
        """Report runtime availability and the execution device (GPU/CPU)."""
        ...

    def run(self, ref: ModelRef, prompt: Optional[str] = None, **opts: Any) -> RunResult:
        """Run an interactive session for ``ref``, streaming output.

        ``prompt`` is an optional initial prompt; when ``None`` the runtime
        drops into an interactive read-eval-print loop. Output is streamed to
        stdout (or the configured ``ProgressPort``). Runtimes that cannot
        support interactive sessions raise ``NotImplementedError``.
        """
        ...


class PullResult:
    """Result of a pull/install operation (returned by ``RuntimePort.pull``)."""

    def __init__(
        self,
        ref: ModelRef,
        success: bool,
        path: Optional[Path] = None,
        sha256: Optional[str] = None,
        bytes_downloaded: int = 0,
        error: Optional[str] = None,
        already_present: bool = False,
    ) -> None:
        self.ref = ref
        self.success = success
        self.path = path
        self.sha256 = sha256
        self.bytes_downloaded = bytes_downloaded
        self.error = error
        self.already_present = already_present

    def __repr__(self) -> str:
        state = "ok" if self.success else f"failed({self.error})"
        return f"PullResult({self.ref.qualified_name()}, {state})"


class RunResult:
    """Result of a ``run`` session (returned by ``RuntimePort.run``)."""

    def __init__(
        self,
        ref: ModelRef,
        success: bool,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        error: Optional[str] = None,
    ) -> None:
        self.ref = ref
        self.success = success
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.error = error

    def __repr__(self) -> str:
        state = "ok" if self.success else f"failed({self.error})"
        return f"RunResult({self.ref.qualified_name()}, {state})"
