from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from derived_stage_provenance import validate_derived_stage_source


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_path(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )


class DerivedStageProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def build(
        self,
        *,
        route_mode: str,
        all_compatible: bool,
        mapped_r: tuple[int, ...] = (4,),
        gold_r: tuple[int, ...] = (4,),
        duplicate_factor_index: bool = False,
    ) -> tuple[Path, Path, str, list[dict]]:
        lines = [
            ",".join(map(str, [1] + [0] * 23 + [1])),
            ",".join(map(str, [2] + [0] * 23 + [1])),
        ]
        hashes = [digest_bytes(line.encode("utf-8")) for line in lines]
        exact_certificate = "a" * 64
        slots = [
            {
                "orbitIndex": 1,
                "orbitSize": 24,
                "kernelOrder": 1,
                "targetLabel": "24T30",
                "targetT": 30,
            },
            {
                "orbitIndex": 2,
                "orbitSize": 24,
                "kernelOrder": 1,
                "targetLabel": "24T31",
                "targetT": 31,
            },
        ]
        orbit_path = self.root / "orbit.jsonl"
        orbit_row = {
            "status": "certified",
            "sourceLabel": "24T20",
            "sourceT": 20,
            "sourceR": [8],
            "exactCertificateSha256": exact_certificate,
            "length24OrbitCount": 2,
            "orbitSizes": [24, 24, 228],
            "targets": slots,
            "routes": [
                {
                    "sourceR": 8,
                    "targetLabel": "24T30",
                    "targetT": 30,
                    "mappedTargetR": list(mapped_r),
                    "goldR": list(gold_r),
                    "allCompatibleClassesGold": all_compatible,
                }
            ],
        }
        orbit_path.write_text(json.dumps(orbit_row) + "\n", encoding="utf-8")

        factor_indexes = [0, 0 if duplicate_factor_index else 1]
        packet_candidates = [
            {
                "factorIndex": factor_indexes[index],
                "coefficientLine": lines[index],
                "coefficientSha256": hashes[index],
                "fieldDiscriminantAbs": str(17 + index),
                "polynomialDiscriminantAbs": str(101 + index),
                "targetR": 4 + 2 * index,
                "degree": 24,
                "monic": True,
                "irreducible": True,
            }
            for index in range(2)
        ]
        upstream_certificate = {"kind": "test-upstream"}
        packet = {
            "status": "certified_multi",
            "sourceSubmissionId": "sub-upstream",
            "sourcePolynomialIndex": 2,
            "sourceLabel": "24T20",
            "sourceR": 8,
            "sourceCoefficientSha256": "b" * 64,
            "sourceFieldDiscriminantAbs": "23",
            "sourceCertificate": upstream_certificate,
            "candidates": packet_candidates,
            "orbitTargets": slots,
            "orbitCertificate": {
                "actualDegrees": [24, 24, 228],
                "expectedDegrees": [24, 24, 228],
                "exponents": [1, 1, 1],
            },
            "orbitMap": {
                "path": str(orbit_path),
                "sha256": digest_path(orbit_path),
                "exactCertificateSha256": exact_certificate,
            },
            "networkCalls": 0,
            "ledgerWrites": 0,
            "submissionCalls": 0,
        }
        candidates_path = self.root / "candidates.json"
        write_json(candidates_path, packet)
        candidates_hash = digest_path(candidates_path)

        assignments = [
            {
                "factorIndex": factor_indexes[index],
                "coefficientSha256": hashes[index],
                "targetLabel": slots[index]["targetLabel"],
                "targetT": slots[index]["targetT"],
                "targetR": packet_candidates[index]["targetR"],
            }
            for index in range(2)
        ]
        frobenius = {
            "method": "exact-unramified-frobenius-cycle-type-exclusion-v1",
            "input": str(candidates_path),
            "inputSha256": candidates_hash,
            "selectedInputRowsSha256": candidates_hash,
            "summary": {
                "rows": 1,
                "resolved": 1,
                "unresolved": 0,
                "contradiction": 0,
            },
            "rows": [
                {
                    "status": "resolved",
                    "sourceSubmissionId": "sub-upstream",
                    "sourcePolynomialIndex": 2,
                    "sourceLabel": "24T20",
                    "sourceR": 8,
                    "assignments": assignments,
                }
            ],
        }
        frobenius_path = self.root / "frobenius.json"
        write_json(frobenius_path, frobenius)

        manifest_path = self.root / "manifest.txt"
        manifest_path.write_text(lines[0] + "\n", encoding="utf-8")
        novelty = {
            "baselinePairRows": 0,
            "ownedExactTargetPairRows": 0,
            "coefficientHashRows": 0,
            "sameTargetFieldDiscriminantRows": 0,
            "existingOutboxHashRows": 0,
            "committedReceiptHashRows": 0,
            "existingExactStagePairRows": 0,
        }
        stage = {
            "certificateVersion": "staged-queued-source-pair-frobenius-v1",
            "status": "staged_exact",
            "routeGateMode": route_mode,
            "batchSize": 1,
            "manifest": {
                "path": str(manifest_path),
                "sha256": digest_path(manifest_path),
            },
            "inputs": {
                "candidates": {
                    "path": str(candidates_path),
                    "sha256": candidates_hash,
                },
                "frobenius": {
                    "path": str(frobenius_path),
                    "sha256": digest_path(frobenius_path),
                },
                "orbitMap": {
                    "path": str(orbit_path),
                    "sha256": digest_path(orbit_path),
                },
            },
            "candidates": [
                {
                    "candidate": packet_candidates[0],
                    "target": {
                        "label": "24T30",
                        "r": 4,
                        "t": 30,
                        "teamCountAtStage": 0,
                    },
                    "frobeniusAssignment": {
                        **assignments[0],
                        "coefficientLine": lines[0],
                    },
                    "source": {
                        "submissionId": "sub-upstream",
                        "polynomialIndex": 2,
                        "label": "24T20",
                        "r": 8,
                        "coefficientSha256": "b" * 64,
                        "fieldDiscriminantAbs": "23",
                        "provenance": upstream_certificate,
                    },
                    "noveltyAudit": novelty,
                    "pairCensusCertificateSha256": exact_certificate,
                }
            ],
            "networkCalls": 0,
            "ledgerWrites": 0,
            "submissionCalls": 0,
        }
        stage_path = self.root / "stage.json"
        write_json(stage_path, stage)
        return manifest_path, stage_path, hashes[0], []

    def validate(self, manifest: Path, stage: Path, digest: str, seen: list[dict]) -> None:
        validate_derived_stage_source(
            manifest,
            stage,
            0,
            "24T30",
            4,
            digest,
            seen.append,
        )

    def test_exact_resolved_mode_accepts_ambiguous_but_safe_mode_rejects(self) -> None:
        manifest, stage, digest, seen = self.build(
            route_mode="exact-resolved-signature", all_compatible=False
        )
        self.validate(manifest, stage, digest, seen)
        self.assertEqual(len(seen), 1)

        manifest, stage, digest, seen = self.build(
            route_mode="all-compatible-classes-gold", all_compatible=False
        )
        with self.assertRaisesRegex(ValueError, "route gate"):
            self.validate(manifest, stage, digest, seen)

    def test_exact_resolved_mode_still_requires_mapped_gold_intersection(self) -> None:
        manifest, stage, digest, seen = self.build(
            route_mode="exact-resolved-signature",
            all_compatible=False,
            mapped_r=(4,),
            gold_r=(8,),
        )
        with self.assertRaisesRegex(ValueError, "route gate"):
            self.validate(manifest, stage, digest, seen)

    def test_duplicate_packet_factor_indexes_fail_closed(self) -> None:
        manifest, stage, digest, seen = self.build(
            route_mode="exact-resolved-signature",
            all_compatible=False,
            duplicate_factor_index=True,
        )
        with self.assertRaisesRegex(ValueError, "factor indexes"):
            self.validate(manifest, stage, digest, seen)


if __name__ == "__main__":
    unittest.main()
