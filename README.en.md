# oai-usage

[简体中文](./README.md) | English

`oai-usage` is a local command-line tool that summarizes token usage from Codex session logs, estimates equivalent costs using current public Standard API pricing, and displays account quota information.

- Only the latest stable Python 3 is maintained. The tool uses only the standard library and requires no third-party dependencies. The `python3` or `python` used to run the script should point to the latest stable Python 3.
- Original JSONL logs are read-only. The tool does not retain conversation text, upload logs, read credential contents, or consume quota resets.
- Installation requires copying just one file, [`oai-usage`](./oai-usage). It includes the price-processing code and fetches the price catalog from GitHub at runtime. The current version is 0.0.1. CLI, report, diagnostic, and JSON explanatory text is in English; user data such as model names from logs is preserved as recorded.

## Quick start

You can download and execute the tool directly: `curl` downloads the single-file CLI, and the latest stable Python 3 executes it from standard input. No persistent installation or temporary script is needed. On macOS, Linux, or WSL, use:

```sh
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 -

# Run with arguments: place arguments after Python's -
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 - --today
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 - watch --quota logs --count 3

# Show the version or help
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 - --version
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python3 - --help
```

On Windows, use PowerShell 7.4+ and install the latest stable Python 3:

```powershell
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python -
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python - --today
curl -fsSL https://raw.githubusercontent.com/megumin31/oai-usage/main/oai-usage | python - watch --count 3
```

`python3` and `python` are common interpreter names on different platforms. If `python` on Linux also points to the required Python 3, the same `curl ... | python -` command works there too. Execution from standard input preserves the CLI arguments, JSON behavior, and `--output` rules of local execution. Generating reports still requires network access to fetch the price catalog.

Live quota queries now use cross-platform asynchronous subprocess pipes; Windows uses Python’s default Proactor event loop. Install a directly executable native Codex locally and sign in with a ChatGPT account. The tool discovers `codex.exe` through PATH, native programs corresponding to official npm wrappers, official standalone directories, and relocated desktop runtimes. You can also specify it with `--codex-binary` or `CODEX_CLI_PATH`; `.cmd` files are not executed through a shell. If a first installation only has files inside a Store package and permissions prevent direct execution, open the Codex desktop app to relocate its runtime, or explicitly specify the native CLI. The default local time zone and explicit `--timezone UTC` need no additional time-zone data. Other IANA named time zones still depend on the system time-zone database, which this change does not supply. See “Installation and testing” below for the actual Windows verification scope.

Run the script directly:

```sh
./oai-usage
```

By default, the tool reports the last 30×24 hours, groups usage by model, and displays API-equivalent usage for the **Current cycle** at the top. The cycle uses its actual start and end times, rather than a fixed duration or calendar week. Common commands:

```sh
# Today in the current reporting time zone
./oai-usage --today

# Use a specified time zone; date arguments are calendar dates in that zone
./oai-usage --since 2026-09-01 --until 2026-09-22 --timezone Asia/Shanghai --by day

# All history, grouped by separate sessions (including separate child tasks)
./oai-usage --days all --by session --top 0

# Show model, date, and session breakdowns together
./oai-usage --all

# Scan only the specified directories; --root can be repeated
./oai-usage --root /path/to/sessions --root /path/to/archived_sessions --quota off

# Write complete JSON to standard output or a file
./oai-usage --quota off --json
./oai-usage --by day --output report.json

# Refresh continuously; press Ctrl+C to exit
./oai-usage watch
./oai-usage watch --quota off --refresh 5 --count 3

# Show the current price catalog and its sources
./oai-usage prices
```

`report` is the default subcommand, so `./oai-usage --today` is equivalent to `./oai-usage report --today`. Use `./oai-usage report --help`, `./oai-usage watch --help`, or `./oai-usage prices --help` for the complete argument list.

The terminal's current-cycle section is shown by default; use `--no-project` to hide it. `--quota off` neither reads account quota nor displays the current cycle, but generating reports still requires network access to fetch the price catalog.

## Installation and testing

The script can be executed directly. To install it as a local command at a target path, first back up any existing command at that path. The following commands write to the target path:

```sh
install -m 755 ./oai-usage ~/.local/bin/oai-usage
oai-usage --version
```

The latest stable Python 3 is required; older Python versions are outside the maintenance scope. CI uses `3.x` and `check-latest: true` to select the latest stable Python 3, without enabling prerelease versions. The new Ubuntu/Windows test matrix runs the full `unittest` suite with `ResourceWarning` treated as an error. The existing Ubuntu price synchronization workflow is retained. This matrix has not yet run on a Windows runner and is not evidence of a passing Windows run.

Updating an installation only requires replacing `oai-usage`. The price catalog and JSON output support only the current format, without compatibility for older formats. When the catalog format changes, the program and cloud `prices.json` must be updated in the same repository release. The program does not embed price values or read a neighboring `prices.json` or local price cache. Each report, watch, or prices command requires access to the price catalog on GitHub. If a valid catalog cannot be obtained at startup, the program asks you to check the network and exits with status code 1.

Tests use the standard library's `unittest`:

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -q
```

The CLI is maintained directly in [`oai-usage`](./oai-usage), with no build or generated section to rebuild. Installation still requires only this file. It consumes the finished [`prices.json`](./prices.json) catalog at its fixed GitHub URL and does not download upstream model catalogs or pricing data. The Actions producer is maintained in [`scripts/update_prices.py`](./scripts/update_prices.py) and the independent synchronous support module [`scripts/price_support.py`](./scripts/price_support.py); the publication script references the module through the update script. The producer neither depends on nor loads code from the CLI, and `price_support.py` is not an installation dependency for users. The former `oai_price_catalog.py` and `scripts/bundle_prices.py` have been deleted.

On the local macOS machine, Python 3.14.7 passed 136 tests and diff checks, covering copied single-file execution, standard input, asynchronous pipes and cleanup, the consumer’s sole download URL, independent producer execution, matching rates between producer output and consumer results, and rejection of obsolete fields. This round’s real native app-server watch used a local price fixture in the current format, returned `app_server` quota with available cycle projections for three consecutive frames, and reaped both quota subprocesses on exit. This does not establish that the new format works with real GitHub price downloads. Previous real GitHub download verification predates the format change; the current public catalog still contains obsolete fields and is rejected by the new consumer. This round has not been published: publish the program and catalog together before rechecking the public download path. Windows verification with a real signed-in account or native-console Ctrl+C remains outstanding, and not all installation types have been tested. On Windows with native Codex installed and signed in, and the matching cloud catalog published, verify from PowerShell:

```powershell
python ./oai-usage --quota live --json
python ./oai-usage watch --quota live --count 3 --json
```

Check that `summary.current_rate_limits.source` is `app_server`. When projection conditions hold, including a valid quota window, sufficient attributable local log usage, and a nonzero used proportion, the corresponding window in `summary.cycle_estimates` should have `projection_available` set to `true` and provide estimated full-cycle and remaining amounts. Separately check that Ctrl+C or a query timeout leaves no app-server or price-download subprocesses behind. An `auto` fallback to logs does not establish that live queries pass acceptance.

## Log scope and dates

Without `--root`, the tool scans `$CODEX_HOME/sessions` and `$CODEX_HOME/archived_sessions`. If `CODEX_HOME` is unset, it uses `~/.codex`. Specifying one or more `--root` arguments replaces the default directories.

Time-range rules:

- The default `--days 30` looks back 30×24 hours from the current time. `--days all` or `--days 0` leaves the start unrestricted.
- `--today`, `--since`, and `--until` use the reporting time zone specified by `--timezone`, which defaults to the system's local time zone. `--until` includes the specified day.
- When a time range is set, usage without timestamps is excluded; its count is recorded in the JSON diagnostics and summary fields.

Records for the same session are merged and deduplicated by timestamp and cumulative usage, even across multiple log files. The session's first `session_meta.id` is its fixed identity. Parent task IDs are retained only as relationship information, so child tasks are not merged into their parents. Inherited fork history snapshots are not counted again as new usage.

## Costs, prices, and quota

The report's “API-equivalent cost” estimates costs using current public Standard API pricing. It is **neither a historical bill nor a subscription bill**. The price catalog comes from the public repository's `main/prices.json`. Run `prices` to view model prices, long-context rules, the last verification date, and the current catalog source.

At each startup of `report`, `watch`, or `prices`, the tool downloads and strictly validates the catalog from the public repository's `main/prices.json` at a fixed HTTPS URL. It uses the catalog only in process memory and does not save it locally. `verified_at` must be today in UTC or an earlier date. Client downloads are limited to 1MB, with a 3-second socket timeout and a 6-second total timeout. Size, format, and fields are constrained; redirects and incomplete data are rejected. The consumer allows only the fixed URL of the finished catalog. File and standard-input execution share the same fixed, minimal download worker source, with an asynchronous scheduler managing its separate subprocess. `watch` downloads again every hour in the background without blocking normal display refreshes. It switches only after the entire catalog has downloaded and passed validation; each frame uses a complete price catalog and quota snapshot. If a refresh fails, it displays a message and continues using the last valid catalog from the current run. A successful refresh clears the refresh-failure message; the catalog is discarded on exit.

Manual prices supplied with `--price` take precedence over the catalog, but a valid catalog must still be downloaded successfully at startup.

The estimate excludes tool fees, regional surcharges, Fast/priority service, and subscription fees. Each usage event is priced independently using `Decimal`. Long-context tiers are selected using the input size of an individual request; sessions only aggregate event results. This is the tool's convention for Standard API-equivalent estimates and does not establish the official long-context pricing scope of older APIs. You can override prices:

```sh
./oai-usage --price 'my-model=2.5,0.25,15,3.125'
```

The argument format is `MODEL=IN,CACHED,OUT[,WRITE]`. All four values are USD prices per million tokens; `WRITE` is optional, and all values must be finite and nonnegative. Models are priced using their exact IDs in the logs; `--price` must also use exact IDs. Names such as `astra`, date suffixes, `preview`, or `latest` are not automatically mapped to other models. Explicitly defined custom model prices can still be used.

The repository's GitHub Actions run every 4 hours (6 times daily, at UTC 00:17, 04:17, 08:17, 12:17, 16:17, and 20:17; the Beijing-time schedule is also 00:17, 04:17, 08:17, 12:17, 16:17, and 20:17). Manual triggers are also supported; GitHub scheduled runs may be delayed. The job extracts model page IDs from OpenAI's official model catalog, then matches them exactly against `openai.models` in `models.dev/api.json`. The official catalog supplies model identities; `models.dev` supplies prices. Matching the two requires no API Key. Actions downloads are limited to 2MB for the official catalog and 8MB for `models.dev`, both with a 5-second socket timeout and a 20-second total timeout per source; redirects are rejected. Synchronization covers GPT 5.4+ models that support text output and text/image/PDF input and provide both input and output prices. Cache read and write prices may be absent. Name suffixes such as dates, `preview`, or `latest` cannot be used to guess imports; new naming patterns require review.

Long-context pricing accepts only a single, explicit context tier from `models.dev`. It uses `tier.size` directly as the threshold, applies this tool's request pricing scope, and records `models.dev` as the rule data source. Request pricing is the sole rule; the catalog provides no selectable scope. New models with valid explicit tiers can be imported automatically without local rules for each model. Entries remain pending if they only have the older `context_over_200k` without an explicit threshold, have multiple pricing tiers, or have unknown pricing dimensions; values are neither guessed nor filled with zero. Unmatched models, models missing required prices, and unsupported models are recorded in Actions logs. Existing models retain their historical prices.

A read-only validation job, running only on this repository's `main`, generates a JSON-only candidate catalog. A separate publication job validates it again using trusted code before atomically updating the fixed `prices.json` path through the GitHub API under one `expectedHeadOid`. Candidates must match the current catalog fields, have a top-level `source` fixed to `models.dev`, and contain no code. Parsing, test, or candidate-price anomalies stop publication. These include deletion of any existing model, disappearance of many models, any present rate of a new model being zero, changes in the zero-price status of existing models, drastic unit-price changes, or changes in long-context thresholds. Loss of an existing rate also stops publication; a previously absent optional cache rate becoming a valid positive rate can be updated automatically. In these cases, manually check upstream prices, revise the baseline prices, and rerun. There is no automatic bypass switch.

When parsing and tests both succeed, price changes are committed immediately. However, `verified_at` can advance only if the upstream listing and prices of every existing model in the catalog have been checked in the current run. If an existing model was not observed in that run, the tool retains its prices and lists the specific model in Actions logs. It does not advance the catalog-wide verification date even if other prices change or 30 days have elapsed. When all existing models have been checked and prices are unchanged, the date alone is refreshed and committed only after `verified_at` reaches 30 days old, to keep scheduled tasks in the public repository active. Before 30 days, no commit is made. `verified_at` records when the upstream prices and model list were checked in the current run; it does not prove official price verification or a fresh review of local long-context rules. This mechanism still trusts the GitHub repository and its publication process. The catalog and source fields are unsigned and cannot independently prove authentic prices. Publication write access is also repository-wide and cannot be restricted to a single path.

Unknown models do not inherit another model's prices. If per-request input size is missing and the cumulative increment exceeds the long-context threshold, or if the details of the last request conflict, the event's tier is unknown. If the cumulative increment does not exceed the threshold and details do not conflict, all requests within it can be determined to belong to the short-context tier. When cache-read tokens exist without a corresponding price, known costs can still be calculated, but total cost is marked incomplete. Therefore:

- `api_cost_usd` is numeric only when total cost is complete; otherwise, it is `null`.
- `known_api_cost_usd` always provides the known portion of cost. An entire event with an unknown tier is excluded from this subtotal; other events are calculated normally.
- `unpriced_usage` represents usage from unpriced models; `uncertain_pricing_usage` represents usage whose pricing conditions cannot be determined. In `pricing_issues`, `unknown_request_size` marks an unknown tier, `unknown_cache_read_price` marks a missing cache-read price, and `unknown_cache_write_price` marks a missing cache-write price. Other known costs can still be included in the subtotal when cache rates are missing.

Quota sources are controlled by `--quota`:

| Option | Behavior |
| --- | --- |
| `auto` (default) | Try a live query; fall back to log snapshots on failure. |
| `live` | Only try a live query. |
| `logs` | Only use quota snapshots from logs. |
| `off` | Do not read quota or create a live-query task. |

At startup, `report` and `watch` fetch the required price catalog and live quota concurrently when using `--quota auto` or `live`. Expected quota failures still follow the rules in the table above. A startup price-download or validation failure exits with status code 1 and cleans up the quota task. A normal report scans logs only after quota observation completes, counting cycle usage up to the observation time. `logs` and `off` start no Codex query tasks or processes; `prices` does not read account quota.

Each live query starts one `codex app-server --listen stdio://` through asynchronous subprocess pipes, completing the initialize response, initialized notification, and account/rateLimits/read request in that order, without keeping a persistent connection. The whole query has a default total timeout of 10 seconds and an 8MiB response-buffer limit. Timeout, cancellation, and exit terminate and wait for subprocesses and close their pipes. Live quota queries read only the current app-server's `rateLimitsByLimitId` and `rateLimitResetCredits.availableCount`. They do not parse the older single-bucket `rateLimits` or top-level snake_case aliases. The live interface and log snapshots are separate current data sources; `--quota logs` and the log fallback for `auto` remain available.

`watch` refreshes every 2 seconds by default. In `auto` or `live` mode, startup waits for the first live quota query to finish before displaying the first frame; each subsequent query begins after a default 30-second wait following completion of the previous query. Use `--refresh` and `--quota-interval` to adjust the display refresh interval and the wait after each query, respectively. `--count N` exits after N refreshes; `0` means continuous operation. With multiple quota buckets, the `codex` bucket is preferred by default; use `--limit LIMIT_ID` to switch. TTY output uses colored cards with rounded corners and quota progress bars by default: titles/borders are cyan, amounts are highlighted, and low/medium/high quota usage is green/yellow/red. `--no-color` or `NO_COLOR` disables ANSI; `--color` enables colors even without a TTY. JSON never contains styling control codes.

Each available quota window is identified as **Primary**, **Secondary**, or **Individual Limit**, and labeled **Current cycle**. It shows the actual cycle start, reset time, and observation time, along with the account's used/remaining proportions, **LOCAL API COST** (**Local Cost Used** on narrow cards), **EST. TOTAL**, **EST. REMAINING**, and token details. The full report also shows cache writes, reasoning, session/event counts, the proportion of tokens with complete pricing, and the cycle start. Amounts use columns on wide screens; narrow cards retain local cost. watch uses compact cards and displays them side by side when the width is at least 96 columns and there are exactly two windows.

Each report/watch recalculates the current cycle from the interface's latest reset time minus `window_minutes`, counting usage up to the quota observation time. This is independent of the **Selected report period** chosen with `--today`, `--days`, `--since`, or `--until`. It does not assume a week or any other fixed duration; any valid window length can be used. After a manual early reset updates the interface boundaries, the tool switches to the new cycle and excludes old-cycle usage even if the log cache has not changed. A reset is not inferred solely from a decrease in the used percentage. Even when quota usage is 0 or extrapolation is unavailable, the tool displays available local cycle usage and cost and explains that projections are unavailable. Expired or invalid windows are shown only as unavailable and cannot serve as the current cycle. Local projections are not official quota figures. If the mapping between models and quota buckets cannot be determined, the tool does not make these projections for dedicated quota buckets.

## JSON

With `--json`, a normal report outputs one strict JSON document; `watch --json` outputs one NDJSON line per frame. `--output FILE` atomically writes complete JSON, saving the last frame in watch mode. Output does not overwrite the script itself or session JSONL logs. Terminal date groups are ordered newest first, while models and sessions are ordered by total token usage descending. `--top` truncates only the terminal display; JSON always contains complete grouping data.

During continuous refresh, `ReportCache` reuses session, pricing, and summary results for unchanged logs. A sliding time window is reaggregated only when it crosses a usage-event boundary.

The top-level report JSON includes `version`, `generated_at`, `period`, `roots`, `pricing`, `diagnostics`, `summary`, and `sessions`. `pricing` includes the catalog's `verified_at`, `catalog_source` (only `github` at runtime), the current download time `fetched_at`, the staleness flag `stale`, and `warnings`, as well as the top-level price source `price_source` and model identity source `model_source`. Each model includes `model_source` and `rule_source` at the same level, and retains ordinary unit prices and the long-context price fields `long_input`, `long_cached_input`, `long_cache_write`, and `long_output`. `cached_input` and `long_cached_input` can be `null`. Catalogs not checked for more than 45 days are marked stale with a warning. `prices --json` returns the same catalog metadata and per-model price fields. `prices.json` accepts only the current fields: the top-level `provider` is `openai`, `model_source` is the official model catalog, and `source` is strictly the `models.dev` price source. Each model's `model_source` must be its corresponding official model page. Long-context `long_context.source` accepts only `models.dev`, which is also used for the output’s `rule_source`. Without long-context pricing, `rule_source` is `null`. These source fields indicate data provenance, not signatures or official price verification. `summary` retains the usual semantics of `usage`, `by_model`, `by_day`, `by_session`, `unpriced_usage`, and `api_cost_usd`, and adds `known_api_cost_usd`, `estimate_is_partial`, `uncertain_pricing_usage`, and `pricing_issues`. Each session may contain `parent_session_id`. The catalog and reports have no `schema_version` or replacement version number. Catalogs reject the obsolete `long_context.scope` field, report and `prices` output omit `long_context_scope`, and long-context pricing always uses request scope. Only the current format is maintained, without fallback or migration compatibility for older formats; arguments do not accept abbreviations or old aliases.

## Data quality diagnostics

Report and JSON `diagnostics` provide factual notices from scanning and parsing, such as unreadable directories or files, malformed JSON lines, invalid usage snapshots, unfinished log lines, duplicate snapshots, inherited fork baselines, or fork baselines that cannot be attributed. Counter resets are handled as new segments; decreases in individual counters produce diagnostics. Control characters are filtered from log-derived text displayed in the terminal.

## License

This project is licensed under the [GNU Affero General Public License v3.0](./LICENSE), with SPDX identifier `AGPL-3.0-only`.
