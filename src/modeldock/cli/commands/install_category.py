"""CLI command: install-category."""

from __future__ import annotations

import typer

from modeldock.cli.console import print_error
from modeldock.cli.factory import manager_for
from modeldock.domain.model import Category

_CATEGORY_HELP: str = "Category name. Available: " + "; ".join(
    f"{item.value} ({item.description})" for item in Category
)


def install_category_cmd(
    category: str = typer.Argument(..., help=_CATEGORY_HELP),
    backend: str = typer.Option(None, "--backend", help="Runtime backend"),
    debug: bool = typer.Option(False, "--debug", help="Show traceback"),
) -> None:
    """Install every model in a category."""
    try:
        mgr = manager_for(backend)
        refs = mgr.install_category(category)
        for ref in refs:
            typer.echo(f"Installed {ref.qualified_name()}")
    except Exception as exc:  # noqa: BLE001 - top-level CLI boundary
        print_error(exc, debug)
        raise typer.Exit(code=1)  # noqa: B904
