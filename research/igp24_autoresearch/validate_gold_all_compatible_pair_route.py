#!/usr/bin/env python3
"""Light postflight for one all-compatible current-gold pair factor."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import audit_full_ledger_gold_reintersection as audit
import prepare_v11_pair_delta as helper
import stage_single_exact_census as exact


ROOT = audit.ROOT
DATA = audit.DATA
DB = audit.DB


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--route-id", required=True)
    parser.add_argument("--result", type=Path, required=True)
    return parser.parse_args()


def one_row(path: Path) -> dict:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("isolated factor output must contain exactly one row")
    return rows[0]


def main() -> int:
    args = arguments()
    certificate_path = args.certificate.expanduser().resolve()
    result_path = args.result.expanduser().resolve()
    if (
        not certificate_path.is_relative_to(DATA)
        or not result_path.is_relative_to(DATA)
    ):
        raise ValueError("certificate or result escaped data root")
    certificate = audit.read_json(certificate_path)
    if (
        certificate.get("schemaVersion")
        != "gold-profile-backfill-safe-route-frontier-v1"
        or certificate.get("status")
        != "certified_isolated_factor_runbooks_ready"
        or certificate.get("coefficientMaterialIncluded") is not False
        or not all((certificate.get("checks") or {}).values())
    ):
        raise ValueError("safe-route certificate is not intact")
    matches = [
        row
        for row in certificate.get("runbooks") or []
        if str(row.get("routeId")) == args.route_id
    ]
    if len(matches) != 1:
        raise ValueError("route id is absent or nonunique")
    runbook = matches[0]
    if (ROOT / str(runbook["output"])).resolve() != result_path:
        raise ValueError("result path differs from sealed runbook")
    possible = {
        (
            str(row["pair"]).rsplit("/r", 1)[0],
            int(str(row["pair"]).rsplit("/r", 1)[1]),
        )
        for row in runbook["possibleCurrentGoldTargets"]
    }
    source = runbook["source"]
    result = one_row(result_path)
    realized = (str(result.get("targetLabel")), int(result.get("targetR", -1)))
    if (
        result.get("status") != "certified"
        or int(result.get("workerExitCode", -1)) != 0
        or str(result.get("sourceSubmissionId")) != str(source["submissionId"])
        or int(result.get("sourcePolynomialIndex", -1))
        != int(source["polynomialIndex"])
        or str(result.get("sourceCoefficientSha256"))
        != str(source["coefficientSha256"])
        or str(result.get("sourceLabel")) != str(source["label"])
        or int(result.get("sourceR", -1)) != int(source["r"])
        or realized not in possible
    ):
        raise ValueError("worker result escaped the sealed source/possible-pair envelope")
    line = exact.canonical_polynomial_line(result.get("coefficientLine"))
    if line is None:
        raise ValueError("worker result is not a canonical degree-24 polynomial")
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    if digest != str(result.get("coefficientSha256")):
        raise ValueError("worker result hash mismatch")
    orbit = result.get("orbitCertificate") or {}
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
        raise ValueError("worker factor/orbit certificate is not exact")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        source_row = connection.execute(
            "SELECT p.coefficient_hash,v.status,v.scoreable,v.label,v.r "
            "FROM polynomials p JOIN verifications v "
            "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
            "AND p.polynomial_index=?",
            (source["submissionId"], source["polynomialIndex"]),
        ).fetchone()
        if (
            source_row is None
            or str(source_row["coefficient_hash"]) != source["coefficientSha256"]
            or str(source_row["status"]) != "accepted"
            or int(source_row["scoreable"] or 0) != 1
            or str(source_row["label"]) != source["label"]
            or int(source_row["r"]) != int(source["r"])
        ):
            raise ValueError("source ledger anchor changed")
        if connection.execute(
            "SELECT 1 FROM polynomials WHERE coefficient_hash=? LIMIT 1", (digest,)
        ).fetchone() is not None:
            raise ValueError("worker output hash is already known in ledger")
        if connection.execute(
            "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", realized
        ).fetchone() is not None:
            raise ValueError("realized pair is baseline")
        if connection.execute(
            "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", realized
        ).fetchone() is not None:
            raise ValueError("realized pair is already known locally")
        target = connection.execute(
            "SELECT team_count,discovered,minimum_disc_abs,generated_at "
            "FROM targets WHERE label=? AND r=?", realized
        ).fetchone()
        if target is None or int(target["team_count"]) != 0:
            raise ValueError("realized pair is no longer current tc0")
        corpus = audit.exact_and_lineage_corpus(connection)
        exclusions = audit.receipt_and_outbox_exclusions(connection, corpus)
        if (
            digest in exclusions["receiptHashes"]
            or digest in exclusions["outboxHashes"]
            or realized in exclusions["receiptPairs"]
            or realized in exclusions["outboxPairs"]
        ):
            raise ValueError("realized factor is already receipt/outbox covered")
    finally:
        connection.close()

    postflight_path = result_path.with_name(result_path.stem + "_postflight.json")
    if postflight_path.exists():
        raise FileExistsError(f"refusing to overwrite postflight: {postflight_path}")
    postflight = {
        "schemaVersion": "gold-all-compatible-isolated-factor-postflight-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "certified_exact_current_gold_not_staged_not_submitted",
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
        "realizedTarget": {
            "pair": audit.pair_text(realized),
            "teamCount": int(target["team_count"]),
            "minimumDiscAbs": (
                str(target["minimum_disc_abs"])
                if target["minimum_disc_abs"] is not None
                else None
            ),
            "generatedAt": str(target["generated_at"]),
        },
        "candidate": {
            "coefficientSha256": digest,
            "primitiveMonicDegree24": True,
            "irreducible": True,
            "exactSquarefreeOrbitCertificate": True,
            "knownLedgerHash": False,
            "receiptHash": False,
            "outboxHash": False,
        },
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "sideEffects": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    rendered = json.dumps(postflight, indent=2, sort_keys=True) + "\n"
    if audit.pair_audit.COEFFICIENT_LINE_RE.search(rendered):
        raise ValueError("coefficient payload entered postflight")
    audit.atomic_replace(postflight_path, rendered)
    print(
        json.dumps(
            {
                "status": postflight["status"],
                "routeId": args.route_id,
                "realizedTarget": audit.pair_text(realized),
                "candidateSha256": digest,
                "postflight": str(postflight_path.relative_to(ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
