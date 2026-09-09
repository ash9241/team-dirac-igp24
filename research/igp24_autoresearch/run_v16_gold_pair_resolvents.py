#!/usr/bin/env python3
"""Run the two pinned v16 gold pair resolvents, sequentially and offline.

The adapter deliberately delegates arithmetic to ``pair_sum_one.sage.py`` but
pins the accepted source rows, the v16 exact orbit census, and the originating
receipt.  Worker stdout is captured because it contains coefficient payloads;
the adapter prints only a coefficient-free summary.  Both outputs are sealed
with create-new/no-overwrite semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/ledger.sqlite3"
V16 = ROOT / "data/autopilot_pair_delta_20260722_v16"
ROUTES = V16 / "missing_pair_all.jsonl"
INVENTORY = V16 / "exact_source_inventory.json"
WORKER = ROOT / "pair_sum_one.sage.py"
RECEIPT = ROOT / "receipts/sub_574f9e56ce804797ab49160e2f9838e6.json"
DEFAULT_OUTPUT = ROOT / "data/v16_gold_pair_resolvent_candidates.jsonl"
DEFAULT_CERTIFICATE = ROOT / "data/v16_gold_pair_resolvent_run_certificate.json"

SUBMISSION_ID = "sub_574f9e56ce804797ab49160e2f9838e6"
SPECS = {
    "24T16875": {
        "polynomialIndex": 4,
        "sourceR": 24,
        "coefficientSha256": "599ca429908ceef8d51e7a6e1cca79fedc9970d8f1364fd1b49e98b9f965db99",
        "goldTarget": ("24T17570", 24),
        "targetMultiplicity": 1,
    },
    "24T17260": {
        "polynomialIndex": 12,
        "sourceR": 24,
        "coefficientSha256": "2fcde9deee77ef0094cad483705149bdcf37e6acbc2f6051aa4bcec998b72f6b",
        "goldTarget": ("24T17796", 24),
        "targetMultiplicity": 2,
    },
}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def canonical_line(raw: str) -> tuple[str, str]:
    values = [int(value) for value in raw.strip().split(",")]
    if (
        len(values) != 25
        or values[0] == 0
        or values[-1] != 1
        or math.gcd(*values) != 1
    ):
        raise ValueError("invalid primitive monic degree-24 coefficient payload")
    line = ",".join(str(value) for value in values)
    return line, sha256_bytes(line.encode("ascii"))


def validate_receipt() -> dict:
    receipt = read_json(RECEIPT)
    response = receipt.get("response") or {}
    manifest = Path(str(receipt.get("manifest"))).expanduser().resolve()
    if (
        receipt.get("commit") is not True
        or str(response.get("submissionId")) != SUBMISSION_ID
        or int(response.get("queuedCount", -1)) != 13
        or int(response.get("rejectedCount", -1)) != 0
        or int(receipt.get("polynomials", -1)) != 13
        or not manifest.is_file()
        or sha256_path(manifest) != str(receipt.get("manifestHash"))
    ):
        raise ValueError("pinned source receipt is absent, changed, or incomplete")
    hashes = []
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        if raw.strip() and not raw.lstrip().startswith("#"):
            hashes.append(canonical_line(raw)[1])
    if len(hashes) != 13 or len(set(hashes)) != 13:
        raise ValueError("pinned receipt manifest cardinality changed")
    for spec in SPECS.values():
        if hashes[int(spec["polynomialIndex"])] != spec["coefficientSha256"]:
            raise ValueError("pinned source receipt index/hash mismatch")
    return {
        "path": str(RECEIPT.relative_to(ROOT)),
        "sha256": sha256_path(RECEIPT),
        "manifestSha256": str(receipt["manifestHash"]),
        "polynomials": len(hashes),
    }


def validate_routes_and_inventory() -> dict[str, dict]:
    rows = {str(row.get("sourceLabel")): row for row in read_jsonl(ROUTES)}
    inventory = read_json(INVENTORY)
    anchors = inventory.get("acceptedAnchorsByPair") or {}
    selected = {}
    for source_label, spec in SPECS.items():
        row = rows.get(source_label)
        anchor_rows = anchors.get(f"{source_label}:{spec['sourceR']}") or []
        if (
            row is None
            or row.get("status") != "certified"
            or list(map(int, row.get("sourceR") or [])) != [spec["sourceR"]]
            or int(row.get("length24OrbitCount", -1)) != 3
            or sorted(map(int, row.get("orbitSizes") or []))
            != [12, 24, 24, 24, 192]
            or len(anchor_rows) != 1
        ):
            raise ValueError(f"v16 source route envelope changed: {source_label}")
        anchor = anchor_rows[0]
        if (
            str(anchor.get("submissionId")) != SUBMISSION_ID
            or int(anchor.get("polynomialIndex", -1)) != spec["polynomialIndex"]
            or str(anchor.get("coefficientSha256")) != spec["coefficientSha256"]
            or str(anchor.get("scoringStatus")) != "scoreable"
            or bool(anchor.get("inBaseline"))
        ):
            raise ValueError(f"v16 accepted anchor changed: {source_label}")
        target_label, target_r = spec["goldTarget"]
        gold_routes = [
            route
            for route in row.get("routes") or []
            if route.get("allCompatibleClassesGold") is True
            and str(route.get("targetLabel")) == target_label
            and int(route.get("sourceR", -1)) == spec["sourceR"]
            and int(route.get("deterministicTargetR", -1)) == target_r
        ]
        if len(gold_routes) != spec["targetMultiplicity"]:
            raise ValueError(f"v16 deterministic gold route count changed: {source_label}")
        target_counts = {
            str(target["targetLabel"]): sum(
                str(other["targetLabel"]) == str(target["targetLabel"])
                for other in row.get("targets") or []
            )
            for target in row.get("targets") or []
        }
        if target_counts.get(target_label) != spec["targetMultiplicity"]:
            raise ValueError(f"v16 target multiplicity changed: {source_label}")
        selected[source_label] = row
    return selected


def validate_ledger_sources_and_targets(routes: dict[str, dict]) -> dict:
    snapshot = {}
    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for source_label, spec in SPECS.items():
            source = connection.execute(
                "SELECT p.coefficient_hash,v.status,v.label,v.r,v.scoreable,"
                "v.in_baseline,v.scoring_status FROM polynomials p JOIN "
                "verifications v USING(submission_id,polynomial_index) WHERE "
                "p.submission_id=? AND p.polynomial_index=?",
                (SUBMISSION_ID, spec["polynomialIndex"]),
            ).fetchone()
            if source is None or (
                str(source["coefficient_hash"]),
                str(source["status"]),
                str(source["label"]),
                int(source["r"]),
                int(source["scoreable"] or 0),
                int(source["in_baseline"] or 0),
                str(source["scoring_status"]),
            ) != (
                spec["coefficientSha256"],
                "accepted",
                source_label,
                spec["sourceR"],
                1,
                0,
                "scoreable",
            ):
                raise ValueError(f"accepted source ledger provenance changed: {source_label}")
            target_pair = spec["goldTarget"]
            target = connection.execute(
                "SELECT team_count,discovered,generated_at FROM targets "
                "WHERE label=? AND r=?", target_pair,
            ).fetchone()
            baseline = connection.execute(
                "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", target_pair
            ).fetchone()
            owned = connection.execute(
                "SELECT 1 FROM verifications WHERE label=? AND r=? AND "
                "scoreable=1 LIMIT 1", target_pair,
            ).fetchone()
            if (
                target is None
                or int(target["team_count"]) != 0
                or bool(target["discovered"])
                or baseline is not None
                or owned is not None
            ):
                raise ValueError(f"gold target is no longer live: {target_pair}")
            snapshot[f"{target_pair[0]}/r{target_pair[1]}"] = {
                "teamCount": int(target["team_count"]),
                "discovered": bool(target["discovered"]),
                "generatedAt": str(target["generated_at"]),
                "baseline": False,
                "owned": False,
            }
    return snapshot


def validate_worker_result(result: dict, source_label: str, route: dict) -> dict:
    spec = SPECS[source_label]
    expected_targets = [
        {
            "orbitIndex": int(target["orbitIndex"]),
            "orbitSize": int(target["orbitSize"]),
            "targetLabel": str(target["targetLabel"]),
            "targetT": int(target["targetT"]),
            "kernelOrder": int(target["kernelOrder"]),
        }
        for target in route.get("targets") or []
    ]
    actual_targets = [
        {
            "orbitIndex": int(target["orbitIndex"]),
            "orbitSize": int(target["orbitSize"]),
            "targetLabel": str(target["targetLabel"]),
            "targetT": int(target["targetT"]),
            "kernelOrder": int(target["kernelOrder"]),
        }
        for target in result.get("orbitTargets") or []
    ]
    orbit = result.get("orbitCertificate") or {}
    candidates = sorted(result.get("candidates") or [], key=lambda row: int(row["factorIndex"]))
    if (
        result.get("status") != "certified_multi"
        or int(result.get("workerExitCode", -1)) != 0
        or str(result.get("sourceSubmissionId")) != SUBMISSION_ID
        or int(result.get("sourcePolynomialIndex", -1)) != spec["polynomialIndex"]
        or str(result.get("sourceCoefficientSha256")) != spec["coefficientSha256"]
        or str(result.get("sourceLabel")) != source_label
        or int(result.get("sourceR", -1)) != spec["sourceR"]
        or actual_targets != expected_targets
        or orbit.get("actualDegrees") != orbit.get("expectedDegrees")
        or any(int(value) != 1 for value in orbit.get("exponents") or [])
        or [int(row["factorIndex"]) for row in candidates] != [0, 1, 2]
    ):
        raise ValueError(f"pair-resolvent worker certificate changed: {source_label}")
    for candidate in candidates:
        line, digest = canonical_line(str(candidate.get("coefficientLine")))
        if (
            digest != str(candidate.get("coefficientSha256"))
            or len(line.encode("ascii")) != int(candidate.get("coefficientBytes", -1))
            or int(candidate.get("targetR", -1)) != 24
            or int(candidate.get("polynomialDiscriminantAbs", 0)) <= 0
            or int(candidate.get("fieldDiscriminantAbs", 0)) <= 0
        ):
            raise ValueError(f"invalid certified factor payload: {source_label}")
    return result


def run_worker(sage: str, source_label: str, route: dict, timeout: int) -> dict:
    spec = SPECS[source_label]
    command = [
        sage,
        "-python",
        str(WORKER),
        SUBMISSION_ID,
        str(spec["polynomialIndex"]),
        "--all-degree-24",
        "--orbit-map",
        str(ROUTES),
        "--expected-source-hash",
        str(spec["coefficientSha256"]),
        "--transforms",
        "1,2,3,5,7",
        "--reduce",
        "best",
        "--nfdisc",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"pair-resolvent worker failed for {source_label}: exit="
            f"{completed.returncode}; stderr-tail={completed.stderr[-1200:]}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise RuntimeError(f"unexpected worker stdout envelope for {source_label}")
    try:
        result = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid worker JSON for {source_label}") from exc
    return validate_worker_result(result, source_label, route)


def atomic_new(path: Path, payload: bytes) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite sealed output: {destination}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if destination.exists():
            raise FileExistsError(f"refusing to overwrite sealed output: {destination}")
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def run(output: Path, certificate: Path, timeout: int, sage: str | None = None) -> dict:
    outputs = {output.expanduser().resolve(), certificate.expanduser().resolve()}
    if len(outputs) != 2 or any(path.exists() for path in outputs):
        raise FileExistsError("refusing to overwrite or alias v16 worker outputs")
    sage_path = sage or shutil.which("sage")
    if not sage_path:
        raise RuntimeError("Sage executable is not available")
    receipt = validate_receipt()
    routes = validate_routes_and_inventory()
    target_snapshot = validate_ledger_sources_and_targets(routes)
    rows = []
    # Intentionally sequential: never more than one Sage child exists.
    for source_label in SPECS:
        rows.append(run_worker(sage_path, source_label, routes[source_label], timeout))
    candidates_payload = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows
    ).encode("utf-8")
    certificate_value = {
        "schemaVersion": "v16-gold-pair-resolvent-run-certificate-v1",
        "status": "two_sources_certified_sequentially",
        "candidateArtifact": {
            "path": str(output.resolve().relative_to(ROOT)),
            "sha256": sha256_bytes(candidates_payload),
            "rows": len(rows),
        },
        "checks": {
            "acceptedScoreableSourceProvenanceMatched": True,
            "deterministicV16GoldRoutesPinned": True,
            "oneSageWorkerAtATime": True,
            "receiptManifestIndexAndHashesMatched": True,
            "targetsLiveTc0NonbaselineUnownedAtPreflight": True,
            "workerFactorizationMatchedExactOrbitCensus": True,
        },
        "receipt": receipt,
        "routeArtifact": {
            "path": str(ROUTES.relative_to(ROOT)),
            "sha256": sha256_path(ROUTES),
        },
        "sources": [
            {
                "label": label,
                "r": SPECS[label]["sourceR"],
                "submissionId": SUBMISSION_ID,
                "polynomialIndex": SPECS[label]["polynomialIndex"],
                "coefficientSha256": SPECS[label]["coefficientSha256"],
                "certifiedFactors": len(row["candidates"]),
            }
            for label, row in zip(SPECS, rows)
        ],
        "targetSnapshot": target_snapshot,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }
    certificate_payload = (
        json.dumps(certificate_value, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    atomic_new(output, candidates_payload)
    try:
        atomic_new(certificate, certificate_payload)
    except Exception:
        # Preserve the coefficient-bearing evidence rather than delete it; a
        # rerun is still safely blocked and the missing certificate is obvious.
        raise
    return {
        "status": certificate_value["status"],
        "sources": len(rows),
        "certifiedFactors": sum(len(row["candidates"]) for row in rows),
        "candidateArtifact": str(output),
        "candidateSha256": sha256_bytes(candidates_payload),
        "certificate": str(certificate),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--certificate", type=Path, default=DEFAULT_CERTIFICATE)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    print(json.dumps(run(args.output, args.certificate, args.timeout), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
