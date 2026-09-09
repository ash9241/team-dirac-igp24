# IGP24 top-10 strategy request for GPT-5.6 Pro

You are taking over as the senior computational-number-theory strategist for
an active IGP24 leaderboard campaign. Work from evidence in this repository,
not from generic brainstorming.

## Objective

Find a credible path to at least **150 retained leaderboard points**, enough to
move team **Dirac** from roughly rank 12 into the top 10 with a safety margin.

The objective is retained score after:

- server-side Galois-group and real-root classification;
- relabelling away from our predicted `24T` group;
- collisions with pairs we already own;
- raids already shared by several teams;
- discriminant competition;
- other teams scoring while we compute.

Accepted polynomials, mathematical novelty, local discoveries, and raw
opportunity ceilings do **not** count unless they can plausibly become
previously unowned, scoreable `(24Tt, r)` pairs.

For this first response, **do not submit anything and do not launch paid
compute**. Inspect, reason, propose, and design cheap falsification tests.

## Repository and live-state instructions

You have access to the repository. Inspect the relevant code and data directly.
Do not rely only on this prompt.

Start with:

- `TOP10_CAMPAIGN_2026-07-27.md`
- `IGP24_Rank_Climbing_System.md`
- `routeA/data/control.sqlite3`
- `daemon/state.json`
- `daemon/knowledge.jsonl`
- `routeA/block_target_book.py`
- `routeA/target_atlas.py`
- `routeA/gap_block_census.py`
- `routeA/build_general_quartic_pilot.py`
- `routeA/build_general_quartic_expansion.py`
- `routeA/build_empirical_tower_experiment.py`
- `routeA/build_catalog_character_campaign.py`
- `routeA/discover_norm_equation_characters.py`
- `routeA/discover_character_closure.py`
- `routeA/constructions/`
- `cloud/plan_character_campaign.py`
- `cloud/plan_character_certification.py`

Inspect historical campaign reports and server-verification records before
calling any family promising.

Refresh the current leaderboard, target ownership, team counts, outstanding
server queue, and top-10 cutoff with the trusted local CLI/API if available.
Treat the following leaderboard figures only as a prior snapshot:

- Dirac was around rank 12 and score 962.
- The previous rank-10 cutoff was around 1066.
- We are targeting a 150-point gross gain because a narrow nominal gap is not
  enough protection against relabelling, collisions, and leaderboard drift.

## Compute available

- GCP project: `dirac-phm`
- Region: `us-central1`
- General CPU quota: 200 vCPUs
- Existing non-campaign usage was about 40 vCPUs
- Safe campaign envelope: 120–144 vCPUs
- Spot/preemptible CPU quota is zero, so assume standard instances
- Typical worker: `e2-standard-8`
- Approximate fully loaded cost: about $0.272/hour per worker including its
  small boot disk
- Default experimental cap: **$30**
- Workers must remain credential-free and must never submit
- CPU is appropriate for PARI/GAP/SymPy work
- Use GPUs only if you identify and benchmark a genuinely GPU-suitable modular
  sieve or other high-throughput kernel

We can parallelize aggressively after a family passes a cheap calibration
gate. Cost is not the main bottleneck; targeting the correct scoreable strata
is.

## What has already failed or been exhausted

Do not re-propose these unchanged.

### 1. Broad degree-12 character-discovery sweep

Campaign:

- `cloud/campaigns/top10-character-wave1-all-v1/`
- 671 deterministic tasks
- 61 linear, 183 product, 183 kernel, and 244 norm-equation shards
- 15 `e2-standard-8` workers at peak
- 671/671 tasks succeeded
- zero infrastructure failures

Final output:

- `cloud/campaigns/top10-character-wave1-all-v1/merged-discoveries.jsonl`
- 93 unique calibrated discovery rows
- 9 predicted degree-24 targets
- 7 degree-12 bases

Result:

- Every attainable signature on those character groups was already in our
  authoritative ownership ledger.
- Score-ready groups: zero.
- Linear/product/kernel searches were nearly barren.
- Exact norm equations were the only productive discovery mechanism, but they
  mostly rediscovered owned positive-norm characters.

### 2. Deep norm-equation sweep

Campaign:

- `cloud/campaigns/top10-character-deepnorm-v1/`
- 40 tasks over bases `12T214`, `12T156`, `12T243`, `12T117`, `12T77`,
  `12T171`, `12T267`, `12T210`, `12T38`, and `12T138`
- Used previously untried prime-square twists from 67 through 199
- More norm solutions, calibration options, and lift attempts
- 40/40 tasks succeeded

Final output:

- `cloud/campaigns/top10-character-deepnorm-v1/merged-discoveries.jsonl`
- 132 unique calibrated seeds
- only four character groups:
  - base 138, target `24T20764`, norm class 13
  - base 156, target `24T21372`, norm class 5
  - base 156, target `24T21374`, norm class 505
  - base 243, target `24T23290`, norm class 85

Result:

- All four were positive-norm characters.
- Their attainable `r ≡ 0 mod 4` signatures were already owned.
- Some target groups had open `r ≡ 2 mod 4` signatures, but these positive-norm
  seeds cannot realize them.
- Score-ready groups: zero.
- Varying the square twist improved norm solvability but did not cross the
  norm-sign/character barrier.

### 3. Character closure/sign-product pilot

- Seven source fields
- Thirteen closure seeds
- 7/7 tasks completed locally
- Zero calibrated closures

Simply multiplying the discovered characters or adding the permutation-sign
character did not produce a usable new group.

### 4. Standard degree-24 unordered-pair resolvents

- Two fresh local pilots: 122 exact computations, zero currently new pairs
- Saved exact corpus: 83,482 pair-resolvent rows, zero currently new pairs
- The standard pair-sum/pair-product closure over the existing source catalog
  appears exhausted.

Relevant planned pilots include:

- `cloud/campaigns/top10-pair-pilot-v1/`
- `cloud/campaigns/top10-pair-signature-pilot-v1/`

Do not recommend scaling these unchanged.

### 5. Cached exact candidates

- Roughly 87,673 exact-marked cached candidates were joined against the
  authoritative ownership ledger.
- All unattempted rows targeted pairs already owned.
- Apparent cached golds had already been submitted or server-relabeled.

There is no hidden ready-to-submit cache harvest.

### 6. Untargeted/random tower volume

- Four historical 1,000-candidate tower waves produced many accepted
  classifications but negligible retained score.
- Reconstructed retained gain was around 0.064 in the relevant competition
  state.

Random volume is useful for classifier discovery, not for a 150-point climb.

### 7. General-quartic and sextic–quartic history

There is already substantial work in:

- `routeA/data/gq96_*`
- `routeA/data/gq_expanded_*`
- `routeA/data/gq_*sextic*`
- `routeA/build_general_quartic_pilot.py`
- `routeA/build_general_quartic_expansion.py`

Audit its server yield before proposing another general-quartic wave. A new
proposal must introduce a genuinely different dependency/intersection
mechanism, not merely new random coefficients or source polynomials.

## Where mathematical opportunity still appears to exist

The exact block-system target book previously showed:

- 21,327 unowned scoreable pairs
- 8,622 gold pairs
- 12,705 raids
- about 13,265 raw points

The largest overlapping quotient lanes were approximately:

| Sextic quotient | Open pairs | Gold pairs | Raw ceiling |
| --- | ---: | ---: | ---: |
| `6T7` | 6,370 | 2,452 | 3,864 |
| `6T11` | 5,584 | 2,458 | 3,600 |
| `6T6` | 5,658 | 2,343 | 3,571 |

This proves that points exist abstractly, but not that our current
constructions reach the rare subgroup/signature strata.

A smaller known-group lane previously contained:

- 208 groups reached by cubic–quadratic towers
- 559 unowned signatures
- 23 golds
- about 67.8 raw points

Previously interesting known groups included:

- `24T17732`
- `24T21954`
- `24T15683`
- `24T13762`
- `24T6486`
- `24T9430`
- `24T6628`
- `24T19038`

Refresh all of these against the current ledger before using them.

## Central technical bottlenecks

### Negative-norm degree-12 characters

The norm-equation solver currently searches

`Norm_{K/Q}(h) = d s^2`

for signed squareclasses supported on the degree-12 discriminant. Positive
classes are readily solved and calibrated, but they land only on signature
parity we already own. The point-bearing siblings often require negative norm
or a different character combination.

Investigate whether the right next mechanism involves:

- narrow class groups rather than ordinary class groups;
- unit-signature rank and explicit units of norm `-1`;
- weak approximation with prescribed signs at real places;
- `S`-unit equations;
- relative norm equations through an intermediate subfield;
- ray class fields with infinite places in the modulus;
- Grunwald–Wang or local-global obstructions;
- constructing a desired Kummer character directly from local Hilbert symbols;
- changing to alternate degree-12 fields with the same abstract group but
  better narrow-unit signatures;
- sharding supported squareclasses themselves, rather than only sharding the
  rational square twist `s`;
- a proof that some desired negative characters are impossible for a given
  field, so we stop wasting time on them.

Do not assume one of these is correct. Determine which is mathematically
applicable to the actual fields and code.

### Rare quotient fibers

The largest theoretical opportunity lies above sextic quotients `6T7`,
`6T11`, and `6T6`, but generic quartic fibers usually generate common wreath
products or already crowded groups.

We need constructions that deliberately enforce non-generic dependency,
intersection, or submodule structure while retaining control of real
signatures. Consider:

- prescribed quartic resolvent fields;
- controlled intersections between quartic splitting fields and the sextic
  base;
- relative `D4`, `C4`, `V4`, or `A4` fibers with exact dependency rank;
- composita sharing a chosen quadratic or cubic subfield;
- embedding-problem formulations and solvability conditions;
- class-field constructions over the sextic field;
- targeted subdirect products rather than full wreath products;
- using the GAP block census backwards to synthesize a group, not merely
  classify a generated polynomial.

Again, propose concrete mechanisms tied to reachable `24T` targets.

## What I need from you

### A. Audit the premise and scoring model

1. Refresh the board and exact ownership state.
2. Recompute the real top-10 gap and a safe gross target.
3. Verify which apparent opportunity is actually reachable under signature,
   quotient, and character constraints.
4. Identify any flaw in our ownership join, parity model, calibration logic,
   or interpretation of score.
5. State plainly whether a 150-point gain is still realistically achievable.

### B. Produce genuinely new strategies

Give 5–10 distinct strategies. For each, specify:

- exact mathematical construction;
- targeted quotient, subgroup family, or `24T` strata;
- attainable real-root signatures;
- why it differs from exhausted work;
- required source fields or seed data;
- local certificate before server submission;
- unavoidable server-calibration uncertainty;
- estimated discoveries per 100 candidates;
- estimated retained points per 100 candidates;
- CPU/GPU suitability;
- expected GCP cost;
- fastest falsification test;
- explicit stop rule.

Do not rank a strategy highly merely because its raw target ceiling is large.
Rank by expected retained points per dollar and per hour.

### C. Choose the best three

For the top three, give:

1. mathematical justification;
2. exact target selection query;
3. a 10–50-candidate local calibration experiment;
4. files and functions to add or modify;
5. deterministic task-sharding design;
6. result schema;
7. deduplication and ownership checks;
8. score forecast with pessimistic/base/optimistic cases;
9. kill/scale thresholds.

### D. Give one implementation-ready plan

For the single best strategy, provide a plan detailed enough for a coding
agent to implement immediately:

- code architecture;
- algorithms or PARI/GAP commands;
- pseudocode;
- exact existing modules to reuse;
- new CLI flags or modules;
- unit tests;
- a local smoke command;
- a GCP Batch matrix;
- worker count and timeouts;
- maximum cost;
- merge and validation commands;
- criteria for declaring a candidate score-ready.

The first paid wave must fit under $30, but it should begin with a much cheaper
local or 1–2-worker falsification gate.

## Required discipline

- Do not recommend generic random search.
- Do not confuse valid candidates with scoring candidates.
- Do not count raw opportunity ceilings as expected gain.
- Do not assume local predicted `24T` labels survive server classification.
- Do not propose a cloud wave without a cheap calibration gate.
- Do not submit anything.
- Do not repeat an exhausted lane unless the mathematical mechanism is
  materially different and you explain why.
- Prefer exact group synthesis or inverse use of the block census over blind
  generation.
- Challenge the current code and strategy if they encode the wrong abstraction.

## Response format

Use this structure:

1. **Executive verdict**
2. **Refreshed scoreboard and achievable gap**
3. **Failure postmortem**
4. **Reachability audit of the remaining islands**
5. **Ranked strategy table**
6. **Detailed top-three strategies**
7. **Implementation-ready plan for strategy #1**
8. **Pessimistic/base/optimistic point forecast**
9. **Stop rules and decision tree**
10. **Important unknowns**

Be specific, quantitative, skeptical, and willing to conclude that a proposed
lane is not viable.
