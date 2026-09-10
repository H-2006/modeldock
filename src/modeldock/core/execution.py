"""ExecutionGuard — the one place ModelDock's execution policy is applied.

Loading a model is not like loading a data file. Every runtime ModelDock
drives ultimately hands the weights to native machine code — llama.cpp's GGUF
loader, an Ollama server, a Python extension module — which then runs with the
invoking user's full privileges. ModelDock cannot sandbox that: a separate
runtime server is outside this process entirely, and a native extension loaded
into this process is past the point where Python can restrain it.

What ModelDock *can* do honestly is refuse, and say so. This module holds that
decision so ``load`` and ``run`` cannot drift apart, and so no adapter has to
re-implement it. See SECURITY.md, "Model Execution & Native Code", and
Architecture.md §4.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from modeldock.common.errors import ExecutionPolicyError
from modeldock.common.logging import get_logger
from modeldock.domain.model import ModelRef

#: Policy values, in increasing order of restriction. Mirrors
#: ``Settings.execution_policy``; validated there, not here.
UNRESTRICTED = "unrestricted"
WARN = "warn"
STRICT = "strict"


class ExecutionGuard:
    """Applies the configured execution policy at the execution boundary.

    Constructed once per ``ModelManager`` and consulted immediately before a
    model is handed to a runtime. The guard deliberately does no I/O of its
    own beyond logging: a user-visible channel is supplied by the caller
    through ``notify``, so the library stays quiet by default and only the CLI
    prints.
    """

    def __init__(
        self,
        policy: str = WARN,
        notify: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._policy = policy
        self._notify = notify
        self._logger = get_logger("core.execution")
        self._warned = False

    @property
    def policy(self) -> str:
        """The configured policy value."""
        return self._policy

    def allows_plugins(self) -> bool:
        """Whether third-party entry-point plugins may be imported and run.

        A plugin is arbitrary Python from any installed distribution, executed
        inside ModelDock's own process the moment a registry is built. Under
        ``strict`` it is not loaded at all — this is the part of a "restricted
        execution context" ModelDock can genuinely enforce.
        """
        return self._policy != STRICT

    def check(self, ref: ModelRef, runtime: Any) -> None:
        """Apply the policy before ``runtime`` executes ``ref``.

        Raises ``ExecutionPolicyError`` when the policy forbids the execution;
        otherwise emits the native-code notice once per guard and returns.
        """
        if self._policy == STRICT and getattr(runtime, "executes_in_process", False):
            backend = getattr(getattr(runtime, "backend", None), "value", "unknown")
            raise ExecutionPolicyError(
                f"the {backend!r} runtime loads model weights into ModelDock's own "
                f"process as native code",
                hint=(
                    "Use a runtime that runs the model in a separate server process, "
                    "or set execution_policy to 'warn' if you accept the risk."
                ),
            )
        self._warn_once(ref, runtime)

    def _warn_once(self, ref: ModelRef, runtime: Any) -> None:
        """Emit the native-code notice at most once per guard.

        Once per guard rather than once per call: a session that loads several
        models should say this clearly one time, not turn it into noise the
        user learns to skip past.
        """
        if self._policy == UNRESTRICTED or self._warned:
            return
        self._warned = True
        backend = getattr(getattr(runtime, "backend", None), "value", "unknown")
        message = (
            f"{ref.qualified_name()} will be executed by the {backend!r} runtime as "
            f"native code, with your user account's full privileges. ModelDock does "
            f"not sandbox it. Run only models you trust; see SECURITY.md "
            f'("Model Execution & Native Code") for how to confine the runtime.'
        )
        self._logger.warning("%s", message)
        if self._notify is not None:
            self._notify(message)


__all__ = ["ExecutionGuard", "UNRESTRICTED", "WARN", "STRICT"]
