#!/usr/bin/env sage -python
"""Exact degree-9/10 source-group to index-24 action census.

This is an independent, action-first lane.  It enumerates every transitive
degree-9 and degree-10 group in GAP's transitive-group library, every
conjugacy class of core-free index-24 subgroups, and the exact induced
degree-24 coset action.  Identity and involution conjugacy classes give the
complete possible complex-conjugation signature map.

The complete structural signature union is intersected first with the raw
``targets(team_count=0, discovered=0)`` frontier and then with the actionable
nonbaseline/locally-unowned subset before any source generation or resolvent
arithmetic.  Raw hits that are already baseline pairs are recorded, but they
cannot authorize arithmetic.

No quartic-over-sextic pair norm and no saved F5/F6 route inventory is read.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

from sage.all import PolynomialRing, ZZ, libgap
from sage.version import version as SAGE_VERSION


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"

SOURCE_OUTPUT = DATA / "degree_9_10_index24_source_inventory_20260727.jsonl"
SOURCE_AUDIT_OUTPUT = DATA / "degree_9_10_index24_source_audit_20260727.json"
ACTION_OUTPUT = DATA / "degree_9_10_index24_actions_20260727.jsonl"
ROUTE_OUTPUT = DATA / "degree_9_10_index24_routes_20260727.jsonl"
SUMMARY_OUTPUT = DATA / "degree_9_10_index24_summary_20260727.json"

OUTPUTS = (
    SOURCE_OUTPUT,
    SOURCE_AUDIT_OUTPUT,
    ACTION_OUTPUT,
    ROUTE_OUTPUT,
    SUMMARY_OUTPUT,
)

GROUP_COUNTS = {9: 34, 10: 45}
POLYNOMIAL_KEYS = {
    "canonicalPolynomial",
    "coefficientLine",
    "fieldCanonicalPolynomial",
    "polynomial",
    "quotientLine",
}
GROUP_LABEL_RE = re.compile(r"^(9|10)T([1-9][0-9]*)$")
RING = PolynomialRing(ZZ, "x")


def canonical_json(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    temporary = Path(str(path) + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
    temporary.replace(path)


def write_json_atomic(path: Path, value: dict) -> None:
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def walk_dicts(value, pointer: str = "$"):
    if isinstance(value, dict):
        yield pointer, value
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                yield from walk_dicts(child, f"{pointer}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, (dict, list)):
                yield from walk_dicts(child, f"{pointer}[{index}]")


def parse_coefficient_line(value) -> tuple[int, ...] | None:
    if not isinstance(value, str):
        return None
    pieces = value.split(",")
    try:
        return tuple(int(piece) for piece in pieces)
    except ValueError:
        return None


def exact_group_certificate(row: dict) -> tuple[str, int, int] | None:
    group = row.get("galoisGroup")
    if isinstance(group, dict):
        label = str(group.get("label", ""))
        match = GROUP_LABEL_RE.fullmatch(label)
        if match:
            degree = int(match.group(1))
            if int(group.get("degree", degree)) != degree:
                raise ArithmeticError("inconsistent Galois-group degree certificate")
            order = int(group.get("order", 0))
            if order <= 0:
                return None
            return label, degree, order
    label = str(row.get("galoisGroupLabel", ""))
    match = GROUP_LABEL_RE.fullmatch(label)
    if match:
        degree = int(match.group(1))
        order = int(row.get("galoisGroupOrder", 0))
        if order > 0:
            return label, degree, order
    return None


def source_inventory_and_audit() -> tuple[list[dict], dict]:
    """Audit local JSON artifacts for strict degree-9/10 source certificates.

    This search is supplementary: the later structural zero is independent of
    whether any source coefficient record exists.  A source is admitted only
    when one record contains a monic exact-degree coefficient line, an exact
    transitive-group label and order, and an exact real-root count.
    """

    artifacts = sorted(
        path
        for path in DATA.rglob("*")
        if path.is_file()
        and path.suffix in {".json", ".jsonl"}
        and path not in OUTPUTS
    )
    manifest = []
    polynomial_like_fields = 0
    degree_band_polynomial_fields = 0
    exact_group_label_values = 0
    certified_records = []

    for path in artifacts:
        raw = path.read_bytes()
        manifest.append(
            {
                "bytes": len(raw),
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256_bytes(raw),
            }
        )
        text = raw.decode("utf-8")
        if path.suffix == ".json":
            documents = [(None, json.loads(text))]
        else:
            documents = [
                (line_number, json.loads(line))
                for line_number, line in enumerate(text.splitlines(), start=1)
                if line.strip()
            ]
        for line_number, document in documents:
            for pointer, row in walk_dicts(document):
                for value in row.values():
                    if (
                        isinstance(value, str)
                        and GROUP_LABEL_RE.fullmatch(value)
                    ):
                        exact_group_label_values += 1
                certificate = exact_group_certificate(row)
                for key in sorted(POLYNOMIAL_KEYS.intersection(row)):
                    coefficients = parse_coefficient_line(row[key])
                    if coefficients is None:
                        continue
                    polynomial_like_fields += 1
                    degree = len(coefficients) - 1
                    if degree not in GROUP_COUNTS:
                        continue
                    degree_band_polynomial_fields += 1
                    if certificate is None:
                        continue
                    label, certificate_degree, certificate_order = certificate
                    if certificate_degree != degree:
                        raise ArithmeticError(
                            "source coefficient degree and exact group disagree"
                        )
                    if "realRoots" not in row:
                        continue
                    real_roots = int(row["realRoots"])
                    status = str(row.get("status", ""))
                    nested_full_certificate = isinstance(
                        row.get("galoisGroup"), dict
                    ) and {
                        "degree",
                        "label",
                        "order",
                    } <= set(row["galoisGroup"])
                    if "certified" not in status and not nested_full_certificate:
                        continue
                    if coefficients[-1] != 1:
                        raise ArithmeticError("source candidate is not monic")
                    polynomial = RING(coefficients)
                    if not polynomial.is_irreducible():
                        raise ArithmeticError("source candidate is reducible")
                    transitive_t = int(label.split("T")[1])
                    exact_group = libgap.TransitiveGroup(degree, transitive_t)
                    if int(libgap.Size(exact_group)) != certificate_order:
                        raise ArithmeticError(
                            "source group order disagrees with GAP"
                        )
                    line = ",".join(str(value) for value in coefficients)
                    digest = sha256_bytes(line.encode("ascii"))
                    recorded_digest = row.get("coefficientSha256")
                    if recorded_digest is not None and str(recorded_digest) != digest:
                        raise ArithmeticError("source coefficient hash changed")
                    certified_records.append(
                        {
                            "certificateInput": str(path.relative_to(ROOT)),
                            "certificateJsonPointer": pointer,
                            "certificateLineNumber": line_number,
                            "coefficientBytes": len(line.encode("ascii")),
                            "coefficientLine": line,
                            "coefficientSha256": digest,
                            "degree": degree,
                            "galoisGroupLabel": label,
                            "galoisGroupOrder": certificate_order,
                            "realRoots": real_roots,
                            "status": "strict_local_degree9_10_source_certificate",
                        }
                    )

    by_hash = defaultdict(list)
    for record in certified_records:
        by_hash[record["coefficientSha256"]].append(record)
    sources = []
    for digest, records in sorted(by_hash.items()):
        coefficient_lines = {record["coefficientLine"] for record in records}
        labels = {record["galoisGroupLabel"] for record in records}
        real_roots = {record["realRoots"] for record in records}
        if len(coefficient_lines) != 1 or len(labels) != 1 or len(real_roots) != 1:
            raise ArithmeticError("duplicate source hash has inconsistent metadata")
        best = min(
            records,
            key=lambda row: (
                row["coefficientBytes"],
                row["certificateInput"],
                row["certificateLineNumber"] or 0,
                row["certificateJsonPointer"],
            ),
        )
        sources.append(
            {
                **{
                    key: value
                    for key, value in best.items()
                    if not key.startswith("certificate")
                },
                "certificateProvenance": [
                    {
                        "input": row["certificateInput"],
                        "jsonPointer": row["certificateJsonPointer"],
                        "lineNumber": row["certificateLineNumber"],
                    }
                    for row in records
                ],
                "coefficientSha256": digest,
            }
        )

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        ledger_degree_histogram = {
            str(commas): int(count)
            for commas, count in connection.execute(
                """
                SELECT
                  length(original_line)-length(replace(original_line, ',', ''))
                    AS commas,
                  count(*)
                FROM polynomials
                GROUP BY commas
                ORDER BY commas
                """
            )
        }
    finally:
        connection.close()

    audit = {
        "artifactFileCount": len(artifacts),
        "artifactManifestSha256": sha256_bytes(
            canonical_json(manifest).encode("utf-8")
        ),
        "certifiedDegree10SourceCount": sum(
            source["degree"] == 10 for source in sources
        ),
        "certifiedDegree9SourceCount": sum(
            source["degree"] == 9 for source in sources
        ),
        "degree9Or10PolynomialLikeFieldCount": degree_band_polynomial_fields,
        "exact9TOr10TLabelValueCount": exact_group_label_values,
        "ledgerOriginalLineDegreeHistogram": ledger_degree_histogram,
        "ledgerOriginalLineDegree9Or10Count": sum(
            ledger_degree_histogram.get(str(degree), 0)
            for degree in GROUP_COUNTS
        ),
        "polynomialLikeFieldCount": polynomial_like_fields,
        "sourceAdmissionRule": (
            "same JSON record has monic irreducible exact-degree coefficient "
            "line, exact 9T/10T label and order, exact realRoots, and a "
            "certified status or full nested Galois-group certificate"
        ),
        "sourceCount": len(sources),
        "status": (
            "certified_sources_found"
            if sources
            else "complete_no_certified_degree9_10_sources"
        ),
    }
    return sources, audit


def fixed_points(permutation, degree: int) -> int:
    return degree - int(libgap.NrMovedPoints(permutation))


def subgroup_identity(group_label: str, subgroup) -> str:
    payload = {
        "groupLabel": group_label,
        "smallGenerators": sorted(
            str(generator)
            for generator in libgap.SmallGeneratingSet(subgroup)
        ),
    }
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def target_frontiers() -> tuple[
    set[tuple[str, int]],
    set[tuple[str, int]],
    dict[tuple[str, int], dict],
    set[tuple[str, int]],
    dict,
]:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        raw_rows = list(
            connection.execute(
                """
                SELECT label,r,generated_at
                FROM targets
                WHERE team_count=0 AND discovered=0
                """
            )
        )
        raw = {
            (str(label), int(real_roots))
            for label, real_roots, _generated_at in raw_rows
        }
        actionable = {
            (str(label), int(real_roots))
            for label, real_roots in connection.execute(
                """
                SELECT t.label,t.r
                FROM targets AS t
                WHERE t.team_count=0
                  AND t.discovered=0
                  AND NOT EXISTS(
                    SELECT 1 FROM baseline_pairs AS b
                    WHERE b.label=t.label AND b.r=t.r
                  )
                  AND NOT EXISTS(
                    SELECT 1 FROM verifications AS v
                    WHERE v.label=t.label
                      AND v.r=t.r
                      AND v.scoreable=1
                  )
                """
            )
        }
        baseline = {
            (str(label), int(real_roots)): {
                "bestNfdiscAbs": str(best_nfdisc_abs),
                "sourceRows": int(source_rows),
            }
            for label, real_roots, best_nfdisc_abs, source_rows
            in connection.execute(
                """
                SELECT label,r,best_nfdisc_abs,source_rows
                FROM baseline_pairs
                """
            )
        }
        locally_scoreable = {
            (str(label), int(real_roots))
            for label, real_roots in connection.execute(
                """
                SELECT DISTINCT label,r
                FROM verifications
                WHERE scoreable=1
                """
            )
        }
        generated_at_values = sorted(
            {str(generated_at) for _, _, generated_at in raw_rows}
        )
        metadata = {
            "actionablePairCount": len(actionable),
            "rawGeneratedAtDistinctCount": len(generated_at_values),
            "rawGeneratedAtMaximum": max(generated_at_values),
            "rawGeneratedAtMinimum": min(generated_at_values),
            "rawPairCount": len(raw),
            "rawToActionableDifferenceCount": len(raw - actionable),
        }
        return raw, actionable, baseline, locally_scoreable, metadata
    finally:
        connection.close()


def action_census(
    sources_by_group: dict[str, list[dict]],
    raw_frontier: set[tuple[str, int]],
    actionable_frontier: set[tuple[str, int]],
) -> list[dict]:
    output = []
    for degree, group_count in GROUP_COUNTS.items():
        for transitive_t in range(1, group_count + 1):
            group_label = f"{degree}T{transitive_t}"
            group = libgap.TransitiveGroup(degree, transitive_t)
            group_order = int(libgap.Size(group))
            if group_order % 24:
                continue

            signature_classes = []
            for class_index, conjugacy_class in enumerate(
                libgap.ConjugacyClasses(group)
            ):
                representative = libgap.Representative(conjugacy_class)
                order = int(libgap.Order(representative))
                if order not in (1, 2):
                    continue
                signature_classes.append(
                    {
                        "classIndex": class_index,
                        "classSize": int(libgap.Size(conjugacy_class)),
                        "order": order,
                        "representative": representative,
                        "sourceR": fixed_points(representative, degree),
                    }
                )

            action_ordinal = 0
            for subgroup_class_index, subgroup_class in enumerate(
                libgap.ConjugacyClassesSubgroups(group)
            ):
                subgroup = libgap.Representative(subgroup_class)
                if int(libgap.Index(group, subgroup)) != 24:
                    continue
                core_order = int(libgap.Size(libgap.Core(group, subgroup)))
                if core_order != 1:
                    continue
                homomorphism = libgap.ActionHomomorphism(
                    group,
                    libgap.RightCosets(group, subgroup),
                    libgap.OnRight,
                )
                if int(libgap.Size(libgap.Kernel(homomorphism))) != 1:
                    raise ArithmeticError("core-free coset action is not faithful")
                action = libgap.Image(homomorphism)
                target_t = int(libgap.TransitiveIdentification(action))
                target_label = f"24T{target_t}"
                if int(libgap.Size(action)) != group_order:
                    raise ArithmeticError("faithful target action changed group order")

                signature_map = defaultdict(set)
                profiles = []
                for class_row in signature_classes:
                    target_r = fixed_points(
                        libgap.Image(
                            homomorphism,
                            class_row["representative"],
                        ),
                        24,
                    )
                    signature_map[class_row["sourceR"]].add(target_r)
                    profiles.append(
                        {
                            "classIndex": class_row["classIndex"],
                            "classSize": class_row["classSize"],
                            "order": class_row["order"],
                            "sourceR": class_row["sourceR"],
                            "targetR": target_r,
                        }
                    )

                structural_pairs = sorted(
                    {
                        (target_label, target_r)
                        for values in signature_map.values()
                        for target_r in values
                    }
                )
                raw_hits = sorted(
                    set(structural_pairs).intersection(raw_frontier)
                )
                actionable_hits = sorted(
                    set(structural_pairs).intersection(actionable_frontier)
                )
                output.append(
                    {
                        "actionOrdinalWithinSourceGroup": action_ordinal,
                        "availableCertifiedSourceCount": len(
                            sources_by_group.get(group_label, [])
                        ),
                        "availableCertifiedSourceHashes": sorted(
                            source["coefficientSha256"]
                            for source in sources_by_group.get(group_label, [])
                        ),
                        "coreOrder": core_order,
                        "degree": degree,
                        "groupLabel": group_label,
                        "groupOrder": group_order,
                        "index": 24,
                        "signatureMap": {
                            str(source_r): sorted(target_rs)
                            for source_r, target_rs in sorted(
                                signature_map.items()
                            )
                        },
                        "signatureProfiles": profiles,
                        "sourceT": transitive_t,
                        "actionableCurrentTc0Hits": [
                            {"label": label, "r": real_roots}
                            for label, real_roots in actionable_hits
                        ],
                        "rawTargetTc0Hits": [
                            {"label": label, "r": real_roots}
                            for label, real_roots in raw_hits
                        ],
                        "structuralPossiblePairs": [
                            {"label": label, "r": real_roots}
                            for label, real_roots in structural_pairs
                        ],
                        "subgroupClassIdentitySha256": subgroup_identity(
                            group_label, subgroup
                        ),
                        "subgroupClassIndex": subgroup_class_index,
                        "subgroupOrder": int(libgap.Size(subgroup)),
                        "targetLabel": target_label,
                        "targetOrder": int(libgap.Size(action)),
                        "targetT": target_t,
                    }
                )
                action_ordinal += 1
            if action_ordinal:
                print(
                    canonical_json(
                        {
                            "actionRowsSoFar": len(output),
                            "groupLabel": group_label,
                            "index24ActionRows": action_ordinal,
                            "phase": "exact-action-census",
                        }
                    ),
                    flush=True,
                )
    return output


def main() -> int:
    started = time.monotonic()
    for path in OUTPUTS:
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")

    ledger_stat_before = DB.stat()
    sources, source_audit = source_inventory_and_audit()
    sources_by_group = defaultdict(list)
    for source in sources:
        sources_by_group[source["galoisGroupLabel"]].append(source)

    (
        raw_frontier,
        actionable_frontier,
        baseline_pairs,
        locally_scoreable_pairs,
        frontier_metadata,
    ) = target_frontiers()
    actions = action_census(
        sources_by_group,
        raw_frontier,
        actionable_frontier,
    )
    if len(actions) != 36:
        raise ArithmeticError(
            f"degree-9/10 structural action count changed: {len(actions)}"
        )

    structural_pairs = {
        (pair["label"], int(pair["r"]))
        for action in actions
        for pair in action["structuralPossiblePairs"]
    }
    raw_structural_hits = structural_pairs.intersection(raw_frontier)
    actionable_structural_hits = structural_pairs.intersection(
        actionable_frontier
    )

    # An actionable structural hit would authorize a separate, fully certified
    # arithmetic construction.  The only raw hits in this snapshot are
    # baseline pairs, so no route is promoted and no resolvent arithmetic is
    # called.
    routes = []
    for action in actions:
        for source in sources_by_group.get(action["groupLabel"], []):
            possible_target_rs = [
                int(value)
                for value in action["signatureMap"].get(
                    str(source["realRoots"]), []
                )
            ]
            actionable_hits = sorted(
                {
                    (action["targetLabel"], target_r)
                    for target_r in possible_target_rs
                }.intersection(actionable_frontier)
            )
            if actionable_hits:
                raise ArithmeticError(
                    "live coefficient-backed route appeared; arithmetic "
                    "certificate is required before promotion"
                )

    if actionable_structural_hits:
        raise ArithmeticError(
            "actionable structural tc0 intersection appeared; arithmetic "
            "construction is required before this family can be closed"
        )

    raw_hit_witnesses = []
    for label, real_roots in sorted(raw_structural_hits):
        is_baseline = (label, real_roots) in baseline_pairs
        is_locally_scoreable = (
            label,
            real_roots,
        ) in locally_scoreable_pairs
        if not is_baseline and not is_locally_scoreable:
            raise ArithmeticError(
                "raw structural hit lacks a novelty-exclusion witness"
            )
        raw_hit_witnesses.append(
            {
                "baseline": is_baseline,
                "baselineBestNfdiscAbs": (
                    baseline_pairs[(label, real_roots)]["bestNfdiscAbs"]
                    if is_baseline
                    else None
                ),
                "baselineSourceRows": (
                    baseline_pairs[(label, real_roots)]["sourceRows"]
                    if is_baseline
                    else 0
                ),
                "label": label,
                "locallyScoreable": is_locally_scoreable,
                "r": real_roots,
            }
        )

    ledger_stat_after = DB.stat()
    if (
        ledger_stat_after.st_size != ledger_stat_before.st_size
        or ledger_stat_after.st_mtime_ns != ledger_stat_before.st_mtime_ns
    ):
        raise ArithmeticError("ledger changed during the bounded measurement")
    ledger_sha256 = sha256_path(DB)

    action_group_labels = sorted(
        {action["groupLabel"] for action in actions},
        key=lambda label: (
            int(label.split("T")[0]),
            int(label.split("T")[1]),
        ),
    )
    target_labels = sorted(
        {action["targetLabel"] for action in actions},
        key=lambda label: int(label[3:]),
    )
    summary = {
        "actionRows": len(actions),
        "approachFamily": (
            "exact low-degree Galois source group, core-free index-24 "
            "subgroup, and degree-24 coset action"
        ),
        "actionableCurrentTc0PairCount": len(actionable_frontier),
        "actionableStructuralHitCount": len(actionable_structural_hits),
        "actionableStructuralHits": [
            {"label": label, "r": real_roots}
            for label, real_roots in sorted(actionable_structural_hits)
        ],
        "arithmeticConstructionAuthorized": False,
        "availableCoefficientBackedActionRows": sum(
            bool(action["availableCertifiedSourceCount"]) for action in actions
        ),
        "certifiedDegree10SourceCount": sum(
            source["degree"] == 10 for source in sources
        ),
        "certifiedDegree9SourceCount": sum(
            source["degree"] == 9 for source in sources
        ),
        "actionableCurrentTc0Definition": (
            "targets.team_count=0 AND discovered=0 AND nonbaseline AND "
            "locally unowned by any scoreable verification"
        ),
        "degree10ActionRows": sum(
            action["degree"] == 10 for action in actions
        ),
        "degree10SourceGroupsWithActions": [
            label for label in action_group_labels if label.startswith("10T")
        ],
        "degree9ActionRows": sum(
            action["degree"] == 9 for action in actions
        ),
        "degree9SourceGroupsWithActions": [
            label for label in action_group_labels if label.startswith("9T")
        ],
        "distinctSourceGroupsWithActions": len(action_group_labels),
        "distinctTargetLabels": len(target_labels),
        "distinctTargetLabelsList": target_labels,
        "exactComplexConjugationClassCoverage": (
            "identity plus every involution conjugacy class in each exact "
            "source group"
        ),
        "gapVersion": str(libgap.eval("GAPInfo.Version")),
        "heavyArithmeticCalls": 0,
        "inputSha256": {
            str(DB.relative_to(ROOT)): ledger_sha256,
        },
        "networkCalls": 0,
        "novelCoefficientBackedCurrentTc0PairCount": 0,
        "rawTargetTc0Definition": (
            "targets.team_count=0 AND discovered=0"
        ),
        "rawTargetTc0PairCount": len(raw_frontier),
        "rawTargetTc0StructuralHitCount": len(raw_structural_hits),
        "rawTargetTc0StructuralHitWitnesses": raw_hit_witnesses,
        "rawTargetFrontierMetadata": frontier_metadata,
        "resolventCalls": 0,
        "routeCount": len(routes),
        "sageVersion": SAGE_VERSION,
        "sourceAudit": str(SOURCE_AUDIT_OUTPUT.relative_to(ROOT)),
        "sourceInventory": str(SOURCE_OUTPUT.relative_to(ROOT)),
        "status": "blocked_zero_novel_actionable_current_tc0_intersection",
        "structuralObstruction": (
            "The exact complex-conjugation images of all 36 core-free "
            "index-24 degree-9/10 actions have three raw target-frontier "
            "hits, but every hit is already a baseline pair. The actionable "
            "nonbaseline/locally-unowned intersection is empty, so no saved "
            "or generated degree-9/10 source can yield a novel current pair "
            "through this action family."
        ),
        "structuralPossiblePairCount": len(structural_pairs),
        "submissionCalls": 0,
        "targetLabelCount": len(target_labels),
        "boundedDegrees": [9, 10],
        "excludedDegrees": [11, 12],
        "excludedMechanisms": [
            "quartic-over-sextic pair norm",
            "saved F5/F6 routes",
        ],
        "runtimeSeconds": time.monotonic() - started,
    }

    write_jsonl_atomic(SOURCE_OUTPUT, sources)
    write_json_atomic(SOURCE_AUDIT_OUTPUT, source_audit)
    write_jsonl_atomic(ACTION_OUTPUT, actions)
    write_jsonl_atomic(ROUTE_OUTPUT, routes)
    write_json_atomic(SUMMARY_OUTPUT, summary)
    print(canonical_json(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
