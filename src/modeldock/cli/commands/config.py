"""CLI command: config (show / set)."""

from __future__ import annotations

from typing import Any, Dict

import typer

from modeldock.cli.console import print_error
from modeldock.common.config import load_settings

config_app = typer.Typer(help="Configuration management")


@config_app.command("show")
def config_show(debug: bool = typer.Option(False, "--debug", help="Show traceback")) -> None:
    """Show the resolved configuration."""
    try:
        settings = load_settings()
        typer.echo(f"default_backend: {settings.default_backend.value}")
        typer.echo(f"cache_dir:       {settings.cache_dir}")
        typer.echo(f"registry_url:    {settings.registry_url}")
        typer.echo(f"log_level:       {settings.log_level}")
        typer.echo(f"progress_style:  {settings.progress_style}")
        typer.echo(f"auto_install:    {settings.auto_install}")
        typer.echo(f"execution_policy: {settings.execution_policy}")
        typer.echo(f"ollama_host:     {settings.ollama_host}")
        typer.echo(f"lmstudio_host:   {settings.lmstudio_host}")
        typer.echo(f"llamacpp_gpu_layers: {settings.llamacpp_gpu_layers}")
    except Exception as exc:  # noqa: BLE001 - top-level CLI boundary
        print_error(exc, debug)
        raise typer.Exit(code=1)  # noqa: B904


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Setting key"),
    value: str = typer.Argument(..., help="Setting value"),
    debug: bool = typer.Option(False, "--debug", help="Show traceback"),
) -> None:
    """Set a configuration value (writes user config.toml)."""
    try:
        from modeldock.common.config import _toml_load
        from modeldock.common.platform import user_config_dir

        cfg_dir = user_config_dir()
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = cfg_dir / "config.toml"
        existing: Dict[str, Any] = {}
        if cfg_path.exists():
            # Reuse the shared loader: `import tomli` fails on Python 3.11+,
            # where tomli is not installed (it is a `python_version < "3.11"`
            # dependency) and tomllib is used instead.
            existing = _toml_load(cfg_path)
        existing[key] = value

        # Rewrite every key, not just this one — writing only the new key would
        # silently delete the rest of the user's configuration. Write to a temp
        # file and replace so an interrupted write cannot truncate the config.
        tmp_path = cfg_path.with_suffix(".toml.tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            # TOML has no stdlib writer; emit a minimal valid TOML manually.
            for existing_key, existing_value in existing.items():
                fh.write(f"{existing_key} = {_toml_scalar(existing_value)}\n")
        tmp_path.replace(cfg_path)
        typer.echo(f"Set {key} = {value} in {cfg_path}")
    except Exception as exc:  # noqa: BLE001 - top-level CLI boundary
        print_error(exc, debug)
        raise typer.Exit(code=1)  # noqa: B904


def _toml_scalar(value: Any) -> str:
    """Render a value as a TOML scalar literal.

    Accepts values already typed by the TOML parser (round-tripping keys the
    user set earlier) as well as raw strings from the command line.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    lowered = text.strip().lower()
    if lowered in {"true", "false"}:
        return lowered
    try:
        int(text)
        return text
    except ValueError:
        return f'"{text}"'
