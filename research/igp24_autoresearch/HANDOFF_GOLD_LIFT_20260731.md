> Historical research record. Numerical forecasts, live rankings, and operational instructions refer to its original date. See the repository README for audited results and current release instructions.

# Team Dirac IGP24 gold-lift handoff

## Start here

**Strategy credit:** Durgesh originated and directed all strategy ideation in
this campaign: the lift strategy, the gold-pair/tc0 focus, batching, and the
move from exhausted fields to broad fresh-family searches. This file records
the implementation, mathematical audit, and live execution state so a teammate
can continue **Durgesh's program** without rediscovering it.

Workspace:

`/path/to/private-file`

Terminal condition: repeat the certified-gold loop until the Team Dirac
leaderboard score is at least `1000`. A dead field or family is a rotation
signal, not a stopping condition.

### Live checkpoint

Latest leaderboard observation after the newest wave:

- Score: `943.304088`
- Rank: `11`
- Scoreable-pair counter: `23312`

No additional valid submissions have cleared since that wave.  
Current active-lane reports are mostly obstructions / incomplete certificates, but one fresh exact route is now ready for commit:

- `q260`: `24T23756/r18`, staged in `outbox/q260_one_gold_20260731.txt` (`candidate sha e9f455af8a3aea04e0c093c5f0b43a5416143a02da5d38dc5b7119bea82c36a2`).

This is currently the highest-priority continuation point.

`sair_api.py` calls currently fail in this environment with:
`error: <urlopen error [Errno 8] nodename nor servname provided, or not known>`.

This checkpoint includes the following six-pair wave:

- q28 `24T16723/r20`: verifier accepted
- q28 `24T16723/r24`: verifier accepted
- q80 `24T19334/r20`: verifier accepted
- q80 `24T19336/r24`: verifier accepted
- q81 `24T19341/r24`: verifier accepted
- q158 `24T21456/r24`: verifier accepted

The newest submission in that wave is q158:

`sub_f52c306a06a64f08a05ebc717801c3ac`

The five-minute check at approximately `2026-07-30T20:19:35Z` moved the score
from `937.507119` to `943.304088`, a gain of `5.796969`; the scoreable-pair
counter rose from `23306` to `23312`. There is currently no unelapsed
submission clock. Any new submission starts a new five-minute clock.

### Prompt for the receiving teammate

Say:

> Run the Team Dirac gold-lift handoff from
> `/path/to/private-file`.
> Preserve Durgesh's pinned-parent broad exact squareclass strategy, resume
> q101/q158/q193/q260 from the recorded artifacts, submit only exact
> singleton-core tc0 candidates, mine during every five-minute verifier
> window, and repeat until the leaderboard is at least 1000.

## Operating rules

1. Refresh targets before treating an old tc0 snapshot as current.
2. Submit only certified tc0 `(G,r)` pairs.
3. Prefer batches of two, but do not hold a proved single if compute time is
   short or no companion is imminent.
4. Wait at least five full minutes after the latest submission before checking
   the leaderboard. Any newer submission resets the clock.
5. Hunt during the wait; do not idle for verifier propagation.
6. Freeze every exact artifact and manifest with SHA-256.
7. For multiple polynomials proving the same pair, submit only the
   lower-absolute-discriminant candidate.
8. Never submit an ambiguous squarefree norm core on maximal exclusions alone.
9. Rotate to fresh quotient fields after a repeated lower-kernel or exact
   sign/Selmer obstruction.

## Mathematical strategy producing the gold wave

The successful method is the **pinned-parent broad exact squareclass lift**.

### What the q-numbers mean

The `q` in q28, q80, q81, q158, q185, and similar names is the transitive
degree-12 quotient-group index. For example, q158 means `12T158`. It is an
inventory key for a family of degree-12 quotient fields, not the final
degree-24 target.

A final score pair looks like `24T21456/r24`:

- `24T21456` is the full degree-24 transitive Galois-group label.
- `r24` is the real-root signature.
- `tc0` means no team had yet scored that exact pair at the target snapshot.

### Lift construction

An accepted even degree-24 polynomial has the form

`F(x) = Q(x^2)`,

where `Q` is irreducible of degree 12. The roots of `Q` define a degree-12
field `K`; the roots of `F` are signed square roots of those roots. The
degree-24 action is therefore a quadratic-character lift of the degree-12
quotient action.

The productive loop is:

1. Start with verifier-accepted even parents whose relevant `12x2` block
   system uniquely pins the quotient action `12Tq`, or independently prove the
   exact quotient group.
2. Extract totally real irreducible `Q` fields from accepted parents.
3. Canonicalize with PARI `polredabs`, hash, and deduplicate.
4. Search broadly across fresh canonical fields rather than spending all masks
   on one old field.
5. Compute the ramified squareclass-to-group alignment.
6. Keep a rational squarefree norm core only when it maps to one full
   degree-24 label: a **singleton core**.
7. Apply cheap local valuation-parity and real-sign gates.
8. Solve the exact `K(S,2)` Selmer/sign system over GF(2). Its state contains
   valuation parity at supported rational primes plus 12 real signs.
9. Reconstruct a representative only after the exact desired state lies in
   the Selmer-state image.
10. Require quotient containment, singleton core, signature, irreducibility,
    and complete maximal/Frobenius witnesses.
11. Stage, hash, dry-run, then commit.

Fresh-field breadth is what produced the constant wave. q153, q155, q185,
q195, q28, q80, q81, and q158 succeeded after moving beyond repeatedly
sampled or obstructed fields.

### Critical speed patch

Use the compact squareclass wrappers, including:

- `character_field_gate_squareclass_compact_priority_20260731.sage.py`
- `broad_structural_character_gate_squareclass_20260731.sage.py`
- family-specific `*_squareclass_fast_20260731.sage.py` wrappers

PARI can return a compact principal generator with enormous exponents. In
`K*/K*2`, reduce those exponents modulo two before expansion. This is exact
for squareclasses and changed q155 from an approximately 30-minute stall to an
approximately 20-second result.

For a uniquely pinned parent, the fast wrappers reuse the accepted parent's
unique block action and avoid repeating an expensive generic Galois-group
calculation.

## Verified and submitted gold

### Earlier clean successes

- q153, submission `sub_5f473d7ff654446094c970a9316e6892`:
  `24T20825/r24`, `24T20827/r24`, verified 2/2.
- q155, submission `sub_a1e6533017bd44d8b514fe248411365b`:
  `24T20834/r20`, `24T20834/r24`, verified 2/2.
  Artifact:
  `data/character_field_gate_q155_ambiguous_exact_9ac2b875_squareclass_20260731.json`;
  SHA `0148c806ee3b1e97653ce5590b6133c098c0f5b93eacecaa93a6114ca08594e7`.
- q185, submission `sub_8a71e8bf2d2147f0a97015af15045f75`:
  `24T21879/r24`, `24T21880/r24`, verified 2/2.
  Artifact:
  `data/broad_structural_character_gate_q185_exact_dd356a26_squareclass_fast_20260731.json`;
  SHA `8249fd9fc29bb81d71b8f3db129d1b17fbc861c544a2fc169f1580515b8cc6c7`.
- q195, submission `sub_5cf083421faa48f6bb89d818ad768bd8`:
  `24T22364/r12`, `24T22364/r20`, verified 2/2.
  Artifact:
  `data/broad_structural_character_gate_q195_exact_f33db95b_squareclass_fast_20260731.json`;
  SHA `417af5ee05dadb56a1cf79523f7ec11adf6409a53f10affba21f3366513d0fb0`.

The q185 score interval rose from `932.216845` to `936.009584`, with four
scoreable pairs in the mixed interval. The q195 interval rose from
`936.009584` to `937.507119`, with two new scoreable pairs. Marginal score per
pair varies; never infer the next total from a fixed two-points-per-batch rule.

### New verified wave after score 937.507119

#### q28

- Submission `sub_c18da0b9688a41ce883494a71a250525`
- Pair `24T16723/r20`, verified 1/1
- Artifact
  `data/broad_structural_character_gate_q28_exact_862af371_squareclass_fast_20260731.json`
- Artifact SHA
  `e8c4108843b8c77c7131cede4cd9f331a0fddb861588104548a53ea14673108e`
- Candidate SHA
  `1bad655c79f5be88c7bfc8379184284b47da04ea1e897cc4fe22d8fc8705ecc7`

- Submission `sub_1cb206c6c1c344f09c95b22e9b4340e1`
- Pair `24T16723/r24`, verified 1/1
- Artifact
  `data/broad_structural_character_gate_q28_exact_788a6360_squareclass_20260731.json`
- Artifact SHA
  `43e5f4eaeb07a70609cb0e9fcc9a788dbff9d409d250614706aec107302043d8`
- Candidate SHA
  `ae73a258f52280f87fdb18843c28500e4a212098b31ace8a239f4ba680a13f4c`

#### q80

- Submission `sub_00d14032c16f4b7db00ded03497d20f4`
- Verified 2/2: `24T19334/r20`, `24T19336/r24`
- Artifact
  `data/broad_structural_character_gate_q80_exact_c83ac320_squareclass_fast_20260731.json`
- Artifact SHA
  `73adff60abbbeea7ec74516b129d5e181e54cdbe5fc90d6c7cfce0a7564d0dd8`
- Candidate SHAs:
  `7a12a727c21efb847b1e6efa62ca6b4a3e8707da04b8b15643563e73767cc671`,
  `3accee7dde9a9dd85e2a1e3b07e3689925b3aa6d6da2fa50941e49076039a2ae`

#### q81

- Submission `sub_8f9e30aa88b34e6f96110830088c6eca`
- Pair `24T19341/r24`, verified 1/1
- Artifact
  `data/broad_structural_character_gate_q81_exact_703ed6d7_squareclass_fast_20260731.json`
- Artifact SHA
  `f957d5473e8fb3d066eeb3a8c43a11427dad4f8ef16a84ca5ab45db836983dfd`
- Candidate SHA
  `2678b50fba2b8f95e4f2b25ba43ff288c3022aa69678222be9aaaf6d02e6f196`

#### q158

- Submission `sub_f52c306a06a64f08a05ebc717801c3ac`
- Submitted `2026-07-30T20:14:01Z`
- Verification result: 1/1 accepted, failed 0, one distinct scoreable pair
- Intended pair `24T21456/r24`
- Selected core 146 candidate SHA
  `68e958f343ae780f7f925472592f3272f3e91db5cdd3bf6b13a62c724523bbcb`
- The selected polynomial has a 5,212-digit absolute discriminant, lower than
  the second certified candidate's 5,336 digits.
- Manifest `outbox/q158_one_gold_20260731.txt`
- Manifest SHA
  `33f61f27fe02ce141a48152374bf49c690473f32cde699087763a8b3a28d6ed9`
- Stage certificate `data/q158_one_gold_stage_20260731.json`
- Stage SHA
  `aa78945f20763b78bf089ab3c25db0f4eb07df8f95831ee0fba6bed6ec9793e7`
- Frozen exact artifact SHA
  `e46cb76b8b5bd0e316c95d4a719b2c19bae1339302377b14c5e899fa5914b19d`
- Receipt `receipts/sub_f52c306a06a64f08a05ebc717801c3ac.json`

Containment audit: the target has exactly one `12x2` block system, with
kernel order 2048 and quotient `12T158` of order 576. The field is
independently exact q158, the core is singleton, and all compatible maximals
have complete certificate witnesses.

#### q260 (ready to submit, not yet committed)

- Manifest `outbox/q260_one_gold_20260731.txt` (203 bytes, 1 row)
- Stage certificate `data/q260_one_gold_stage_20260731.json`
- Target pair `24T23756/r18`
- Candidate SHA `e9f455af8a3aea04e0c093c5f0b43a5416143a02da5d38dc5b7119bea82c36a2`
- Field SHA `df4f8ac773067128285097ee8b1182ded0bc0db1cdef8f43591c9e52938734d3`
- Exact artifact
  `data/broad_structural_character_gate_q260_23756r18_exact_df4f8ac7_signed_squareclass_fixedsig_20260731.json`
- Artifact SHA `ab9824c76c7578064935f60786ede14be22bbddce34decc5e7d9000e1907db49`
- Artifact guarantees one tc0 pair (`frozenGoldPairs: 1`) and has full maximal
  certificates in `maximalSubgroupCertificate`.
- Coefficient line:
  `-20271461896,0,7934829370720,0,-196068485918368,0,-2295730363772576,0,70023418341100252,0,-97840516940131712,0,35697119448142816,0,-3310610929167728,0,58136785168098,0,-44074393112,0,-28255944,0,-32,0,1`

Action: submit this packet as soon as network is available.

## Live continuation queue

### 1. q101

- Census `data/broad_structural_tr_field_census_q101_20260731.json`
- 316 fresh canonical fields
- Live pair at last snapshot: `24T19703/r24`
- A local screen had passed at least field 62 before the latest pause.
- Current exact status (July 31): no sealed route is ready.
  - `data/broad_structural_character_gate_q101_exact_a0f4ad39_squareclass_fast_20260731.json`
    is exact Selmer/sign obstructed.
  - `data/broad_structural_character_gate_q101_exact_a5552077_squareclass_fast_20260731.json`
    and `_w10000_r1_...` pass Selmer/sign but are blocked by incomplete
    maximal certificates (`contained_candidate_incomplete_maximal_certificate`).
  - `data/broad_structural_character_gate_q101_exact_a668e01e_squareclass_fast_20260731.json`
    and `_w10000_r1_...` also remain certificate-incomplete.
  - `data/broad_structural_character_gate_q101_exact_ea7db26c_squareclass_fast_20260731.json`
    is exact Selmer/sign obstructed.
  - `data/broad_structural_character_gate_q101_exact_b917520b_w10000_r1_anypin_squareclass_fast_20260731.json`
    is exact Selmer/sign obstructed with `0` solvable masks.
- Latest next batch queued by the live agents: `914b78c6`, `c5529c04`, `e75d74f5`
  (mixed-source singleton fields, any-pinned mode, witness=10000, maxReconstructions=1).

- `data/broad_structural_character_gate_q101_exact_ce793ef1_w10000_r1_independent_squareclass_20260731.json`
  is exact in quotient and local-pass, but still blocked by incomplete maximal
  exclusions (`contained_candidate_incomplete_maximal_certificate`; witness for
  `24T16949` unresolved). 9993 primes were checked.

Operational rule for this family: run one compact exact pass on each queued field and do not submit until the maximal
certificate is complete.

### 2. q158

- One pair is now submitted.
- Continue remaining exact q158 singleton-core fields, shortest/discriminant
  first.
- Known closures: fields `7fbd...` core 2, `ef79...` core 3, and `8221...`
  core 6 are exact q158 but sign-obstructed; `d7e5...` and `e96...` identify
  as q18, not q158. Do not repeat them.
- The successful source field begins `6a95d10d...127d`.

### 3. q193

- Current tc0 targets at last snapshot: `24T21913/r14` and `/r18`.
- The full accepted-parent scan has 7,893 rows.
- An unbounded PARI `polredabs` call stalled; the active restart uses a
  bounded per-field canonical-reduction timeout. Continue from its emitted
  chunk artifacts and skip/resume timeouts.
- Existing chunks include
  `data/broad_structural_tr_field_census_q193_chunk0_1000_20260731.json`
  through later numbered chunks and matching local artifacts.

### 4. q260

- Full sig10 local census is in the 12 singleton-passers stage.
- Agent `q260` reported `12` singleton passers in the current compact
  slice, with one confirmed exact obstruction (`data/..._03e5a033...json`).
  At least `11` passers remain after that field and are being processed.
- In current exact set, `03e5a033...`, `6d9e793a...`, `5430812e...`, `870c1a4c...`, `1f163a14...`, `2adbe8e9...`, `eb24cecd...`,
  and `df4f8ac7...` are all `exact_selmer_sign_obstruction` or
  `failed_exact_candidate_checks` with `certifiedExactRouteCount: 0`.
- Continue exact reconstruction only on currently alive singleton fields in
  increasing discriminant order. Treat all legacy q260 local artifacts as
  stale until re-certified under active containment rules.

### 5. q168

- `data/broad_structural_character_gate_q168_core2_exact_20260731.json` reports
  both `24T21573/r20` and `24T21573/r24` as exact Selmer/sign obstructed.
- No safe stageable candidates remain from this family.
- Keep this lane on hold unless a refreshed exact census adds a new singleton
  field.

### Rotation order after those lanes

Refresh the target table, then rank families by:

`number of live tc0 signatures × number of fresh canonical fields`,

with a strong bonus for accepted parents having a unique `12x2` quotient.
Run one census/local lane, one exact compact-squareclass lane, and one
independent containment/family lane in parallel. Submit as soon as one or two
new pairs are sealed, then rotate while the verifier clock runs.

## What did not work

### Ambiguous cores and weak containment

The intended q214 `24T22560/r20` and `/r24` submission
`sub_5cc3c914121f435fb3c8310e387b8ce6` classified instead as
`24T21631/r20` and `24T19594/r24`. Core 1 was ambiguous, and maximal
exclusions were used without exact containment. Do not resubmit.

The corrected closure artifact is:

`data/q214_failclosed_fullgroup_classification_20260731.json`

SHA:

`e3a0ce37f2f0f16a20eae7b8a94c1a4bb5a29e3f0a302a91d5814181149fc0da`

Exact classification of all 207 core-1 reconstructions found no singleton
`24T22560`; the singleton cores were locally obstructed.

q77, q136, and q138 produced many local routes but only ambiguous cores. They
are unsafe without an independent full-group classifier.

### Source-parent quotient misidentification

An accepted parent can have several `12x2` block systems. Structural source
labels alone therefore misidentified:

- a q106 singleton candidate that independently proved to be q100;
- some q158 candidates that independently proved to be q18;
- many q191 candidates as q146, q224, or q9;
- one q78 field as q34.

Require uniqueness of the accepted parent's block quotient or independent
exact Galois identification.

### Exact Selmer/sign obstruction

Local passing is necessary but not sufficient. Exact obstructions closed:

- all four q150 singleton fields;
- several q158 fields listed above;
- q191 singleton routes;
- q226 target `24T22818/r24`;
- q187, q95, q236;
- prioritized q174, q47, q168, and q242 routes.

q78 was fully exhausted: 917 accepted sources reduced to four canonical
fields, only three were exact q78, and all exact q78 routes were locally
obstructed.

q191 closure:

`data/q191_singleton_exact_closure_20260731.json`

SHA:

`5b6229fd6a87feea185ceca9d3badea9ec1e5adc4d45e8f299394f16a36e2134`

### Fixed lower-kernel traps

- q91: all 256 exact masks exhausted.
- q227 old field: all 512 exact masks exhausted.
- q185 old field `470c...`: repeated kernel-512 barrier.
- q109 fields `b889...` and `43be...`: union of two lower-kernel maximals.
- q100, q224, and q142: exact routes exhausted or fixed in lower kernels.

Change fields after repeated routes land in the same proper maximal subgroup.

### Compute and throughput failures

- Generic full Galois-group calls, unbounded `polredabs`, and discriminant
  factorization can stall. Use unique-parent fast wrappers, bounded per-field
  timeouts, atomic output, and skip/resume.
- Expanding huge PARI generators before mod-2 reduction wastes minutes.
- Large non-tc0 batches add little score. The unit of progress is a fresh
  verified `(G,r)` pair, not polynomial count.
- One field can realize r20 but exhaust every r24 Selmer coset, as in the first
  q28 hit. Change fields for the missing signature.
- Multiple polynomials for the same pair do not give multiple score gains.
  Submit one lower-discriminant representative.

## Restart and submission commands

```bash
cd /path/to/private-file
python3 sair_api.py targets
```

Stage/commit the fastest packet first, then sync once the API is healthy:

```bash
python3 sair_api.py submit --file outbox/q260_one_gold_20260731.txt --description 'Dirac exact tc0 gold' --commit
```

Then synchronize and continue (old packets only):

```bash
python3 sair_api.py sync sub_c18da0b9688a41ce883494a71a250525
python3 sair_api.py sync sub_1cb206c6c1c344f09c95b22e9b4340e1
python3 sair_api.py sync sub_00d14032c16f4b7db00ded03497d20f4
python3 sair_api.py sync sub_8f9e30aa88b34e6f96110830088c6eca
python3 sair_api.py sync sub_f52c306a06a64f08a05ebc717801c3ac
```

Create a broad quotient-field census:

```bash
sage -python broad_structural_tr_field_census_20260730.sage.py \
  --quotient-t Q \
  --output data/broad_structural_tr_field_census_qQ_20260731.json
```

Run the local screen:

```bash
sage -python broad_structural_character_gate_squareclass_20260731.sage.py \
  --quotient-t Q \
  --target-label 24T... \
  --census data/broad_structural_tr_field_census_qQ_20260731.json \
  --phase local \
  --output data/broad_structural_character_gate_qQ_local_20260731.json
```

Run exact reconstruction on one passing field:

```bash
sage -python broad_structural_character_gate_squareclass_20260731.sage.py \
  --quotient-t Q \
  --target-label 24T... \
  --census data/broad_structural_tr_field_census_qQ_20260731.json \
  --phase exact \
  --field-hash FIELD_SHA256 \
  --witness-primes 1000 \
  --max-reconstructions 16 \
  --max-coset-reconstructions-per-sign 16 \
  --output data/broad_structural_character_gate_qQ_exact_FIELD_20260731.json
```

Dry-run and commit a frozen manifest:

```bash
python3 sair_api.py submit --file outbox/PACKET.txt --description 'Dirac exact tc0 gold'
python3 sair_api.py submit --file outbox/PACKET.txt --description 'Dirac exact tc0 gold' --commit
```

After five full minutes from the most recent submission:

```bash
python3 sair_api.py sync SUBMISSION_ID
python3 sair_api.py leaderboard
```

After the score check, update this file's checkpoint and immediately continue
the queue. Stop only when the observed Team Dirac score is at least `1000`.
