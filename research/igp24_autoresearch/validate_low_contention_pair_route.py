#!/usr/bin/env python3
"""Light postflight validator for one sealed low-contention pair runbook.

The validator reads a coefficient-bearing worker result only to recompute its
hash and exact certificate.  It prints and writes only coefficient-free data.
It never calls the network, changes the ledger, stages, or submits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import prepare_v11_pair_delta as helper
import stage_single_exact_census as exact_census


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RECEIPTS = ROOT / "receipts"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--route-id", required=True)
    parser.add_argument("--result", type=Path, required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected one JSON object: {path}")
    return value


def one_jsonl(path: Path) -> dict:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("worker result must contain exactly one JSON object")
    return rows[0]


def receipt_snapshot(connection: sqlite3.Connection) -> tuple[set[str], set[tuple[str, int]], dict]:
    candidates, corpus = exact_census.scan_candidates(DATA)
    pair_index: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for candidate in candidates:
        pair_index[str(candidate["coefficientSha256"])].add(
            (str(candidate["targetLabel"]), int(candidate["targetR"]))
        )
    hashes, pairs, audit = exact_census.receipt_exclusions(
        RECEIPTS, DATA, connection, pair_index
    )
    return hashes, pairs, {
        "artifactFileIndexSha256": corpus["artifactFileIndexSha256"],
        "receiptCount": audit["receiptCount"],
        "receiptPolynomialHashes": audit["receiptPolynomialHashes"],
        "receiptTargetPairs": audit["receiptTargetPairs"],
        "queuedPossiblePairMapRows": audit["queuedPossiblePairMapRows"],
    }


def main() -> int:
    args = arguments()
    certificate_path = args.certificate.expanduser().resolve()
    result_path = args.result.expanduser().resolve()
    if not certificate_path.is_relative_to(ROOT) or not result_path.is_relative_to(DATA):
        raise ValueError("certificate/result escaped the project data roots")
    certificate = read_json(certificate_path)
    if (
        certificate.get("schemaVersion")
        != "low-contention-unordered-pair-route-audit-v1"
        or certificate.get("status") != "certified_light_only_runbooks_ready"
        or certificate.get("coefficientMaterialIncluded") is not False
    ):
        raise ValueError("input route certificate is not sealed and coefficient-free")
    matches = [
        row for row in certificate.get("runbooks") or []
        if str(row.get("routeId")) == args.route_id
    ]
    if len(matches) != 1:
        raise ValueError(f"route id is absent or nonunique: {args.route_id}")
    runbook = matches[0]
    if (ROOT / str(runbook["output"])).resolve() != result_path:
        raise ValueError("result path differs from the sealed runbook output")

    source = runbook["source"]
    target = runbook["target"]
    target_pair = (str(target["label"]), int(target["r"]))
    row = one_jsonl(result_path)
    if (
        row.get("status") != "certified"
        or int(row.get("workerExitCode", -1)) != 0
        or str(row.get("sourceSubmissionId")) != str(source["submissionId"])
        or int(row.get("sourcePolynomialIndex", -1)) != int(source["polynomialIndex"])
        or str(row.get("sourceCoefficientSha256")) != str(source["coefficientSha256"])
        or str(row.get("sourceLabel")) != str(source["label"])
        or int(row.get("sourceR", -1)) != int(source["r"])
        or str(row.get("targetLabel")) != target_pair[0]
        or int(row.get("targetR", -1)) != target_pair[1]
    ):
        raise ValueError("worker result differs from the sealed exact route")
    line = exact_census.canonical_polynomial_line(row.get("coefficientLine"))
    if line is None:
        raise ValueError("worker output is not a canonical primitive monic degree-24 polynomial")
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    if digest != str(row.get("coefficientSha256")):
        raise ValueError("worker output coefficient hash mismatch")
    orbit = row.get("orbitCertificate") or {}
    actual = [int(value) for value in orbit.get("actualDegrees") or []]
    expected = [int(value) for value in orbit.get("expectedDegrees") or []]
    exponents = [int(value) for value in orbit.get("exponents") or []]
    if (
        not actual
        or actual != expected
        or actual.count(24) != 1
        or len(exponents) != len(actual)
        or any(value != 1 for value in exponents)
    ):
        raise ValueError("worker orbit/factorization certificate is not exact and squarefree")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        source_row = connection.execute(
            "SELECT p.coefficient_hash,v.status,v.scoreable,v.label,v.r "
            "FROM polynomials p JOIN verifications v "
            "USING(submission_id,polynomial_index) "
            "WHERE p.submission_id=? AND p.polynomial_index=?",
            (source["submissionId"], source["polynomialIndex"]),
        ).fetchone()
        if (
            source_row is None
            or str(source_row["coefficient_hash"]) != str(source["coefficientSha256"])
            or str(source_row["status"]) != "accepted"
            or int(source_row["scoreable"] or 0) != 1
            or str(source_row["label"]) != str(source["label"])
            or int(source_row["r"]) != int(source["r"])
        ):
            raise ValueError("source ledger anchor changed or is no longer scoreable")
        if connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1", (digest,)
        ).fetchone() is not None:
            raise ValueError("worker output hash is already present in the ledger")
        if connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", target_pair
        ).fetchone() is not None:
            raise ValueError("target pair is baseline")
        if connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", target_pair
        ).fetchone() is not None:
            raise ValueError("target pair is already known in the local ledger")
        target_row = connection.execute(
            "SELECT team_count,discovered,minimum_disc_abs,generated_at "
            "FROM targets WHERE label=? AND r=?", target_pair
        ).fetchone()
        if target_row is None or int(target_row["team_count"]) not in (1, 2):
            raise ValueError("target is no longer a live tc1/tc2 pair")
        receipt_hashes, receipt_pairs, receipt_audit = receipt_snapshot(connection)
        if digest in receipt_hashes:
            raise ValueError("worker output hash is already present in a receipt")
        if target_pair in receipt_pairs:
            raise ValueError("target pair is already covered by a receipt")
    finally:
        connection.close()

    postflight_path = result_path.with_name(result_path.stem + "_postflight.json")
    if postflight_path.exists():
        raise FileExistsError(f"refusing to overwrite postflight certificate: {postflight_path}")
    postflight = {
        "schemaVersion": "low-contention-pair-route-postflight-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_novel_live_not_staged_not_submitted",
        "routeCertificate": helper.relative_artifact(certificate_path),
        "routeId": args.route_id,
        "result": helper.relative_artifact(result_path),
        "source": {
            "submissionId": source["submissionId"],
            "polynomialIndex": source["polynomialIndex"],
            "label": source["label"],
            "r": source["r"],
            "coefficientSha256": source["coefficientSha256"],
            "ledgerStatus": "accepted_scoreable",
        },
        "target": {
            "label": target_pair[0],
            "r": target_pair[1],
            "teamCount": int(target_row["team_count"]),
            "discovered": bool(target_row["discovered"]),
            "minimumDiscAbs": (
                str(target_row["minimum_disc_abs"])
                if target_row["minimum_disc_abs"] is not None else None
            ),
            "generatedAt": str(target_row["generated_at"]),
        },
        "candidate": {
            "coefficientSha256": digest,
            "primitiveMonicDegree24": True,
            "irreducible": True,
            "exactSquarefreeOrbitCertificate": True,
            "knownLedgerHash": False,
            "receiptHash": False,
        },
        "receiptExclusion": receipt_audit,
        "coefficientMaterialIncluded": False,
        "sideEffects": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    helper.atomic_json(postflight_path, postflight)
    print(json.dumps({
        "status": postflight["status"],
        "routeId": args.route_id,
        "target": f"{target_pair[0]}/r{target_pair[1]}",
        "teamCount": int(target_row["team_count"]),
        "candidateSha256": digest,
        "postflight": str(postflight_path.relative_to(ROOT)),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
