# Configuration

ModelDock is zero-config by default. Customize when needed.

---

## Config File Location

| OS | Path |
|----|------|
| Linux / macOS | `~/.config/modeldock/config.toml` |
| Windows | `%APPDATA%\modeldock\config.toml` |

---

## Config File Format

```toml
default_backend  = "ollama"
auto_install     = true
log_level        = "INFO"
progress_style   = "rich"
execution_policy = "warn"
```

---

## Environment Variables

Override config with `MODELDOCK_*` env vars:

| Variable | Description | Default |
|----------|-------------|---------|
| `MODELDOCK_LOG_LEVEL` | `DEBUG`/`INFO`/`WARNING`/`ERROR` | `ERROR` |
| `MODELDOCK_DEFAULT_BACKEND` | Runtime backend | `ollama` |
| `MODELDOCK_AUTO_INSTALL` | Auto-download missing models | `false` |
| `MODELDOCK_CACHE_DIR` | Override cache location | platform default |
| `MODELDOCK_CATALOG_SOURCE` | `auto`/`ollama`/`bundled` | `auto` |
| `MODELDOCK_EXECUTION_POLICY` | `unrestricted`/`warn`/`strict` | `warn` |

---

## Config Precedence (low to high)

1. Built-in defaults
2. Dynamic catalog (ollama.com)
3. System config (`<sys-prefix>/etc/modeldock`)
4. User config (`~/.config/modeldock/config.toml`)
5. Environment variables (`MODELDOCK_*`)
6. Explicit runtime overrides

---

## CLI Config Commands

```bash
modeldock config show          # view current config
modeldock config set auto_install true   # change a setting
```

---

## Programmatic Configuration

Override the singleton manager before first use:

```python
import modeldock as md

md.configure(
    backend="ollama",
    auto_install=True,
    log_level="INFO",
    progress_style="rich",
    cache_dir="/custom/cache/path",
    ollama_host="http://localhost:11434",
)
```

Or create an isolated manager:

```python
mgr = md.Manager(
    backend="ollama",
    auto_install=True,
    ollama_host="http://localhost:11434",
)
```

---

## Catalog Source

Control which registry is used:

| Value | Behavior |
|-------|----------|
| `auto` | Try dynamic, fallback to bundled (default) |
| `ollama` | Dynamic only — requires internet |
| `bundled` | Static catalog.json only — fully offline |

Set via config file or `MODELDOCK_CATALOG_SOURCE` env var.

---

## Restricted Execution

Loading a model makes a runtime execute it as native code with your user
account's full privileges. `execution_policy` controls how much ModelDock is
willing to do on your behalf:

| Value | Behavior |
|-------|----------|
| `warn` | Warn once per session before a model is executed (default) |
| `unrestricted` | No warning; previous behavior |
| `strict` | Also refuse third-party plugins and in-process model loading |

`strict` is not a sandbox — it restricts what ModelDock's own process executes,
not what a runtime server does with your model. Set via config file or
`MODELDOCK_EXECUTION_POLICY`. See
[SECURITY.md](https://github.com/OpenAgentHQ/modeldock/blob/main/SECURITY.md)
for how to confine the runtime itself.

---

## Next Steps

- [SDK Reference](../sdk/python-api.md) — full API reference
- [CLI Reference](../sdk/cli.md) — all CLI commands
