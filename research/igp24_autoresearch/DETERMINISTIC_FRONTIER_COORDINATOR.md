> Historical research record. Numerical forecasts, live rankings, and operational instructions refer to its original date. See the repository README for audited results and current release instructions.

# Deterministic frontier coordinator

`run_deterministic_frontier_coordinator.py` is the reusable execution lane for
finalized deterministic pair-route frontiers (tc7 and later). It is offline:
there is no target refresh, network request, API dry-run, or submission path.

## Route-certificate contract

The input certificate must be coefficient-free and contain:

- a `status` containing `ready`, with every declared `checks` value true;
- `coefficientMaterialIncluded: false` and
  `credentialMaterialIncluded: false`;
- the six-field finalized `boundary` used by the tc4–tc6 certificates;
- nonduplicated `runbooks`, in execution order;
- for each route, a pinned accepted source, current target, deterministic
  single length-24 orbit, pinned action artifact, and the allowlisted
  `pair_sum_one.sage.py` command with source-hash, target, and output guards.

The coordinator rechecks the full accepted-pair and target boundary before
execution, after every exact result, and before final sealing.

## Audit command

Audit is the default and performs no writes or worker launches:

```sh
python3 run_deterministic_frontier_coordinator.py \
  --certificate data/FINALIZED_FRONTIER.json \
  --batch-name low_contention_tc7_20260722 \
  --reserved data/global_exact_corpus_delta_after_b4f7_20260722_outbox_postflight.json \
  --reserved data/OTHER_RESERVED_PAIR_HASH_METADATA.json
```

Audit reports each route as ready, resumable from an exact result checkpoint,
or blocked fail-closed. Reservation files are repeatable and may expose
`selected`, `mappings`, `reservedPairs`, or `reservedHashes` metadata.

## Explicit execution command

Only `--execute` can launch workers. `--max-new-workers` limits newly launched
workers in one invocation; previously completed exact result checkpoints are
resumed without using that budget.

```sh
python3 run_deterministic_frontier_coordinator.py \
  --certificate data/FINALIZED_FRONTIER.json \
  --batch-name low_contention_tc7_20260722 \
  --reserved data/global_exact_corpus_delta_after_b4f7_20260722_outbox_postflight.json \
  --execute \
  --max-new-workers 8
```

Workers run synchronously under the shared coordinator lock, so at most one
Sage/GAP worker is active. The same command resumes an interrupted batch.

## Outputs

For batch name `NAME`, execution creates:

- `data/NAME_checkpoint.json`: atomically replaced, coefficient-free resume
  metadata;
- `outbox/.NAME.partial.txt`: atomically replaced private partial manifest;
- private per-route manifests plus coefficient-free postflight/stage metadata;
- `outbox/NAME.txt`: sealed private final manifest;
- `data/NAME_batch_certificate.json`: final coefficient-free batch seal;
- `data/NAME_receipt_mapping_ready.json`: ordered pair/hash metadata ready for
  later receipt attachment.

Partial, individual, and final manifests use mode `0600`. Coefficients are
never printed or copied into checkpoint, postflight, stage, batch, or mapping
metadata. No output authorizes or performs submission.

## Tests

```sh
python3 -W error::ResourceWarning -m unittest -v \
  tests.test_run_deterministic_frontier_coordinator
```

The suite verifies write-free audit mode, explicit execution gating,
reservation filtering, exact-result resume without relaunch, one-worker
partial checkpointing followed by ordered resume/finalization, private
manifest permissions, coefficient-free metadata/stdout, absence of a
network/submission path, and compatibility with the tc4 and tc5/tc6 route
certificate shapes.
