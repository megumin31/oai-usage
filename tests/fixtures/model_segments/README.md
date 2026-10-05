# Fictional per-model quota fixtures

Every account, model, receipt, observation, workload, and rate in this directory
is invented test data. `synthetic-astra` and `synthetic-sol` are fake model IDs;
these files make no claim about prices or limits for any real model or account.

`fictional_prices.json` deliberately uses the catalog schema's required
`standard_api_equivalent`, `openai`, and `https://models.dev/api.json` values.
That URL is a schema compatibility value, **not provenance for these invented
rates**. No fixture is downloaded. All prices are fictional: $1 per million
uncached input tokens, $0.2 cached input, $1.5 cache-write, and $2 output.
The verification date is also fixture metadata, not a real rate verification.

- `two_models.json`: $15 / 20pp gives hypothetical total $75 for synthetic-astra;
  $20 / 20pp gives $100 for synthetic-sol. The explicit current snapshot is 60%
  used, yielding conditional remaining $30 and $40. These are alternatives,
  never an additive $175 quota or $70 balance.
- `continuous_zero_delta.json`: three adjacent same-profile intervals sum to
  $15 / 20pp, retaining the cost of the zero-delta interval and cancelling only
  the two truly shared interior observations.
- `independent_fragments.json`: the same $15 / 20pp has four uncertain endpoints.
- `many_small_fragments.json`: ten independent 2pp intervals sum to 20pp, but
  twenty uncancelled endpoints leave a zero lower delta bound and an unbounded
  total upper bound under unknown rounding. Total observed delta alone is
  insufficient to establish usable precision.
- `alternating_models.json`: A → B → A preserves the inner endpoint uncertainty
  separately in A's estimate rather than cancelling across profile boundaries.
- `quantization_trap.json`: fictional true usage 10.005% → 11.995% is a 1.99pp
  change but floor display shows 10% → 11%. The 1pp observed signal has no finite
  upper total bound, so no point estimate is published.
- `mixed_workload.json`: a declared null-profile mixed interval remains visible
  as unsupported; its receipt costs are never apportioned to the pure profiles.

CLI tests load these exact files with an audit hook rejecting network and
session-directory access. No private session logs are used.
