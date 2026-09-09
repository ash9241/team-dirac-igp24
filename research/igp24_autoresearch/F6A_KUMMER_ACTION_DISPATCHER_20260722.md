> Historical research record. Numerical forecasts, live rankings, and operational instructions refer to its original date. See the repository README for audited results and current release instructions.

# F6-A signed two-block/Kummer action dispatcher

Status: implemented and light-tested; Sage/GAP census not launched.

## Fresh scope

The planner validates the accepted even source receipt, sealed structural and
isomorphism certificates, and fresh live target state before exposing a task.
It schedules 55 source/subset-size tasks:

- `24T5786`, `24T6436`, `24T11202`, `24T14141`, and `24T14292`: `k=2..11`.
- `24T10913`: only `k=7..11`.

It records and skips 22 exact/theorem-closed tasks:

- `k=1` for every active source, because the singleton signed action is the
  source action rather than a sibling action.
- `24T10913`, `k=2..6`, covered by the saved exhaustive pair/subset action
  censuses with no `24T10916` action.
- `24T12043`, every `k=1..11`, covered by the isolated complete zero-hit
  certificate. This source is never passed to the worker.

The active safe frontier contains eight live target pairs: both `r8` and
`r16` for `24T5653` and `24T11204`, plus `24T6910/r16`, `24T10916/r20`,
`24T11566/r16`, and `24T14142/r16`.

## Exact authorization and specialization contract

The worker enumerates every fixed-point-free centralizer involution, hence
every exact two-point block system of the standard source action. For each
length-12 quotient orbit of `k`-subsets it constructs the signed degree-24
action. Polynomial arithmetic remains forbidden unless all of these hold:

1. The induced action is transitive and faithful, with trivial core and
   point-stabilizer index 24.
2. GAP's exact `TransitiveIdentification` equals a sealed target label.
3. The exact mapping from the accepted source signature to target signatures
   equals a safe profile from the sealed subgroup census.

A promotion does not assume that an abstract block system numbers the actual
roots. Its downstream dispatcher must factor every degree-12 component of
`q.symmetric_power(k)`, test each `factor(x^2)`, and retain only a monic,
irreducible degree-24 polynomial with the exact promoted Galois label and a
promoted live signature. Repeated/colliding specializations are rejected.
This exhaustive factor dispatcher closes the root-alignment gate without an
unproved root numbering.

## Checkpoint and hard gates

The action worker is single-process and checkpoints atomically after every
source/`k` task. A matching checkpoint resumes automatically. A failed
checkpoint requires explicit `--retry-failed` after inspection. A lock file
prevents two workers from sharing the checkpoint.

Hard maxima are:

- 3,600 seconds wall time per invocation; the recommended run uses 1,800.
- 64 two-point block systems per source.
- 924 quotient subsets and symmetric-power degree.
- 4,096 saved length-12 action rows.
- 64 exact promotions.
- one heavy worker; zero polynomial arithmetic, network, staging, submission,
  or coefficient material in the checkpoint/census.

Light preflight:

```bash
python3 prepare_index24_f6a_kummer_action_plan.py --preflight-only
python3 run_index24_f6a_kummer_action_worker.sage.py --preflight-only
```

Safe one-heavy-worker command (not yet run):

```bash
env SAGE_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 sage -python run_index24_f6a_kummer_action_worker.sage.py --census-only --max-wall-seconds 1800 --max-block-systems 64 --max-subset-universe 924 --max-action-rows 4096 --max-promotions 64
```

Expected paths:

- Resume checkpoint: `data/index24_f6a_kummer_action_checkpoint.json`
- Final coefficient-free census: `data/index24_f6a_kummer_action_census.json`

If all 55 tasks complete with zero promotions, F6-A is blocked. Reopen only
for a new exact root-action-aligned relative invariant outside signed quotient
subset products, or if a target/source certificate changes and a new light
plan proves that the task was not already exact/theorem closed.
