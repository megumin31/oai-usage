# Stable frontier conditional estimates

Revision 4 changes the experimental `segments robust` presentation from an all-or-nothing strict certificate to a practical **conditional estimate plus evidence labels**. Default report/watch, accounting, prices, quota-epoch isolation, and the installed program remain unchanged.

## What the number means

Within one model/service-tier/effort profile and isolated reset epoch, assume a fixed coefficient `a=100/B`. True quota consumption equals `a*C + e`, where the target's observed API-equivalent cost is C and all other consumption is nonnegative e. A stable low rate corresponds to a stable upper capacity frontier. The program never chooses a single maximum C/delta ratio.

A displayed finite range additionally assumes each selected original atom has `0 <= e <= .05*a*C`, and each shared endpoint's measurement error lies within the declared envelope. The 5% cap remains **per original atom** after coarsening; it is not relaxed to a budget for a whole aggregate. If every episode is contaminated, an apparently stable frontier can remain below true capacity. Constant or cost-proportional contamination is observationally indistinguishable from a smaller clean capacity. The unconditional capacity upper bound is therefore always unbounded.

## Discovery and original evidence

- Form fixed chronological, nonoverlapping coarsenings of 1, 2, 4 and 8 admitted blocks. Keep tails and zero-cost paths, and never cross gaps, epoch/profile changes or timing variants
- Evaluate closed endpoints and open cells of the feasible-rate overlap sweep. No floating-point midpoint is required, so singleton support and one-ULP cells remain visible
- Expand every selected coarse membership back into unique original atom IDs. Repeated tilings, aliases or timing variants do not create extra observations
- Refit the ratio of sums to the original shared-endpoint feasible interval. Every original one-sided inequality and every selected atom's near-clean inequality remains active
- A support group requires predicted local signal of at least 2E and an observed telescoped increment whose lower measurement bound is strictly positive. A deterministic interval DP maximizes nonoverlapping informative groups, then original capped information and duration; it can leave an unhelpful prefix unused without removing its physical constraints
- Two informative nonoverlapping groups and a finite positive E1 interval permit provisional display. Adjacent groups may share their boundary because its error is solved jointly. Such groups can contain no more information than one longer episode, so group count is not proof of independent replication

Overlapping qualified rate bands belong to the same frontier neighborhood. Within that neighborhood the selected hypothesis has the strongest original capped information, then group count and duration, with fixed coarsening/ID tie breaks. It is **not** the most favorable point estimate. Only separated feasible neighborhoods are ranked by rate. Higher-rate modes can be contaminated segments and do not automatically veto the supported lower-rate frontier. Membership-sensitivity ranges are distinct from the physical range for one fixed membership.

The old 25% prevalence and exact Kish cutoffs are no longer blanket display gates. Rare support is explicitly labeled. These are engineering evidence descriptions, not confidence levels.

## Three distinct ranges

1. **E1 measurement range**: the primary conditional feasible capacity interval under ±1pp endpoint error. The displayed point must lie inside it
2. **Quantization-only range**: for unsaturated integer readings and a fixed unknown-phase 1pp-wide quantizer, use shared ±0.5pp error without absolute clipping. This gives a ±1pp difference allowance without claiming floor/nearest/ceil at zero. It does not include cache or timing error. If infeasible, say quantization alone cannot explain the hypothesis; if the main point is outside it, disclose that extra measurement error is needed. Noninteger readings are marked inapplicable
3. **E2 stress range**: recompute with ±2pp endpoint error and the exact same selected atoms. A zero slope lower bound means the upper capacity is unbounded

Where applicable, quantization-only feasible rates nest inside E1, and E1 inside E2. None is a statistical confidence interval. The −60/0/+60s alternatives are timing stress tests, not a measured asynchronous-lag bound. Their point spread and all alternative intervals remain visible; they are not pooled as independent repetitions.

## Validation and confidence labels

The practical number uses all available evidence and is retrospective. Separately fit an earlier training-only frontier using the canonical exposure cutoff, then freeze its coefficient and test it on later blocks. The later check distinguishes:

- Training frontier unavailable or no later evidence
- Limited later recurrence, including potentially legitimate extra-device consumption
- Later quota drop below the candidate's minimum implied consumption, which contradicts that earlier candidate under the chosen error assumption
- Supported later recurrence of the **earlier frozen candidate**

The full-evidence estimate never inherits that earlier point's validation silently. Its point change, stress sensitivity, timing sensitivity, cache-mix changes, source/epoch ambiguity and support dependence are shown separately. `provisional_conditional` means usable only under the stated assumptions; even `historically_repeated_conditional` is not official quota or demonstrated accuracy.

The former R3 publication screen remains under each scenario's `strict_validation`. Missing strict holdout support no longer erases a supported conditional frontier. Genuine insufficient signal, infeasible original constraints, future evidence, incomplete input coverage or quarantined retired-window returns still leave the practical value unavailable. Current remaining amounts still require independently fresh observations and remain unknown on this offline path.

## Output

`model_scenarios[].frontier` holds the practical fit, original support, hypotheses, ranges, frozen earlier validation and timing sensitivity. `model_epoch_estimates` gives one row per epoch/profile, choosing the first supported scheme in fixed order: zero offset before ±60s, and 15m before 5m before 30m. This is not selection by largest capacity. Every alternative is retained. Different models, profiles and epochs are alternatives and cannot be added.

The existing `candidate` field remains the legacy strict-training diagnostic and is explicitly labeled as such. `estimated_total_api_cost_usd`, `total_conditional_range_usd`, and the scenario status now follow the practical frontier. Raw accounting totals are unchanged.

## Support selector fixes

Endpoint-disjoint support is maximum cardinality, then maximum Kish concentration across all path/cycle matchings. A convex-hull combination avoids exponential enumeration across disconnected runs. Unexpected branching uses a bounded search with explicit guarantee flags. Counts are not statistical independence. Original atomic IDs, physical intervals and chronology are canonicalized; contradictory aliases or overlapping original intervals are rejected.

## Scope and reproducibility

The protocol and synthetic regressions were frozen before R4 real-log fitting. Tests cover clean traces, rare clean support plus higher-rate pollution, no-clean observational equivalence, quantization, error/offset sensitivity, resets/profile boundaries, disconnected paths, interval nesting, weak prefixes, aliases, overlap, singleton cells and phase-neutral support. Original uploaded logs are read-only. No account query, new collector, push, Mac write or installation is part of this revision.
