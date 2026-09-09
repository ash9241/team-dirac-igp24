> Historical research record. Numerical forecasts, live rankings, and operational instructions refer to its original date. See the repository README for audited results and current release instructions.

# Cross-1000 GCP campaign

This runbook keeps the benchmark API and its credentials on the Mac. Google
Cloud only performs deterministic polynomial discovery and certification, then
returns checksummed archives for local merging and submission.

## Why this campaign

- Official score at full-array launch: `602.507267`. After the first 83
  server-calibration pilots reconciled, the official score was `644.489073`
  (rank 17). The score can move as the shared table changes.
- The remaining already-certified reserve is only about `26.55`, so submitting
  more of the same family cannot reach 1,000.
- The nonstandard degree-12 character surface contains 481 mapped targets. At
  the current ledger snapshot, choosing the better root parity for each target
  exposes `1044.932976782322` points of live opportunity before discovery loss,
  collisions, and score movement.
- Earlier direct character families matched their intended server label in
  391 of 396 cases. Subgroup-recovery rows were materially less reliable, so
  this campaign keeps them out of mass certification.

The `1044.93` figure is an opportunity ceiling, not a guaranteed score gain.
The pilot and calibration stages are what turn it into safe, server-authoritative
submission inventory.

## Execution model

1. Discovery tasks are sharded by catalog, base transitive group, construction
   lane, and parameter shard. Every task has unique output paths.
2. Workers verify all packaged inputs and the task manifest before running.
3. Workers receive no benchmark API key and cannot submit anything.
4. Result archives contain manifests, reports, artifacts, and hashes. The Mac
   rejects incomplete or altered archives during merge.
5. Pilot tasks are sharded by `(predicted target, base group, norm
   squareclass)`, carry several independent construction seeds, and use
   distinct live roots for sibling keys. The server's actual label calibrates
   that exact character key.
6. Only calibrated direct families are mass-certified, scored against the
   refreshed ledger, and submitted in descending expected value.

The four independent discovery lanes are linear shifts, product lifts,
kernel-product lifts, and norm-equation lifts. Every array index is
deterministic, so either Standard or Spot workers can retry the same shard.

## Prepared arrays

| Campaign | Tasks | Bases | Provisioning | Parallel tasks | Maximum VMs | Max task duration | Live opportunity |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| `cross1000-pilot-v3` | 20 | 2 | Standard | 20 | 5 | 2 h | 43.678253 |
| `cross1000-v1-standard` | 2,149 | 216 | Standard | 64 | 8 | 45 min | 1044.932977 |
| `cross1000-v1-repair1` | 8 | — | Standard | 8 | 1 | 45 min | — |
| `cross1000-v1-repair2` | 1 | — | Standard | 1 | 1 | 2 h | 6.040321 |
| `cross1000-cert-pilot-partial` | 186 | — | Standard | 64 | 8 | 30 min | 257.242738 |
| `cross1000-cert-full-partial` | 79 | — | Standard | 79 | 10 | 90 min | 117.180889 |
| `cross1000-deep-v1` | 2,424 | 155 | Standard | 128 | 16 | 60 min | 779.010887 missing |
| `cross1000-deep-cert-pilot-key-cp2` | 39 | — | Standard | 16 | 2 | 90 min | 38.748840 calibration |

The live regional quota permits 200 general CPUs and no preemptible CPUs in
`us-central1`. These Standard E2 VMs consume the general `CPUS` quota; the
separate 72-CPU `E2_CPUS` metric remained unused during the observed jobs. The
campaign therefore caps simultaneous planned allocation at 192 general CPUs,
leaving 8 CPUs of headroom. Tasks use one vCPU each and normally pack eight per
`e2-standard-8`. Batch itself has no additional service fee, but VM, disk,
logging, storage, and network charges still apply. `taskSpec.maxRunDuration` is
a per-task limit, not a total job cost cap. The GCP jobs keep running if the
laptop sleeps or disconnects.

Prepared artifacts:

- `cloud/campaigns/cross1000-pilot/campaign.tar.gz`
  SHA-256 `398a0f6e4ca4ecf28f96ebc4058b22a4a7bc3177643a4df8927aa10e1b3a80cf`
- `cloud/campaigns/cross1000-v1/campaign.tar.gz`
  SHA-256 `875cfc0248e7a3bc2cfd7569b71c0b284c91fa9c5b5d2e594865383144113303`
- `cloud/campaigns/cross1000-cert-pilot-partial/campaign.tar.gz`
  SHA-256 `65b5b812ecef9e4f3a0ae8da631a782d312bd0f957b89c8b5bdba570489846e0`
- `cloud/campaigns/cross1000-cert-full-partial/campaign.tar.gz`
  SHA-256 `9a41f1e9614d2231e0833f794e81956d0d8a973d9e7bcd90b4a7018870a527c6`
- `cloud/campaigns/cross1000-v1-repair2/campaign.tar.gz`
  SHA-256 `3e378236930c96aa92b2eb20d14e1480928a3f2766a12e6a4b5ad331afe76224`
- `cloud/campaigns/cross1000-deep-v1/campaign.tar.gz`
  SHA-256 `b1d0e2dfe18eab6b158b81f3617099b13637e1fc110f17bd0d6a1b394b05946f`
- `cloud/campaigns/cross1000-deep-cert-pilot-key-cp2/campaign.tar.gz`
  SHA-256 `8ea35e0439e1238bc9bee57d333d4a551f883af29c30631bbcb47b5243242a85`
- Batch specifications and machine-readable reports are beside each package.

## One-time GCP preflight

Authentication is intentionally interactive:

```sh
gcloud auth login
gcloud config set project dirac-phm
gcloud auth print-access-token >/dev/null
```

Then inspect the project before making changes:

```sh
gcloud storage buckets list --project=dirac-phm
gcloud services list --enabled --project=dirac-phm
gcloud compute project-info describe --project=dirac-phm
```

Batch is enabled and the campaign uses the existing `dirac-phm-data` bucket.
For a fresh project, enable the services and create or select a regional
bucket, then regenerate the Batch files for that bucket.

```sh
gcloud services enable batch.googleapis.com compute.googleapis.com logging.googleapis.com storage.googleapis.com --project=dirac-phm
gcloud storage buckets create gs://YOUR-GLOBALLY-UNIQUE-BUCKET --project=dirac-phm --location=us-central1 --uniform-bucket-level-access
```

The Batch service agent/default compute identity must be able to read the
package and write the results prefix. Prefer a dedicated least-privilege
service account if project policy does not already provide that access.

## Pilot launch

Upload exactly the locally verified package, verify its cloud hash/metadata,
and launch only the 20-task pilot first:

```sh
gcloud storage cp cloud/campaigns/cross1000-pilot/campaign.tar.gz gs://dirac-phm-data/campaigns/cross1000-pilot/campaign-v3.tar.gz --project=dirac-phm
gcloud storage objects describe gs://dirac-phm-data/campaigns/cross1000-pilot/campaign-v3.tar.gz --project=dirac-phm
gcloud batch jobs submit cross1000-pilot-v3 --project=dirac-phm --location=us-central1 --config=cloud/campaigns/cross1000-pilot/batch-v3.json
```

Monitor job and task states:

```sh
gcloud batch jobs describe cross1000-pilot-v3 --project=dirac-phm --location=us-central1
gcloud batch tasks list --job=cross1000-pilot-v3 --project=dirac-phm --location=us-central1
```

Fetch and cryptographically verify the outputs:

```sh
mkdir -p cloud/campaigns/cross1000-pilot/downloaded-v3
gcloud storage cp 'gs://dirac-phm-data/campaigns/cross1000-pilot/results-v3/*.tar.gz' cloud/campaigns/cross1000-pilot/downloaded-v3/ --project=dirac-phm
python3 -m cloud.merge_character_campaign cloud/campaigns/cross1000-pilot/matrix.json cloud/campaigns/cross1000-pilot/cloud-discoveries-v3.jsonl --archive-dir cloud/campaigns/cross1000-pilot/downloaded-v3
```

The corrected pilot completed 20/20 tasks in 750 seconds and the strict merge
verified 17 unique constructions across two targets. Do not scale a future
revision if its pilot has systemic bootstrap failures, checksum failures, no
useful discoveries, or unexpectedly high runtimes.

## Full discovery launch

After the pilot passes, refresh the target ledger and regenerate the plan if
the live snapshot changed materially. Otherwise:

```sh
gcloud storage cp cloud/campaigns/cross1000-v1/campaign.tar.gz gs://dirac-phm-data/campaigns/cross1000-v1/campaign-v1-standard.tar.gz --project=dirac-phm
gcloud batch jobs submit cross1000-v1-standard --project=dirac-phm --location=us-central1 --config=cloud/campaigns/cross1000-v1/batch-standard-v1.json
```

Monitor `cross1000-v1-standard` and merge the `results-v1-standard` prefix. A
complete merge requires all 2,149 archives. Use
`--allow-incomplete` only for diagnosis or an intentional partial checkpoint;
never treat an incomplete merge as final inventory.

## Calibration and certification

Plan direct pilots by character key, retaining independent seed alternatives
and assigning distinct roots when several keys predict the same target:

```sh
python3 -m cloud.plan_character_certification cloud/campaigns/cross1000-v1/discoveries.jsonl cloud/campaigns/cross1000-cert-pilot --campaign-id=cross1000-cert-pilot --pilot-by-key --alternatives-per-target=3
```

Package and run that matrix through the same Batch flow. Merge with
`--artifact-kind=candidates`, submit the small pilot set locally, and reconcile
the actual labels. Relabel each discovery family from those authoritative
results before planning full certification:

```sh
python3 -m cloud.calibrate_character_discoveries cloud/campaigns/cross1000-v1/discoveries.jsonl cloud/campaigns/cross1000-cert-pilot/candidates.jsonl cloud/campaigns/cross1000-v1/discoveries-calibrated.jsonl --additional-candidates=cloud/campaigns/cross1000-cert-pilot-next/candidates.jsonl
python3 -m cloud.plan_character_certification cloud/campaigns/cross1000-v1/discoveries-calibrated.jsonl cloud/campaigns/cross1000-cert-full --campaign-id=cross1000-cert-full --full --alternatives-per-target=3
```

The calibrator accepts only server-accepted pilots whose verified root count
matches the prediction, maps them by `(base_t, norm_squareclass)`, relabels the
two observed T-number disagreements, and filters every uncalibrated family.
The first calibration tranche produced 84 keys: 82 exact T labels and 2
server-authoritative relabels.

After merging the original array, plan the deeper missing-character norm wave
with disjoint scales 67 through 199:

```sh
python3 -m cloud.plan_deep_character_campaign cloud/campaigns/cross1000-v1/discoveries.jsonl cloud/campaigns/cross1000-deep-v1 --campaign-id=cross1000-deep-v1
```

Package that matrix normally. Allocate its parallelism only after accounting
for every still-running discovery, repair, and certification VM so the total
planned general-CPU usage stays at or below 192.

The deep planner defaults to two catalog rows per task. This matters because
some alternate-catalog bases contain many fields; base-only sharding made those
rows serial stragglers. Each PARI field initialization is timeboxed separately,
as is every `bnfisintnorm` call, so a hard arithmetic instance is skipped
without discarding easier equations from the same shard. The launched deep
matrix has 303 field chunks times 8 disjoint scale shards.

Full certification is also direct-only. The final local scheduler must refresh
targets, remove owned pairs and polynomial hashes, prefer the stronger root
parity for each calibrated family, and stage submissions. Stop only after the
official reconciled score exceeds 1,000.

## Safety invariants

- Never upload `.env`, API-key files, local SQLite ledgers, submission payloads,
  or submitted-hash sets. The packager rejects credential-bearing bundles.
- Never place `IGP24_API_KEY` or `IGP24_API_KEY_FILE` in Batch environment
  variables. The worker explicitly clears and rejects them.
- Never submit from a cloud worker. Discovery and certification are compute;
  benchmark writes stay on the Mac.
- Preserve result archives until local hash verification and reconciliation are
  complete.
- Use a new Batch job ID for reruns, because submitted Batch jobs are immutable.

Relevant Google Cloud references:

- Batch task arrays and `BATCH_TASK_INDEX`: <https://docs.cloud.google.com/batch/docs/create-run-job>
- Batch quotas: <https://docs.cloud.google.com/batch/quotas>
- Batch pricing: <https://cloud.google.com/batch/pricing?hl=en>
- Spot VM pricing: <https://cloud.google.com/spot-vms/pricing?hl=en>
