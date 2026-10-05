# Experimental per-model quota scenarios, version 2

Version 2 of `oai-usage segments analyze` estimates **alternative, conditional API-equivalent scenarios** from explicitly paired observations and complete request receipts. It does not change the default report, quota panel, `watch`, or the [version 1 workload estimator](./segmented-quota-format.md). Official account percentages remain the primary quota information.

中文入口：版本 2 只用单一模型／速度／推理强度／缓存配置的合格片段分别校准，回答“如果同一额度全部用于这一配置，相当于多少 API 金额”。整周期和剩余金额都是备选情景，不能相加，也不是通用模型权重。整数百分比的取整规则是显式假设，不是已知官方规则；分段、换模型或多采几次都不会自动消除独立边界误差。目前没有自动生成可信证据的日志采样器或收据导入器；模板断言故意为 `false`，不能仅为得到数字而改成 `true`。

## What a row means

A row answers two distinct questions:

1. **Estimated total:** if the whole quota cycle were spent on this same model/workload profile, what nominal API-equivalent amount would that represent?
2. **Estimated remaining:** if the quota remaining at the explicitly selected snapshot were spent on that profile, what nominal API-equivalent amount would that represent?

Rows are alternatives for the **same account pool**, not separate allocations. Never sum their totals or remaining amounts. A $75 scenario and a $100 scenario do not give a $175 quota. They do not establish a universal quota weight or a stable exchange rate between models. The result is neither a subscription dollar balance, account-wide cost, official dollar allowance, nor a prediction guaranteed for future work.

Applicability is limited to the exact `account_key`, `plan_type`, `limit_id`, `window_kind`, `mode`, window length, reset instant, and observation source, together with the declared model, speed, effort, cache workload, and numeric price basis. Reset drift, other cycles, other pools, and other profiles are not merged. Two profiles may use the same model but different speed, effort, or cache workload; they still require separate calibration.

API-equivalent cost uses the selected price catalog and complete observed usage vectors, including cache reads, cache writes, output, and applicable price tiers. It does not infer quota consumption from the API price. All receipts are repriced on the supplied standard API basis; a speed label does not automatically add Fast, tool, or regional fees or establish a historical bill. Changing cache or input/output mix can change the relationship even when the model ID stays the same. JSON reports `observed_usage`, `observed_cache_read_fraction`, `observed_cache_write_fraction`, and `observed_output_to_input_ratio`; the labels alone are not evidence of a stable mix. Reasoning tokens are already part of output, and cached/cache-write tokens are parts of input, not additional tokens to add again.

`price_fingerprint` is a SHA-256 fingerprint of the selected catalog's numeric model prices and tier configuration, with equivalent decimal spellings normalized. Source metadata is excluded. It identifies a price basis, not trustworthy provenance or real-world price verification. The same fingerprint is included in every row's `applicability`. A changed price basis requires recomputing the scenario; it cannot be silently treated as the same coefficient.

## Run the fictional examples

From the repository root:

```sh
python3 ./oai-usage segments analyze --input tests/fixtures/model_segments/two_models.json --price-catalog tests/fixtures/model_segments/fictional_prices.json
python3 ./oai-usage segments analyze --input tests/fixtures/model_segments/two_models.json --price-catalog tests/fixtures/model_segments/fictional_prices.json --json
```

The [complete two-model manifest](../tests/fixtures/model_segments/two_models.json) and [fictional catalog](../tests/fixtures/model_segments/fictional_prices.json) contain only invented data:

- `synthetic-astra`: local cost $15 over 20 percentage points gives a nominal full-cycle equivalent of $75
- `synthetic-sol`: local cost $20 over 20 percentage points gives a nominal full-cycle equivalent of $100
- The explicitly selected snapshot is 60% used, or 40% remaining, so the alternative nominal remaining amounts are $30 and $40

Both fake model IDs use invented rates. The required catalog source URL, provider, and verification date are schema compatibility metadata, **not provenance or verification for actual prices**. See the [fixture explanation](../tests/fixtures/model_segments/README.md). These examples test arithmetic and rejection behavior, not real account capacity, official model prices, calibration precision, or held-out predictive accuracy.

`--json` returns every scenario and its diagnostic fields. `--output result.json` saves JSON to a new file; `--output -` writes JSON to stdout. Existing output files are refused. Analysis reads only the explicit manifest and local price catalog: no network, live quota lookup, automatic log discovery, credential reads, request replay, or receipt importer.

Other executable examples use the same command and price catalog:

| Manifest in `tests/fixtures/model_segments/` | What it demonstrates |
| --- | --- |
| [`quantization_trap.json`](../tests/fixtures/model_segments/quantization_trap.json) | A fictional true 1.99-point change displayed as 1 point under a floor assumption; no point estimate |
| [`continuous_zero_delta.json`](../tests/fixtures/model_segments/continuous_zero_delta.json) | Same-profile shared endpoints cancel, while zero-delta cost stays in the sum |
| [`independent_fragments.json`](../tests/fixtures/model_segments/independent_fragments.json) | Separated fragments keep four uncertain endpoints |
| [`many_small_fragments.json`](../tests/fixtures/model_segments/many_small_fragments.json) | Ten separate 2-point fragments total 20 points but retain 20 uncertain endpoints; no point estimate |
| [`alternating_models.json`](../tests/fixtures/model_segments/alternating_models.json) | A → B → A does not cancel A's boundaries across B's interval |
| [`mixed_workload.json`](../tests/fixtures/model_segments/mixed_workload.json) | A predeclared mixed interval stays visible as unsupported, without allocating its costs or delta to profiles |

## Evidence and the remaining collection gap

There is currently **no automatic trusted-log sampler or log-to-receipt importer** that supplies all required evidence. A manually authored manifest is an interchange format for evidence you actually have, not a way to turn unverified historical logs into a calibration by filling in assertions.

The read-only route for existing logs is still:

```sh
python3 ./oai-usage segments diagnose --root "$HOME/.codex/sessions" --root "$HOME/.codex/archived_sessions" --price-catalog ./prices.json
```

Use your actual local directories if they differ. Historical candidates remain ineligible, and diagnosis fits no coefficient or per-model scenario. JSONL arrival/completion times do not prove both request START and END; the first `token_count` after an idle gap is not a fresh baseline. Costs between historical log timestamps may overlap and must not be summed as independent intervals.

The optional, one-shot `segments snapshot` command is unchanged; see the [version 1 capture instructions](./segmented-quota-format.md#try-it-without-collecting-anything). It collects one quota response, not receipts or a complete manifest. Retrieval time is not server measurement time. Waiting, repeated equal readings, or a successful RPC does not prove freshness or settlement. No unattended sampler is started.

Before collecting observations, predeclare the collection period, context, workload profiles, every attempted interval, and `selection_policy: "all_attempted_segments"`. Keep failures and zero-delta attempts. After a real gap, obtain a new baseline rather than bridging unknown activity. The program cannot establish that the declaration was made in advance or detect an omitted attempt. Do not cherry-pick segments, profile labels, price bases, or rounding assumptions after seeing which result looks favorable.

The [version 1 evidence, ownership, timing, and assertion rules](./segmented-quota-format.md#evidence-before-numbers) continue to apply:

- Complete per-response usage needs genuine ownership and independently evidenced start/end boundaries. Exact retransmissions are deduplicated; inherited parent receipts do not become new child usage
- A contained request must satisfy `before.observed_at < started_at <= completed_at < after.capture_started_at`. A request crossing or touching a capture boundary rejects the affected attempts
- Every attempt explicitly attests to complete local receipts, no other-device/unrepresented usage, no boundary in-flight work, assumed settlement, and attribution to the declared pool. These are user assertions, not facts authenticated by the program
- Unknown or incomplete pricing, contradictory identities, overlapping intervals, negative quota changes, and saturated calibration endpoints are not repaired by a favorable cost ratio
- Unsupported assertions must stay `false`. Retain the failed attempt and accept `cannot_estimate`; never invent evidence or type false claims to obtain a number

Use opaque local account labels. Do not put credentials or account secrets in manifests.

## Complete version 2 authoring template

This is a complete JSON shape with every required field. All values are fictional. The false assertions are intentional: this template must return `cannot_estimate`, even though its arithmetic looks usable. The fixture's `true` values are assertions inside a synthetic test world, not a template for real evidence. Replace values only when supported by actual observations.

```json
{
  "schema_version": 2,
  "context": {
    "account_key": "fictional-account-no-real-data",
    "plan_type": "synthetic-plan",
    "limit_id": "synthetic-shared-pool",
    "window_kind": "secondary",
    "mode": "synthetic-mode"
  },
  "label": "FICTIONAL authoring shape; assertions intentionally false",
  "workloads": [
    {
      "id": "astra-standard",
      "model": "synthetic-astra",
      "speed": "standard",
      "effort": "high",
      "cache_workload": "uncached-input-only"
    }
  ],
  "quantization": {"rounding": "unknown"},
  "current_snapshot": "after-1",
  "collection_period": {
    "start": "2026-09-22T00:00:00Z",
    "end": "2026-09-22T01:00:00Z"
  },
  "selection_policy": "all_attempted_segments",
  "snapshots": [
    {
      "id": "before-1",
      "context": {
        "account_key": "fictional-account-no-real-data",
        "plan_type": "synthetic-plan",
        "limit_id": "synthetic-shared-pool",
        "window_kind": "secondary",
        "mode": "synthetic-mode"
      },
      "capture_started_at": "2026-09-22T00:00:00Z",
      "observed_at": "2026-09-22T00:00:01Z",
      "window_minutes": 10080,
      "resets_at": "2026-09-29T00:00:00Z",
      "used_percent": 10,
      "percent_resolution": 1,
      "source": "app_server_rpc",
      "observation_basis": "retrieved_not_measured"
    },
    {
      "id": "after-1",
      "context": {
        "account_key": "fictional-account-no-real-data",
        "plan_type": "synthetic-plan",
        "limit_id": "synthetic-shared-pool",
        "window_kind": "secondary",
        "mode": "synthetic-mode"
      },
      "capture_started_at": "2026-09-22T00:10:00Z",
      "observed_at": "2026-09-22T00:10:01Z",
      "window_minutes": 10080,
      "resets_at": "2026-09-29T00:00:00Z",
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
      "workload_id": "astra-standard",
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
      "response_id": "fictional-response-1",
      "thread_id": "fictional-thread-1",
      "owner_thread_id": "fictional-thread-1",
      "owner_created_at": "2026-09-22T00:00:00Z",
      "started_at": "2026-09-22T00:01:00Z",
      "completed_at": "2026-09-22T00:05:00Z",
      "model": "synthetic-astra",
      "workload_id": "astra-standard",
      "usage": {
        "input_tokens": 15000000,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "total_tokens": 15000000
      }
    }
  ]
}
```

### What changes from version 1

The top-level object requires `schema_version`, `context`, `label`, `workloads`, `quantization`, `collection_period`, `selection_policy`, `snapshots`, `segments`, and `receipts`; the only optional key is `current_snapshot`. Other extra keys are rejected. Version 2 replaces v1's singular `workload`; it does not accept both forms at once.

- `workloads` contains 1–100 profiles, each with exactly `id`, `model`, `speed`, `effort`, and `cache_workload`. All are nonempty strings without surrounding whitespace. IDs are unique. Duplicate applicability profiles are rejected, including differences only in the capitalization of speed/effort/cache labels. Model IDs remain exact
- Every segment adds required `workload_id`: a declared profile ID for a single-profile interval, or JSON `null` for an interval predeclared as mixed
- Every receipt adds required `workload_id`, which must be a declared profile ID, never `null`. Its `model` must match that profile. These declarations do not independently verify speed, effort, or cache-workload comparability
- `quantization` contains exactly `rounding`, one of `unknown`, `floor`, `nearest`, or `ceil`. It is a global assumption for the entire manifest, not a per-interval choice. The key is mandatory; the recommended conservative default assumption is `unknown`
- `current_snapshot` may be omitted, JSON `null`, or a snapshot ID. Omission or `null` requests no remaining estimate and never selects an endpoint automatically. An ID absent from `snapshots` produces remaining-only `unknown_current_snapshot`; it preserves otherwise valid totals. A missing required key or malformed ID is a schema error
- Snapshot structure is unchanged. Both versions retain `percent_resolution` in `[1, 100]` percentage points; v1's minimum ±1-point assumption is not silently relaxed. With `floor`, `nearest`, or `ceil`, non-boundary displayed percentages must be on the declared resolution grid; 0 and 100 are accepted clipped endpoints

All other strict shape, timestamp, usage-vector, duplicate, ownership, boundary, and collection rules are inherited from [version 1](./segmented-quota-format.md#complete-authoring-shape). Extra fields, duplicate JSON keys, malformed values, and conflicting identifiers are errors, not silently ignored input. The input limit is 8 MB; each snapshots/segments/receipts list has at most 100,000 items. A selected current snapshot can be a shared calibration endpoint or a separate later observation. Calling it “current” does not make it live.

### Pure intervals and mixed intervals

Only an explicitly single-profile interval can calibrate that profile. A predeclared `workload_id: null` interval is reported as `unsupported_mixed_workload`, with `mixed_workload_attribution_unsupported`, even if its other evidence is complete. Its receipts and known cost remain diagnostic. The estimator neither divides its quota delta by API-price/token shares nor fits a mixed-model regression. A clean, explicitly mixed attempt does not by itself invalidate separately controlled pure intervals.

An interval declared as one profile but containing a receipt assigned to another is rejected as `unexpected_mixed_workload`. A model/profile mismatch is also rejected; because the actual model’s speed/effort/cache variant is then ambiguous, every declared variant of that actual model is blocked too. Rejected attempts block the exact reset/window/source groups of the affected profiles, including profiles touched by unexpected mixing; cross-identity attempts affect both endpoint identities. Where an invalid attempt's affected profile cannot be determined, blocking is conservative. Invalid mixed attempts can therefore block affected profiles too. Unrelated identities remain separate.

Do not relabel a failed pure interval as predeclared mixed, drop it, or create new profile names after inspecting outcomes to recover a favorable point estimate. The software cannot prove that such selection did not occur.

## Quantization: explicit assumptions, not an official rule

Let displayed used percentage be `p` and `percent_resolution` be `r`. The global rounding assumption gives these **closed outer bounds**, each clipped to `[0, 100]`:

| `quantization.rounding` | Endpoint bound before clipping |
| --- | --- |
| `unknown` | `[p-r, p+r]` |
| `floor` | `[p, p+r]` |
| `nearest` | `[p-r/2, p+r/2]` |
| `ceil` | `[p-r, p]` |

The closed bounds intentionally include endpoints that an exact floor/ceil or tie-breaking rule might exclude. No particular official rounding method is known or discovered by this tool. `unknown` is a chosen conservative ±r assumption, not a guarantee that all server error is within ±r. Explicit methods are conditional sensitivity assumptions and should not be chosen to make a result pass. These bounds do not include delayed posting, cached readings, or hidden account activity.

### Why a displayed 1-point change can be misleading

In the entirely fictional [quantization trap](../tests/fixtures/model_segments/quantization_trap.json), true usage goes from 10.005% to 11.995%, a 1.99-point change. If a floor-to-integer display is assumed, it shows only 10% to 11%, a 1-point change. The invented local cost is $1.4925. Dividing by the displayed change would give $149.25 per full cycle; dividing by the fictional true change gives $75. The difference is nearly a factor of two even before other errors.

The analyzer sees only the displayed values. Under the floor assumption, its endpoint bounds are `[10, 11]` and `[11, 12]`, so aggregate change can be anywhere in `[0, 2]` percentage points. The diagnostic total bounds are `[$74.625, unbounded]`. The point estimate stays `null`: the one-point signal is below the gate, the lower delta bound is zero, and the range has no finite upper bound. The fixture's fictional true percentages are an explanation, not extra hidden precision supplied to the estimator.

### Shared observations versus independent fragments

Within **one exact profile and identity group**, define a coefficient for each snapshot ID: `+1` every time it is an after endpoint and `-1` every time it is a before endpoint. The total delta is the sum of these coefficient-weighted observations. Endpoint bounds are combined with the coefficient sign; the resulting aggregate lower and upper deltas are clipped below at zero.

- Adjacent intervals of the same profile sharing the literal observation ID cancel that interior endpoint algebraically. This does not require statistical independence
- Separate fragments retain both boundaries per fragment. More fragments do not make independent rounding errors average away; no probability distribution or independence assumption is used
- Equal percentages or equal-looking copied observations are not shared evidence. New IDs do not create precision, and duplicate/alias timing does not make overlapping intervals eligible
- A → B → A cannot cancel A's inner boundaries across the intervening B interval. A and B calibrate separately; summing their scenario rows would change the question
- A zero displayed delta still contributes its full eligible cost, its endpoint bounds, and the appropriate snapshot coefficients. It is never dropped for lacking a standalone ratio

For the same fictional $15 over 20 displayed percentage points under `unknown`, a continuous same-profile chain with shared boundaries has two uncancelled endpoints and delta bounds `[18, 22]`. Two independent fragments have four endpoints and bounds `[16, 24]`. They have the same nominal $75 total but different uncertainty. Ten separate 2-point fragments also total 20 displayed points, but their 20 uncertain endpoints produce delta bounds `[0, 40]` under the same assumption: the upper dollar bound is unbounded and the point estimate is withheld. Total displayed consumption alone is not a precision guarantee. The JSON fields `uncancelled_endpoint_count` and `boundary_coefficient_weight` expose this distinction.

At displayed zero, clipping makes the error asymmetric: with `unknown` and `r=1`, the used-percentage interval is `[0, 1]`, not `[-1, 1]`. Calibration at an endpoint of exactly 100% is rejected as `quota_saturated`; censored or over-cap usage cannot be bounded by these ordinary rounding assumptions.

## Total estimate, bounds, and engineering screens

For eligible single-profile intervals in the same identity group, let:

- `C` be summed local API-equivalent cost
- `D` be summed displayed percentage-point change
- `[L, U]` be the aggregated quantization-only delta bounds

The nominal full-cycle amount is a **ratio of sums**, never an unweighted mean of interval ratios:

```text
K_nominal = 100 × C / D
K_lower   = 100 × C / U
K_upper   = 100 × C / L
```

A point estimate is published only when there are eligible intervals, positive cost, no blocking rejected attempt, and all these engineering screens pass:

1. `D >= 5` percentage points
2. `L > 0` and `max(D-L, U-D) / D <= 0.5`
3. `(K_upper-K_lower) / K_nominal <= 0.5`

The third screen is additional to v1's percentage-error screen. Passing any of them does not demonstrate statistical sufficiency, independent samples, calibration precision, predictive accuracy, or a valid model for the account. They are engineering screens, not statistical confidence levels. `statistical_confidence_interval` is always `null`.

When a screen fails, `estimated_total_api_cost_usd` is `null`, and reasons explain why. Usable **diagnostic bounds may still be present** even though no point estimate is published. A bound `[lower, null]` means the upper amount is unbounded, not zero or a missing finite number; in particular, `L=0` cannot yield a finite upper amount for positive cost. No usable positive cost/delta bound yields no amount range. A blocked profile group suppresses bounds too, because reporting a range from a selected eligible subset would be misleading.

## Remaining amount is a separate as-of estimate

Only `current_snapshot` chooses the remaining-percentage observation. The analyzer never silently substitutes the latest calibration endpoint, queries live quota, or treats a historical date as the present. Output identifies `snapshot_id`, `observed_at`, `used_percent`, `remaining_percent`, context/reset, `usable_for_remaining`, and `is_live_lookup: false`. An observation that fails validation is labeled as an unusable selected snapshot, not presented as the row’s valid account balance.

A selected snapshot must match the exact context and row identity, come from an eligible source, and lie inside its declared cycle. It must not precede any calibration endpoint in that shared account-pool identity, even an endpoint used for another profile, or show a decrease relative to earlier calibration observations. A separate capture must start at or after the latest calibration observation; only the exact shared latest endpoint ID may overlap that capture boundary. Historical-log, mismatched, older-than-calibration, overlapping-capture, decreased/cached, or out-of-cycle snapshots make the remaining estimate unavailable. A structurally invalid snapshot instead aborts the manifest as a schema error.

A missing selection (omitted `current_snapshot` or JSON `null`), unknown selected ID, or failed current-snapshot check does **not** invalidate an otherwise valid full-cycle total. “Older” here means older relative to the supplied calibration observations; there is no wall-clock freshness guarantee or live balance claim.

Let current displayed usage be `p_now`, with quantization interval `[p_low, p_high]`. Remaining fractions and amounts are:

```text
f_nominal = (100-p_now) / 100
f_lower   = (100-p_high) / 100
f_upper   = (100-p_low) / 100
R_nominal = K_nominal × f_nominal
R_lower   = K_lower × f_lower
R_upper   = K_upper × f_upper
```

These nonnegative product bounds are conservative and do **not** assume independence between calibration error and the current snapshot. If the current snapshot is also a calibration endpoint, the same error can occur in both factors. The rectangular product construction remains an outer bound but can be looser than a joint, dependency-aware optimization; the tool does not claim it is tight.

Publishing `remaining.estimated_remaining_api_cost_usd` requires a valid total, a valid current snapshot, and `(R_upper-R_lower)/R_nominal <= 0.5`. Diagnostic remaining bounds can remain visible when the point is withheld for excessive uncertainty. At a nominal zero, a range exactly `[0, 0]` has zero relative width; any nonzero uncertainty around nominal zero fails the width screen. An unbounded total upper amount remains unbounded for positive possible remaining fraction, while an exactly zero possible remaining fraction yields zero.

Near 100% used, a small absolute percentage error can dominate the small remaining balance. For example, at 99% used under `unknown`, `r=1` means 0–2% may remain, not an exact 1%. Even a well-bounded full-cycle estimate can therefore have no reportable remaining point. At 100% used, the current snapshot is evaluated separately from calibration: an `unknown`/`nearest`/`ceil` assumption can still allow positive residual uncertainty, while clipped floor bounds can be exactly exhausted. This is conditional interval arithmetic, not proof of official exhaustion or a reason to calibrate against a saturated endpoint.

## Read the output without overstating it

The v2 JSON has `kind: "per_model_conditional_quota_scenarios"`, `alternatives_not_additive: true`, and `trust_level: "conditional_on_user_assertions"`. `model_scenarios` carries each profile's scope, total status/reasons, separate remaining status/reasons, quantization bounds, counts, observed usage, and price fingerprint. Top-level `status: "conditional_estimate"` means at least one total scenario passed; it does not certify all rows or any remaining amount. Inspect each row.

All attempts remain listed with declared/observed profile IDs, eligibility or unsupported status, cost, delta, receipt counts, and rejection reasons. Inherited and duplicate receipts and receipts outside declared segments remain counted diagnostically. `cannot_estimate` and `null` are meaningful results, not zero cost or zero quota.

The experiment supplies no validation against held-out real observations and no guarantee for future workload behavior. Quantization-only bounds exclude incorrect attestations, incomplete receipt coverage, timing uncertainty, delayed posting, stale quota, unseen device/cloud usage, wrong pool attribution, price mismatch, and workload changes. Treat account percentages as authoritative and every dollar scenario as conditional on evidence and explicitly stated assumptions.
