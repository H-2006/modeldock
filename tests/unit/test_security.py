"""Security regression tests for command execution boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

from modeldock.domain.model import ModelRef, RuntimeBackend

# ``parents[2]`` — tests/unit/<file> -> tests/unit -> tests -> repo root.
# This was ``parents[1]`` and resolved to a directory that does not exist, so
# every audit below walked an empty file list and passed vacuously.
# ``test_audited_source_tree_is_discovered`` now makes that failure loud.
SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "modeldock"
AUDITED_DIRS = (
    SRC_ROOT / "adapters",
    SRC_ROOT / "common",
)


def _python_files() -> list[Path]:
    files: list[Path] = []

    for directory in AUDITED_DIRS:
        files.extend(directory.rglob("*.py"))

    return files


def test_audited_source_tree_is_discovered() -> None:
    """The audits below are only meaningful if they actually walk source files."""
    for directory in AUDITED_DIRS:
        assert directory.is_dir(), f"audited directory missing: {directory}"
    assert _python_files(), "security audit walked zero files"


def test_no_shell_execution_in_model_runtime_paths() -> None:
    """Model/runtime code must not invoke commands through a shell."""
    forbidden_calls = {
        "system",
        "popen",
        "create_subprocess_shell",
    }

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            func = node.func

            if isinstance(func, ast.Attribute):
                if func.attr in forbidden_calls:
                    raise AssertionError(
                        f"Forbidden shell execution API {func.attr!r} found in {path}:{node.lineno}"
                    )

                if func.attr in {"run", "Popen", "call", "check_call", "check_output"}:
                    for keyword in node.keywords:
                        if (
                            keyword.arg == "shell"
                            and isinstance(keyword.value, ast.Constant)
                            and keyword.value.value is True
                        ):
                            raise AssertionError(
                                f"subprocess call uses shell=True in {path}:{node.lineno}"
                            )


def test_model_names_are_treated_as_data() -> None:
    """Shell metacharacters in a model name must survive parsing as inert data.

    Routed through the real ``ModelRef.parse`` rather than asserted against a
    literal: the boundary being defended is that ModelDock parses a name into a
    domain object instead of ever letting it become a command fragment.
    """
    malicious_model_name = "model;echo injected && whoami | cat"

    ref = ModelRef.parse(malicious_model_name, backend=RuntimeBackend.OLLAMA)

    assert ref.name == malicious_model_name
    assert ref.qualified_name() == f"{malicious_model_name}:latest"
