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

Windows examples explicitly use `curl.exe` to avoid the different `curl` alias in older PowerShell versions. If `python` is missing or opens the Microsoft Store, but `py --version` works, replace `python` with `py`. The default local time zone and `--timezone UTC` need no extra dependencies. For IANA names such as `--timezone America/New_York`, run `python -m pip install tzdata` if the system has no time-zone database, as is typical on Windows.

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

## Experimental segmented estimate (opt-in)

`segments` is a separate experiment; the default report, quota panel, and `watch` behavior are unchanged. It compares explicitly paired quota observations with fully evidenced local request receipts to give a **conditional API-equivalent amount for the declared workload**. It is not an official quota, subscription dollar balance, or account-wide cost. Account percentages remain the primary quota display.

### Version 1: one declared workload

Start with a read-only diagnosis of your existing local logs (use your actual directories if `CODEX_HOME` differs):

```sh
python3 ./oai-usage segments diagnose --root "$HOME/.codex/sessions" --root "$HOME/.codex/archived_sessions" --price-catalog ./prices.json
```

This offline route shows data adequacy, historical candidate pairs, and possible local API cost between log timestamps. Historical candidates remain ineligible and no `K` is fitted: landing timestamps cannot prove request boundaries or fresh quota observations. Costs cover all selected local sessions; candidate intervals can overlap, so never sum them. Add `--json` for full diagnostics.

From a repository checkout, try the synthetic, entirely offline analysis example:

```sh
python3 ./oai-usage segments analyze --input tests/fixtures/segments/complete.json --price-catalog tests/fixtures/prices.json
```

Add `--json` for structured output, or `--output trial-result.json` to save JSON to a new file. Analysis reads only the named manifest and price catalog: no network, log discovery, credential reads, or automatic receipt importer. The fixture checks arithmetic and rejection rules; it does not validate real-world accuracy.

For a real trial, you may voluntarily run these one-shot commands on your own computer with native Codex already signed in. Capture `before-1` before the deliberately bounded local workload, and `after-1` after it finishes and you can support the settlement assumption:

```sh
python3 ./oai-usage segments snapshot --id before-1 --account-key local-account-a --mode standard --window secondary --output before-1.json
# Run the bounded local workload and review the evidence before the second command.
python3 ./oai-usage segments snapshot --id after-1 --account-key local-account-a --mode standard --window secondary --output after-1.json
```

Each invocation makes one live quota query and prints JSON; it does not start background collection, read credential contents, or replay work. The local account label and declared mode are not verified by the RPC. Retrieval time is not the server's measurement time, and waiting does not prove that quota has settled.

You must manually author the manifest described in [the format and evidence guide](./docs/segmented-quota-format.md). Ordinary JSONL arrival/completion timestamps do **not** establish both request START and END. Existing historical quota snapshots and the first `token_count` after a gap cannot establish a fresh baseline. If trustworthy timing, complete receipts, exclusive device use, boundary safety, settlement, or pool attribution is unsupported, leave the corresponding assertion false and accept `cannot_estimate`; never manufacture evidence or set assertions true just to obtain a number.

Predeclare the collection period and all attempted segments for one account/plan/pool/window/mode and one workload definition. Keep failed attempts and zero-percentage-change segments; a rejected attempt blocks its exact window/reset/source group. Cross-reset attempts block both affected groups; unrelated cycles remain separate. Reset changes or drift are not merged. The estimator uses `K = 100 × Σ(local API cost) / Σ(percentage-point change)`, reports the actual model mix, and never averages per-segment ratios. Its optional range covers only declared endpoint quantization (at least ±1 percentage point per endpoint, with cancellation only for shared snapshot IDs; this is a chosen assumption, not a server-rounding guarantee). The ≥5-point signal and ≤50% relative quantization bound are engineering gates, not statistical sufficiency, independent samples, a confidence interval, or demonstrated accuracy.

### Version 2: alternative per-model scenarios

A `schema_version: 2` manifest separates controlled, single-profile intervals by exact model, speed, effort, and cache workload. Each row answers “if this same account quota were used entirely for this profile, what full-cycle API-equivalent amount would it represent?” It also estimates the amount remaining **as of an explicitly selected quota snapshot**, when that snapshot and its uncertainty pass validation. These are alternatives: never add their totals or remaining amounts, and never treat their ratios as universal model quota weights.

Try the entirely invented two-model example:

```sh
python3 ./oai-usage segments analyze --input tests/fixtures/model_segments/two_models.json --price-catalog tests/fixtures/model_segments/fictional_prices.json
```

The synthetic example gives a nominal full-cycle equivalent of $75 for `synthetic-astra` and $100 for `synthetic-sol`; a snapshot showing 40% remaining gives $30 or $40 respectively. These names, prices, observations, and amounts are test data, not actual model prices, measured quotas, or validation of calibration accuracy. Add `--json` to inspect the actual token/cache mix, applicability, numeric price fingerprint, quantization-only bounds, and reasons for unavailable estimates.

- A result applies only to the same account, plan, pool, window, reset, observation source, mode, model/speed/effort/cache workload, and numeric price basis. A changed profile needs its own calibration
- Predeclare all attempted segments and retain failures and zero-delta costs. A predeclared mixed interval (`workload_id: null`) is diagnosed as unsupported; no API-price or token-share apportionment is performed. Unexpected mixing in a single-profile interval invalidates the affected profile groups
- `quantization.rounding` explicitly selects an assumption: `unknown` uses ± one declared resolution step; `floor`, `nearest`, and `ceil` use their respective closed outer bounds. The official rounding rule is not known. A snapshot cancels only when its exact ID is shared within the same profile; independent fragments keep their boundary uncertainty, including across model switches
- The ≥5-percentage-point signal, ≤50% relative percentage error, and ≤50% total-range-width/nominal gates are engineering screens, not statistical confidence intervals or demonstrated precision. Weak evidence keeps the point estimate unknown; bounds may remain diagnostic, with a `null` upper bound meaning unbounded
- Missing, mismatched, or older-than-calibration current snapshots can leave remaining unknown while a valid total remains available. Near 100% used, remaining uncertainty may be too wide even when the total passes

See the [version 2 format, formulas, rounding examples, and safe authoring template](./docs/model-quota-scenarios.md). There is still **no automatic trusted-log sampler or receipt importer**. `segments diagnose` remains a read-only historical adequacy check and cannot produce a calibrated estimate. Leave unsupported attestations false; do not type claims you cannot support to obtain a number.

### Historical logs: isolate reset epochs

```sh
python3 ./oai-usage segments historical --root "$HOME/.codex/sessions" --root "$HOME/.codex/archived_sessions" --price-catalog ./prices.json
```

This offline mode reads explicitly selected logs and prices through the response/fork/compaction-reconciled ledger. It does not sample live quotas. Each quota epoch is calibrated separately by model, observed service tier, and reasoning effort. A new window, same-deadline decrease, or unresolved timestamp alias breaks continuity. Possible grants and cache regressions stay ambiguous; recovering an old peak never pools them back into an earlier epoch. Returning old-window caches cannot become the current balance.

Primary amounts appear only when the epoch has no unresolved ambiguity and the signal, quantization, and predefined sensitivity checks pass. Unbounded, wide, or unstable results stay unknown; diagnostic nominal ratios are not reliable capacities. Zero-percentage-change costs remain included, and disconnected fragments retain their endpoint errors. Historical results and the current cycle are labeled separately; historical coefficients are not transferred to the current cycle.

Add `--json` for all predefined policies, offsets, epoch boundaries, hashed observer provenance, and rejection reasons. This historical output version is independent of the strict manifest schemas. Default report/watch/JSON and `segments analyze`/`diagnose` remain unchanged. See [historical rules, ambiguity, and output](./docs/historical-quota-heuristic.md). These engineering screens do not prove account attribution, absence of other devices, synchronized settlement, or predictive accuracy.

### Robust one-sided contamination model (offline, conditional)

`segments robust` looks for repeated low-consumption-rate groups within separate reset epochs and validates them on later evidence. It retains zero-delta costs, shared endpoint error, and every preset block/offset alternative; it does not maximize or average inferred capacities. Nonnegative external usage, sufficient near-clean support, workload stability and bounded measurement error are explicit assumptions, not facts proven by local history. Failed estimates still expose candidates, support and reasons.

```sh
python3 ./oai-usage segments robust --root "$HOME/.codex/sessions" --root "$HOME/.codex/archived_sessions" --price-catalog ./prices.json
```

The holdout split uses unique observed target-workload exposure, shared across timing variants. Additional grouped-path diagnostics retain contiguous small-signal evidence without changing physical bounds or promoting primary totals; one-sided bounds explicitly keep the capacity upper bound unbounded.

Revision 4 displays a supported stable-frontier conditional estimate even when strict holdout evidence is limited, with an explicit confidence label and separate quantization, endpoint-error and timing sensitivities. Original-atom constraints remain intact; no-clean contamination remains unidentifiable. See [the R4 design](./docs/stable-frontier-r4.md).

Primary totals are conditional alternatives for the same model and epoch, not additive balances or transferable cycle coefficients. Offline caches without independent freshness evidence leave current remaining unknown. See [the algorithm, identification assumptions, and validation](./docs/robust-quota-estimator.md).

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

Dates use the local time zone by default. UTC needs no extra data; other named time zones use the system IANA database or the first-party [`tzdata`](https://docs.python.org/3/library/zoneinfo.html#data-sources) package. Missing named zones produce a hint to check the name or install `tzdata`. Terminal dates show the actual offset at that point in time as `UTC±HH:MM`, or `UTC` for a zero offset.

## Development and testing

Maintain `oai-usage` directly, without a build step. Actions run [`scripts/update_prices.py`](./scripts/update_prices.py) every 4 hours to maintain the catalog from models.dev's `openai.models`, then validate and publish through [`scripts/publish_prices.py`](./scripts/publish_prices.py). Only the current data format is maintained; publish the program and catalog together when it changes.

Run tests:

```sh
python3 -m pip install tzdata
python3 -W error::ResourceWarning -m unittest discover -s tests -q
```

`tzdata` is a full-suite test dependency so named-zone, DST, and midnight-boundary tests all run. CI installs it on Ubuntu and Windows and sets an empty `PYTHONTZPATH` to exercise the package data source. The app-server is simulated in tests. Real Windows account queries, cycle projections, and Ctrl+C cleanup still need verification on a Windows machine.

## License

[GNU Affero General Public License v3.0](./LICENSE), SPDX: `AGPL-3.0-only`.

### R5: joint original-reading constraints (experimental)

The new offline `segments joint` path preserves original adjacent quota readings
and shared interior endpoints. It solves one epoch/profile/alignment hypothesis
jointly, with all admitted edges one-sided and selected whole portions carrying
aggregate 5% nuisance budgets. Discovery grids do not add independent evidence;
alternative timing offsets are unioned, never intersected. `segments robust`
retains R4 for comparison. See [R5 method and limitations](docs/joint-raw-estimator-r5.md).

```sh
python3 ./oai-usage segments joint --root /explicit/authorized/logs --price-catalog ./prices.json --json
```
