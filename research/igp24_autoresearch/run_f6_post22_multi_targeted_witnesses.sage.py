#!/usr/bin/env sage -python
"""Target separating GAP classes in five certified F6 source fields.

For each route, the source polynomial is scanned only at primes whose natural
cycle type matches the preferred separating conjugacy class.  A desired
factor is certified only if every source conjugacy class with that same
natural cycle type and every compatible factor/slot matching select the same
factor for the desired target action.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import sqlite3
import time
from pathlib import Path

from sage.all import GF, PolynomialRing, ZZ, next_prime


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
CAMPAIGN = DATA / "campaign_20260727_f627"
CANDIDATES = CAMPAIGN / "f6_post22_multi_wave_candidates.jsonl"
CHARACTERS = CAMPAIGN / "f6_post22_multi_permutation_character_audit.json"
CENSUS = CAMPAIGN / "f6_post22_pair_revival_census.jsonl"
HITS = CAMPAIGN / "f6_post22_multi_targeted_exact_hits.jsonl"
SUMMARY = CAMPAIGN / "f6_post22_multi_targeted_summary.json"
ROUTE_OUTPUTS = [
    CAMPAIGN / "f6_post22_multi_targeted_01_3698_r8.json",
    CAMPAIGN / "f6_post22_multi_targeted_02_3721_r8.json",
    CAMPAIGN / "f6_post22_multi_targeted_03_3818_r8.json",
    CAMPAIGN / "f6_post22_multi_targeted_04_8353_r0.json",
    CAMPAIGN / "f6_post22_multi_targeted_05_15342_r16.json",
]
SOURCE_PAIRS = [
    ("24T3698", 8),
    ("24T3721", 8),
    ("24T3818", 8),
    ("24T8353", 0),
    ("24T15342", 16),
]
GLOBAL_CAP_SECONDS = 540.0


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def atomic_new(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    if path.exists() or temporary.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def factor_pattern(coefficient_list: list[int], prime: int):
    ring = PolynomialRing(GF(prime), "x")
    factorization = list(ring(coefficient_list).factor())
    if any(int(exponent) != 1 for _, exponent in factorization):
        return None
    return tuple(
        sorted(
            int(factor.degree())
            for factor, exponent in factorization
            for _ in range(int(exponent))
        )
    )


def live_gate(connection: sqlite3.Connection, label: str, r: int, digest: str, line: str):
    target = connection.execute(
        "SELECT team_count,discovered,generated_at FROM targets WHERE label=? AND r=?",
        (label, r),
    ).fetchone()
    baseline = int(
        connection.execute(
            "SELECT COUNT(*) FROM baseline_pairs WHERE label=? AND r=?", (label, r)
        ).fetchone()[0]
    )
    owned = int(
        connection.execute(
            "SELECT COUNT(*) FROM verifications WHERE label=? AND r=? AND scoreable=1",
            (label, r),
        ).fetchone()[0]
    )
    hash_matches = int(
        connection.execute(
            "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?", (digest,)
        ).fetchone()[0]
    )
    line_matches = int(
        connection.execute(
            "SELECT COUNT(*) FROM polynomials WHERE coefficients=?", (line,)
        ).fetchone()[0]
    )
    current = bool(
        target is not None
        and int(target["team_count"]) == 0
        and int(target["discovered"]) == 0
        and baseline == 0
        and owned == 0
        and hash_matches == 0
        and line_matches == 0
    )
    return {
        "teamCount": int(target["team_count"]) if target else None,
        "discovered": int(target["discovered"]) if target else None,
        "generatedAt": str(target["generated_at"]) if target else None,
        "baselineMatches": baseline,
        "ownedScoreableMatches": owned,
        "ledgerCoefficientHashMatches": hash_matches,
        "ledgerCoefficientLineMatches": line_matches,
        "currentTc0AndNew": current,
    }


def main() -> int:
    started = time.monotonic()
    deadline = started + GLOBAL_CAP_SECONDS
    packets = {
        (str(row["sourceLabel"]), int(row["sourceR"])): row
        for row in read_jsonl(CANDIDATES)
    }
    audit = json.loads(CHARACTERS.read_text(encoding="utf-8"))
    audits = {
        (str(row["sourceLabel"]), int(row["sourceR"])): row
        for row in audit["rows"]
    }
    census = {str(row["sourceLabel"]): row for row in read_jsonl(CENSUS)}
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    route_summaries = []
    hits = []
    try:
        for ordinal, ((source_label, source_r), output_path) in enumerate(
            zip(SOURCE_PAIRS, ROUTE_OUTPUTS), 1
        ):
            if output_path.exists():
                route_summaries.append(
                    json.loads(output_path.read_text(encoding="utf-8"))
                )
                continue
            packet = packets[(source_label, source_r)]
            character = audits[(source_label, source_r)]
            preferred = character["separatingClasses"][0]
            natural = tuple(int(value) for value in preferred["naturalCycleType"])
            aliases = [
                row
                for row in character["allClasses"]
                if tuple(int(value) for value in row["naturalCycleType"]) == natural
            ]
            source = connection.execute(
                """
                SELECT p.coefficients,p.coefficient_hash,v.status,v.scoreable,v.label,v.r
                FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index)
                WHERE p.submission_id=? AND p.polynomial_index=?
                """,
                (
                    packet["sourceSubmissionId"],
                    int(packet["sourcePolynomialIndex"]),
                ),
            ).fetchone()
            if (
                source is None
                or str(source["coefficient_hash"])
                != str(packet["sourceCoefficientSha256"])
                or str(source["status"]) != "accepted"
                or int(source["scoreable"]) != 1
                or str(source["label"]) != source_label
                or int(source["r"]) != source_r
            ):
                raise ValueError(f"source pin failed for {source_label}/r{source_r}")
            source_coefficients = [
                int(value) for value in str(source["coefficients"]).split(",")
            ]
            factors = sorted(
                packet["candidates"], key=lambda row: int(row["factorIndex"])
            )
            factor_coefficients = [
                [int(value) for value in str(row["coefficientLine"]).split(",")]
                for row in factors
            ]
            desired_slot = int(character["desiredSlotPosition"])
            desired_label = str(character["desiredTargetLabel"])
            route = next(
                item
                for item in census[source_label]["routes"]
                if int(item["sourceR"]) == source_r
                and str(item["targetLabel"]) == desired_label
            )
            primes_scanned = 0
            target_type_matches = 0
            nonsquarefree_source = 0
            nonsquarefree_sibling = 0
            witness = None
            prime = 7
            while time.monotonic() < deadline:
                prime = int(next_prime(prime))
                primes_scanned += 1
                source_pattern = factor_pattern(source_coefficients, prime)
                if source_pattern is None:
                    nonsquarefree_source += 1
                    continue
                if source_pattern != natural:
                    continue
                target_type_matches += 1
                observed = [
                    factor_pattern(coefficients, prime)
                    for coefficients in factor_coefficients
                ]
                if any(pattern is None for pattern in observed):
                    nonsquarefree_sibling += 1
                    continue
                compatible = []
                for alias in aliases:
                    expected = [
                        tuple(int(value) for value in pattern)
                        for pattern in alias["actionCycleTypes"]
                    ]
                    for assignment in itertools.permutations(range(len(factors))):
                        # assignment[factor position] = action-slot position.
                        if all(
                            observed[factor_position]
                            == expected[assignment[factor_position]]
                            for factor_position in range(len(factors))
                        ):
                            compatible.append(
                                {
                                    "classIndex": int(alias["classIndex"]),
                                    "factorToSlot": list(assignment),
                                }
                            )
                desired_factor_positions = sorted(
                    {
                        assignment["factorToSlot"].index(desired_slot)
                        for assignment in compatible
                    }
                )
                if len(desired_factor_positions) == 1:
                    witness = {
                        "prime": prime,
                        "sourceNaturalCycleType": list(source_pattern),
                        "observedSiblingCycleTypes": [
                            list(pattern) for pattern in observed
                        ],
                        "compatibleClassAssignments": compatible,
                        "desiredFactorPositions": desired_factor_positions,
                        "desiredFactorIndex": int(
                            factors[desired_factor_positions[0]]["factorIndex"]
                        ),
                    }
                    break
            status = "resolved" if witness is not None else "deadline_unresolved"
            result = {
                "schemaVersion": "f6-post22-multi-targeted-witness-v1",
                "status": status,
                "sourceLabel": source_label,
                "sourceR": source_r,
                "sourceSubmissionId": str(packet["sourceSubmissionId"]),
                "sourcePolynomialIndex": int(packet["sourcePolynomialIndex"]),
                "sourceCoefficientSha256": str(
                    packet["sourceCoefficientSha256"]
                ),
                "desiredTargetLabel": desired_label,
                "desiredSlotPosition": desired_slot,
                "preferredSeparatingClass": preferred,
                "naturalCycleTypeAliasClassIndexes": [
                    int(row["classIndex"]) for row in aliases
                ],
                "uniqueNaturalCycleType": len(aliases) == 1,
                "search": {
                    "firstPrime": 11,
                    "lastPrime": prime,
                    "primesScanned": primes_scanned,
                    "targetCycleTypeMatches": target_type_matches,
                    "nonsquarefreeSourceSkipped": nonsquarefree_source,
                    "nonsquarefreeSiblingSkipped": nonsquarefree_sibling,
                    "globalCapSeconds": GLOBAL_CAP_SECONDS,
                },
                "witness": witness,
                "candidateInputSha256": sha256_file(CANDIDATES),
                "characterAuditSha256": sha256_file(CHARACTERS),
                "submissionCalls": 0,
                "networkCalls": 0,
            }
            if witness is not None:
                factor_position = witness["desiredFactorPositions"][0]
                factor = factors[factor_position]
                line = str(factor["coefficientLine"])
                digest = sha256_bytes(line.encode("ascii"))
                target_r = int(factor["targetR"])
                gate = live_gate(connection, desired_label, target_r, digest, line)
                result["resolvedFactor"] = {
                    "factorIndex": int(factor["factorIndex"]),
                    "coefficientSha256": digest,
                    "coefficientBytes": int(factor["coefficientBytes"]),
                    "targetLabel": desired_label,
                    "targetR": target_r,
                    "goldR": [int(value) for value in route["goldR"]],
                    "liveGate": gate,
                }
                exact_hit = (
                    target_r in {int(value) for value in route["goldR"]}
                    and gate["currentTc0AndNew"]
                )
                result["exactCurrentTc0"] = exact_hit
                if exact_hit:
                    hits.append(
                        {
                            **result["resolvedFactor"],
                            "coefficientLine": line,
                            "sourceLabel": source_label,
                            "sourceR": source_r,
                            "sourceSubmissionId": str(
                                packet["sourceSubmissionId"]
                            ),
                            "sourcePolynomialIndex": int(
                                packet["sourcePolynomialIndex"]
                            ),
                            "witnessCertificate": str(
                                output_path.relative_to(ROOT)
                            ),
                        }
                    )
            payload = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
            atomic_new(output_path, payload)
            route_summaries.append(result)
            print(
                canonical_json(
                    {
                        "event": "checkpoint",
                        "ordinal": ordinal,
                        "sourceLabel": source_label,
                        "status": status,
                        "exactCurrentTc0": bool(
                            result.get("exactCurrentTc0", False)
                        ),
                        "prime": witness["prime"] if witness else None,
                    }
                ),
                flush=True,
            )
    finally:
        connection.close()

    existing_hits = read_jsonl(HITS) if HITS.exists() else []
    by_hash = {
        str(row["coefficientSha256"]): row for row in existing_hits + hits
    }
    hit_payload = "".join(
        canonical_json(row) + "\n"
        for row in sorted(
            by_hash.values(),
            key=lambda row: (
                int(row["targetLabel"].removeprefix("24T")),
                int(row["targetR"]),
            ),
        )
    ).encode()
    if HITS.exists():
        if HITS.read_bytes() != hit_payload:
            raise ValueError("existing targeted hit checkpoint differs")
    else:
        atomic_new(HITS, hit_payload)
    summary = {
        "schemaVersion": "f6-post22-multi-targeted-summary-v1",
        "routes": len(route_summaries),
        "statusCounts": {
            status: sum(row["status"] == status for row in route_summaries)
            for status in sorted({row["status"] for row in route_summaries})
        },
        "exactCurrentTc0Hits": len(by_hash),
        "exactCurrentTc0Pairs": sorted(
            (row["targetLabel"], int(row["targetR"]))
            for row in by_hash.values()
        ),
        "hitCheckpoint": str(HITS.relative_to(ROOT)),
        "hitCheckpointSha256": sha256_file(HITS),
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "globalCapSeconds": GLOBAL_CAP_SECONDS,
        "submissionCalls": 0,
        "networkCalls": 0,
    }
    payload = (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode()
    atomic_new(SUMMARY, payload)
    print(canonical_json(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
