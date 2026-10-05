# Offline historical quota estimates with reset epochs

`segments historical` is an opt-in research mode. It reads explicit local log roots and an explicit price catalog through the existing repaired `Scanner`, `account`, and `priced_events` ledger. It neither queries quotas nor starts a sampler. The strict manifest analyzers, historical `diagnose`, and default report/watch/JSON behavior remain unchanged.

```sh
python3 ./oai-usage segments historical \
  --root "$HOME/.codex/sessions" --root "$HOME/.codex/archived_sessions" \
  --price-catalog ./prices.json --output historical-result.json
```

Both `--root` (repeatable) and `--price-catalog` are required. Named outputs must be new files. `--json` and `--output -` expose the complete result. Source/account labels are descriptive and do not verify a common account, coverage, or pool attribution. No credentials, conversation text, raw session identifiers, or raw log paths are exported.

## Epochs and uncertain observations

The protocol field is **used** percentage. Remaining percentage is `100 − used`; increasing remaining is compatible with a grant or reset. It can also result from stale observations arriving out of order. Log timestamps record local arrival, not measurement or settlement. Observer provenance is retained as stable session hashes, and absent measurement timestamps remain unknown.

A quota epoch is a chronological evidence segment with its own identifier. It is not synonymous with the declared reset timestamp, nor a claim that the server confirmed a grant. The pool, window duration, declared reset, and plan remain available independently. Each epoch/model/service-tier/effort combination is calibrated separately.

- A supported new window or a context change starts a new epoch and rejects the crossing interval. Evidence classification uses only observations available at the boundary; it cannot identify who granted quota or why.
- A used-percent decline with unchanged declared identity starts an **ambiguous** epoch. It is not automatically called either a reset or a cache regression. Later monotone intervals can accumulate diagnostic costs and deltas inside this epoch. They do not inherit the old high-water mark, and returning to the old peak never joins them to the pre-decline epoch.
- Timestamp differences, including one second, are not silently merged. Unresolved aliases are explicit ambiguous boundaries. Neither a favorable fitted ratio nor future recovery proves that two timestamps identify the same grant.
- Once a supported new window retires an older one, returning old-window observations are quarantined. They cannot reactivate that old epoch, contaminate its accepted samples, or become current remaining. Uncertain current identity keeps remaining unknown.

The metadata heuristic for a supported new window requires a used decrease of at least 20pp, a deadline advance greater than 60 seconds, a forward inferred window start within the preceding observation minus 60 seconds and the new observation, and a nonconflicting preceding observation inside its declared window. The 60-second tolerance acknowledges uncertain arrival timing; it is an engineering rule, not backend reset confirmation. Other changes remain ambiguous. Plan/duration changes are supported context boundaries, not evidence of a grant. A previously retired exact identity is conservatively quarantined even if it could represent a real plan reversal or reissued grant; independent fresh evidence would be needed to distinguish that case.

These rules isolate observed discontinuities. They cannot prove that an unobserved reset did not occur between two monotone samples. An ambiguous epoch remains diagnostic; it does not supply a primary capacity or balance. Historical and current scopes stay separate, and an older coefficient is never substituted for sparse current evidence.

## Fixed selection rules and sensitivity

Every built-in policy is run at every predeclared cost-alignment offset. No result is chosen because it has a high ratio.

| Policy | Minimum continuous span | Maximum observation gap | Minimum delta |
| --- | ---: | ---: | ---: |
| short | 300 seconds | 300 seconds | 5pp |
| medium | 900 seconds | 900 seconds | 10pp |
| long | 1800 seconds | 1800 seconds | 15pp |

Offsets are −60, 0, and +60 seconds. For observed quota endpoints `a,b`, costs use event arrival times in `(a+offset,b+offset]`. Profile purity and boundaries are checked again after shifting. A shifted cost interval cannot cross an epoch or conflicting quota observation.

The gap limit is a heuristic continuity rule, not proof of a task boundary or global activity. Task markers are counted, but the implementation does not reconstruct guaranteed idle-free periods. Unseen devices, delayed posting, in-flight responses, and account/pool attribution remain unknown.

One global quota timeline covers all selected sessions for each pool/window. Equal simultaneous observations combine their provenance; conflicting values split eligibility. Existing response/fork/compaction accounting remains authoritative. Defects are scoped to affected dated intervals when possible; unknown input coverage or a file changing during the read cannot be silently ignored.

Contiguous same-profile intervals inside one epoch form a block. Positive local cost at zero percentage change is retained. Empty zero-change intervals may extend a block subject to continuity rules. Mixed models/settings are rejected without splitting the quota delta by API cost or tokens. Positive quota change with no local usage, unknown prices, accounting defects, counters resetting, and saturated endpoints remain exclusions. Rejected evidence is retained with reasons.

## Primary amounts and diagnostics

For admitted blocks in one epoch and profile, let `C` be summed local API-equivalent cost and `D` be summed used-percentage change. The diagnostic nominal ratio is:

```text
nominal per-100pp capacity = 100 × C / D, when C > 0 and D > 0
```

It is **not** the actual API cost consumed across a full cycle, nor the total quota value received through multiple grants. Unknown device usage cannot be reconstructed from this ratio. A changed workload, cache mix, tier, plan, epoch, or price basis cannot inherit it silently.

Each endpoint uses the explicit, unverified quantization assumption `[max(0,p−1), min(100,p+1)]`. Coefficients cancel only for the same shared snapshot. Independent fragments retain every boundary. If the resulting summed delta range is `[L,U]`, the quantization-only capacity range is `[100C/U,100C/L]`, with no finite upper when `L=0`.

The primary estimated-total field stays `null` when the epoch is ambiguous, signal is insufficient, the upper bound is unbounded, the range is too wide, or the predefined sensitivity checks indicate instability. Nominal ratios and diagnostic ranges remain available for inspection; they are not printed as reliable capacities. The engineering width limit is 50% of nominal. Predefined sensitivity variants are compared within the same epoch/profile: a nominal max-minus-min spread exceeding 50% of the median available nominal suppresses primary values. Missing variants are counted and exposed, not manufactured or replaced; available variants do not establish general robustness. It is not a statistical confidence or accuracy claim.

Remaining is evaluated separately, using a compatible cached observation within the same epoch. It needs a usable total, valid current identity, and acceptable remaining uncertainty. A narrow total does not imply a narrow remaining amount near 100% used. A returned retired-window observation cannot supply remaining. Every record retains its observation time, age, provenance, and `is_live_lookup: false`.

The analysis clock is not an input cutoff: it describes the supplied history. Future evidence relative to that clock suppresses affected primary values and current claims. A time-held-out backtest must truncate the input evidence first and rerun accounting; changing the clock alone does not remove future receipts or reconciliations.

No quantization range bounds unknown external usage, stale measurement error, settlement, account attribution, or price error. `statistical_confidence_interval` remains `null`. This implementation has no claimed held-out predictive accuracy. Earlier exploratory backtests are context, not validation of the new epoch rules.

## Reviewing all evidence

Text uses the fixed medium/0s view and selects recent evidence by time, including unknown results. It keeps historical and current rows separate. It never replaces a newer unknown epoch with an older favorable value. JSON retains all policies, offsets, epochs, boundary classifications, sources, attempt reasons, and diagnostic nominal values.

An eligible block is structurally admissible diagnostic evidence, not necessarily a publishable estimate: epoch ambiguity and amount/sensitivity gates can still leave its primary values null. Historical remaining names an old cached snapshot and must not be interpreted as current available balance.

Per-profile output includes accepted token categories, cache fraction, costs, delta, independent-boundary weight, and exclusion counts. Cache mix is described rather than fitted. Mixed-interval rejection counts may appear under multiple profiles; they are not independent costs. Alternative policies and epochs must not be added as capacities. Consecutive equivalent rejected edges may be compacted for output while preserving atomic interval counts and costs.

The historical output schema is versioned independently of strict manifest v1/v2. Default report/watch exports are unchanged. A consumer should distinguish primary values from diagnostic nominal ratios and treat null as unavailable, never zero.

## Regression coverage and limitations

Deterministic regressions cover normal consumption; same-deadline drops and later recovery; changed-window grants; plan changes; retired-window returns; one-second alias ambiguity; interleaved observers; saturation then reset; zero-delta costs; shared versus independent quantization boundaries; profile changes; shifted boundaries; missing current evidence; and unknown primary values. Wholly invented quota metadata and local costs exercise these boundary conditions without exposing private account observations or claiming measured calibration accuracy.

The synthetic metadata examples cover early new deadlines and interleaved old/new windows. They test boundary isolation and retirement behavior; they do not establish backend grant causation. There is no automatic deployment or live collector in this change.
