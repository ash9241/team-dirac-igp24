#!/usr/bin/env python3
"""Stage exact multi-orbit F6 pair factors resolved by Frobenius certificates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_digest(path: Path) -> str:
    return digest(path.read_bytes())


def atomic_write(path: Path, payload: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--pair-shards", type=Path, nargs="+", required=True)
    parser.add_argument("--pilots", type=Path, nargs="+", required=True)
    parser.add_argument("--frobenius", type=Path, nargs="+", required=True)
    parser.add_argument("--frozen-targets", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    args = parser.parse_args()
    if len(args.pilots) != len(args.frobenius):
        parser.error("--pilots and --frobenius must have the same length")

    pair_rows = {}
    for path in args.pair_shards:
        for row in read_jsonl(path):
            pair_rows[str(row["sourceLabel"])] = row
    frozen = {
        (str(row["label"]), int(row["r"])): row
        for row in read_jsonl(args.frozen_targets)
    }
    selected = []
    with sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for pilot_path, frobenius_path in zip(args.pilots, args.frobenius):
            pilot = json.loads(pilot_path.read_text())
            proof = json.loads(frobenius_path.read_text())
            if pilot.get("status") != "certified_multi":
                raise RuntimeError(f"not a certified multi pilot: {pilot_path}")
            if proof.get("summary", {}).get("resolved") != 1 or len(proof.get("rows", [])) != 1:
                raise RuntimeError(f"Frobenius proof is not uniquely resolved: {frobenius_path}")
            proof_row = proof["rows"][0]
            if proof_row.get("status") != "resolved":
                raise RuntimeError("Frobenius packet status is not resolved")
            source_label = str(pilot["sourceLabel"])
            source_r = int(pilot["sourceR"])
            pair_row = pair_rows[source_label]
            exact_gold_routes = {
                (str(route["targetLabel"]), int(value))
                for route in pair_row.get("routes", [])
                if int(route["sourceR"]) == source_r
                for value in set(route["goldR"]) & set(route["mappedTargetR"])
            }
            candidates = {
                int(candidate["factorIndex"]): candidate
                for candidate in pilot["candidates"]
            }
            source = connection.execute(
                """
                SELECT p.coefficient_hash,v.field_disc_abs,v.label,v.r,v.scoreable
                FROM polynomials AS p JOIN verifications AS v
                  USING(submission_id,polynomial_index)
                WHERE p.submission_id=? AND p.polynomial_index=?
                """,
                (str(pilot["sourceSubmissionId"]), int(pilot["sourcePolynomialIndex"])),
            ).fetchone()
            if source is None or int(source["scoreable"] or 0) != 1:
                raise RuntimeError("source provenance is absent or not scoreable")
            for assignment in proof_row["assignments"]:
                target_label = str(assignment["targetLabel"])
                target_r = int(assignment["targetR"])
                if (target_label, target_r) not in exact_gold_routes:
                    continue
                frozen_row = frozen.get((target_label, target_r))
                if frozen_row is None:
                    continue
                candidate = candidates[int(assignment["factorIndex"])]
                if str(candidate["coefficientSha256"]) != str(assignment["coefficientSha256"]):
                    raise RuntimeError("Frobenius assignment coefficient mismatch")
                line = str(candidate["coefficientLine"])
                coefficient_hash = digest(line.encode("ascii"))
                if coefficient_hash != str(candidate["coefficientSha256"]):
                    raise RuntimeError("candidate coefficient hash does not recompute")
                coefficient_rows = int(connection.execute(
                    "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?", (coefficient_hash,)
                ).fetchone()[0])
                owned_pair_rows = int(connection.execute(
                    "SELECT COUNT(*) FROM verifications WHERE label=? AND r=? AND scoreable=1",
                    (target_label, target_r),
                ).fetchone()[0])
                same_field_rows = int(connection.execute(
                    """SELECT COUNT(*) FROM verifications
                       WHERE label=? AND r=? AND field_disc_abs=? AND scoreable=1""",
                    (target_label, target_r, str(candidate["fieldDiscriminantAbs"])),
                ).fetchone()[0])
                if coefficient_rows or owned_pair_rows or same_field_rows:
                    raise RuntimeError(f"multi candidate is not novel: {target_label}/r{target_r}")
                selected.append(
                    {
                        "candidate": candidate,
                        "factorCertificate": {
                            **pilot["orbitCertificate"],
                            "resolventSquarefree": True,
                        },
                        "frobeniusArtifact": {
                            "path": str(frobenius_path.resolve()),
                            "sha256": file_digest(frobenius_path),
                        },
                        "frobeniusAssignment": assignment,
                        "noveltyAudit": {
                            "coefficientHashRows": coefficient_rows,
                            "ownedExactTargetPairRows": owned_pair_rows,
                            "sameTargetFieldDiscriminantRows": same_field_rows,
                        },
                        "pairCensusCertificateSha256": str(pair_row["exactCertificateSha256"]),
                        "pilotArtifact": {
                            "path": str(pilot_path.resolve()),
                            "sha256": file_digest(pilot_path),
                        },
                        "resolventSha256": str(pilot["attempts"][-1]["resolventSha256"]),
                        "source": {
                            "coefficientSha256": str(source["coefficient_hash"]),
                            "fieldDiscriminantAbs": str(source["field_disc_abs"]),
                            "label": source_label,
                            "polynomialIndex": int(pilot["sourcePolynomialIndex"]),
                            "r": source_r,
                            "submissionId": str(pilot["sourceSubmissionId"]),
                        },
                        "target": {
                            "frozenGeneratedAt": str(frozen_row["generated_at"]),
                            "label": target_label,
                            "r": target_r,
                            "t": int(frozen_row["t"]),
                            "teamCountAtFrozenSnapshot": int(frozen_row["team_count"]),
                        },
                    }
                )

    selected_before_deduplication = len(selected)
    best_by_target_pair = {}
    for row in selected:
        key = (str(row["target"]["label"]), int(row["target"]["r"]))
        incumbent = best_by_target_pair.get(key)
        if incumbent is None or int(row["candidate"]["polynomialDiscriminantAbs"]) < int(
            incumbent["candidate"]["polynomialDiscriminantAbs"]
        ):
            best_by_target_pair[key] = row
    selected = list(best_by_target_pair.values())
    selected.sort(key=lambda row: (int(row["target"]["t"]), int(row["target"]["r"])))
    target_pairs = [(row["target"]["label"], row["target"]["r"]) for row in selected]
    hashes = [str(row["candidate"]["coefficientSha256"]) for row in selected]
    if len(selected) != len(set(target_pairs)) or len(selected) != len(set(hashes)):
        raise RuntimeError("duplicate target pair or coefficient in multi batch")
    manifest = "".join(str(row["candidate"]["coefficientLine"]) + "\n" for row in selected)
    atomic_write(args.manifest, manifest)
    certificate = {
        "batchSize": len(selected),
        "candidates": selected,
        "certificateVersion": "index24-f6-multi-pair-frobenius-v1",
        "createdAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "duplicateTargetAssignmentsDropped": selected_before_deduplication - len(selected),
        "manifest": {"path": str(args.manifest.resolve()), "sha256": digest(manifest.encode("ascii"))},
        "networkCalls": 0,
        "status": "staged_exact",
        "submissionCalls": 0,
    }
    atomic_write(args.certificate, json.dumps(certificate, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "batchSize": len(selected),
        "certificateSha256": file_digest(args.certificate),
        "duplicateTargetAssignmentsDropped": selected_before_deduplication - len(selected),
        "manifestSha256": file_digest(args.manifest),
        "targets": target_pairs,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
