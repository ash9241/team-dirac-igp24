#!/usr/bin/env sage -python
"""Assign recursive F6 degree-24 factors to exact GAP actions by Frobenius."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import sqlite3
from pathlib import Path

from sage.all import GF, PolynomialRing, ZZ, prime_range


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CAMPAIGN = DATA / "campaign_20260727_f627"
DB = DATA / "ledger.sqlite3"
CANDIDATES = CAMPAIGN / "f6_recursive_16948_16949_candidates.jsonl"
CHARACTERS = CAMPAIGN / "f6_recursive_16948_16949_character_audit.json"
CENSUS = CAMPAIGN / "f6_recursive_16948_16949_census.jsonl"
OUTPUT = CAMPAIGN / "f6_recursive_16948_16949_factor_assignment.json"
MANIFEST = CAMPAIGN / "f6_recursive_16948_16949_exact_tc0.txt"


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_text(value: str) -> str:
    return sha_bytes(value.encode())


def atomic_new(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    if path.exists() or temporary.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def factor_type(polynomial, prime: int) -> tuple[int, ...] | None:
    reduced = polynomial.change_ring(GF(prime))
    if reduced.degree() != polynomial.degree():
        return None
    if reduced.gcd(reduced.derivative()).degree() != 0:
        return None
    return tuple(
        sorted(
            int(factor.degree())
            for factor, exponent in reduced.factor()
            for _ in range(int(exponent))
        )
    )


def live_gate(connection, label: str, r: int) -> dict:
    target = connection.execute(
        "SELECT team_count,discovered,generated_at FROM targets WHERE label=? AND r=?",
        (label, r),
    ).fetchone()
    baseline = int(
        connection.execute(
            "SELECT COUNT(*) FROM baseline_pairs WHERE label=? AND r=?",
            (label, r),
        ).fetchone()[0]
    )
    owned = int(
        connection.execute(
            "SELECT COUNT(*) FROM verifications WHERE label=? AND r=? AND scoreable=1",
            (label, r),
        ).fetchone()[0]
    )
    current = bool(
        target is not None
        and int(target["team_count"]) == 0
        and int(target["discovered"]) == 0
        and baseline == 0
        and owned == 0
    )
    return {
        "baselineMatches": baseline,
        "currentTc0": current,
        "discovered": int(target["discovered"]) if target is not None else None,
        "generatedAt": str(target["generated_at"]) if target is not None else None,
        "ownedScoreableMatches": owned,
        "teamCount": int(target["team_count"]) if target is not None else None,
    }


def main() -> int:
    if OUTPUT.exists() or MANIFEST.exists():
        raise FileExistsError("refusing to overwrite recursive assignment outputs")
    candidate_rows = {
        str(row["sourceLabel"]): row
        for row in (
            json.loads(line)
            for line in CANDIDATES.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    audit = json.loads(CHARACTERS.read_text(encoding="utf-8"))
    audit_rows = {str(row["sourceLabel"]): row for row in audit["rows"]}
    census_rows = {
        str(row["sourceLabel"]): row
        for row in (
            json.loads(line)
            for line in CENSUS.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    if set(candidate_rows) != set(audit_rows) or set(candidate_rows) != set(census_rows):
        raise ValueError("candidate, character, and census source sets differ")

    ring = PolynomialRing(ZZ, "x")
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    assignments = []
    exact_hits = []
    for source_label in sorted(candidate_rows):
        packet = candidate_rows[source_label]
        character = audit_rows[source_label]
        census = census_rows[source_label]
        if packet["status"] != "certified_multi" or character["gassmannBlocked"]:
            raise ValueError("source packet is not dispatchable")
        source_line = str(packet["sourceCoefficientLine"])
        if sha_text(source_line) != str(packet["sourceCoefficientSha256"]):
            raise ValueError("source line hash mismatch")
        source = ring([ZZ(value) for value in source_line.split(",")])
        candidates = list(packet["candidates"])
        candidate_polynomials = [
            ring([ZZ(value) for value in row["coefficientLine"].split(",")])
            for row in candidates
        ]
        for row, polynomial in zip(candidates, candidate_polynomials):
            if sha_text(str(row["coefficientLine"])) != str(
                row["coefficientSha256"]
            ):
                raise ValueError("candidate line hash mismatch")
            if (
                polynomial.degree() != 24
                or not polynomial.is_monic()
                or not polynomial.is_irreducible()
                or int(polynomial.number_of_real_roots()) != int(row["r"])
            ):
                raise ValueError("candidate algebraic gates failed")

        slots = list(character["slots"])
        if len(slots) != 3 or len(candidates) != 3:
            raise ValueError("dispatcher requires exactly three slots and factors")
        class_rows = list(character["allClasses"])
        possible = set(itertools.permutations(range(3)))
        evidence = []
        for prime in prime_range(2, 100000):
            natural = factor_type(source, int(prime))
            if natural is None:
                continue
            candidate_types = [
                factor_type(polynomial, int(prime))
                for polynomial in candidate_polynomials
            ]
            if any(value is None for value in candidate_types):
                continue
            classes = [
                row
                for row in class_rows
                if tuple(int(value) for value in row["naturalCycleType"])
                == natural
            ]
            if not classes:
                raise ValueError("source Frobenius type absent from exact GAP group")
            surviving = {
                assignment
                for assignment in possible
                if any(
                    all(
                        tuple(
                            int(value)
                            for value in class_row["actionCycleTypes"][slot_position]
                        )
                        == candidate_types[assignment[slot_position]]
                        for slot_position in range(3)
                    )
                    for class_row in classes
                )
            }
            if not surviving:
                raise ValueError("Frobenius evidence eliminates every assignment")
            if len(surviving) < len(possible):
                evidence.append(
                    {
                        "candidateFactorTypes": [
                            list(value) for value in candidate_types
                        ],
                        "compatibleAssignmentCountAfter": len(surviving),
                        "compatibleAssignmentCountBefore": len(possible),
                        "compatibleClassIndexes": [
                            int(row["classIndex"]) for row in classes
                        ],
                        "naturalCycleType": list(natural),
                        "prime": int(prime),
                    }
                )
                possible = surviving
            if len(possible) == 1:
                break
        if len(possible) != 1:
            raise ValueError(
                f"factor assignment remains ambiguous for {source_label}: {len(possible)}"
            )
        unique = next(iter(possible))
        assigned = []
        gold_by_label = {
            str(route["targetLabel"]): {
                int(value) for value in route["goldR"]
            }
            for route in census["routes"]
        }
        for slot_position, candidate_index in enumerate(unique):
            slot = slots[slot_position]
            candidate = candidates[candidate_index]
            label = str(slot["targetLabel"])
            r = int(candidate["r"])
            gate = live_gate(connection, label, r)
            coefficient_hash = str(candidate["coefficientSha256"])
            duplicate_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
                    (coefficient_hash,),
                ).fetchone()[0]
            )
            exact_tc0 = bool(
                gate["currentTc0"]
                and r in gold_by_label.get(label, set())
                and duplicate_count == 0
            )
            assigned_row = {
                "candidate": candidate,
                "candidateIndex": candidate_index,
                "exactCurrentTc0": exact_tc0,
                "ledgerCoefficientOccurrences": duplicate_count,
                "liveGate": gate,
                "orbitIndex": int(slot["orbitIndex"]),
                "slotPosition": slot_position,
                "targetLabel": label,
                "targetR": r,
                "targetT": int(slot["targetT"]),
            }
            assigned.append(assigned_row)
            if exact_tc0:
                exact_hits.append(
                    {
                        **assigned_row,
                        "sourceLabel": source_label,
                        "sourceCoefficientSha256": str(
                            packet["sourceCoefficientSha256"]
                        ),
                    }
                )
        assignments.append(
            {
                "sourceLabel": source_label,
                "sourceR": 16,
                "assignmentSlotToCandidateIndex": list(unique),
                "evidence": evidence,
                "assignedFactors": assigned,
                "proof": (
                    "For each squarefree prime, the source factor degrees are "
                    "the natural Frobenius cycle type. Every GAP conjugacy class "
                    "with that natural type was retained; candidate-to-action "
                    "bijections incompatible with all such classes were removed. "
                    "The displayed prime intersection leaves one bijection."
                ),
            }
        )
    connection.close()

    manifest = "".join(
        str(row["candidate"]["coefficientLine"]) + "\n" for row in exact_hits
    )
    summary = {
        "schemaVersion": "f6-recursive-factor-assignment-v1",
        "assignments": assignments,
        "candidateArtifact": str(CANDIDATES.relative_to(ROOT)),
        "candidateArtifactSha256": sha_bytes(CANDIDATES.read_bytes()),
        "censusArtifact": str(CENSUS.relative_to(ROOT)),
        "censusArtifactSha256": sha_bytes(CENSUS.read_bytes()),
        "characterArtifact": str(CHARACTERS.relative_to(ROOT)),
        "characterArtifactSha256": sha_bytes(CHARACTERS.read_bytes()),
        "exactCurrentTc0Hits": exact_hits,
        "exactCurrentTc0HitCount": len(exact_hits),
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "manifestSha256": sha_text(manifest),
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    payload = (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode()
    atomic_new(OUTPUT, payload)
    atomic_new(MANIFEST, manifest.encode())
    print(
        json.dumps(
            {
                "assignment": str(OUTPUT.relative_to(ROOT)),
                "assignmentSha256": sha_bytes(payload),
                "exactCurrentTc0HitCount": len(exact_hits),
                "manifest": str(MANIFEST.relative_to(ROOT)),
                "manifestSha256": sha_text(manifest),
                "targets": [
                    f"{row['targetLabel']}/r{row['targetR']}"
                    for row in exact_hits
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
