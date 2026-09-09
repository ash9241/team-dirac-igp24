#!/usr/bin/env python3
"""Freeze exact unique-orbit F6 pair-resolvent pilots into an audited batch."""

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


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def atomic_text(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "ledger.sqlite3")
    parser.add_argument("--pair-shards", type=Path, nargs="+", required=True)
    parser.add_argument("--pilots", type=Path, nargs="+", required=True)
    parser.add_argument("--frozen-targets", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    args = parser.parse_args()

    pair_rows = {}
    for path in args.pair_shards:
        for row in read_jsonl(path):
            pair_rows[str(row["sourceLabel"])] = row
    frozen = {
        (str(row["label"]), int(row["r"])): row
        for row in read_jsonl(args.frozen_targets)
    }

    candidates = []
    with sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for pilot_path in args.pilots:
            pilot = json.loads(pilot_path.read_text())
            if pilot.get("status") != "certified":
                raise RuntimeError(f"pilot is not certified: {pilot_path}")
            targets = list(pilot.get("orbitTargets") or [])
            if len(targets) != 1:
                raise RuntimeError(f"pilot is not a unique length-24 orbit: {pilot_path}")
            source_label = str(pilot["sourceLabel"])
            source_r = int(pilot["sourceR"])
            target_label = str(pilot["targetLabel"])
            target_r = int(pilot["targetR"])
            if target_label != str(targets[0]["targetLabel"]):
                raise RuntimeError("pilot target label does not match the exact orbit target")

            pair_row = pair_rows[source_label]
            exact_gold_routes = [
                route
                for route in pair_row.get("routes", [])
                if int(route["sourceR"]) == source_r
                and str(route["targetLabel"]) == target_label
                and target_r in {int(value) for value in route["mappedTargetR"]}
                and target_r in {int(value) for value in route["goldR"]}
            ]
            if not exact_gold_routes:
                raise RuntimeError(f"no exact census-certified gold route for {pilot_path}")
            frozen_row = frozen.get((target_label, target_r))
            if frozen_row is None:
                raise RuntimeError(f"target pair is absent from frozen gold: {target_label}/r{target_r}")

            line = str(pilot["coefficientLine"])
            digest = sha256_bytes(line.encode("ascii"))
            if digest != str(pilot["coefficientSha256"]):
                raise RuntimeError("candidate coefficient hash does not recompute")
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
            if str(source["label"]) != source_label or int(source["r"]) != source_r:
                raise RuntimeError("source provenance label/signature mismatch")
            coefficient_rows = int(connection.execute(
                "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?", (digest,)
            ).fetchone()[0])
            owned_pair_rows = int(connection.execute(
                "SELECT COUNT(*) FROM verifications WHERE label=? AND r=? AND scoreable=1",
                (target_label, target_r),
            ).fetchone()[0])
            same_field_rows = int(connection.execute(
                """
                SELECT COUNT(*) FROM verifications
                WHERE label=? AND r=? AND field_disc_abs=? AND scoreable=1
                """,
                (target_label, target_r, str(pilot["fieldDiscriminantAbs"])),
            ).fetchone()[0])
            if coefficient_rows or owned_pair_rows or same_field_rows:
                raise RuntimeError(
                    f"candidate is not novel: {target_label}/r{target_r} "
                    f"coefficient={coefficient_rows} pair={owned_pair_rows} field={same_field_rows}"
                )
            candidates.append(
                {
                    "coefficientBytes": int(pilot["coefficientBytes"]),
                    "coefficientLine": line,
                    "coefficientSha256": digest,
                    "factorCertificate": {
                        **pilot["orbitCertificate"],
                        "resolventSquarefree": True,
                    },
                    "fieldDiscriminantAbs": str(pilot["fieldDiscriminantAbs"]),
                    "noveltyAudit": {
                        "coefficientHashRows": coefficient_rows,
                        "ownedExactTargetPairRows": owned_pair_rows,
                        "sameTargetFieldDiscriminantRows": same_field_rows,
                    },
                    "pairCensusCertificateSha256": str(pair_row["exactCertificateSha256"]),
                    "pilotArtifact": {
                        "path": str(pilot_path.resolve()),
                        "sha256": sha256_path(pilot_path),
                    },
                    "polynomialDiscriminantAbs": str(pilot["polynomialDiscriminantAbs"]),
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
                    "transform": pilot["transform"],
                }
            )

    candidates.sort(key=lambda row: (int(row["target"]["t"]), int(row["target"]["r"])))
    hashes = [row["coefficientSha256"] for row in candidates]
    target_pairs = [(row["target"]["label"], row["target"]["r"]) for row in candidates]
    if len(hashes) != len(set(hashes)) or len(target_pairs) != len(set(target_pairs)):
        raise RuntimeError("batch contains duplicate coefficients or target pairs")

    manifest_text = "".join(row["coefficientLine"] + "\n" for row in candidates)
    atomic_text(args.manifest, manifest_text)
    certificate = {
        "batchSize": len(candidates),
        "candidates": candidates,
        "certificateVersion": "index24-f6-unique-pair-batch-v1",
        "createdAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "manifest": {
            "path": str(args.manifest.resolve()),
            "sha256": sha256_bytes(manifest_text.encode("ascii")),
        },
        "networkCalls": 0,
        "status": "staged_exact",
        "submissionCalls": 0,
    }
    atomic_text(args.certificate, json.dumps(certificate, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "batchSize": len(candidates),
        "certificateSha256": sha256_path(args.certificate),
        "manifestSha256": sha256_path(args.manifest),
        "targets": target_pairs,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
