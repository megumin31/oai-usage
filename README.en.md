# oai-usage

[简体中文](./README.md) | English

Summarize token usage from local Codex logs, estimate API-equivalent costs, and display account quotas and current-cycle projections. One file, using only the Python standard library.

## Quick start

Use the latest stable Python 3. Examples use `python3`; replace it with `python` if that is your interpreter command. Windows users should first read [Windows setup and notes](#windows-setup-and-notes) below.

Download and run directly:

```sh
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 -
```

The default report groups local logs within the current account quota cycle by model. When several valid windows exist, it selects the longest (for example, Secondary) and labels the actual start and window. If quota lookup is disabled or no current cycle can be validated, it explicitly falls back to the last 30×24 hours. Prices require network access. Live quota requires native Codex installed locally and signed in with a ChatGPT account.

Tokens and API-equivalent costs cover only local Codex session logs. Account quota is account-wide and has different coverage: an empty local period does not mean the account was unused. Cloud-coordinated Work or dot tasks can run tools on this computer without adding their model usage to these logs; the quota percentage does not identify which tasks or devices caused usage.

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

## Windows setup and notes

Use a current stable PowerShell (7.4+) and the latest stable Python 3. First, install PowerShell and the official Python install manager with WinGet from your existing PowerShell:

```powershell
winget install --id Microsoft.PowerShell --exact --source winget
winget install --id Python.PythonInstallManager --exact --source winget
```

After installation, close and reopen your terminal. Open **PowerShell 7** from the Start menu, or enter `pwsh` in a new terminal. PowerShell 7 installs alongside the built-in Windows PowerShell 5.1; existing windows do not switch automatically. Then install Python; with the manager's default configuration, `default` selects the latest stable release without pinning a minor version:

```powershell
pymanager install default
```

Check your versions:

```powershell
$PSVersionTable.PSVersion
python --version
```

If `winget` is unavailable, install or update **App Installer** from the Microsoft Store. Alternatively, follow the official [PowerShell installation guide](https://learn.microsoft.com/powershell/scripting/install/install-powershell-on-windows) and [Python installation guide](https://docs.python.org/3/using/windows.html).

Run directly in PowerShell 7:

```powershell
curl.exe -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python -
```

Windows examples explicitly use `curl.exe` to avoid the different `curl` alias in older PowerShell versions. If `python` is missing or opens the Microsoft Store, but `py --version` works, replace `python` with `py`. No additional Python dependencies are required.

After downloading the script to your current directory, you can also run:

```powershell
python .\oai-usage
python .\oai-usage watch
python .\oai-usage --days all
```

For the Codex desktop app's default Windows-native agent environment, run this script with Windows Python in PowerShell. Without `CODEX_HOME`, the script reads `sessions` and `archived_sessions` under `%USERPROFILE%\.codex`. Changing only the app's integrated terminal shell does not change this path. The agent environment is a separate setting; see the [Codex Windows documentation](https://learn.chatgpt.com/docs/windows/windows-app#windows-subsystem-for-linux-wsl).

If you changed the agent environment or `CODEX_HOME`, confirm that the script reads the actual log location; it does not automatically search other directories in WSL. Repeat `--root` to specify log directories explicitly (replacing the defaults), including both `sessions` and `archived_sessions`. `--root` only changes log scanning, not the Codex executable or login environment used for live quota queries. Reports use the valid current quota cycle by default, with a 30-day fallback when unavailable; use `--days all` for older records.

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

The terminal shows two independent period blocks: This cycle (Selected) and Last 30 days (reference). Each keeps its name, dates, and source, then shows its own By model token and cost breakdown with a Total footer for all models in that period; overlapping periods are never added together. `--today`, `--days`, `--since`, and `--until` override Selected and its model breakdown, while the last 30 days remains a reference. Without a valid cycle, Selected falls back to the last 30 days. Identical Selected and reference ranges appear only once. `--top` limits displayed models separately in each period, while Total always covers every model; `--top 0` shows all. Token columns are ordered Input, Cached, Output, Reasoning, Total. Total remains Input + Output; Reasoning is already included in Output. Ratios use aggregate numerators and denominators, and sessions are counted uniquely. JSON retains every model without adding a synthetic Total model.

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

When the complete dashboard does not fit the terminal height, `watch` switches to full scrollback frames and explains the change. Scroll to read both periods, their model rows, and both Total footers. It stays in scrollback after a resize so earlier frames remain accessible; `--top 0` includes every model.

Windows consoles automatically enable terminal controls and restore the original mode on exit. If this fails, colors are disabled and `watch` prints plain frames without clearing the screen or hiding the cursor.

Prices come from third-party models.dev OpenAI data, maintained as the repository's [`prices.json`](./prices.json). The consumer downloads only the finished GitHub catalog and saves no price cache. Startup price failure exits the program. Watch refreshes prices hourly in the background and keeps the current run's valid catalog if a refresh fails.

Costs use exact model IDs and select price tiers per request. They are **neither historical bills nor subscription bills**. Unknown models, insufficient request details, or missing cache prices mark the estimate as incomplete.

Account quota and current cycle share one panel: account percentages come from quota snapshots, while local tokens and API-equivalent costs come from logs. A cycle starts at reset time minus window length, with both window and observation times validated; it is not a calendar month. The default report selects the longest valid window through the current report time; cycle cost projections stop at the quota snapshot time. These cutoffs are labeled separately. Explicit report date filters remain independent of cycle projections. Estimated total and remaining amounts extrapolate from local costs and account usage. They are **not official quotas or subscription dollar balances**; unavailable projections are explained. `--no-project` hides cycle estimates while keeping account quota visible.

Query cleanup has bounded pipe draining on all platforms. On POSIX, the owned process group is terminated, including descendants that inherit output pipes. Windows kills the direct child and closes the pipes within the cleanup deadline; descendant-tree termination has not been validated on native Windows.

Accounting supports both legacy `token_count` snapshots and per-response `token_usage_record` receipts. Response IDs remove retransmissions; matching request vectors and cumulative coverage prevent counting both formats twice. Legacy-only history stays included, each native receipt retains its response time and model, and a native cumulative baseline is never charged as a new request. Forked parent receipts and compaction checkpoints are excluded. If the counter domains cannot be reconciled, the report names the uncertain legacy interval and retains observed response usage without inventing a residual charge.

Price errors distinguish download failures from invalid catalog data, including background refresh warnings. `--today` can show an empty interval at exact local midnight, and watch continues into the new day.

Logs are read from `sessions` and `archived_sessions` under `$CODEX_HOME` (or `~/.codex` when unset); `--root` replaces these directories. Original logs are read-only, with no log uploads or credential-content reads. `--json` emits complete data; watch emits one JSON line per frame. `period.selection` identifies `current_cycle`, `explicit`, or `fallback`. Current-cycle reports include `cycle_window` and `cycle_resets_at`; fallback reports include `fallback_reason`. Existing usage, cost, and grouping JSON fields are retained. The additive `period_summaries` field provides local totals, complete `by_model` / `by_model_detail` breakdowns, bounds, and sources for each period; the reference does not change the original `period` or `summary`. `--output` cannot overwrite the program or JSONL logs.

Dates use the local time zone by default. UTC needs no extra data; other named time zones depend on the system time-zone database. Terminal dates show the actual offset at that point in time as `UTC±HH:MM`, or `UTC` for a zero offset.

## Development and testing

Maintain `oai-usage` directly, without a build step. Actions run [`scripts/update_prices.py`](./scripts/update_prices.py) every 4 hours to maintain the catalog from models.dev's `openai.models`, then validate and publish through [`scripts/publish_prices.py`](./scripts/publish_prices.py). Only the current data format is maintained; publish the program and catalog together when it changes.

Run tests:

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -q
```

CI covers Ubuntu and Windows using a simulated app-server. Real Windows account queries, cycle projections, and Ctrl+C cleanup still need verification on a Windows machine.

## License

[GNU Affero General Public License v3.0](./LICENSE), SPDX: `AGPL-3.0-only`.
