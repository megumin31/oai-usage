# Revision 4 practical display

The active practical estimator is described in [Stable frontier conditional estimates](./stable-frontier-r4.md). The following sections preserve the R3 strict certificate and diagnostic design for compatibility; its hard publication gates now apply to `strict_validation`, not to every practical displayed value.

# Conditional estimates with one-sided contamination

`segments robust` is an explicit offline experiment built on the repaired accounting ledger and reset-epoch isolation. Default report/watch and strict manifest v1/v2 retain their existing behavior. `segments historical` retains strict continuity and single-profile selection, with shared fixes for missing-plan retirement and rejected-block token totals. Both local log roots and a local price catalog are required; this command does not query live quotas or start a collector.

```sh
python3 ./oai-usage segments robust \
  --root "$HOME/.codex/sessions" --root "$HOME/.codex/archived_sessions" \
  --price-catalog ./prices.json --json
```

## What the model can identify

Inside a single quota epoch and model/service-tier/effort profile, suppose a constant capacity `B` applies. Define the consumption rate `a = 100 / B` in percentage points per local API-equivalent dollar:

```text
true quota increment q = a × target-profile cost C + other consumption e,  e >= 0
reported increment d = q + endpoint measurement errors
```

Other-device consumption moves the rate upward. Quantization and asynchronous cached observations can move measured increments in either direction. The estimator searches for repeated low-rate groups and validates their recurrence later. It does not take the maximum implied capacity, average block capacities, apportion mixed-model quota changes, or choose favorable policy variants.

A finite capacity requires an additional **identifying assumption**: a sufficiently large, repeated low-rate group is close to uncontaminated, and local workload weighting is stable. The near-clean model permits `0 <= e <= 0.05 × a × C` for those supporting blocks, where `e` includes all non-target consumption: visible local companions and unobserved/external usage. The legacy JSON key `near_clean_external_fraction` applies to that full sum. This is a user-motivated assumption, not an official quota fact or a conclusion proven by clustering.

For example, `a=.2,e=0` and `a=.1,e=.1×C` produce exactly the same observations. The latter contamination is nonconstant whenever local costs change. No algorithm can distinguish those worlds from these fields alone. Stable dense groups and successful holdout checks therefore support a **conditional model**, not a verified true capacity.

The one-sided model alone provides no finite upper bound on capacity. With exact `q`, `B >= 100C/q`. With a valid measurement envelope, the corresponding bound is `B >= 100C/q_upper`. Using reported `d` in place of true `q` is unsafe: an actual 1.99pp increment rounded to 1pp can nearly double the naive bound. Any reported lower bound is explicitly conditional on correct cost/epoch attribution, nonnegative contamination, and the measurement envelope actually covering the error. Unbounded cache or settlement error invalidates an unconditional bound.

## Evidence and fixed block alternatives

Costs come from the same response/fork/compaction-reconciled ledger as the rest of the tool. Reset epochs remain separate. Missing plan metadata is unknown and cannot retire a known plan; missing intervals remain separate rather than being filled or bridged. The robust command can retain a small cross-observer decline or a one-second deadline alias only with the recent source-prefix evidence defined in protocol revision 2. It preserves the raw percentage, plan, deadline and snapshot IDs, and exposes its conditional continuity assumptions. Same-source or larger drops, unsupported aliases and prior-generation returns retain explicit boundaries. Future recovery never pools earlier epochs back together. Retired-window returns remain quarantined.

Each complete grid has one fitted profile, selected by the most reconciled local events; ties use earliest event, then lexical profile. This choice does not inspect cost, quota or fitted capacity. Unknown and unpriced profiles participate, and an invalid selected owner does not fall back to another profile. Fit cost, tokens and cache mixture use only the owner. Other profiles are visible nonnegative nuisance, including companions with unknown prices. The full account quota increment is neither split by cost nor copied into other profiles' fits. Event-frequency selection can affect workload coverage and does not prove a clean anchor. Incomplete grids have no fitted owner, while their full local usage remains diagnostic.

The method evaluates nonoverlapping chronological blocks at predefined target spans of 5, 15 and 30 minutes, each with cost alignment offsets of -60, 0 and +60 seconds. Each epoch grid starts at its first valid observation. A target boundary uses the nearest observation within `min(60,L/10)` seconds, with earlier-observation tie breaking; missing boundaries are not interpolated or bridged. Admitted spans must be between 0.8L and 1.2L. An adjacent observation gap above 300 seconds is rejected. This is not a task-boundary or idle-detection rule: observations only at 15-minute endpoints are excluded even if their target grid is otherwise valid. Shared boundary snapshots retain their identities. Positive-cost zero-delta blocks and raw negative increments on conditionally continuous paths remain evidence. Gaps, short tails, incomplete target prices and invalid accounting remain visible with reasons. Multiple overlapping policy alternatives are never combined as independent samples.

Ordinary historical caches are not automatically rejected wholesale. Within an isolated ambiguous epoch the robust model can produce a conditional candidate, while preserving the unresolved epoch interpretation and measurement assumptions. This does not resolve the cause of the boundary. A retired-window quarantine cannot supply a fitted current balance.

## Shared endpoint error

Each snapshot has one shared error variable, rather than giving every adjacent block an independent error. The main quantization envelope is the clipped interval `[max(0,p-1), min(100,p+1)]`; a separately predefined pressure check uses two percentage points per endpoint. Neither envelope is proven to bound arbitrary cache or settlement delay.

For a near-clean chain with total cost `C`, endpoint intervals `[L_i,U_i]` and `[L_j,U_j]` imply:

```text
a >= (L_j - U_i) / (1.05 × C)
a <= (U_j - L_i) / C
```

The implementation must enforce these constraints over every contiguous subchain of a frozen supporting group. Shared endpoints cancel; independent fragments keep their separate uncertainty. Many tiny or duplicated observations do not acquire a narrow deterministic interval merely by being counted repeatedly. Quantity-of-evidence metrics and weight concentration are not statistical independent sample sizes or confidence levels.

`fits.*.rate_interval.binding_constraints` reports the actual subchains that bind the lower and upper slope: raw endpoints, cost, observed delta, shared outer error difference, near-clean fraction and formula. The capacity bounds reverse the slope bounds through `B=100/a`. A missing lower slope witness means the nonnegative slope domain supplies that bound; the capacity upper bound may be unbounded. These are **conditional feasible bounds**, not statistical confidence intervals. For a wholly synthetic example, a continuous 6pp chain costing $6 with E=1 and rho=.05 gives $75–$157.50 even when its point candidate is $100. The whole chain gets a single outer ±2pp difference, not one allowance per block. No bootstrap interval is computed without a validated dependence and sampling model.

## Training, validation and disclosure

Split evidence chronologically before inspecting fitted ratios. The early part trains the candidate group; the later part validates the frozen rate and group definition. Purge a shared train/holdout boundary as needed, and expose the purged evidence. Do not choose a new low-rate peak using holdout observations.

The revision-3 split is fixed before fitting at two thirds of unique eligible target-workload observation exposure within the epoch/profile. All predefined timing variants use one canonical union of their eligible target-receipt intervals: overlaps count once, and quota-only/no-receipt intervals supply no exposure. The first third of total exposure defines the training-half frontier. Inactive time cannot manufacture an empty holdout, and different profiles keep separate splits. Missing eligible workload can change this retrospective sampling design; neither percentages nor cost magnitudes choose the cutoff. Training uses rate intervals and bounded information/span weights, then checks joint endpoint feasibility. An individual near-clean anchor also needs predicted local signal `a*C >= E`; wide, low-information blocks remain in one-sided constraints and diagnostics instead of automatically becoming near-clean equalities. This membership threshold cannot narrow the final physical slope bounds; the fitted rate must satisfy it without changing frozen members. Training support requires at least 25% of capped information weight, three endpoint-disjoint anchors with Kish concentration at least three, both fixed training exposure halves, at least 2L duration, and at least 10pp of predicted local signal. Holdout requires at least 25% weight, two endpoint-disjoint anchors with concentration at least two, at least L duration, and 5pp predicted local signal. Information weight is `C/(2E)*min(1,span/L)`, capped at four times the positive training median; holdout uses that same cap. Strong components separated by more than 25% are reported as multimodal. A cached-input-fraction shift of at least 0.20 between known training and holdout mixes is a drift gate; missing mix remains unknown. Primary publication requires E=1 and E=2 to pass, at least three passing timing schemes spanning two block lengths and two offsets, and no capacity spread above 50% of the median available candidate. Multiple strong, separated peaks, insufficient signal, a wide interval, missing later recurrence, large negative residuals, cache-mix drift, or inconsistent predefined alternatives keep primary amounts unavailable. The candidate, conditional interval, support span, support weights, holdout diagnostics and failure reasons remain available even then.

Holdout diagnostics measure consistency with the model: whether predicted local consumption exceeds the allowed quota increment and whether low-interference support recurs. A positive residual can be legitimate external consumption; it is not automatically an error in true capacity. There is no true-capacity oracle in the local historical logs. Exploratory fit residuals are not measured true-capacity accuracy, calibration targets or acceptance thresholds.

Source and accounting completeness, account/pool attribution, unknown settings, cache mix, source time, and price basis remain explicit. A changed cache mix can violate the fixed-workload rate assumption even when the model name is unchanged. The method must retain all preset block/offset/noise alternatives and never tune thresholds after seeing favorable historical results.

## Revision 3: retained evidence and diagnostic aggregation

A counter reset in a different model's session is not automatically a defect in the selected owner's cost. Robust mode ignores this veto only when the affected session has a nonempty, entirely known model set across original non-inherited usage snapshots, owned receipts, and reconciled events, and that set excludes the target model. Unknown records, model switches involving the owner, and malformed/unreconciled ledger defects retain their protections. The companion's consumption remains nonnegative nuisance; `nuisance_counter_reset_count` discloses the retained occurrences. Reset point membership follows the same `(left,right]` interval as receipts, including alignment offsets, so a boundary event is not charged to two blocks. Ordinary historical mode is unchanged.

Small neighboring blocks can jointly contain substantial information even when each predicted increment is below E. `fits.*.aggregation_diagnostic` therefore examines the **already frozen** candidate and selected training membership. It groups adjacent selected edges chronologically until `a*sum(C) >= E`, then starts a new group. Gaps, unselected edges, profile/epoch changes and the train/holdout split break groups. Unfinished low-signal tails remain disclosed. Group weights sum the original capped atomic weights; support counts use groups and never add their constituents again.

Holdout uses the frozen candidate's physical per-edge compatibility without the individual signal floor, then applies the same grouping. Every original endpoint and per-edge 5% near-clean constraint remains in the physical solver; this is not a larger pooled nuisance budget. No coefficient or physical range is refitted, narrowed, or clipped to the artificial E/C membership floor. This is an additional diagnostic, not a route for promoting primary totals. For example, 60 continuous $4 blocks at 0.4pp each have 24pp training signal; grouping reveals that information without claiming 60 independent observations. Disconnected fragments do not cancel endpoint uncertainty.

`fits.*.one_sided_capacity_bounds` also exposes the full admitted path's conditional lower bound even when no near-clean cluster can be supported. It uses all eligible blocks and their actual shared endpoints. Its upper bound is always null: arbitrary nonnegative nuisance usage allows arbitrarily large capacity. It does not require a near-clean anchor, but still requires valid cost/epoch attribution and the stated bounded-error envelope. Thus it is neither an official guaranteed balance nor a confidence interval.

The exposure split is retrospective: future observation availability and full-ledger reconciliation can affect it. A genuine predictive time-cutoff replay must materialize and reparse only evidence available by that cutoff. Do not describe ordinary train/holdout consistency as prospective accuracy.

## Output and remaining amounts

A primary `Estimated total` is a conditional same-epoch/model alternative, never an official balance or an amount additive across models. A diagnostic candidate is distinct from a publishable conditional estimate. Historical and current epochs remain separate; older coefficients cannot fill missing current evidence.

Primary remaining also requires a reliable observation from that same current epoch. Recent log arrival alone does not establish measurement freshness. The offline log path therefore keeps primary remaining unknown without independent freshness evidence; a labeled diagnostic as-of amount may be retained. No new live read or collector is implicit in this command.

The analysis clock is not a historical input cutoff. Future selected receipts or quota observations cannot support primary amounts. A genuine time-held-out prediction backtest must truncate its input evidence before re-running parsing and accounting; changing the report clock alone does not remove future reconciliations. The built-in chronological split measures retrospective consistency on the completed ledger. Later fork/compaction evidence can affect that ledger, so this split alone is not a proof that a prediction could have been made with the inputs available at the time.

Full JSON preserves every attempted variant, candidate, rejection and assumption. Text should expose each model's available diagnostic evidence rather than collapsing the entire result to an unexplained unknown. Prices remain nominal local API-equivalent comparisons, not the subscription's undisclosed internal quota weights.

## Data contract and regression checks

The explicit subcommand returns `kind=robust_contamination_quota_scenarios`, `schema_version=1`. The existing deduplicated `local_usage` and per-model ledger remain the cost source. `quota_epochs` and `current_quota_epochs` preserve boundary interpretation; `fixed_grid_blocks` records grid selection and missing boundaries; `blocks` retains attempted cost/quota pairings; `model_scenarios` contains each epoch/profile/timing alternative. Each scenario keeps both endpoint envelopes under `fits`, the chronological `split`, selected supporting block IDs, reasons, sensitivity results and the conditional identification assumptions. A `candidate` is diagnostic until all publication gates pass. `statistical_confidence_interval` is null by design.

The new command requires explicit local roots and a price catalog. It uses no account RPC, and no new cache or background collection process. Existing default reports, `--watch` and strict manifest analysis do not call this experiment implicitly. Consumers should keep missing primary estimates as unknown and display the reasons; they must not substitute a diagnostic candidate or a coefficient from another reset epoch. A future fresh-snapshot integration would need explicit measurement/receipt identity, account/pool attribution and settlement evidence before enabling primary remaining values.

The regression suite covers these materially different failure modes:

| Evidence or change | Required behavior |
| --- | --- |
| Clean, stable synthetic workload | Conditional candidate survives only with repeated later support and timing coverage. |
| Positive, variable external consumption | Dirty blocks remain one-sided constraints; supported low-rate groups may still be found. |
| Persistent cost-proportional contamination | Observational equivalence remains explicit; no claim of true-capacity identification. |
| Zero quota delta, zero-cost gap or low-information wide interval | Retain constraints and cost evidence; do not promote these automatically to clean anchors. |
| Shared endpoints versus disconnected fragments | Solve complete contiguous paths; never apply one endpoint-error allowance across independent fragments. |
| Large rescaling of costs | Preserve feasibility decisions and ordered physical ranges. |
| Reset alias, decline, retired identity, future-dated receipt | Isolate epochs and gate affected output; retain diagnostic evidence. |
| Sparse sampling, split-straddling blocks, missing variant | Expose missing/purged evidence and enforce the fixed publication coverage gate. |
| Cache mix or workload changes, multiple rate groups | Preserve alternatives and fail the affected stability gates. |

Revision 2 changes continuity and block ownership, and fixes two accounting-output semantics; it does not narrow E/rho bounds or change fitting, holdout or publication thresholds. Full-history JSON can be large because every failed epoch/profile alternative is retained; compact reports should summarize it without loading unrelated arrays.

Revision 3 changes the exposure partition and reset-defect scope, and adds grouped-path/one-sided diagnostics. E=1/E=2, rho=.05, physical bounds, primary information/support/width thresholds and timing-coverage thresholds remain unchanged. Grouped diagnostics do not supply additional independent variants.
