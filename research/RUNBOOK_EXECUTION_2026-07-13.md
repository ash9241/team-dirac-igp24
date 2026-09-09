> Historical research record. Numerical forecasts, live rankings, and operational instructions refer to its original date. See the repository README for audited results and current release instructions.

# IGP24 Runbook Execution — 2026-07-13

## Outcome

The generation, reconciliation, fresh-target, and pilot portions of the
runbook completed. As of `2026-07-14T16:18Z`, the live account was rank 25,
score `183.072339`, with 5,359 scoreable pairs and no queued submission. The
extra volume pairs reduced the score because they diluted the portfolio rather
than adding rare targets.

The initial component library had no eligible exact pair. A class-field-derived
component pair subsequently produced one independent candidate for the live
`24T17920, r=6` raid. Its dry-run gate passed and one real submission was made.
Submission `sub_4aacf0d7d3d7452ba4aafe8920365cbe` subsequently verified exactly as
`24T17920, r=6`. It is complete and must never be resubmitted.

A later mixed-scale D4 pilot hit `24T18986, r=20`, but its three-candidate
follow-up missed all three intended labels. That family is now explicitly
blocked from submission. Three still-later server submissions containing 46
results appeared outside the local controller and were reconciled; no local
submitter or launch agent was running when checked. The controller refuses
every real POST until the server queue, durable ledger, ownership view, and
family calibration gate are all clear.

## GCP forge

- Project: `dirac-phm`
- Reused worker: `phm-acq` (`e2-standard-4`, Debian 12)
- `phm-a100` remained stopped.
- The pre-existing `phm-acq` disk contained an unrelated LivingMap workspace.
  No IGP24 census, targeted queue, centroid, knowledge, or submission manifest
  artifacts were present, so none were imported.
- Competition API environment variables were absent on the worker.
- Installed and verified PARI/GP 2.17.2, GAP 4.12.1, and GAP `transgrp`.
- Both GCP instances were stopped after artifact recovery.

## Exact 8×3 maps

The direct product action produced:

| Components | Degree-24 group | Order |
|---|---:|---:|
| 8T44 × 3T1 | 24T2683 | 1,152 |
| 8T44 × 3T2 | 24T4997 | 2,304 |
| 8T50 × 3T1 | 24T17919 | 120,960 |
| 8T50 × 3T2 | 24T19234 | 241,920 |

The common-sign C2 fiber product produced:

| Components | Degree-24 group | Order |
|---|---:|---:|
| 8T44 × 3T2 | 24T2736 | 1,152 |
| 8T50 × 3T2 | 24T17920 | 120,960 |

Components containing 3T1 have no nontrivial sign quotient and were correctly
reported as unavailable for the C2 fiber construction.

Artifact hashes:

- Direct map: `502db6bbd10f8558aeed7784100db847d201d0be3236c2bd9dfdb7c2cf3c8789`
- Fiber map: `62722febd7b9b34da384e534457a49aeb62d038bbb8eebed45468c2e80d8009d`
- Direct GAP output: `4068b70b415b9772e5b80604f9e606198480cb72ff4b1eac35239da7e8abb5d5`
- Fiber GAP output: `aacdd3914669fdb19986b9cf033b2554d8a5b16b218795613742ad83262834b3`

The worker-generated artifact hashes and the locally parsed maps matched
exactly. The recovered worker reports are under the ignored `cloud/output/`
runtime directory.

## Initial target and controller result

The available historical progress snapshot reports 22 teams on 24T2736, five
teams on 24T17920, and 37–48 teams on the four direct-product labels. None of
the exact `(T, r)` pairs was present in the latest local unclaimed-pair file.

Consequently:

- Direct candidates generated: 0
- Fiber candidates generated: 0
- Four-hour rolling vault: 0
- Dry-run submissions: 0
- Controller result: stopped at `no positive-EV candidates selected`

This is the intended safety behavior and prevents spending server slots on
already saturated labels.

## Fresh recovery and exact raid pilot

The configured credential returned a complete fresh snapshot at
`2026-07-13T23:35:42+00:00`:

- 25,000 labels
- 165,836 `(T, r)` pairs
- 40,523 gold pairs
- 34,758 team-count-one raids
- 622 baseline pairs

Reconciliation completed all 14 locally known submissions and ingested 12,052
authoritative results. Another 2,134 historical server-only submissions do not
expose payloads and were deliberately not guessed.

The fresh snapshot showed `24T17920, r=6` at team count one. The original
fiber library could not realize it because its octics had only zero or two real
roots. The replacement components are:

- `8T50` octic with six real roots and discriminant squareclass `-65106259`;
- `3T2` cubic with one real root and the same squareclass.

Their degree-24 fiber compositum is locally irreducible with exactly six real
roots. Two primitive-element variants were generated, but both have the same
lineage, so the scheduler correctly selected only one.

Dry-run result:

- candidate pool: 1
- selected: 1
- forecast score: 0.45
- stale or duplicate: 0
- unresolved local submissions: 0
- gate: passed

The executed wave refreshed targets again at `2026-07-13T23:43:12+00:00`,
submitted exactly once at `2026-07-13T23:43:14+00:00`, and received submission
ID `sub_4aacf0d7d3d7452ba4aafe8920365cbe`. At the time of this update its server
state was one queued polynomial and zero verified results.

## Oracle evidence

Exact GAP cycle indices were generated for all six labels. Every parsed cycle
index sums to probability 1.0. The combined JSON artifact SHA-256 is
`550931bcacbcedb03d81ad8627914700a38091a23b276739d194599eb704204f`.

### Full structural atlas and pivot

The small six-label atlas was expanded into an exact census of all 25,000
degree-24 transitive groups. The dominant remaining construction surface is
the block-chain shape `(2,4,8)`:

- 9,373 groups in the full census;
- 7,824 currently live groups;
- 21,768 unowned gold pairs and 10,123 unowned team-count-one raids in the
  live snapshot;
- solo-equivalent score ceiling `26,829.5` before discriminant effects.

The full `(2,4,8)` cycle atlas contains 9,373 groups, 256,956 cycle patterns,
and 8,626 distinct cycle indices. Its SHA-256 is
`2b181e54b35cb55b127374d29dbf4f819fcbf024999ff73ab0a33a30a6d5b0d1`.

Exhaustive low-degree compositum maps showed no uncovered direct targets:

- all 100 degree-8 × degree-3 product routes: no gold/raid target;
- all 301 degree-12 × degree-2 routes: no gold/raid target;
- all 80 degree-6 × degree-4 routes: no gold/raid target;
- the only live common-sign fiber targets were the already-known
  `24T17920` signatures.

An initial live-only cycle screen appeared to select candidates, but that was
selection bias: closed groups had been omitted from the comparison set. The
full-atlas holdout measured only 66.1% top-1 accuracy and 75.6% precision among
nominally high-confidence predictions. The oracle now conditions on verified
construction-family support and priors. With that correction, all 300 new
`EDCW` candidates are rejected: 286 of 296 historical accepted `EDCW`
polynomials landed in the already-crowded `24T6875` family. The empirical
replay miner likewise has zero high-precision, open, unowned routes.

Therefore the next attack is not another legacy replay. It is a genuinely new
`(2,4,8)` tower construction with an exact structural compatibility set,
lineage-held-out calibration, and a one-to-five-candidate pilot only after the
queue is clear.

### Offline non-split tower experiment

The next attack was implemented and run offline. A reproducible sampler built
100 irreducible cubic-quadratic-quadratic-quadratic towers from 500 attempts,
covering real-root signatures `0,2,4,6,8,10`. Each record now retains its
degree-6 and degree-12 intermediate polynomials instead of accepting a guessed
`target_t`.

Exact intermediate cycle atlases were generated for all 16 degree-6 and all
301 degree-12 transitive groups:

- degree 6 SHA-256: `2aa9d27475fa77fc61e9591cd78185cdc70c8ad172520b56094793a470731c82`;
- degree 12 SHA-256: `662ec2f18ac32c2cbdb4b7c1d7d430424eac65fa5200028d909c0b1928b53a29`.

PARI order/parity invariants identify a single degree-6 quotient for 87 of the
100 towers. Unramified cycle support leaves 4–8 degree-12 quotient groups for
most candidates, however, and the exact block census therefore leaves a median
of 203 compatible degree-24 labels. Expanding from 200 to 1,000 unramified
primes did not reduce this set, proving that the remaining ambiguity is shared
cycle structure rather than insufficient sampling.

The full degree-24 oracle selected zero candidates at probability 0.75 with a
maximum two-label prediction set, and the controller dry-run stopped at
`no positive-EV candidates selected`. No POST was made. This family now needs
an exact degree-12 resolvent/order discriminator before it can support a pilot;
more primes or a lower confidence threshold would not solve the problem.

### Mixed-scale D4 signature breakthrough (2026-07-14)

The elementary quadratic-tower pilot was retired after five accepted fields
landed only in saturated `24T22789`, `24T23437`, and `24T23883`.  A new
relative-D4 construction fixed the signature obstruction in the previously
verified cyclic-cubic `24T18986` family.  Moving the D4 scale from the cubic
base into its sextic intermediate permits five positive real conjugates and
therefore exactly 20 real roots; the former construction could produce only
multiples of eight.

A bounded search generated 2,000 irreducible `r=20` fields with exact `6T6`
intermediate group.  Of these, 1,096 passed a 98% single-label structural gate
against the twelve exact block/action siblings.  The selected candidate had:

- predicted `24T18986` probability `0.9970950418`;
- exact local signature `r=20`;
- field discriminant
  `16525686288577901743259599596808761881979942437942375906558547145107192152064`;
- dry-run forecast `0.3391980668` points.

Submission `sub_bf8f25cd9ba347eda418749a8109b552` verified exactly as
`24T18986, r=20`.  The controller measured `0.3401862938` realized points and
the public scoreable-pair count increased from 5,352 to 5,353.  This validates
the mixed-scale D4 signature mechanism, but not its exact-label discriminator.

### D4 follow-up invalidation and safe restart (2026-07-14)

The follow-up wave predicted `24T18987`, `24T18990`, and `24T18996`, all at
`r=20`. The authoritative labels were `24T18986`, `24T18986`, and `24T13137`.
Combined with the pilot, the prospective family audit therefore has:

- four accepted trials and one exact-label/exact-pair hit;
- exact-label precision `0.25` and 95% Wilson lower bound
  `0.0455872608`;
- one independent lineage rather than the required three;
- one compatibility-set violation because `24T13137` was outside the claimed
  twelve sibling labels.

The prior 98% cycle-index gate is invalidated. It had confused cycle-pattern
separation with exact construction-family support. `routeA.family_calibration`
now produces a reproducible prospective audit, while the scheduler,
controller, and target atlas all reject explicitly uncalibrated candidates.
The recalibrated 1,096-record shard is blocked: four records are already
committed and the remaining 1,092 are ineligible. An offline controller dry
run selected zero candidates and made zero submissions.

The full safe restart completed as follows:

- reconciled 2,158 server submissions and 1,294,669 results, including 46 new
  results;
- established 5,417 authoritative owned pairs and no unresolved queue state;
- captured snapshot `ed0f4f9b-f9ec-441c-9939-be8e0cddfdd9` at
  `2026-07-14T16:12:26Z` with all 25,000 labels;
- rebuilt an ownership-aware atlas with 73,974 open gold/raid pairs, 40,059
  gold pairs, 33,915 raids, zero candidate-ready rows, ten
  family-calibration gaps, one exact-label seed gap, and 73,963 architecture
  gaps.

No additional mixed-scale D4 candidate is submission-eligible. A new exact
invariant must first explain the `24T13137` outcome and distinguish the open
siblings prospectively across independent lineages.

## Ledger and live services

The reconciled local ledger contains:

- 12,077 candidates;
- 18 completed payload-linked submissions;
- 12,062 accepted payload-linked verifications;
- 2,158 authoritative server submissions and 1,294,669 server verification
  rows;
- 5,417 authoritative owned pairs;
- zero queued or unresolved local submissions.

The pre-existing Mac volume daemon was stopped cleanly and is hard-disabled by
default. It can submit only if `IGP24_ENABLE_LEGACY_VOLUME=1` is set explicitly.
Both GCP instances are stopped. No second targeted POST may be issued while any
queued work remains.

## Current offline command sequence

Reconciliation and progress refresh have completed. Repeat them before any
future candidate selection, then audit the family and rebuild the atlas:

```bash
python3 -m routeA.reconcile
python3 -m routeA.progress
python3 -m routeA.performance
python3 -m routeA.family_calibration \
  routeA/data/mixed_scale_d4_selected.jsonl \
  routeA/data/mixed_scale_d4_recalibrated.jsonl \
  --family mixed_scale_d4_ct_tminus5_e11 \
  --report routeA/data/mixed_scale_d4_calibration.json
python3 -m routeA.target_atlas \
  --candidate-shard routeA/data/mixed_scale_d4_recalibrated.jsonl
```

The current D4 audit must remain blocked. Generate and screen a new exact
invariant or construction family offline. A real pilot is permitted only if
its exact construction-compatible target set and a lineage-held-out test both
pass; cycle-index confidence by itself is a veto signal, not submission
authorization.

Do not resubmit the pilot payload. Rotate the API credential again because it
was shared in chat, then update `~/.config/igp24/api_key` without placing the
replacement value in the repository or on GCP.
