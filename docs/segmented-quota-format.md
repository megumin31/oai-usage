# Experimental segmented quota manifest, version 1

This is an opt-in, manually authored interchange format for `oai-usage segments`. It does not change normal reports or quota collection. The result is a conditional local-workload API equivalent, not an official quota, account-wide cost, subscription dollar balance, or accuracy claim.

中文入口：本格式需手工编写，不会从普通日志自动构造可信的请求起止证据。下方模板中的断言均为 `false`；缺少证据时请保持原样，接受无法估算。不要把示例时间或合成 fixture 当成真实观测。

## Start with your existing logs

Use explicit local log roots and a local price catalog; replace these paths if `CODEX_HOME` or the native environment differs:

```sh
python3 ./oai-usage segments diagnose --root "$HOME/.codex/sessions" --root "$HOME/.codex/archived_sessions" --price-catalog ./prices.json
python3 ./oai-usage segments diagnose --root "$HOME/.codex/sessions" --root "$HOME/.codex/archived_sessions" --price-catalog ./prices.json --json
```

For a repository-only demonstration without private logs:

```sh
python3 ./oai-usage segments diagnose --root tests/fixtures/segments/logs --price-catalog tests/fixtures/prices.json
```

`--root` is required and repeatable. Diagnosis reads the chosen logs without querying Codex, fetching prices, reading credential contents, or changing logs. It uses the existing accounting/deduplication and pricing path, surfaces data-adequacy blockers, and lists historical adjacent candidate pairs per logical session/pool/window with possible local cost between log timestamps. These costs cover all selected local sessions and are not attributed account/pool charges. Candidate intervals can overlap: never sum their costs or quota deltas. Zero-delta pairs remain visible, and no fixed idle timeout invents activity boundaries. Task-boundary markers are raw occurrences, including duplicate files, not unique tasks; they do not prove complete per-request timing. If logs change during the two read passes, or selected source coverage is missing/unreadable/incomplete, candidate cost pairing becomes unavailable rather than silently pairing stale costs. The terminal shows the first 20 candidates; JSON includes every pair and its blockers. This is a practical inspection route, not an automatic trusted-manifest importer: every historical candidate remains ineligible, and diagnosis fits no `K` or quantization range. A log timestamp is not a request's complete START/END evidence or a fresh quota measurement. `--output diagnostics.json` saves JSON to a new file.

## Try it without collecting anything

Run from the repository root:

```sh
python3 ./oai-usage segments analyze --input tests/fixtures/segments/complete.json --price-catalog tests/fixtures/prices.json
python3 ./oai-usage segments analyze --input tests/fixtures/segments/complete.json --price-catalog tests/fixtures/prices.json --json
```

`--output result.json` saves JSON to a new file; `--output -` emits JSON on stdout. Existing output files are refused. `analyze` reads only the explicit manifest and local price catalog, with no network, Codex query, log scanning, credential access, or automatic importer. The fixture is synthetic test data, not evidence of a measured account or validation of predictive accuracy.

`segments snapshot` is a separate, voluntary, one-shot action on a computer where native Codex is already signed in:

```sh
python3 ./oai-usage segments snapshot --id before-1 --account-key local-account-a --mode standard --window secondary --output before-1.json
# Run the bounded local workload, retaining independently evidenced request times.
# Capture the endpoint only when the required assertions can be supported.
python3 ./oai-usage segments snapshot --id after-1 --account-key local-account-a --mode standard --window secondary --output after-1.json
```

These commands each make one live quota query, always print JSON, and optionally save one snapshot object. They do not create a manifest, collect request receipts, start a background watcher, sign in, read credential contents, or automatically replay tasks. `--limit` defaults to `codex`; `--window` accepts `primary`, `secondary`, or `individual_limit`. `--codex-binary` and `--timeout` are available when needed. Do not run these commands as an unattended collection recipe.

## Evidence before numbers

1. Before observing ratios, declare a collection period, one context, one workload definition, and the rule to retain **all attempted segments**. Keep a record of every attempt, including failures and zero-delta segments. The software cannot prove that this declaration was made in advance or discover omitted attempts. Do not curate a favorable subset after looking at the results.
2. Obtain a baseline specifically for each attempted interval. After a gap, obtain a new baseline; never bridge unobserved activity by reusing an old endpoint. A deliberately adjacent interval may share the exact same boundary observation ID, subject to the strict request timing rules below.
3. Obtain complete per-request usage with independently evidenced START and END times and ownership. Ordinary JSONL landing times, a receipt's completion timestamp, or a cumulative counter alone do not establish the request's start. A first `token_count` after a gap cannot reconstruct a fresh baseline or the missing boundary evidence. There is no automatic historical-log-to-manifest importer in this MVP.
4. Historical quota snapshots are ineligible endpoints. Even a new RPC only establishes when its response was retrieved, not when the service measured usage. Cached quota, delayed posting, unrelated device/cloud activity, and usage attributed to another pool cannot be ruled out by these files. Idle time or repeated equal readings do not prove settlement.
5. Author the manifest from the evidence and explicitly review each assertion. Do not invent timestamps, ownership, completeness, or activity exclusions. Never mark an assertion true just to make the estimator run. If evidence is absent, leave the relevant assertion false and retain the failed attempt. `cannot_estimate` is a valid, expected result.

The manifest records asserted facts but does not authenticate them. Keep the underlying evidence locally for review; do not include credentials, email addresses, or account secrets. `account_key` should be an opaque local label, not an authentication token.

## Complete authoring shape

This illustrative template includes every required key. Its dates, labels, model, and token counts are invented. It deliberately keeps every assertion false and therefore **must not yield an estimate**. Replace values only with supported observations, not by copying the synthetic fixture's attestations. Snapshot files are objects to insert into `snapshots`; `before` and `after` are IDs, not filenames.

```json
{
  "schema_version": 1,
  "context": {
    "account_key": "local-account-a",
    "plan_type": "plus",
    "limit_id": "codex",
    "window_kind": "secondary",
    "mode": "standard"
  },
  "workload": {
    "label": "bounded local coding trial",
    "models": ["gpt-5.4"],
    "effort": "high"
  },
  "collection_period": {
    "start": "2026-10-01T10:00:00Z",
    "end": "2026-10-01T11:00:00Z"
  },
  "selection_policy": "all_attempted_segments",
  "snapshots": [
    {
      "id": "before-1",
      "context": {
        "account_key": "local-account-a",
        "plan_type": "plus",
        "limit_id": "codex",
        "window_kind": "secondary",
        "mode": "standard"
      },
      "capture_started_at": "2026-10-01T10:00:00Z",
      "observed_at": "2026-10-01T10:00:01Z",
      "window_minutes": 10080,
      "resets_at": "2026-10-05T00:00:00Z",
      "used_percent": 20,
      "percent_resolution": 1,
      "source": "app_server_rpc",
      "observation_basis": "retrieved_not_measured"
    },
    {
      "id": "after-1",
      "context": {
        "account_key": "local-account-a",
        "plan_type": "plus",
        "limit_id": "codex",
        "window_kind": "secondary",
        "mode": "standard"
      },
      "capture_started_at": "2026-10-01T10:30:00Z",
      "observed_at": "2026-10-01T10:30:01Z",
      "window_minutes": 10080,
      "resets_at": "2026-10-05T00:00:00Z",
      "used_percent": 30,
      "percent_resolution": 1,
      "source": "app_server_rpc",
      "observation_basis": "retrieved_not_measured"
    }
  ],
  "segments": [
    {
      "id": "attempt-1",
      "before": "before-1",
      "after": "after-1",
      "assertions": {
        "complete_local_receipts": false,
        "no_other_device_usage": false,
        "no_boundary_inflight": false,
        "settlement_assumed": false,
        "pool_attribution": false
      }
    }
  ],
  "receipts": [
    {
      "response_id": "response-1",
      "thread_id": "native-thread-a",
      "owner_thread_id": "native-thread-a",
      "owner_created_at": "2026-10-01T09:55:00Z",
      "started_at": "2026-10-01T10:05:00Z",
      "completed_at": "2026-10-01T10:06:00Z",
      "model": "gpt-5.4",
      "usage": {
        "input_tokens": 1000,
        "cached_input_tokens": 200,
        "cache_write_input_tokens": 0,
        "output_tokens": 100,
        "reasoning_output_tokens": 20,
        "total_tokens": 1100
      }
    }
  ]
}
```

All shown keys are required, and extra keys are rejected at each defined object level. Duplicate JSON keys, non-finite numbers, conflicting IDs, and malformed values are errors. Input is limited to 8 MB; each of `snapshots`, `segments`, and `receipts` has at most 100,000 items. Text identifiers/labels must be nonempty, no more than 200 characters, and contain no control characters. All timestamps must include an explicit time zone (`Z` or a numeric offset); comparisons use UTC.

### Context, workload, and period

- A manifest has exactly one `account_key`, `plan_type`, `limit_id`, `window_kind`, and `mode`. Every used snapshot must match all five. The RPC supplies plan/pool/window data but does not verify the local account label or declared mode. Keep distinct accounts, plans, pools, windows, and modes in separate predeclared collections; do not use a label to merge them.
- `workload.models` is a nonempty list of distinct exact model IDs. `label` describes the scope and `effort` declares the effort setting. Every contained receipt's model must be declared. The tool cannot verify effort, mode, task comparability, or a stable model mix. Do not repurpose a result for a different workload.
- `collection_period.start < collection_period.end`; each attempt's entire pair of captures must lie inside it. `selection_policy` must be `all_attempted_segments`. Activity in a genuine declared gap is reported as outside the segments; it is never charged to the next interval. Its absence from the estimate is not evidence that the account was idle.

### Snapshots and time boundaries

- `id` identifies one observation. Exact duplicates under the same ID are removed; conflicting duplicates are errors. Use the same ID when a real observation is shared between adjacent segments. Do not invent independent IDs for copies or infer independent measurements from repeated RPC calls.
- `capture_started_at` is the beginning of the query/observation operation; `observed_at` is its receipt time. The former must be at or before the latter. `observation_basis` must be `retrieved_not_measured`.
- `source` is `app_server_rpc`, `user_observation`, or `historical_log`. A manually documented `user_observation` still needs genuine evidence and all assertions. `historical_log` is recognized only to reject it as an estimation endpoint. A segment cannot mix observation sources.
- `window_minutes` is an integer from 1 to 5,256,000. Both capture times must be inside the declared cycle: `resets_at - window_minutes <= capture_started_at <= observed_at < resets_at`.
- `used_percent` is a finite number in `[0, 100]`. `percent_resolution` is a finite number in `[1, 100]`, in **percentage points**, treated as a full ± error at each endpoint. Version 1 never accepts better-than-one-point precision; the snapshot command writes `1`. This ±1-point minimum is a chosen conservative assumption, not a verified guarantee of server precision or rounding; it does not bound caching or delayed posting.
- A pair must satisfy `before.observed_at < after.capture_started_at`, with identical window length, reset instant, and source. A reset change or drift inside a pair rejects it. Valid pairs with different reset identities are reported in separate groups and never pooled. No tolerance merges near-equal reset timestamps.
- Segments must not overlap. A literal shared endpoint ID can join adjacent segments; an alias for the same timestamp is not evidence for a separate interval.

### Receipts and ownership

A receipt is per response, not a cumulative counter or inferred residual. `response_id` must identify the response across retransmissions. An exact duplicate is removed; conflicting usage, timing, model, or thread data for the same native response is an error.

`thread_id` is the response's original native thread ID from its receipt; `owner_thread_id` identifies the logical native thread owning the containing rollout, and `owner_created_at` is that rollout owner's creation time. These are native execution identities, not the cloud coordinator's Work/dot conversation ID. A parent's receipt copied into a fork keeps the parent's `thread_id`, while the containing rollout has the child's `owner_thread_id`; do not relabel it as child activity. Owner/thread mismatches are excluded as inherited copies. When the thread and owner match, a receipt starting before its owner's creation is invalid, even if it also completed before creation. No branch may claim the same response as new usage.

For an eligible contained receipt, the strict ordering is:

```text
owner_created_at <= started_at <= completed_at
before.observed_at < started_at <= completed_at < after.capture_started_at
```

Both START and END need evidence. A request crossing **or touching** a capture boundary rejects every affected segment, even when its completion looks assignable. A receipt cannot be assigned to multiple attempts. Declared no-inflight assertions do not override these checks.

All six usage fields in the template are mandatory, including zeroes. Each must be a finite, nonnegative whole number no greater than `2^63 - 1`; booleans are invalid. `total_tokens = input_tokens + output_tokens`, `cached_input_tokens + cache_write_input_tokens <= input_tokens`, and `reasoning_output_tokens <= output_tokens`. Reasoning is included in output and must not be added again. Use the complete observed request vector for price-tier selection. Unknown model prices, missing required cache prices, or any other incomplete pricing reject the containing segment instead of silently treating unknown cost as zero.

### Assertions

All five keys are mandatory explicit booleans, reviewed separately for each attempt:

- `complete_local_receipts`: the interval's local request usage is fully represented, with trustworthy ownership, start/end timing, and usage vectors
- `no_other_device_usage`: no other device, cloud-coordinated task, or other unrepresented account activity contributed to the observed change
- `no_boundary_inflight`: no request is in flight across either quota observation boundary
- `settlement_assumed`: the user can support the assumption that the endpoints correspond to settled usage; the program cannot establish freshness or settlement
- `pool_attribution`: the included workload actually consumes the declared account, plan, pool, window, and mode

Any false assertion rejects the attempt. These flags are user attestations, not measurements or proof supplied by the program. If the available evidence cannot support an assumption, leave it false. Do not remove that attempt to make other estimates appear valid.

## Estimator and interpretation

Within each identical window/reset/source group, let `C` be the sum of eligible local API-equivalent request costs and `D` the sum of endpoint changes in percentage points. The conditional full-cycle equivalent is:

```text
K = 100 × C / D
```

This is a ratio of sums (percentage-change-weighted when each individual ratio exists), never an unweighted average of segment ratios. Zero-change segments, including those with positive local cost, remain in the sum; they do not have a usable standalone ratio. A negative delta rejects the attempt. A positive delta without positive local cost also rejects it. Any endpoint at 100% rejects the attempt as `quota_saturated`: possible over-cap or censored usage cannot be bounded by ordinary rounding assumptions.

The quantization-only range uses each endpoint's declared `[max(0, p-r), min(100, p+r)]`, where `r = percent_resolution >= 1` percentage point. Shared snapshot IDs are collected algebraically, so an exact shared interior observation cancels. Other endpoint errors are conservatively bounded without assuming independence, a probability distribution, or averaging-down of error. If the aggregate delta lies in `[L, U]` with `L > 0`, the displayed range is `[100×C/U, 100×C/L]`.

The current engineering gates require positive local cost, `D >= 5` percentage points, `L > 0`, and `max(D-L, U-D)/D <= 0.5`. These thresholds are not a sample-size test or evidence of statistical sufficiency. More segments, more receipts, or passing the gates does not establish independent samples, accuracy, or generalization. Any rejected attempt blocks its exact window/reset/source group; it is still listed with reasons. A cross-reset attempt blocks both affected groups. Other cycle groups are assessed separately; their successful estimates cannot rehabilitate or hide a rejected group. Schema errors abort analysis instead of producing a partial estimate.

JSON labels the trust level as conditional on user assertions (historical diagnosis is unverified), retains before/after percentages and the last recorded account percentage with its observation time, and reports attempted/eligible/rejected counts, per-attempt reasons, duplicates and inherited exclusions, outside-segment receipts, observed span and active duration, actual model costs/token usage/cost fractions, group delta bounds, and `conditional_estimate` or `cannot_estimate`. On failure, the equivalent and range are `null`; `statistical_confidence_interval` is always `null`. Inspect `rejection_reasons` and each group's `reasons`, rather than interpreting a missing number as zero.

The range excludes delayed posting, cached quota, unknown account activity, misattribution, incomplete or false attestations, price mismatch, changing model mix/effort, and other systematic errors. It is **not** a confidence interval. No held-out validation or future-workload accuracy is supplied, and mixed-model output describes only the observed mixture. Official account percentages remain authoritative.
