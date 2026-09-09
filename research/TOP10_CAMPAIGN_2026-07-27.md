> Historical research record. Numerical forecasts, live rankings, and operational instructions refer to its original date. See the repository README for audited results and current release instructions.

# IGP24 Top-10 Campaign — 2026-07-27

## Live position

- Dirac: rank 12, score `962.094390`, `22,851` scoreable pairs.
- Rank 10: `1066.392613`.
- Live top-10 gap: `104.298223`.
- A 150-point gain gives `1112.094390`, a `45.701777` cushion over the
  current rank-10 score.
- Outstanding server queue: 110 polynomials. This includes the 100-candidate
  degree-12 quadratic-lift calibration probe.

The current queue is not large enough to supply 150 points, and most of it is
calibration or baseline-unlock work. Do not count queued points before the
server classifies them.

## What the audit ruled out

### Standard degree-24 pair resolvents

- Two fresh local pilots: 122 exact computations, zero currently new pairs.
- Saved cloud corpus: 83,482 exact pair-resolvent rows, zero currently new
  pairs.
- Conclusion: the standard pair-sum/pair-product closure is exhausted for the
  current source catalog. Do not scale it on GCP.

### Cached exact candidates

- 87,673 exact-marked saved cloud rows were joined against the authoritative
  ledger.
- Every unattempted row targets a pair already owned.
- Apparent cached golds were previously submitted and server-relabeled.
- Conclusion: there is no hidden ready-to-submit harvest.

### Untargeted tower volume

- Four historical 1,000-candidate tower waves produced 366 distinct accepted
  pairs.
- At their landing-time competition state, the common classes were already
  crowded; reconstructed retained gain was only about `0.064`.
- Conclusion: random tower volume is a classifier-discovery tool, not a
  scoring strategy.

## Where the points remain

The exact block-2/4/8 target book intersects the current board in:

- 21,327 unowned scoreable pairs;
- 8,622 gold pairs;
- 12,705 raids;
- `13265.35` raw points.

The largest lower-quotient lanes are:

| Quotient lane | Open pairs | Gold pairs | Raw ceiling |
| --- | ---: | ---: | ---: |
| 6T7 | 6,370 | 2,452 | 3,864.49 |
| 6T11 | 5,584 | 2,458 | 3,600.08 |
| 6T6 | 5,658 | 2,343 | 3,570.52 |

These ceilings overlap, but they show that a 150-point gain is mathematically
available. The problem is reaching rare subgroup and signature strata.

Already calibrated server groups contain a smaller, immediately researchable
signature lane:

- 208 groups previously reached by cubic–quadratic towers;
- 559 unowned signatures on those groups;
- 23 golds and `67.82` raw points.

The highest-value known-group targets include:

- `24T17732`: six missing gold signatures;
- `24T21954`: three missing gold signatures;
- `24T15683`: two missing gold signatures;
- `24T13762`: three missing gold signatures;
- `24T6486`, `24T9430`, `24T6628`, and `24T19038`: one or more missing golds.

## 200-point gross portfolio

Aim for 200 gross points so that relabeling, collisions, and competing teams
can reduce the result while retaining at least 150.

| Lane | Gross target | Method |
| --- | ---: | --- |
| Rare 6T7/6T11/6T6 quotient fibers | 110–130 | Generate structurally diverse quartic fibers over fixed, certified sextic quotients; bias toward dependency relations that produce proper subgroups. |
| Known-group signature siblings | 25–35 | Start from server-calibrated tower seeds and vary real-place sign data while preserving the dependency architecture. Target r=12,16,20,24 first. |
| Degree-12 character lifts | 20–30 | Complete norm-equation and unit-sign squareclasses, especially the 12T210/12T214 cluster and strict server-calibrated character labels. |
| Baseline/recovery/reserve | 10–20 | Only discriminant-competitive baseline unlocks, current exact recoveries, and a reserve alternate architecture. |

## Calibration and scale gates

1. Let the current 100-probe quadratic-lift batch finish.
2. Measure distinct accepted pairs, new golds, raids, and retained score—not
   merely accepted polynomials.
3. Scale a family only if a 100-candidate probe produces either:
   - at least 5 retained points; or
   - at least 10 distinct rare-group hits with a credible sign-sibling path.
4. Kill a family after two independent probes below 1 retained point per 100
   candidates.
5. Refresh the board and ownership ledger before every submission batch.
6. Submit golds first, then k=1 raids. Avoid high-team-count raids unless they
   are discriminant improvements.

## GCP execution envelope

- Project: `dirac-phm`, region `us-central1`.
- General CPU quota: 200; observed usage: 40; available headroom: 160 vCPU.
- Preemptible CPU quota: zero.
- Use 120–144 vCPU for campaign arrays, leaving headroom for existing
  `phm-*` instances.
- Workers remain secret-free and never submit. They write deterministic
  candidate shards and reports to GCS.
- CPU is the primary resource for PARI resultants, irreducibility, root counts,
  and GAP subgroup work. Use GPUs only after a modular-sieving kernel is shown
  to dominate runtime.

## Immediate sequence

1. No further submissions while the 110-polynomial backlog is unresolved.
2. Build local sign-sibling pilots around the highest-value calibrated groups.
3. Use the 100-probe result to choose the first cloud family.
4. Launch a 120–144-vCPU GCP array only after the local/server calibration
   gate passes.
5. Stage score-ranked candidate batches locally; refresh and submit only from
   the trusted controller.
