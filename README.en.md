# oai-usage

[简体中文](./README.md) | English

Summarize token usage from local Codex logs, estimate API-equivalent costs, and display account quotas and current-cycle projections. One file, using only the Python standard library.

## Quick start

Use the latest stable Python 3. Examples use `python3`; replace it with `python` if that is your interpreter command. On Windows, use PowerShell 7.4+.

Download and run directly:

```sh
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 -
```

The default report covers the last 30×24 hours, grouped by model. Prices require network access. Live quota requires native Codex installed locally and signed in with a ChatGPT account.

<details>
<summary>Optional arguments</summary>

Place arguments after Python's `-`. Each command can be copied independently.

Today:

```sh
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 - --today
```

Watch continuously; press Ctrl+C to exit:

```sh
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 - watch
```

All options:

```sh
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 - --help
```

</details>

## Common commands

After downloading [`oai-usage`](./oai-usage), run the local file:

```sh
python3 ./oai-usage
```

Watch continuously:

```sh
python3 ./oai-usage watch
```

Group by day and save JSON:

```sh
python3 ./oai-usage --by day --output report.json
```

Prices and source:

```sh
python3 ./oai-usage prices
```

Use `report --help`, `watch --help`, or `prices --help` for all options. Common filters include `--today`, `--days all`, `--since`, `--until`, `--root`, and `--by`; `--price` overrides unit prices.

## Quota, prices, and data

`--quota` selects the quota source:

| Mode | Behavior |
|---|---|
| `auto` (default) | Fall back to log snapshots if the live query fails. |
| `live` | Use only live query results. |
| `logs` | Use only log snapshots. |
| `off` | Skip quota queries; prices still require network access. |

`watch` refreshes every 2 seconds by default. Each quota query is followed by a 30-second wait; adjust these with `--refresh` and `--quota-interval`. `--count N` limits refreshes. Use `--codex-binary` if Codex cannot be found automatically.

Windows consoles automatically enable terminal controls and restore the original mode on exit. If this fails, colors are disabled and `watch` prints plain frames without clearing the screen or hiding the cursor.

Prices come from third-party models.dev OpenAI data, maintained as the repository's [`prices.json`](./prices.json). The consumer downloads only the finished GitHub catalog and saves no price cache. Startup price failure exits the program. Watch refreshes prices hourly in the background and keeps the current run's valid catalog if a refresh fails.

Costs use exact model IDs and select price tiers per request. They are **neither historical bills nor subscription bills**. Unknown models, insufficient request details, or missing cache prices mark the estimate as incomplete.

The current cycle uses the quota interface's actual window, independently of report date filters. Estimated total and remaining amounts extrapolate from local costs and account usage. They are **not official quotas or subscription dollar balances**; unavailable projections are explained. `--no-project` hides cycle cards.

Logs are read from `sessions` and `archived_sessions` under `$CODEX_HOME` (or `~/.codex` when unset); `--root` replaces these directories. Original logs are read-only, with no log uploads or credential-content reads. `--json` emits complete data; watch emits one JSON line per frame. `--output` cannot overwrite the program or JSONL logs.

Dates use the local time zone by default. UTC needs no extra data; other named time zones depend on the system time-zone database.

## Development and testing

Maintain `oai-usage` directly, without a build step. Actions run [`scripts/update_prices.py`](./scripts/update_prices.py) every 4 hours to maintain the catalog from models.dev's `openai.models`, then validate and publish through [`scripts/publish_prices.py`](./scripts/publish_prices.py). Only the current data format is maintained; publish the program and catalog together when it changes.

Run tests:

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -q
```

CI covers Ubuntu and Windows using a simulated app-server. Real Windows account queries, cycle projections, and Ctrl+C cleanup still need verification on a Windows machine.

## License

[GNU Affero General Public License v3.0](./LICENSE), SPDX: `AGPL-3.0-only`.
