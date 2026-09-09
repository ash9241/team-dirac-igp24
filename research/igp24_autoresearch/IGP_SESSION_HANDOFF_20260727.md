> Historical research record. Numerical forecasts, live rankings, and operational instructions refer to its original date. See the repository README for audited results and current release instructions.

# IGP24 session handoff — 2026-07-27

Read this file first whenever the user says **“resume IGP”**.

## Frozen state

- Team: Dirac.
- Last confirmed live score: `961.760386`.
- Goal remains: `>= 1000`.
- The live score and target counts could not be refreshed at shutdown because the
  SAIR endpoint stopped resolving. Do not infer a newer score from local state.
- All search agents were stopped at handoff. No background search or submission
  process remains active.

## Submission accounting

Exactly two polynomials received a submission receipt during this session:

- pairs: `24T14837/r16`, `24T15247/r0`
- both were `tc1` when submitted, so they were not gold pairs
- submission: `sub_a1222edd640d4f4fad7ce9be150131ff`
- batch: `igp24_batch_7c3c5cfff9f242e085f41361a58e51f0`
- receipt:
  `receipts/sub_a1222edd640d4f4fad7ce9be150131ff.json`

Eight further distinct, exactly certified polynomials are staged, but **none of
these eight has a receipt and none should be described as submitted**:

1. Five exact gold-pair candidates:
   `outbox/f6_post22_unique_wave_exact_plus_f5_5line.txt`
   - `24T10482/r8`
   - `24T14293/r16`
   - `24T16948/r16`
   - `24T16949/r16`
   - `24T15337/r20`
   - lines: `5`
   - SHA-256:
     `653dea629745bb970772d075500ec8285290a4e7aab1cffe37c6350178261357`

2. Two exact competitive shared-pair candidates:
   `outbox/competitive_shared_exact_2_20260727.txt`
   - `24T15043/r4`, snapshot `tc3`; exact field discriminant about
     `61.23` times below the then-current minimum
   - `24T11787/r12`, snapshot `tc4`; exact field discriminant about
     `18.14` times below the then-current minimum
   - lines: `2`
   - SHA-256:
     `7b7bdf8d53f4348805881b47e4e6c71d53e9782c94b3b9663b929c18c33d9627`
   - certificate:
     `data/competitive_shared_exact_2_20260727_certificate.json`
   - certificate SHA-256:
     `180358649f85557d7bb27cfc670ad1ff5b8eb949109b53bbad5c0d853f9ac304`

3. One new exact gold-pair candidate:
   `outbox/p27_exact_24T24877_r14_20260727.txt`
   - pair: `24T24877/r14`
   - snapshot: `tc0`, undiscovered, nonbaseline, locally unowned
   - lines: `1`
   - SHA-256:
     `b00094bd08a531b4070ecaa11e3be563bcddeb3cd322ca49fd6e12077e26dd4f`
   - coefficient SHA-256:
     `24b7d00911885b4aace2a519ddc388bf5ce56ade0f4901ba61096cede037572b`
   - coefficients:
     `-125,0,3648,-7296,-23232,109344,-159008,56064,115428,-156416,47520,57024,-63584,13680,18576,-14080,-105,3840,-1040,-480,240,24,-24,0,1`
   - exact field discriminant:
     `598982108025123479690046841962242584514396160000`
   - exact polynomial discriminant:
     `1511747050832789110172446745910124553868090689551871537225008152576000000000000`
   - certificate:
     `data/p27_s4_wreath_6t11_pairnorm_exact_certificates_20260727.json`
   - certificate SHA-256:
     `6a01c16626b4e243747d111ee1f2f884802570b29bf6a98755b4147170bbe00a`
   - reproducible audit:
     `p27_s4_wreath_6t11_pairnorm_exact_certificate_20260727.sage.py`

The field-discriminant integer of the final candidate also occurs locally for a
different field/group, `24T9427/r4`. This is not a field-isomorphism collision:
the normal-closure groups differ, and the coefficient hash is new.

At shutdown, all three intended commit commands were attempted sequentially.
Each failed before creating a receipt with:

`<urlopen error [Errno 8] nodename nor servname provided, or not known>`

No retry loop is running.

## Resume sequence

1. Restore connectivity and refresh the live targets and leaderboard first.
   Do not spend time diagnosing why older server-side rows remain queued; the
   user explicitly asked to prioritize new gold pairs.
2. Revalidate the eight staged pairs against the fresh target snapshot.
3. If still useful and not already credited, submit the three manifests:

```bash
cd /path/to/private-file
python sair_api.py submit \
  --file outbox/f6_post22_unique_wave_exact_plus_f5_5line.txt \
  --description "5 exact audited gold pairs" --commit
python sair_api.py submit \
  --file outbox/competitive_shared_exact_2_20260727.txt \
  --description "2 exact competitive shared pairs" --commit
python sair_api.py submit \
  --file outbox/p27_exact_24T24877_r14_20260727.txt \
  --description "exact 24T24877 r14 discriminant-pair norm construction" \
  --commit
```

4. Save every returned receipt, refresh the score, and deduplicate by exact
   `(group, signature)` pair. Do not submit the other three presentations of
   `24T24877/r14`; they certify the same scoring pair.
5. Only then launch a new search round from the fresh live target snapshot.

If all six staged tc0 pairs were still gold, and the two competitive rows still
had the same shared counts, their snapshot-value estimate was only
`6 + 3/16 = 6.1875` points. From the last confirmed score that would be roughly
`967.947886`, not `1000`. Never claim that these eight alone meet the goal.

## What worked

### F6 post-22 accepted-source wave

- Re-auditing accepted F6 sources added after the earlier pair-action census
  exposed 191 degree-24 actions from 150 source labels.
- Fifty-nine routes hit then-current tc0 pairs; executing all 53 unique-orbit
  routes produced four exact candidates:
  `24T10482/r8`, `24T14293/r16`, `24T16948/r16`,
  `24T16949/r16`.
- Independent reruns reproduced coefficients, source receipts, factor degrees,
  action labels, signatures, and novelty.
- Recursive closure and the remaining multi-orbit routes added nothing beyond
  those four.

### F5 derivative-radicand recovery

- A derivative-radicand construction produced exact `24T15337/r20`.
- Its intermediate source was separately certified as `24T19738`; a complete
  catalog/Frobenius audit plus the preserved Kummer relation excluded the last
  competing group.
- This is the fifth row in the F5/F6 staged manifest.

### Exhaustive saved-candidate mining

- A coefficient-hash census over `5,944` saved exact candidates found only two
  presently useful competitive shared rows, but both have very strong exact
  discriminant improvements: `24T15043/r4` and `24T11787/r12`.
- This lane was valuable as a one-time exhaustive recovery pass. The saved
  inventory is now exhausted under that snapshot.

### New quartic-over-sextic discriminant-pair-norm family

For integers `k,m`, set

```text
L = 4k(9m^2 + 4k^3)
M = m^2(27m^2 + 16k^3)
g(z) = z(z+L)^2 - (8k^2 z + M)^2 + D(Az+B)^2
q(y) = g(y^2)
P(x) = g((x^4 + 4kx^2 + 4mx)^2).
```

The relative quartic `x^4 + 4kx^2 + 4mx + y` has paired discriminant
product

```text
D * (256(Ay^2+B))^2
```

at paired roots of `q`. For an exact `6T11` sextic base this forces the
three opposite-pair parities to agree, giving the required dimension-four
sign kernel.

- The full ambient group is `S4 wr 6T11 = 24T24917`.
- The constrained kernel has order `47,775,744`.
- Its two relevant complement preimages are `24T24877` and `24T24876`,
  each of order `2,293,235,712`.
- Frobenius types exclude `24T24876` and every one of the nine proper
  transitive maximal subgroups of `24T24877`.
- Four coefficient-distinct presentations were independently certified as
  exact `24T24877/r14`; the smallest-field-discriminant presentation is staged.

This is the strongest genuinely new mathematical idea from the session.

## What did not work and is blocked

Do not rerun these lanes merely with wider coefficient boxes. Reopen one only
after a material structural change.

- **Old F5 ledger routes:** all `839` previously unexecuted representatives
  across `185` source signatures and `109` then-tc0 pairs were closed with zero
  current hit. Blocked.
- **Remaining F6 routes:** unique-orbit ceiling, all multi-orbit routes, and
  recursive pair/block closure were exhausted after the four F6 hits. Blocked
  without a new source population.
- **Saved exact candidate census:** all `5,944` unique hashes were checked;
  only the two staged competitive rows survived. Exhausted.
- **Scalar twists:** `3,845` saved F5/F6 actions were audited. `1,516` lacked
  the flip but never closed to a tc0 action; `2,329` already contained it.
  Certificate:
  `data/scalar_twist_saved_f5_f6_completion_certificate_20260727.json`
  (SHA-256
  `38da2c0d68dd22ed7b0d1f7d64c0f98a01294b907cf100d51fa77b3318fb5ed5`).
  Blocked.
- **Odd-subset scalar twists:** all `2,404` odd-subset orbits were tested.
  The only `164` one-rank-below actions already had the natural global flip.
  Zero liftable target. Blocked.
- **`12T6` A4/K4 norm family:** `308` candidates, all discovered labels.
  Blocked.
- **`12T8` ordered-edge K4 family:** the action has an extra global square
  relation, forcing kernel rank at most `8` where targets need `9`.
  A `25,000`-group catalog, `3,150`-case pilot, and `38,830`-case salvage
  produced no target. Obstruction:
  `data/q8_ordered_edge_k4_obstruction_20260727.json`. Blocked.
- **Generic radical-cover families:** generic `q(x^3)`, norm-cube kernels,
  even monomial lifts `(6,4)`, `(4,6)`, `(3,8)`, and generic Chebyshev cubic
  lifts have no live signature intersection in their exact generic groups.
  Blocked absent a proper-subgroup mechanism.
- **Shifted derivative siblings:** `19` aligned shifts on `16` quotients gave
  `17` exact degree-12 factors, all noncurrent. Blocked.
- **Low-degree index-24 action sweep:** degree-7/8 source groups gave
  `121` actions and `146` signature pairs, with no tc0 intersection. Blocked.
- **Other exhausted lanes:** q66 K4 norm, nonlinear quadratic transforms,
  monomial/Chebyshev/cube generic families, direct product/compositum routes,
  and the order-384 / `12T30` affine-reciprocal routes produced no certified
  live pair. Treat them as blocked unless the approach registry records a new
  structural hook.
- **Degree-9/10 index-24 follow-up:** it was started but interrupted when the
  user asked to stop. There is no certified result to resume blindly; first
  inspect whether a complete artifact exists.

## Pair-norm follow-up: useful negative result

The successful pair-norm family presently controls the group but not enough
signatures:

- initial `k=-1,m=1` pilot over `5,001` bases: four exact `r14` candidates,
  no `r10` or `r18`
- `k=-2,m=1`: `9,581` exact `6T11` bases and `4,873` irreducible target
  survivors, all `r14`; observed signatures
  `{8:1802, 14:4873, 16:2906}`
- `k=-2,m=2`: `2,605` exact `6T11` bases and `1,148` target survivors,
  all `r14`; observed signatures
  `{2:188, 14:1148, 16:1269}`

Artifacts:

- `data/p27_s4_wreath_6t11_pairnorm_pilot_20260727.jsonl`
- `data/p27_s4_wreath_6t11_pairnorm_km_n2_1_20260727.jsonl`
- `data/p27_pairnorm_km_n2_2_target_20260727.jsonl`

Do not bulk-scan those strata again. The next credible use of this idea is a
mathematically new signature-control deformation preserving the paired
discriminant relation—for example a nontrivial recentering/pairing of the
sextic parameter or a more flexible depressed-quartic model—not another blind
box expansion. Derive the archimedean possibilities before arithmetic.

## Operating rules on resume

- Begin with a fresh live target census.
- Maintain the approach-family registry and keep early agents independent.
- Run adversarial exact-group and signature audits before treating a candidate
  as gold.
- One scoring pair gets one submission even if several polynomial
  presentations realize it.
- Never report a local candidate as submitted without a server receipt.
- Prefer new structural families over replaying the blocked elegant
  reductions above.

