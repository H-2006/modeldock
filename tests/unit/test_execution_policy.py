"""Tests for the restricted execution context (issue #139).

Two things are being defended here, and they are deliberately different in
kind. ModelDock can genuinely *refuse* to run third-party plugin code and to
drive a backend that loads native code into its own process — those are
assertions about behaviour. It cannot sandbox a runtime server it does not
own, so the rest is a warning, and these tests pin the warning down to exactly
once per session on the paths that actually execute a model.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock, patch

import pytest

from modeldock.adapters.registry.catalog_registry import CatalogProviderRegistry
from modeldock.adapters.runtimes.registry import RuntimeRegistry
from modeldock.common.config import Settings, load_settings
from modeldock.common.errors import ConfigError, ExecutionPolicyError
from modeldock.core.execution import STRICT, UNRESTRICTED, WARN, ExecutionGuard
from modeldock.core.manager import ModelManager
from modeldock.domain.model import ModelRef, RuntimeBackend
from tests.conftest import FakeCache, FakeRegistry, FakeRuntime


class _InProcessRuntime(FakeRuntime):
    """A runtime that loads model weights into ModelDock's own process."""

    backend = RuntimeBackend.GPT4ALL
    executes_in_process = True


def _fake_entry_point(name: str, target: Any, dist: str = "third-party-pkg") -> MagicMock:
    ep = MagicMock()
    ep.name = name
    ep.load.return_value = target
    # Provenance decides whether a registration is a security event; the
    # default here is a foreign distribution, since that is the case worth
    # testing.
    ep.dist.name = dist
    return ep


def _patched_entry_points(entries: List[MagicMock]) -> MagicMock:
    """Mock importlib.metadata.entry_points() supporting .select()."""
    eps = MagicMock()
    eps.select.return_value = entries
    return eps


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_default_policy_warns() -> None:
    """The shipped default must satisfy "warn about native model code"."""
    assert Settings().execution_policy == WARN


@pytest.mark.parametrize("value", [UNRESTRICTED, WARN, STRICT])
def test_every_policy_value_is_accepted(value: str) -> None:
    assert Settings(execution_policy=value).execution_policy == value


def test_invalid_policy_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        Settings(execution_policy="sandboxed")


def test_policy_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODELDOCK_EXECUTION_POLICY", STRICT)
    assert load_settings().execution_policy == STRICT


def test_invalid_env_policy_falls_back_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per the config contract, a bad env value warns and keeps the default."""
    monkeypatch.setenv("MODELDOCK_EXECUTION_POLICY", "nonsense")
    assert load_settings().execution_policy == WARN


def test_policy_is_read_from_a_config_file(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('execution_policy = "unrestricted"\n', encoding="utf-8")
    assert load_settings(config_path=cfg).execution_policy == UNRESTRICTED


def test_policy_is_exported_to_subprocesses() -> None:
    env = Settings(execution_policy=STRICT).to_env_overrides()
    assert env["MODELDOCK_EXECUTION_POLICY"] == STRICT


# ---------------------------------------------------------------------------
# ExecutionGuard
# ---------------------------------------------------------------------------


def test_warn_policy_notifies_once_per_session() -> None:
    """A session loading several models should say this once, not become noise."""
    seen: List[str] = []
    guard = ExecutionGuard(WARN, notify=seen.append)

    guard.check(ModelRef.parse("llama3"), FakeRuntime())
    guard.check(ModelRef.parse("mistral"), FakeRuntime())

    assert len(seen) == 1


def test_the_warning_names_the_mechanism_and_the_backend() -> None:
    seen: List[str] = []
    ExecutionGuard(WARN, notify=seen.append).check(ModelRef.parse("llama3"), FakeRuntime())

    message = seen[0]
    assert "llama3:latest" in message
    assert "ollama" in message
    assert "native code" in message
    # It must not claim a containment ModelDock does not provide.
    assert "does not sandbox" in message


def test_unrestricted_policy_is_silent() -> None:
    seen: List[str] = []
    ExecutionGuard(UNRESTRICTED, notify=seen.append).check(ModelRef.parse("llama3"), FakeRuntime())
    assert seen == []


def test_strict_refuses_a_runtime_that_executes_in_process() -> None:
    with pytest.raises(ExecutionPolicyError) as excinfo:
        ExecutionGuard(STRICT).check(ModelRef.parse("llama3"), _InProcessRuntime())

    message = str(excinfo.value)
    assert "gpt4all" in message
    # The error has to say how to proceed deliberately, not just refuse.
    assert "execution_policy" in message


def test_strict_still_allows_a_server_backed_runtime() -> None:
    """Strict restricts what ModelDock itself executes, not all model use."""
    ExecutionGuard(STRICT).check(ModelRef.parse("llama3"), FakeRuntime())


@pytest.mark.parametrize(
    ("policy", "allowed"),
    [(UNRESTRICTED, True), (WARN, True), (STRICT, False)],
)
def test_plugin_permission_follows_the_policy(policy: str, allowed: bool) -> None:
    assert ExecutionGuard(policy).allows_plugins() is allowed


def test_an_undeclared_runtime_is_not_assumed_to_be_in_process() -> None:
    """A duck-typed runtime without the attribute must not break strict mode."""

    class _Bare:
        backend = RuntimeBackend.OLLAMA

    ExecutionGuard(STRICT).check(ModelRef.parse("llama3"), _Bare())


# ---------------------------------------------------------------------------
# Plugin discovery gating — the part that is real enforcement
# ---------------------------------------------------------------------------


def test_runtime_plugins_are_not_even_looked_up_when_disallowed() -> None:
    """Not "loaded but ignored" — entry_points() is never consulted at all."""
    with patch("modeldock.adapters.runtimes.registry.entry_points") as eps:
        RuntimeRegistry(allow_plugins=False)

    eps.assert_not_called()


def test_runtime_plugin_code_never_executes_when_disallowed() -> None:
    plugin = _fake_entry_point("vllm", MagicMock())

    with patch(
        "modeldock.adapters.runtimes.registry.entry_points",
        return_value=_patched_entry_points([plugin]),
    ):
        RuntimeRegistry(allow_plugins=False)

    plugin.load.assert_not_called()


def test_runtime_plugins_load_when_allowed() -> None:
    """The gate must be a gate, not a permanent block."""
    plugin = _fake_entry_point("vllm", lambda: FakeRuntime())

    with patch(
        "modeldock.adapters.runtimes.registry.entry_points",
        return_value=_patched_entry_points([plugin]),
    ):
        RuntimeRegistry(allow_plugins=True)

    plugin.load.assert_called_once()


def test_a_plugin_shadowing_a_builtin_is_logged(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing else reveals that the shipped adapter is no longer in use."""
    # ``configure_logging`` sets propagate=False on the "modeldock" logger, and
    # any earlier CLI test in the session leaves it that way. caplog only sees
    # records that reach the root logger, so restore propagation for this test;
    # ``at_level(..., logger="modeldock")`` lifts that logger's ERROR level so a
    # WARNING is not filtered before it propagates.
    monkeypatch.setattr(logging.getLogger("modeldock"), "propagate", True)
    plugin = _fake_entry_point("ollama", lambda: FakeRuntime())

    with (
        patch(
            "modeldock.adapters.runtimes.registry.entry_points",
            return_value=_patched_entry_points([plugin]),
        ),
        caplog.at_level("WARNING", logger="modeldock"),
    ):
        RuntimeRegistry(allow_plugins=True)

    assert "replaces the built-in ollama adapter" in caplog.text
    assert "third-party-pkg" in caplog.text


def test_modeldocks_own_entry_point_does_not_warn(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ModelDock advertises its own ``ollama`` runtime as an entry point.

    Warning on that would fire on every single invocation and teach users to
    ignore the warning that actually matters.
    """
    monkeypatch.setattr(logging.getLogger("modeldock"), "propagate", True)
    plugin = _fake_entry_point("ollama", lambda: FakeRuntime(), dist="modeldock")

    with (
        patch(
            "modeldock.adapters.runtimes.registry.entry_points",
            return_value=_patched_entry_points([plugin]),
        ),
        caplog.at_level("WARNING", logger="modeldock"),
    ):
        RuntimeRegistry(allow_plugins=True)

    assert caplog.text == ""


def test_a_real_registry_construction_is_quiet(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the above against the installed distribution, not a mock."""
    monkeypatch.setattr(logging.getLogger("modeldock"), "propagate", True)

    with caplog.at_level("WARNING", logger="modeldock"):
        RuntimeRegistry(allow_plugins=True)

    assert "replaces the built-in" not in caplog.text


def test_catalog_plugins_are_not_looked_up_when_disallowed() -> None:
    with patch("modeldock.adapters.registry.catalog_registry.entry_points") as eps:
        CatalogProviderRegistry(allow_plugins=False)

    eps.assert_not_called()


def test_catalog_registry_still_serves_builtins_when_plugins_are_off(tmp_path: Path) -> None:
    """Refusing plugins must not disable first-party catalogs."""
    registry = CatalogProviderRegistry(allow_plugins=False)
    assert RuntimeBackend.LM_STUDIO in registry.available_backends()


# ---------------------------------------------------------------------------
# ModelManager wiring — both execution entry points
# ---------------------------------------------------------------------------


def _manager(policy: str, runtime: Any, notify: Any = None) -> ModelManager:
    return ModelManager(
        runtime=runtime,
        registry=FakeRegistry(),
        cache=FakeCache(),
        settings=Settings(catalog_source="bundled", execution_policy=policy),
        notify=notify,
    )


def test_load_applies_the_policy() -> None:
    seen: List[str] = []
    runtime = FakeRuntime(installed=[ModelRef.parse("llama3")])

    _manager(WARN, runtime, notify=seen.append).load("llama3")

    assert len(seen) == 1


def test_run_applies_the_policy_too() -> None:
    """``run`` bypasses LifecycleOrchestrator, so it needs its own check."""
    seen: List[str] = []
    runtime = FakeRuntime(installed=[ModelRef.parse("llama3")])

    manager = _manager(WARN, runtime, notify=seen.append)
    try:
        manager.run("llama3")
    except NotImplementedError:
        pass  # FakeRuntime has no interactive session; the guard ran first.

    assert len(seen) == 1


def test_strict_refuses_before_the_runtime_is_touched() -> None:
    runtime = _InProcessRuntime(installed=[ModelRef.parse("llama3")])

    with pytest.raises(ExecutionPolicyError):
        _manager(STRICT, runtime).load("llama3")

    # Refused, not merely warned about after the fact.
    assert runtime.clients == []


def test_strict_manager_builds_a_plugin_free_runtime_registry() -> None:
    with patch("modeldock.adapters.runtimes.registry.entry_points") as eps:
        _manager(STRICT, FakeRuntime())

    eps.assert_not_called()


def test_the_library_is_silent_without_a_notify_channel(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """SDK use must not print; only a front end that opts in does."""
    runtime = FakeRuntime(installed=[ModelRef.parse("llama3")])

    _manager(WARN, runtime).load("llama3")

    assert capsys.readouterr().err == ""


def test_cli_warning_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    """Never stdout: it would corrupt --json output and piped model tokens."""
    from modeldock.cli.console import print_warning

    print_warning("native code ahead")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "native code ahead" in captured.err
