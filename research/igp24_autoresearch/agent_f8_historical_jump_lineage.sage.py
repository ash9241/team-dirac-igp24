#!/usr/bin/env sage -python
"""Offline algebraic-lineage audit for the July 15--16 Team Dirac jump.

The audit deliberately classifies rows by exact polynomial and permutation-
group invariants.  HTML prose and timestamps are used only to delimit the
historical waves and reconcile their attributed score.  No network or
submission operation is performed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from sage.all import NumberField, PolynomialRing, QQ, RealField, ZZ, libgap, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
HTML = Path("/path/to/private-file")
FROZEN = DATA / "agent_f7_frozen_23018_live_pairs.jsonl"
LINEAGE = DATA / "agent_f8_historical_jump_lineage.jsonl"
PARENTS = DATA / "agent_f8_historical_parent_fields.jsonl"
INTERSECTIONS = DATA / "agent_f8_historical_live_intersections.jsonl"
UNIT_ROUTES = DATA / "agent_f8_historical_unit_signature_routes.jsonl"
SUMMARY = DATA / "agent_f8_historical_jump_summary.json"
MANIFEST = ROOT / "outbox" / "agent_f8_historical_jump_live.txt"


WAVES = [
    {
        "key": "nested_character_25",
        "submissionId": "sub_29d7d4882e464d7f84bf4c1f95a035f3",
        "createdAt": "2026-07-14T22:28:42Z",
        "rows": 25,
        "exact": 25,
        "points": 18.312,
    },
    {
        "key": "t00035_atlas_280",
        "submissionId": "sub_e81b35ac934e4d2fb9ab40168a3635fd",
        "createdAt": "2026-07-15T05:08:16Z",
        "rows": 280,
        "exact": 280,
        "points": 63.381,
    },
    {
        "key": "score700_bank_1000",
        "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
        "createdAt": "2026-07-15T16:07:12Z",
        "rows": 1000,
        "exact": 883,
        "points": 315.232,
    },
    {
        "key": "calibration_83",  # gitleaks:allow -- experiment name, not a credential
        "submissionId": "sub_b34d3ffa621c4200a0d1c7e0e690291d",
        "createdAt": "2026-07-15T18:48:05Z",
        "rows": 83,
        "exact": None,
        "points": 41.972483,
    },
]


CANDIDATE_AUDITS = {
    ("24T21879", 24): DATA / "agent_f8_candidate_24T21879_r24.json",
    ("24T21880", 24): DATA / "agent_f8_candidate_24T21880_r24.json",
    ("24T22404", 24): DATA / "agent_f8_candidate_24T22404_r24.json",
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> str:
    rendered = "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
    return hashlib.sha256(rendered.encode()).hexdigest()


def coefficient_line(polynomial) -> str:
    return ",".join(str(ZZ(value)) for value in polynomial.list())


def canonical_parent(polynomial, ring) -> tuple[str, str]:
    reduced = ring(pari(polynomial).polredabs())
    line = coefficient_line(reduced)
    return hashlib.sha256(line.encode()).hexdigest(), line


def exact_group_profile(label: str) -> dict:
    group = libgap.TransitiveGroup(24, int(label[3:]))
    partitions = {}
    for block in libgap.AllBlocks(group):
        if int(libgap.Length(block)) != 2:
            continue
        blocks = libgap.Orbit(group, block, libgap.OnSets)
        partition = tuple(
            sorted(tuple(sorted(int(value) for value in item)) for item in blocks)
        )
        if partition in partitions:
            continue
        action = libgap.ActionHomomorphism(group, blocks, libgap.OnSets)
        quotient = libgap.Image(action)
        kernel_order = int(libgap.Size(libgap.Kernel(action)))
        if kernel_order <= 0 or kernel_order & (kernel_order - 1):
            raise ValueError(f"non-2-power two-block kernel for {label}")
        partitions[partition] = (
            int(libgap.TransitiveIdentification(quotient)),
            int(libgap.Size(quotient)),
            kernel_order,
        )
    profiles = sorted(set(partitions.values()))
    if len(profiles) != 1:
        raise ValueError(f"ambiguous two-block structural profile for {label}: {profiles}")
    quotient_t, quotient_order, kernel_order = profiles[0]
    signatures = set()
    for conjugacy_class in libgap.ConjugacyClasses(group):
        representative = libgap.Representative(conjugacy_class)
        if int(libgap.Order(representative)) in (1, 2):
            signatures.add(24 - int(libgap.NrMovedPoints(representative)))
    return {
        "groupOrder": int(libgap.Size(group)),
        "kernelOrder": kernel_order,
        "kummerRank": kernel_order.bit_length() - 1,
        "possibleSignatures": sorted(signatures),
        "quotientOrder": quotient_order,
        "quotientT12": quotient_t,
        "twoBlockSystemCount": len(partitions),
    }


def unit_reachable_signatures(quotient) -> tuple[list[int], int, int]:
    field = NumberField(quotient.change_ring(QQ), "u")
    embeddings = field.embeddings(RealField(160))
    units = list(field.unit_group(proof=False).gens_values())
    generator_mask = sum(
        1 << index
        for index, embedding in enumerate(embeddings)
        if embedding(field.gen()) < 0
    )
    columns = []
    for unit in units:
        columns.append(
            (
                sum(
                    1 << index
                    for index, embedding in enumerate(embeddings)
                    if embedding(unit) < 0
                ),
                int(unit.norm() < 0),
            )
        )
    states = {(0, 0)}
    for sign_mask, norm_sign in columns:
        states |= {
            (state_mask ^ sign_mask, state_norm ^ norm_sign)
            for state_mask, state_norm in tuple(states)
        }
    real_count = len(embeddings)
    reachable = sorted(
        {
            2 * (real_count - (generator_mask ^ sign_mask).bit_count())
            for sign_mask, norm_sign in states
            if norm_sign == 0
        }
    )
    return reachable, len(units), len(states)


def summarize_candidate_audit(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "status": "missing"}
    payload = json.loads(path.read_text())
    candidates = payload.get("search", {}).get("candidateResults", [])
    certified = [
        row for row in candidates if str(row.get("status", "")).startswith("certified_")
    ]
    missing_maximals = Counter()
    for row in candidates:
        certificate = row.get("maximalSubgroupCertificate", {})
        for maximal in certificate.get("properTransitiveMaximals", []):
            if maximal.get("witness") is None:
                missing_maximals[str(maximal.get("label"))] += 1
    return {
        "candidateRows": len(candidates),
        "certifiedRows": len(certified),
        "missingMaximalHistogram": dict(sorted(missing_maximals.items())),
        "path": str(path.resolve()),
        "sha256": sha256_path(path),
        "status": "exact_candidate" if certified else "no_full_group_certificate",
    }


def main() -> int:
    if not HTML.exists() or not FROZEN.exists():
        raise ValueError("historical HTML or frozen live-pair artifact is missing")
    frozen_rows = jsonl(FROZEN)
    if len(frozen_rows) != 23018:
        raise ValueError(f"expected frozen 23,018 pairs, found {len(frozen_rows)}")
    frozen = {(str(row["label"]), int(row["r"])): row for row in frozen_rows}
    wave_by_submission = {wave["submissionId"]: wave for wave in WAVES}
    ring = PolynomialRing(QQ, "x")

    with sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        placeholders = ",".join("?" for _wave in WAVES)
        rows = list(
            connection.execute(
                f"""
                SELECT p.submission_id,p.polynomial_index,p.coefficients,
                       p.coefficient_hash,v.label,v.r,v.field_disc_abs,
                       v.status,v.scoreable,s.created_at
                FROM polynomials AS p
                JOIN verifications AS v USING(submission_id,polynomial_index)
                JOIN submissions AS s USING(submission_id)
                WHERE p.submission_id IN ({placeholders})
                ORDER BY s.created_at,p.polynomial_index
                """,
                tuple(wave_by_submission),
            )
        )

    if len(rows) != sum(wave["rows"] for wave in WAVES):
        raise ValueError("historical wave row count mismatch")
    labels = sorted({str(row[4]) for row in rows}, key=lambda value: int(value[3:]))
    profiles = {label: exact_group_profile(label) for label in labels}

    lineage = []
    parent_aggregate = defaultdict(
        lambda: {
            "denseRows": 0,
            "evenRows": 0,
            "labels": set(),
            "kummerRanks": set(),
            "rows": 0,
            "signatures": set(),
            "submissions": set(),
        }
    )
    wave_counts = defaultdict(Counter)
    even_source_rows = []
    dense_subfield_counts = Counter()
    for row in rows:
        submission_id, index, coefficients, coeff_hash, label, r = row[:6]
        field_disc, status, scoreable, created_at = row[6:]
        wave = wave_by_submission[str(submission_id)]
        if str(created_at) != wave["createdAt"]:
            raise ValueError(f"created-at mismatch for {wave['key']}")
        if str(status) != "accepted" or int(scoreable) != 1:
            raise ValueError("historical row is not accepted and scoreable")
        values = [ZZ(value) for value in str(coefficients).split(",")]
        if len(values) != 25 or values[-1] != 1:
            raise ValueError("non-monic degree-24 historical row")
        polynomial = ring(values)
        even = all(values[position] == 0 for position in range(1, 25, 2))
        centered_even = even
        parent_rows = []
        quotient = None
        norm_core = None
        if even:
            quotient = ring(values[::2])
            if not quotient.is_irreducible():
                raise ValueError("even historical quotient is reducible")
            parent_hash, parent_line = canonical_parent(quotient, ring)
            parent_rows.append((parent_hash, parent_line, quotient.number_of_real_roots()))
            norm_core = int(ZZ(quotient[0]).squarefree_part())
            transform = "exact_trace_zero_q_of_x2"
            even_source_rows.append((row, quotient))
        else:
            center = -QQ(polynomial[23]) / 24
            shifted = polynomial(ring.gen() + center)
            centered_even = all(shifted[position] == 0 for position in range(1, 25, 2))
            if centered_even:
                raise ValueError("unexpected affine-even dense historical row")
            field = NumberField(polynomial, "a")
            subfields = field.subfields(12)
            dense_subfield_counts[len(subfields)] += 1
            if not subfields:
                raise ValueError("dense historical field has no degree-12 parent")
            for subfield, _embedding, _reverse in subfields:
                parent_hash, parent_line = canonical_parent(subfield.polynomial(), ring)
                parent_rows.append(
                    (parent_hash, parent_line, subfield.polynomial().number_of_real_roots())
                )
            transform = "nonlinear_primitive_element_of_quadratic_over_degree12"
        parent_hashes = sorted({item[0] for item in parent_rows})
        profile = profiles[str(label)]
        mechanism = (
            "full_rank11_norm_character_kummer"
            if profile["kummerRank"] == 11
            else "proper_lower_rank_kummer_span_recovery"
        )
        live_signatures = sorted(
            target_r
            for target_r in profile["possibleSignatures"]
            if (str(label), target_r) in frozen
        )
        item = {
            "baseRealRootCounts": sorted({int(value[2]) for value in parent_rows}),
            "centeredEvenAfterRationalTranslation": bool(centered_even),
            "coefficientSha256": str(coeff_hash),
            "fieldDiscAbs": str(field_disc) if field_disc else None,
            "label": str(label),
            "liveGroupFeasibleSignatures": live_signatures,
            "mechanism": mechanism,
            "normCore": norm_core,
            "parentFieldSha256": parent_hashes,
            "polynomialIndex": int(index),
            "profile": profile,
            "r": int(r),
            "submissionId": str(submission_id),
            "transform": transform,
            "wave": wave["key"],
        }
        lineage.append(item)
        wave_counts[wave["key"]][mechanism] += 1
        wave_counts[wave["key"]]["dense" if not even else "even"] += 1
        for parent_hash, parent_line, parent_real_roots in parent_rows:
            aggregate = parent_aggregate[parent_hash]
            aggregate["canonicalPolynomial"] = parent_line
            aggregate["denseRows"] += int(not even)
            aggregate["evenRows"] += int(even)
            aggregate["labels"].add(str(label))
            aggregate["kummerRanks"].add(int(profile["kummerRank"]))
            aggregate["realRootCounts"] = aggregate.get("realRootCounts", set())
            aggregate["realRootCounts"].add(int(parent_real_roots))
            aggregate["rows"] += 1
            aggregate["signatures"].add(int(r))
            aggregate["submissions"].add(str(submission_id))

    parent_rows = []
    for parent_hash, aggregate in sorted(parent_aggregate.items()):
        parent_rows.append(
            {
                "canonicalPolynomial": aggregate["canonicalPolynomial"],
                "denseRows": aggregate["denseRows"],
                "evenRows": aggregate["evenRows"],
                "labels": sorted(aggregate["labels"], key=lambda value: int(value[3:])),
                "kummerRanks": sorted(aggregate["kummerRanks"]),
                "parentFieldSha256": parent_hash,
                "realRootCounts": sorted(aggregate["realRootCounts"]),
                "rows": aggregate["rows"],
                "signatures": sorted(aggregate["signatures"]),
                "submissions": sorted(aggregate["submissions"]),
            }
        )

    historical_by_label = defaultdict(list)
    for item in lineage:
        historical_by_label[item["label"]].append(item)
    intersection_rows = []
    for label in labels:
        source_items = historical_by_label[label]
        profile = profiles[label]
        parent_hashes = sorted(
            {value for item in source_items for value in item["parentFieldSha256"]}
        )
        for target_r in profile["possibleSignatures"]:
            target = frozen.get((label, target_r))
            if target is None:
                continue
            intersection_rows.append(
                {
                    "executableFromRecoveredCoefficients": False,
                    "historicalParentFieldSha256": parent_hashes,
                    "historicalRows": len(source_items),
                    "historicalSignatures": sorted({item["r"] for item in source_items}),
                    "mechanism": source_items[0]["mechanism"],
                    "pair": f"{label}/r{target_r}",
                    "profile": profile,
                    "target": target,
                }
            )

    # Exact unit-sign reachability is meaningful only for the full rank-11
    # character mechanism.  Lower-rank conjugate-squareclass dependencies are
    # not preserved by arbitrary unit multiplication, so those intersections
    # remain structural rather than executable.
    route_sources = defaultdict(list)
    unit_source_audits = []
    for source_row, quotient in even_source_rows:
        submission_id, index, _coeffs, coeff_hash, label, source_r = source_row[:6]
        if profiles[str(label)]["kummerRank"] != 11:
            continue
        target_rs = {
            int(item["target"]["r"])
            for item in intersection_rows
            if item["pair"].startswith(f"{label}/")
        }
        if not target_rs:
            continue
        reachable, unit_generators, unit_states = unit_reachable_signatures(quotient)
        hits = sorted(target_rs.intersection(reachable))
        source_audit = {
            "coefficientSha256": str(coeff_hash),
            "label": str(label),
            "polynomialIndex": int(index),
            "reachableSignaturesByNormPositiveUnits": reachable,
            "sourceR": int(source_r),
            "submissionId": str(submission_id),
            "unitGeneratorCount": unit_generators,
            "unitSignNormStates": unit_states,
            "unitSignatureLiveIntersection": hits,
        }
        unit_source_audits.append(source_audit)
        for target_r in hits:
            route_sources[(str(label), target_r)].append(source_audit)

    unit_routes = []
    for pair, source_audits in sorted(route_sources.items()):
        candidate_audit = summarize_candidate_audit(CANDIDATE_AUDITS[pair])
        executable = candidate_audit.get("certifiedRows", 0) > 0
        unit_routes.append(
            {
                "candidateAudit": candidate_audit,
                "executableExactCandidate": executable,
                "mechanism": "historical_parent_same_core_s_unit_sign_walk",
                "pair": f"{pair[0]}/r{pair[1]}",
                "sources": source_audits,
                "status": (
                    "exact_candidate" if executable else "blocked_at_full_group_equality"
                ),
            }
        )
        for intersection in intersection_rows:
            if intersection["pair"] == f"{pair[0]}/r{pair[1]}":
                intersection["executableFromRecoveredCoefficients"] = executable
                intersection["unitSignRouteStatus"] = unit_routes[-1]["status"]

    lineage_sha = write_jsonl(LINEAGE, lineage)
    parents_sha = write_jsonl(PARENTS, parent_rows)
    intersections_sha = write_jsonl(INTERSECTIONS, intersection_rows)
    unit_routes_sha = write_jsonl(UNIT_ROUTES, unit_routes)
    MANIFEST.write_text("", encoding="utf-8")

    first_jump = 602.516590 - 183.072339
    controlled_character_points = 396.934
    score700_points = 315.232
    summary = {
        "artifacts": {
            "frozen": {"path": str(FROZEN.resolve()), "sha256": sha256_path(FROZEN)},
            "html": {"path": str(HTML.resolve()), "sha256": sha256_path(HTML)},
            "intersections": {"path": str(INTERSECTIONS.resolve()), "sha256": intersections_sha},
            "lineage": {"path": str(LINEAGE.resolve()), "sha256": lineage_sha},
            "manifest": {"path": str(MANIFEST.resolve()), "sha256": sha256_path(MANIFEST)},
            "parents": {"path": str(PARENTS.resolve()), "sha256": parents_sha},
            "unitRoutes": {"path": str(UNIT_ROUTES.resolve()), "sha256": unit_routes_sha},
        },
        "candidateOutcome": {
            "exactCandidates": sum(
                route["candidateAudit"].get("certifiedRows", 0) for route in unit_routes
            ),
            "manifestPolynomials": 0,
            "unitSignPairsBeforeFullGroupGate": len(unit_routes),
            "unitSignPairsBlockedAtFullGroupGate": sum(
                route["status"] == "blocked_at_full_group_equality" for route in unit_routes
            ),
        },
        "causalScoreReconciliation": {
            "characterControlledSharePct": 100 * controlled_character_points / first_jump,
            "controlledCharacterAttributedPoints": controlled_character_points,
            "firstJumpOfficialDelta": first_jump,
            "pairOrbitImmediatePointsExcludedFromF8": 269.806575,
            "residualUnattributedPoints": 22.510,
            "score700SharePct": 100 * score700_points / first_jump,
            "score700AttributedPoints": score700_points,
        },
        "denseParentRecovery": {
            "affineEvenDenseRows": sum(item["centeredEvenAfterRationalTranslation"] for item in lineage if item["transform"].startswith("nonlinear")),
            "denseRows": sum(item["transform"].startswith("nonlinear") for item in lineage),
            "degree12SubfieldCountHistogram": dict(sorted(dense_subfield_counts.items())),
            "interpretation": "dense models are nonlinear primitive-element presentations of quadratic extensions over a recovered degree-12 subfield, not a third generating mechanism",
        },
        "excludedMechanisms": [
            "generic rational twists",
            "unordered-pair resolvents",
            "pure cubic 8T42 augmentation",
            "repeated split-prime full-rank Kummer",
        ],
        "family": "F8_HISTORICAL_JUMP_LINEAGE",
        "frozenLivePairs": len(frozen_rows),
        "historicalCensus": {
            "distinctCoefficientHashes": len({item["coefficientSha256"] for item in lineage}),
            "distinctLabels": len(labels),
            "distinctParentFields": len(parent_rows),
            "distinctPairs": len({(item["label"], item["r"]) for item in lineage}),
            "kernelRankHistogram": dict(sorted(Counter(item["profile"]["kummerRank"] for item in lineage).items())),
            "rows": len(lineage),
            "transforms": dict(sorted(Counter(item["transform"] for item in lineage).items())),
            "waves": {
                wave["key"]: {
                    **dict(sorted(wave_counts[wave["key"]].items())),
                    "exactClaim": wave["exact"],
                    "coefficientHashSequenceSha256": hashlib.sha256(
                        "".join(
                            f"{item['coefficientSha256']}\n"
                            for item in lineage
                            if item["wave"] == wave["key"]
                        ).encode()
                    ).hexdigest(),
                    "pointsAttributed": wave["points"],
                    "rows": wave["rows"],
                }
                for wave in WAVES
            },
        },
        "liveIntersection": {
            "exactGroupFeasiblePairs": len(intersection_rows),
            "exactGroupFeasibleLabels": len({item["target"]["label"] for item in intersection_rows}),
            "lowerRankPairsNonExecutable": sum(
                item["mechanism"] == "proper_lower_rank_kummer_span_recovery"
                for item in intersection_rows
            ),
            "rank11Pairs": sum(
                item["mechanism"] == "full_rank11_norm_character_kummer"
                for item in intersection_rows
            ),
        },
        "mechanismConclusion": {
            "largeLeapCause": "mass exact norm-character Kummer construction over degree-12 parents, with half of the score700 verified rows landing in rank-11 character kernels and half in proper lower Kummer-span subgroups",
            "lowerRankRecovery": "structurally nonempty but non-executable from retained coefficients because the special conjugate-squareclass dependency recipe/provenance is absent",
            "unitSignWalk": "three live signatures are reachable at the real-place/norm level, but every generated candidate audit failed equality against one proper transitive maximal subgroup",
        },
        "networkCalls": 0,
        "status": "blocked_no_exact_executable_candidate",
        "submissionCalls": 0,
    }
    rendered = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    temporary = SUMMARY.with_suffix(SUMMARY.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(SUMMARY)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
