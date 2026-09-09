# Reproducing and checking this release

## A standard-library entry point

Python 3.12 is the supported baseline for the release scripts. From the repository root:

```sh
python3 scripts/lookup_pair.py 24T15308 20
python3 scripts/validate_release.py
```

The validator checks all 30,288 representatives, their exact coefficient hashes, group/signature formatting, the pair index, F5/F6 and GQ‑48 evidence counts, the explicit transcript truncation, coverage reconciliation, and links in the reader-facing documents. It does not identify Galois groups.

## Arithmetic replay

Install PARI/GP through [its official distribution instructions](https://pari.math.u-bordeaux.fr/download.html), then run:

```sh
python3 examples/f5/replay_example.py --gp gp
```

This release was checked with **PARI/GP 2.17.2** on macOS ARM64. The script reconstructs the worked polynomial and verifies degree, irreducibility, and real-root count. Magma’s label identification and exact field-discriminant computation were not rerun.

## Focused source tests

Use a separate Python environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-test.txt
cd research
PYTHONPATH=. python -m pytest \
  tests/test_pair_product_resolvent.py \
  tests/test_nested_character_lift.py \
  tests/test_unit_character_lift.py \
  tests/test_ledger.py \
  tests/test_scheduler.py \
  tests/test_api_client.py \
  tests/test_controller.py -q
```

PARI/GP must be available for the algebraic tests. The release run produced **30 passed, 2 skipped**. The skipped checks require GAP and its degree-24 transitive-group catalog; GAP was unavailable in the publication environment. If installed, set `IGP24_GAP` to its executable to enable those checks. The test copy marks the missing prerequisite explicitly instead of failing during setup.

These tests cover exact resultant construction, polynomial square roots, ledger identity/state, scheduling, and mocked submission behavior. They do not launch cloud workers or make real submissions. All **876** archived Python source files also passed syntax parsing. The rest of the historical test suite and all campaign scripts were not rerun.

## Bulk data

```sh
python3 scripts/download_corpus.py --output downloads
python3 scripts/validate_corpus.py downloads
```

The full corpus validator was run over **all 1,833,772 exported rows**. It verified the ten compressed file hashes and row counts, coefficient hashes, data-field format, and accepted-row degree, monicity, label, and real-root bounds. These are integrity checks on archived records, not fresh irreducibility or Galois computations for every row.

## Figures and article

The manuscript is [article/essay.md](../article/essay.md), and its image links work within GitHub. The measured figures were made with Matplotlib; the other illustrations were generated with Image Gen and are labeled conceptual. Prompts are in [article/images](../article/images/) and [the illustration provenance](../article/evidence/illustration-prompts.json).

```sh
python3 -m pip install -r requirements-figures.txt
python3 article/website-source/plot-essay.py
```

The plot script reads the included JSON/CSV data and writes PNG/SVG figures. Its paths have been adjusted for this repository. The React/CSS files in `article/website-source/` preserve the essay’s renderer; they are not a complete deployable Next.js project. The [release](https://github.com/ash9241/team-dirac-igp24/releases/tag/v1.0.0) also contains `two-batchmates.html`, a standalone copy of the approved illustrated website that can be downloaded and opened locally.

## Historical environment

The campaign used several machines and environments: SageMath, GAP, PARI/GP, and the competition’s Magma verifier, plus Python workers and Google Cloud. A single fully pinned historical environment was not recorded. The checked dependency versions in this release describe the publication checks, not every July/August worker.

Reviving an old experiment may require the source ledger, generated action catalogs, compatible GAP packages, and manifests named by that script. Those dependencies are documented where available; temporary caches and operational databases are not bundled. Instructions in historical handoffs refer to the competition state on their original dates.

## Publication checks

The release tree is built from selected copies with a new Git history. The publication review excludes credentials, raw session logs, `.env` files, operational SQLite databases, and unrelated personal files. Gitleaks 8.30.1 scans the text/source tree; two precisely identified false positives are the literal experiment name `calibration_83`, annotated in their source lines. The numeric corpus is validated by its stricter typed schema.

The [source manifest](../provenance/source-files.json) records the copied files’ original hashes. [Release-file hashes](../provenance/release-files.json) identify the final published contents; [sanitization notes](../provenance/sanitization.json) record the publication edits. The source workspace was preserved.
