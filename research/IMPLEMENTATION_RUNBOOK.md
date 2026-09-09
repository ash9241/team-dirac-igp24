> Historical research record. Numerical forecasts, live rankings, and operational instructions refer to its original date. See the repository README for audited results and current release instructions.

# Team Dirac IGP24 Control-Plane Runbook

This runbook implements the staged strategy in `team_dirac_igp24_strategy_durgesh.pdf`. The legacy Mac volume daemon is stopped and disabled by default because its marginal batches increased pair count while reducing score.

## Safety rules

- Only the Mac may possess `IGP24_API_KEY` or submit to SAIR.
- GCP workers generate checksummed artifacts and never call the competition API.
- Real submissions require `routeA/controller.py --execute`; dry-run is the default.
- Never retry an ambiguous POST. Reconcile it first.
- Never POST while either the server queue or local durable ledger is unresolved.
- Never ingest a partially verified batch as complete.
- An explicit compatible-label list is not calibration evidence. Structural
  candidates with zero family support are blocked unless an exact
  compatibility proof or a passing prospective family audit is recorded.
- The retired blind burst driver refuses non-dry-run execution.
- A cycle-index match is never sufficient by itself: require a construction-compatible label set and a family/lineage-held-out precision gate.

## 1. Configure and rotate the API credential

Create a replacement credential through the competition account, configure it, verify it, and only then revoke the exposed credential.

```bash
mkdir -p ~/.config/igp24
chmod 700 ~/.config/igp24
umask 077
$EDITOR ~/.config/igp24/api_key
chmod 600 ~/.config/igp24/api_key
python3 igp24_api.py me
```

Do this only while no submission process is running. Legacy volume mode stays disabled unless `IGP24_ENABLE_LEGACY_VOLUME=1` is explicitly set.

## 2. Recover the GCP forge

Interactive authentication is required:

```bash
gcloud auth login
gcloud compute instances describe igp24-forge --zone=us-central1-b --project=dirac-phm
gcloud compute ssh igp24-forge --zone=us-central1-b --project=dirac-phm
```

Recover and checksum `census.json`, `targeted_queue.json`, `centroid_counts.json`, manifests, logs, and any remote knowledge before modifying the instance. Do not trust a recovered `targeted_queue.json` unless its entries use `matcher_version=extension-v1`; the daemon rejects the retired A/B/C proxy format.

On the worker, run `cloud/bootstrap_worker.sh`. Verify PARI and GAP/transgrp versions. Ensure neither `IGP24_API_KEY` nor `IGP24_API_KEY_FILE` exists in the worker environment.

Cloud jobs use an allow-listed JSON manifest:

```bash
python3 cloud/generator_worker.py cloud/jobs/product-map.json
```

Every declared artifact receives a SHA-256 entry in `cloud/output/<job>.report.json`.

## 3. Ledger migration and reconciliation

The initial local migration has imported routeA manifests into `routeA/data/control.sqlite3`. It is repeatable:

```bash
python3 -m routeA.migrate_legacy
```

After configuring the rotated API credential:

```bash
python3 -m routeA.reconcile
python3 -m routeA.progress
```

Reconciliation must complete before oracle or burst-yield claims are evaluated. Server-only submissions without exposed payloads remain explicitly unresolved rather than being guessed.

## 4. Build the deterministic 8x3 attack

Build diverse lower-degree components with PARI:

```bash
python3 -m routeA.build_component_library 3 routeA/data/components/cubics.jsonl \
  --bound 8 --max-records 200 --max-per-stratum 50
python3 -m routeA.build_component_library 8 routeA/data/components/octics.jsonl \
  --bound 5 --max-records 500 --max-per-stratum 50
```

Emit the exact GAP product-action calculation, run it on the forge, and parse it on the Mac:

```bash
python3 -m routeA.gap_product_map \
  routeA/data/components/octics.jsonl routeA/data/components/cubics.jsonl \
  --emit-script routeA/data/components/product_8x3.g

# On a GAP host:
gap -q routeA/data/components/product_8x3.g > routeA/data/components/product_8x3.out

python3 -m routeA.gap_product_map \
  routeA/data/components/octics.jsonl routeA/data/components/cubics.jsonl \
  --parse-output routeA/data/components/product_8x3.out \
  --output routeA/data/components/product_8x3.jsonl
```

Generate only pairs that remain gold or team-count-one raids:

```bash
python3 -m routeA.build_8x3_candidates \
  routeA/data/components/octics.jsonl routeA/data/components/cubics.jsonl \
  routeA/data/components/product_8x3.jsonl routeA/data/candidates_8x3.jsonl \
  --mode direct --limit 50000
```

For the shared discriminant-resolvent experiment, emit/run/parse with `--mode fiber-c2`, then build candidates with the same mode. This path only accepts components with equal discriminant squareclass.

## 5. Short-horizon vault and staged submissions

Refresh immediately, then create a maximum four-hour vault:

```bash
python3 -m routeA.progress
python3 -m routeA.rolling_vault routeA/data/candidates_8x3.jsonl \
  --horizon-hours 4 --limit 250000
```

Dry-run at most the first 25 candidates:

```bash
python3 routeA/controller.py routeA/data/rolling_vault.jsonl \
  --candidate-limit 25 --no-refresh
```

After reviewing the manifest and forecast, perform the real pilot with a fresh target snapshot:

```bash
python3 routeA/controller.py routeA/data/rolling_vault.jsonl \
  --candidate-limit 5 --execute
```

Start with one to five independent lineages. Scale only after the pilot passes
85% exact-label agreement. Each executed wave is exactly one server submission;
the controller refuses to start the next while the previous one is unresolved.
Staged campaign syntax is:

```bash
python3 routeA/controller.py routeA/data/rolling_vault.jsonl \
  --wave-schedule 25,50,100,200 --execute
```

The controller stops on unresolved submissions, stale/duplicate rate above 5%, exact-label agreement below 85% after 200 accepted candidates, or realized score below 50% of forecast.

## 6. Oracle and extension work

Generate cycle-index likelihoods only for structurally compatible labels. For
the main remaining `(2,4,8)` surface, use the complete 9,373-group atlas; a
live-label-only atlas is invalid because it creates target-selection bias:

```bash
python3 -m routeA.gap_features 123,456,789 --emit-script routeA/data/cycle_indices.g
gap -q routeA/data/cycle_indices.g > routeA/data/cycle_indices.out
python3 -m routeA.gap_features 123,456,789 \
  --parse-output routeA/data/cycle_indices.out --output routeA/data/cycle_indices.json
```

`routeA/oracle_v2.py` eliminates impossible cycle types and computes tempered, construction-prior posteriors. The 175-label centroid model remains a diagnostic/veto baseline until a lineage-held-out prospective evaluation passes.

`routeA/structural_oracle.py` additionally intersects candidates with
server-verified construction-family support from `routeA/knowledge.jsonl`.
Unknown families may be explored offline, but cannot be executed until an
exact group-theoretic compatibility set or prospective calibration exists.
The scheduler and controller enforce this metadata; a high cycle-index
posterior cannot override an explicit failed or missing calibration.

Audit a prospectively tested family and emit a submission-gated shard with:

```bash
python3 -m routeA.family_calibration INPUT.jsonl RECALIBRATED.jsonl \
  --family FAMILY_NAME --report CALIBRATION.json
```

The default gate requires at least five accepted prospective trials, three
independent lineages, 85% exact-label precision, and zero observed labels
outside the claimed compatibility set. A blocked audit is an offline result,
not permission to collect more server labels.

The completed exhaustive product maps show that simple 8×3, 12×2, and 6×4
composita are saturated. New work should target non-split `(2,4,8)` towers or
fiber/subdirect products outside those maps.

For an intermediate-field-aware tower experiment:

```bash
python3 -m routeA.build_tower_experiment routeA/data/tower_experiment_248.jsonl \
  --attempts 500 --limit 100 --seed 248240713
python3 -m routeA.tower_quotient_screen \
  routeA/data/tower_experiment_248.jsonl \
  cloud/output/cycle_index_degree6.json \
  cloud/output/cycle_index_degree12.json \
  cloud/output/full_block_census.jsonl \
  routeA/data/tower_compatible_248.jsonl --primes 1000
python3 -m routeA.structural_oracle \
  routeA/data/tower_compatible_248.jsonl \
  cloud/output/full_cycle_index_248.json \
  routeA/data/tower_submission_candidates_248.jsonl \
  --min-probability 0.75 --max-prediction-set 2
```

The current run yields zero candidates. Do not relax this gate. Implement a
degree-12 resolvent/order discriminator first; the cycle-support equivalence
classes remain unchanged even at 1,000 unramified primes.

Additional implemented generators:

- `relative_quadratic.py`: degree-12 seed plus a prescribed relative radicand;
- `quadratic_tower_cubic.py`: genuine cubic-quadratic-quadratic-quadratic towers;
- `fiber_product_8x3.py`: common discriminant-resolvent composita.

Their outputs use the same worker-shard schema and pass through the same scheduler/controller gates.

## 7. Legacy volume policy

Keep the Mac volume daemon stopped. It is hard-disabled in code unless
`IGP24_ENABLE_LEGACY_VOLUME=1` is explicitly supplied. Do not re-enable it
without a fresh retained-score analysis showing positive marginal value and a
clear submission queue.

Run the complete local safety suite with:

```bash
python3 -m pytest -q
```
