# AgentX foundation configuration

A1.03 defines only the configuration foundation needed by current AgentX bootstrap work.
It is deliberately not a service locator, dependency-injection container, provider registry,
or secret store.

## Model

`agentx.infrastructure.config.AgentXConfig` contains four fields:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `profile` | `str` | `default` | Named operating profile/environment identifier. |
| `data_dir` | `pathlib.Path` | platform-local AgentX data directory | Root for future local AgentX data owned by later subsystems. |
| `log_level` | `LogLevel` | `INFO` | Foundation logging verbosity selection. |
| `debug` | `bool` | `false` | Explicit development/debug mode flag. |

No provider, database, device, event, task, capability, model, Hive, or kernel settings are
defined here. Those settings belong to the tasks that own those contracts.

## Source precedence

Configuration is loaded in this exact order, with later sources overriding earlier ones:

1. built-in defaults;
2. TOML configuration file;
3. supported `AGENTX_` environment variables;
4. explicit programmatic runtime overrides.

`load_config(..., overrides={...})` is the current explicit override mechanism. A large CLI is
not part of A1.03.

## Configuration file

The canonical Windows-first location is:

```text
%LOCALAPPDATA%\AgentX\config.toml
```

If `LOCALAPPDATA` is unavailable on Windows, AgentX computes the equivalent location below the
current user's home directory. On non-Windows platforms, AgentX follows `XDG_CONFIG_HOME` when it
is an absolute path and otherwise uses `<home>/.config/agentx/config.toml`.

The canonical data directory is `%LOCALAPPDATA%\AgentX\data` on Windows. Non-Windows systems use
`XDG_DATA_HOME/agentx` when available and otherwise `<home>/.local/share/agentx`.

These paths are computed only. Importing or loading configuration does **not** create directories
or files.

The default configuration file is optional. If it does not exist, built-in defaults continue to
apply. Passing an explicit `config_path` makes that file required; a nonexistent explicit path is
an error.

A minimal file is flat TOML:

```toml
profile = "development"
data_dir = "state"
log_level = "INFO"
debug = false
```

A relative `data_dir` in TOML is resolved relative to the TOML file's directory. Environment and
runtime-override data paths must be absolute so their meaning does not change with the process
working directory. Native absolute paths and Windows absolute paths are recognized without
rewriting separators. User-home markers are expanded with `pathlib.Path.expanduser()`.

## Environment variables

Only the following variables are configuration inputs:

| Variable | Field | Parsing |
| --- | --- | --- |
| `AGENTX_PROFILE` | `profile` | non-empty single-line string |
| `AGENTX_DATA_DIR` | `data_dir` | absolute path |
| `AGENTX_LOG_LEVEL` | `log_level` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`, case-insensitive |
| `AGENTX_DEBUG` | `debug` | exactly `true` or `false`, case-insensitive |

Environment variables without the `AGENTX_` prefix are ignored. An unknown `AGENTX_` variable is
rejected so misspellings do not silently change behavior.

## Validation and unknown keys

Malformed TOML raises `ConfigParseError`. Unreadable or invalid file paths raise a configuration
file error. Invalid field values and unknown keys raise `ConfigValidationError`.

Unknown keys are rejected in all three externally supplied layers:

- TOML;
- `AGENTX_` environment variables;
- runtime overrides.

This strict policy is intentional for the early research system: a typo should fail early rather
than produce an apparently valid but partially ignored configuration.

## Secrets boundary

**Configuration is not a secret store.**

A1.03 defines no API-key, password, token, credential, or generic secret fields. Do not place
secrets in example TOML or add secret values to this model. The loader does not enumerate or log
environment values beyond the explicitly supported `AGENTX_` names.

Future secret handling belongs behind the Trusted Kernel/secrets boundary and must be introduced
by the task that owns that security contract. A1.03 intentionally does not design or implement
that mechanism.
