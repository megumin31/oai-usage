# R5: joint constraints on original quota readings

`segments joint` is a new, offline experimental estimator. `segments robust`
retains R4 for matched comparisons. Neither command changes the ordinary ledger,
prices, installed application, or account state.

```sh
python3 ./oai-usage segments joint \
  --root /explicit/logs/sessions --root /explicit/logs/archived_sessions \
  --price-catalog ./prices.json --json > joint-new-file.json
```

Use only explicitly authorized log roots. Reports contain derived usage metadata;
they do not contain conversation bodies. Do not publish private input logs.

## Evidence and assumptions

For one isolated epoch, model/service-tier/effort profile, and receipt-alignment
hypothesis, assume `a=100/B` is constant. Every original adjacent quota reading is
retained. Exact target-profile receipts in `(left+offset, right+offset]` supply its
cost; other models' spend is not apportioned to that target. Every admitted raw
edge supplies `true_delta >= a*C`.

Quota/epoch conflicts, reset boundaries, saturation, excessive observation gaps
(over 300 seconds), invalid cycle bounds, invalid alignment, and future evidence
remain explicit exclusions. Source-supported cache returns retain their signed
increments. They must still pass the common bounded-error model; they are not
made monotone by editing the observations.

A measurement-valid edge with incomplete or corrected target costs stays in the
physical graph with the safe lower bound `C=0`. Its ledger subtotal is separately
labeled diagnostic. Such an edge cannot be part of a near-clean budget. An exact
zero-target-cost edge can be inside a larger clean portion: other consumption
there is allowed within the **whole portion's** budget.

Finite capacity upper bounds require selected near-clean portions. Each portion
has aggregate nuisance consumption between zero and `0.05*a*C_portion`. This is
not a 5% bound separately on each internal raw edge. Selected portions are
nonoverlapping; touching portions retain separate budgets. Different subdivisions
of the same raw-edge union therefore remain different hypotheses.

## Exact shared-endpoint chain solver

Let `Q_i` be displayed cumulative quota, `S_i` cumulative target cost, and
`eta_i = displayed_i - true_i`, with declared bounds `[l_i,u_i]`. Define
`x_i = Q_i - eta_i - a*S_i`. All raw edges say `x` is nondecreasing. A selected
portion `[s,t]` adds `x_t-x_s <= rho*a*(S_t-S_s)`, where `rho=.05`.

The joint feasible rate interval includes:

- Every raw subpath's upper slope bound
  `(Q_j-Q_i-l_j+u_i)/(S_j-S_i)`
- Every interior subpath within a run of touching clean portions's lower bound
  `(Q_j-Q_i-u_j+l_i)/(S_j-S_i + rho*C_cover(i,j))`

`C_cover` is the full cost of every clean portion touched by the subpath, including
cost outside its two interior endpoints. A dirty gap breaks clean reachability.
The same endpoint error is shared throughout the chain. The implementation uses
monotone convex hulls, exact integer hull predicates, and O(n log n) time / O(n)
space rather than enumerating O(n²) raw subpaths. Floating output and near-equality
tolerances remain disclosed implementation arithmetic, not statistical error bars.

For fixed raw geometry, clean budgets and error envelope, adding consistent
constraints can only narrow the feasible set. Refitting candidate membership,
changing the error model, changing alignment, or correcting the ledger can move,
widen or empty the displayed set. No monotonic convergence guarantee is made for
those different analyses.

## Discovery and support

Time-only nonoverlapping 5/15/30-minute partitions and coarsenings 1/2/4/8 discover
candidate whole clean portions. Tails and gaps are retained; the raw constraint
graph is never replaced by these partitions. An endpoint-event sweep proposes
marginal-overlap memberships. An informative endpoint-disjoint subset DP also
proposes jointly testable subsets, avoiding false negatives caused by adding every
marginally compatible clean assumption at once.

Every proposal is checked against **all** admitted raw edges and its selected
aggregate budgets. Discovery is a finite deterministic family, not an exhaustive
search over all feasible clean subsets. An unavailable result does not prove that
capacity is fundamentally unidentifiable.

Support uses indivisible selected portions or adjacent merges. One large budget
cannot be split into many apparent repetitions. An informative group needs at
least `2E` predicted percentage points and a strictly positive observed lower
increment. At least two groups and a finite positive E1 set permit a provisional
conditional value. Endpoint-disjoint counts are descriptions of dependence, not
proof of statistical independence. Ranking uses unique target cost divided by
`2E`, not observation cadence or repeated grid counts; this is an engineering
score, not Fisher information.

Overlapping qualified rate bands form alternative-membership neighborhoods.
Within the lowest-rate separate neighborhood, choose the strongest original
support with deterministic non-capacity-maximizing ties. The point is the selected
portions' ratio of sums, clipped to their exact joint interval. Its displayed
physical range belongs to that one membership; the neighborhood envelope is
reported separately.

## Measurement and offset alternatives

- E1, the main conditional set: each raw endpoint error is within ±1pp
- Quantization-only: a fixed unknown-phase 1pp quantizer, represented by shared
  ±0.5pp errors without absolute clipping; only unsaturated integer displays apply
- E2: ±2pp stress with the **same** clean portions

None is a statistical confidence interval. Quantization-only infeasibility means
that those selected assumptions cannot explain all original readings using
rounding alone. A narrow E1 set does not establish true accuracy.

Offsets −60/0/+60 seconds are mutually exclusive receipt-alignment hypotheses.
They are solved separately and their supported conditional sets are **unioned**,
never intersected. Disjoint gaps in that union remain gaps. Unsupported offsets
are disclosed, not declared impossible. The nominal zero-offset result is chosen
when available, otherwise fixed −60/+60 priority; capacity size does not choose
an offset. These offsets are stress tests, not a measured maximum latency.

## Validation and output

A separate earlier fit freezes its coefficient and tests every admitted later raw
edge. Any passing clean-support proposal takes precedence over a larger failing
proposal. This is retrospective consistency on the full-input reconciled ledger,
not an as-of predictive test. Genuine historical replay requires physically
truncated source files; `now` alone does not truncate reconciliation evidence.

`model_scenarios` contains one joint fit per epoch/profile/offset.
`model_epoch_estimates` contains the nominal or fixed-priority summary, offset
alternatives/union, raw constraint counts, support, sensitivity and validation.
`raw_observations`, `raw_edges`, and `raw_profile_edges` retain audit evidence.
Counts across profiles or offsets do not represent independent observations.
Current remaining capacity stays unknown on this offline path.

A fully contaminated but stable frontier can underestimate true capacity. Without
an external clean anchor, nonnegative contamination alone leaves its upper bound
unbounded. These outputs are conditional API-equivalent workload estimates, not
official quotas or demonstrated accuracy.
