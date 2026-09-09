# Data, coverage, and conventions

## Two snapshots

The **local research ledger** preserves 1,833,772 polynomial rows across 3,238 submissions dated July 4–August 16, 2026. Its saved verification and scoring records were synchronized at different times. It contains 1,680,129 accepted rows, 153,625 rows with no local verification record, and 18 recorded failures. The accepted rows cover 30,288 distinct `(24Tt, r)` pairs and 8,521 labels.

The **published leaderboard** is generated September 1, 2026. It credits Dirac with 30,426 scoreable pairs, score 405.86189, and rank 14. It is a later record. A local row marked pending or not scoreable may have changed afterward. Conversely, a local accepted pair is not necessarily a credited pair on the final board.

The [coverage report](coverage.json) computes set intersection and differences using the public placement table and the local accepted-pair index. **29,939** of the **30,421** retrieved public placements have a local accepted representative; **482** do not. There are **349** local accepted pairs absent from that public table. Pagination reached its end without duplicate rows, but the public table contains **five fewer pairs** than the leaderboard headline. A fresh headline check still returned 30,426; this API discrepancy remains unresolved. The sum of rounded placement points is 405.861513, compared with the headline 405.86189.

The raw difference of 138 between the two headline counts is **not** the number of missing public pairs: the two sets contain different kinds of records. The report gives the actual missing set. A read-only attempt to refresh authenticated submission details returned HTTP 403 during preparation; the export does not fabricate later verification states.

## Files

| File | Contents |
|---|---|
| [pairs.csv](pairs.csv) | One row per locally accepted pair, with acceptance counts, local scoring counts, and a representative’s hash and receipt coordinates |
| [representatives.jsonl.gz](representatives.jsonl.gz) | 30,288 representative polynomial records, one per locally accepted pair |
| [submissions.csv](submissions.csv) | Submission IDs, timestamps, local summary counts, and sync times; descriptions and raw API payloads are omitted |
| [local-corpus-summary.json](local-corpus-summary.json) | Exact counts, selection rule, compressed file sizes, and SHA-256 hashes for all ten bulk shards |
| [public-placements.csv](public-placements.csv) | The retrieved public per-pair score, credited-team count, and scoring discriminants for Dirac |
| [coverage.json](coverage.json) | Provenance and reconciliation of the public placements and local representatives |
| [public-pairs-without-local-representative.csv](public-pairs-without-local-representative.csv) | Publicly credited pairs without a saved accepted representative in this local ledger |
| [local-pairs-not-in-public-placements.csv](local-pairs-not-in-public-placements.csv) | Locally accepted pairs absent from the retrieved public scoring table |

## Get every local polynomial row

The ten `polynomials-NNN.csv.gz` files are attached to [v1.0.0](https://github.com/ash9241/team-dirac-igp24/releases/tag/v1.0.0). They total approximately 324 MB compressed. Download them with hash verification:

```sh
python3 scripts/download_corpus.py --output downloads
python3 scripts/validate_corpus.py downloads
```

These files preserve every row in the primary local `polynomials` table, including repeated coefficients and unresolved submissions. They do not contain all unsubmitted candidates, temporary cloud outputs, raw databases, or every runtime cache. A representative dataset is convenient for browsing; the bulk files preserve the fuller submission record.

Read a shard without extracting it:

```python
import csv, gzip

with gzip.open("downloads/polynomials-001.csv.gz", "rt", newline="") as f:
    for row in csv.DictReader(f):
        if row["label"] == "24T15308" and row["r"] == "20":
            coefficients = [int(a) for a in row["coefficients"].split(",")]
            print(row["submission_id"], row["polynomial_index"], coefficients)
```

## Schema

| Field | Meaning |
|---|---|
| `submission_id`, `polynomial_index` | Saved competition receipt coordinates; index is zero-based |
| `submitted_at` | Submission creation time from the ledger, in UTC |
| `coefficients` | Comma-separated integers in ascending power order: constant term first, leading coefficient last |
| `coefficient_sha256` | SHA-256 of the exact ASCII coefficient string, without a trailing newline |
| `status` | `accepted`, `failed`, or `unverified_in_local_snapshot` |
| `label`, `t`, `r` | Returned group label, numeric label index, and number of real roots |
| `scoring_status`, `scoreable` | Saved scoring state; `scoreable` is nullable 0/1 in the bulk CSV and an integer/null in JSON |
| `in_baseline`, `baseline_unlocked` | Nullable saved baseline flags |
| `disc_source` | Source of the recorded scoring discriminant |
| `field_disc_abs` | Absolute exact field discriminant, when present |
| `scoring_disc_abs` | Absolute scoring discriminant recorded for this row at the local sync |
| `poly_disc_abs`, `mixed_disc_abs` | Other archived discriminant values, when returned |

Empty CSV cells and JSON `null` mean unavailable. Large discriminants are decimal strings, not floating-point numbers. The public table uses its own field names: `points`, `kTeams`, `scoringDiscAbs`, `minScoringDiscAbs`, and `discSource`.

A coefficient hash detects identical coefficient strings. It does **not** identify number fields up to isomorphism. Multiple polynomials may define the same field; multiple fields may have the same group/signature pair.

The representative rule first prefers a row locally marked scoreable, then the shortest coefficient text, then deterministic lexical/receipt tie-breakers. It does not select the field with the smallest discriminant, and a representative need not be the specific polynomial behind the team’s final public best discriminant.

## Rebuild and validate

[export_ledger.py](../scripts/export_ledger.py) opens a provided SQLite database in read-only mode and exports only named public fields. It checks the coefficient hashes and the degree, monicity, and label/signature format of accepted records. It does not recompute 1.68 million Galois groups.

```sh
python3 scripts/export_ledger.py \
  --ledger /path/to/ledger.sqlite3 \
  --output data \
  --assets /path/to/release-assets
```

The source ledger is not distributed: it includes raw responses, operational metadata, and data unnecessary for publication. The released CSVs preserve the mathematical and receipt fields described above. The small-file validation command is `python3 scripts/validate_release.py`; the corpus validator additionally checks every downloaded row and each file hash.
