#!/usr/bin/env python3
"""Offline deterministic T00134 twist-route audit.

This worker joins the verified sole-holder/top-5,000/top-10,000 placement
snapshots to:

* accepted scoreable even degree-24 source polynomials and the exact cached
  global-pair-flip action map;
* the saved F5/F6 aligned scalar-twist closure census; and
* the certified simple-compositum pilot.

It applies the complete local baseline/verification/receipt/text-outbox pair
boundary and excludes every locally tested twist source presentation.  The
only arithmetic performed here is exact rational Sturm arithmetic for
negative signatures.  It has no network, submission, outbox, or ledger-write
path and emits no coefficients.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import audit_low_contention_pair_routes as pair_routes
import audit_low_contention_tc7_tc9_routes as outbox_audit
import audit_rank11_low_hanging_fruit_raid as raid
import run_low_contention_sequential as lane
import stage_single_exact_census as exact_single
from stage_v14_negative_twist import exact_real_root_count


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"

SOLE = DATA / "rank10_t00134_authenticated_nested_20260730_unique_placements.jsonl"
TOP5000 = DATA / "rank10_t00134_top5000_placements_20260730.jsonl"
TOP10000 = DATA / "rank10_t00134_top10000_placements_20260730.jsonl"
TOP5000_CHECKPOINT = DATA / "rank10_t00134_top5000_checkpoint_20260730.json"
TOP10000_CHECKPOINT = DATA / "rank10_t00134_top10000_checkpoint_20260730.json"
ACTION_MAP = DATA / "agent_gold_b_even_twist_action_map.jsonl"
SCALAR_ACTIONS = DATA / "scalar_twist_saved_f5_f6_action_closures_20260727.jsonl"
COMPOSITUM_PILOT = DATA / "agent_non12_simple_compositum_pilot.json"
RECOVERED_BANK = DATA / "agent_non12_recoverable_subfields.jsonl"

AUDIT = DATA / "fresh_t00134_twist_light_audit_20260730.json"
ROUTES = DATA / "fresh_t00134_twist_light_routes_20260730.jsonl"
HEAVY_TARGETS = DATA / "fresh_t00134_twist_heavy_targets_20260730.json"

TEAM_ID = "teamv2_32d618912a0e471fb418e886de946622"
TEAM_NUMBER = "IGP24-T00134"
TEAM_NAME = ""
EXPECTED_SHA256 = {
    SOLE: "ac822d37ab6888d75c305b8b5851f08b765d1a9507ac652c3537902561deb3d9",
    TOP5000: "9d3a2499c38f2d94fc145ca6b7e52dfe9a4cb8c4bf73935e6efa8476fdce555e",
    TOP10000: "a3819feb09ab2649a421d7e059b12a64df6dc92537a08211f4d06e055ae236c8",
    ACTION_MAP: "88c3b265f9b3ece23c9faf1cd5867289998efeecf29ba35d322edd4fa2f70e27",
    SCALAR_ACTIONS: "32d2a1e0e07cea52d615f4a73ca0eee05cbc565c53a39823c88f8469d66cf471",
    RECOVERED_BANK: "a92bd831e5b794e55e137fcdeadfc0bce0cce0cc843861aa0cf219346c54675b",
}


def canonical_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def exclusive_text(path: Path, value: str) -> None:
    """Create one artifact atomically, refusing every pre-existing path."""

    if path.exists():
        raise FileExistsError(f"refusing to overwrite sealed artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_pins() -> dict[str, str]:
    result = {}
    for path, expected in EXPECTED_SHA256.items():
        actual = sha256_path(path)
        if actual != expected:
            raise ValueError(
                f"input hash changed for {path.relative_to(ROOT)}: {actual} != {expected}"
            )
        result[str(path.relative_to(ROOT))] = actual
    for path in (
        TOP5000_CHECKPOINT,
        TOP10000_CHECKPOINT,
        COMPOSITUM_PILOT,
        DB,
    ):
        result[str(path.relative_to(ROOT))] = sha256_path(path)
    return result


def validate_checkpoint(path: Path, expected_rows: int) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        value.get("complete") is not True
        or int(value.get("pages", -1)) * 100 != expected_rows
        or int(value.get("pageLimit", -1)) * 100 != expected_rows
        or len(value.get("rows") or []) != expected_rows
        or str(value.get("teamId")) != TEAM_ID
        or str(value.get("teamNumber")) != TEAM_NUMBER
        or str(value.get("teamName")) != TEAM_NAME
    ):
        raise ValueError(f"incomplete or wrong-team checkpoint: {path}")
    return {
        "complete": True,
        "completedAt": str(value["completedAt"]),
        "pages": int(value["pages"]),
        "rows": len(value["rows"]),
        "sha256": sha256_path(path),
    }


def validate_placements(path: Path, expected_rows: int) -> list[dict]:
    rows = read_jsonl(path)
    if len(rows) != expected_rows:
        raise ValueError(f"{path} has {len(rows)} rows, expected {expected_rows}")
    seen = set()
    previous = math.inf
    fetched = set()
    for row in rows:
        if (
            str(row.get("teamId")) != TEAM_ID
            or str(row.get("teamNumber")) != TEAM_NUMBER
            or str(row.get("teamName")) != TEAM_NAME
        ):
            raise ValueError(f"wrong-team placement in {path}")
        pair = (str(row["label"]), int(row["r"]))
        if pair in seen:
            raise ValueError(f"duplicate placement {pair} in {path}")
        seen.add(pair)
        if (
            not pair[0].startswith("24T")
            or pair[1] < 0
            or pair[1] > 24
            or pair[1] % 2
        ):
            raise ValueError(f"invalid degree-24 pair {pair}")
        k_teams = int(row["kTeams"])
        points = float(row["points"])
        if (
            k_teams < 1
            or not math.isfinite(points)
            or points <= 0
            or points > previous + 1e-12
        ):
            raise ValueError(f"invalid or unordered placement {pair}")
        previous = points
        fetched.add(str(row["fetchedAt"]))
    if len(fetched) != 1:
        raise ValueError(f"mixed fetch boundary in {path}")
    return rows


def walk_values(value: object) -> Iterable[tuple[str, object]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_values(child)


def tested_twist_sources(single_rows: list[dict]) -> tuple[set[str], dict]:
    """Collect source presentations already exercised by twist/sign-flip work."""

    hashes: set[str] = set()
    files = []
    paths = set(DATA.glob("*twist*.jsonl"))
    paths.update(DATA.glob("*signflip*/**/results*.jsonl"))
    for path in sorted(paths):
        if path.resolve() == ROUTES.resolve() or not path.is_file():
            continue
        before = len(hashes)
        rows = read_jsonl(path)
        for row in rows:
            for key, value in walk_values(row):
                if key == "sourceCoefficientSha256" and value:
                    hashes.add(str(value))
        files.append(
            {
                "path": str(path.relative_to(ROOT)),
                "rows": len(rows),
                "newSourceHashes": len(hashes) - before,
                "sha256": sha256_path(path),
            }
        )

    exact_pin_hashes = set()
    exact_occurrences = 0
    for row in single_rows:
        marker = canonical_json(
            {
                "family": row.get("family"),
                "families": row.get("families"),
                "proof": row.get("proof"),
                "schema": row.get("schema"),
            }
        ).lower()
        if "twist" not in marker and "signflip" not in marker:
            continue
        exact_occurrences += 1
        for pin in row.get("sourcePins") or []:
            digest = pin.get("coefficientSha256")
            if digest:
                exact_pin_hashes.add(str(digest))
    hashes.update(exact_pin_hashes)
    return hashes, {
        "definition": (
            "every sourceCoefficientSha256 recursively present in local "
            "*twist*.jsonl and *signflip*/**/results*.jsonl artifacts, plus "
            "validated twist/signflip exact-corpus source pins"
        ),
        "files": files,
        "exactCorpusTwistOccurrences": exact_occurrences,
        "exactCorpusTwistSourcePins": len(exact_pin_hashes),
        "distinctTestedSourceHashes": len(hashes),
        "sourceHashSetSha256": canonical_digest(sorted(hashes)),
    }


def unanimous_target(action: dict) -> str | None:
    labels = {str(value) for value in action.get("targetLabels") or []}
    systems = action.get("systems") or []
    system_labels = {str(row.get("targetLabel")) for row in systems}
    if (
        int(action.get("systemCount", -1)) != len(systems)
        or len(labels) != 1
        or system_labels != labels
    ):
        return None
    return next(iter(labels))


def score_bounds(placement: dict) -> dict:
    """Conservative current-min-preserving estimate for a new team.

    The nominal value uses a candidate score of 2^-k and splits the current
    opponent points once more; the maximum lets the opponent's full current
    points be displaced.  These are prioritization estimates, not arithmetic
    candidate scores.
    """

    k_teams = int(placement["kTeams"])
    points = float(placement["points"])
    candidate_base = 2.0 ** (-k_teams)
    return {
        "kind": "current_min_preserving_structural_estimate",
        "nominalRelativeSwing": candidate_base + points / 2.0,
        "maximumRelativeSwingUpperBound": candidate_base + points,
    }


def gate_state(
    pair: tuple[str, int],
    placement: dict,
    snapshot: dict,
    receipt_pairs: set[tuple[str, int]],
    outbox_pairs: set[tuple[str, int]],
) -> dict:
    reasons = []
    if pair in snapshot["baseline"]:
        reasons.append("baseline_pair")
    if pair in snapshot["owned"]:
        reasons.append("accepted_scoreable_pair")
    if pair in snapshot["knownPairs"]:
        reasons.append("known_verification_pair")
    pre_reservation = not reasons
    if pair in receipt_pairs:
        reasons.append("receipt_reserved_pair")
    if pair in outbox_pairs:
        reasons.append("outbox_reserved_pair")
    return {
        "survivesLedgerPairBoundary": pre_reservation,
        "survivesAllPairExclusions": not reasons,
        "exclusionReasons": reasons,
        "placement": {
            "kTeams": int(placement["kTeams"]),
            "opponentPoints": float(placement["points"]),
            "scoringDiscAbs": str(placement.get("scoringDiscAbs")),
            "minScoringDiscAbs": str(placement.get("minScoringDiscAbs")),
        },
        "scoreBounds": score_bounds(placement),
    }


def summarize_routes(rows: list[dict], boundary: str) -> dict:
    present = [row for row in rows if boundary in row["boundaries"]]
    ledger = [
        row
        for row in present
        if row["boundaries"][boundary]["survivesLedgerPairBoundary"]
    ]
    final = [
        row
        for row in present
        if row["boundaries"][boundary]["survivesAllPairExclusions"]
    ]
    return {
        "routeOccurrencesBeforePairExclusions": len(present),
        "distinctTargetPairsBeforePairExclusions": len(
            {(row["targetLabel"], int(row["targetR"])) for row in present}
        ),
        "routeOccurrencesAfterLedgerPairBoundary": len(ledger),
        "distinctTargetPairsAfterLedgerPairBoundary": len(
            {(row["targetLabel"], int(row["targetR"])) for row in ledger}
        ),
        "routeOccurrencesAfterAllPairExclusions": len(final),
        "distinctTargetPairsAfterAllPairExclusions": len(
            {(row["targetLabel"], int(row["targetR"])) for row in final}
        ),
        "positiveOccurrences": sum(row["sign"] == "positive" for row in present),
        "negativeOccurrences": sum(row["sign"] == "negative" for row in present),
    }


def placement_boundary(
    rows: list[dict],
    snapshot: dict,
    receipt_pairs: set[tuple[str, int]],
    outbox_pairs: set[tuple[str, int]],
) -> dict:
    pairs = {(str(row["label"]), int(row["r"])) for row in rows}
    excluded_ledger = (
        snapshot["baseline"] | snapshot["owned"] | snapshot["knownPairs"]
    )
    final = pairs - excluded_ledger - receipt_pairs - outbox_pairs
    return {
        "pairs": pairs,
        "byPair": {(str(row["label"]), int(row["r"])): row for row in rows},
        "eligiblePairs": final,
        "summary": {
            "placementRows": len(rows),
            "distinctLabels": len({pair[0] for pair in pairs}),
            "kTeamDistribution": dict(
                sorted(Counter(int(row["kTeams"]) for row in rows).items())
            ),
            "pointSum": sum(float(row["points"]) for row in rows),
            "afterBaseline": len(pairs - snapshot["baseline"]),
            "afterOwned": len(pairs - snapshot["owned"]),
            "afterKnownVerification": len(pairs - snapshot["knownPairs"]),
            "afterLedgerPairUnion": len(pairs - excluded_ledger),
            "afterReceiptReservation": len(pairs - excluded_ledger - receipt_pairs),
            "afterAllPairExclusions": len(final),
            "eligiblePairSetSha256": canonical_digest(
                [[label, r] for label, r in sorted(final)]
            ),
        },
    }


def accepted_even_sources(
    connection: sqlite3.Connection,
    relevant_labels: set[str],
    tested_sources: set[str],
) -> tuple[list[dict], dict]:
    placeholders = ",".join("?" for _ in relevant_labels)
    query = f"""
        SELECT v.label,v.t,v.r,v.submission_id,v.polynomial_index,
               v.field_disc_abs,p.coefficients,p.coefficient_hash
        FROM verifications AS v
        JOIN polynomials AS p USING(submission_id,polynomial_index)
        WHERE v.status='accepted' AND v.scoreable=1
          AND v.label IN ({placeholders})
        ORDER BY v.t,v.r,p.coefficient_hash,v.submission_id,v.polynomial_index
    """
    by_hash: dict[str, dict] = {}
    relevant_occurrences = 0
    even_occurrences = 0
    noncanonical = 0
    tested = set()
    conflicts = set()
    for db_row in connection.execute(query, sorted(relevant_labels)):
        relevant_occurrences += 1
        row = dict(db_row)
        values = [int(value) for value in str(row["coefficients"]).split(",")]
        if (
            len(values) != 25
            or values[-1] != 1
            or values[0] == 0
            or math.gcd(*values) != 1
            or any(values[index] != 0 for index in range(1, 25, 2))
        ):
            noncanonical += 1
            continue
        even_occurrences += 1
        digest = str(row["coefficient_hash"])
        if digest in tested_sources:
            tested.add(digest)
            continue
        normalized = {
            "sourceLabel": str(row["label"]),
            "sourceT": int(row["t"]),
            "sourceR": int(row["r"]),
            "sourceSubmissionId": str(row["submission_id"]),
            "sourcePolynomialIndex": int(row["polynomial_index"]),
            "sourceCoefficientSha256": digest,
            "sourceCoefficientBytes": len(
                ",".join(str(value) for value in values).encode("ascii")
            ),
            "sourceFieldDiscriminantAbs": (
                str(row["field_disc_abs"])
                if row["field_disc_abs"] is not None
                else None
            ),
            "_coefficients": values,
        }
        old = by_hash.get(digest)
        if old is None:
            by_hash[digest] = normalized
        elif (
            old["sourceLabel"],
            old["sourceR"],
            old["_coefficients"],
        ) != (
            normalized["sourceLabel"],
            normalized["sourceR"],
            normalized["_coefficients"],
        ):
            conflicts.add(digest)
    if conflicts:
        raise ValueError(f"conflicting accepted presentations: {sorted(conflicts)[:3]}")
    return list(by_hash.values()), {
        "acceptedRelevantOccurrences": relevant_occurrences,
        "primitiveMonicEvenOccurrences": even_occurrences,
        "nonEvenOrNoncanonicalOccurrences": noncanonical,
        "testedSourceHashesExcluded": len(tested),
        "freshDistinctEvenSourceHashes": len(by_hash),
    }


def even_twist_routes(
    sources: list[dict],
    actions: dict[str, dict],
    boundaries: dict[str, dict],
    snapshot: dict,
    receipt_pairs: set[tuple[str, int]],
    outbox_pairs: set[tuple[str, int]],
) -> tuple[list[dict], dict]:
    started = time.monotonic()
    exact_checks = 0
    ambiguous_sources = 0
    no_target_source = 0
    occurrences = []
    signature_distribution = Counter()
    sturm_lengths = Counter()
    all_boundary_labels = {
        label
        for boundary in boundaries.values()
        for label, _r in boundary["pairs"]
    }
    for source in sources:
        action = actions[source["sourceLabel"]]
        target_label = unanimous_target(action)
        if target_label is None:
            ambiguous_sources += 1
            continue
        if target_label not in all_boundary_labels:
            no_target_source += 1
            continue
        quotient_roots, sturm_length = exact_real_root_count(
            source["_coefficients"][::2]
        )
        exact_checks += 1
        negative_r = 2 * quotient_roots - int(source["sourceR"])
        if negative_r < 0 or negative_r > 24 or negative_r % 2:
            raise ArithmeticError("invalid exact negative-twist signature")
        signature_distribution[(int(source["sourceR"]), negative_r)] += 1
        sturm_lengths[sturm_length] += 1
        for sign, target_r in (
            ("positive", int(source["sourceR"])),
            ("negative", negative_r),
        ):
            pair = (target_label, target_r)
            member_boundaries = {}
            for name, boundary in boundaries.items():
                placement = boundary["byPair"].get(pair)
                if placement is None:
                    continue
                member_boundaries[name] = gate_state(
                    pair, placement, snapshot, receipt_pairs, outbox_pairs
                )
            if not member_boundaries:
                continue
            occurrences.append(
                {
                    "routeFamily": "generic_quadratic_twist_of_even_source",
                    "sign": sign,
                    "sourceLabel": source["sourceLabel"],
                    "sourceR": int(source["sourceR"]),
                    "sourceSubmissionId": source["sourceSubmissionId"],
                    "sourcePolynomialIndex": int(source["sourcePolynomialIndex"]),
                    "sourceCoefficientSha256": source[
                        "sourceCoefficientSha256"
                    ],
                    "sourceCoefficientBytes": int(source["sourceCoefficientBytes"]),
                    "sourceFieldDiscriminantAbs": source[
                        "sourceFieldDiscriminantAbs"
                    ],
                    "targetLabel": target_label,
                    "targetR": target_r,
                    "quotientRealRootCount": quotient_roots,
                    "negativeTwistRealRootCount": negative_r,
                    "sturmSequenceLength": sturm_length,
                    "actionSystemCount": int(action["systemCount"]),
                    "actionBlockQuotientT12": sorted(
                        {int(row["blockActionT12"]) for row in action["systems"]}
                    ),
                    "boundaries": member_boundaries,
                    "exactProof": {
                        "acceptedSource": True,
                        "sourcePolynomial": (
                            "primitive monic even degree-24 P(x)=Q(x^2)"
                        ),
                        "action": (
                            "every exact cached 12x2 block system has the same "
                            "generic global-flip target label"
                        ),
                        "signature": (
                            "positive r=source_r; negative "
                            "r=2*SturmRealRoots(Q)-source_r"
                        ),
                        "genericityRequirementForConstruction": (
                            "choose a fresh squarefree ramification prime at "
                            "which P is squarefree"
                        ),
                    },
                }
            )
    occurrences.sort(
        key=lambda row: (
            int(row["targetLabel"][3:]),
            int(row["targetR"]),
            row["sign"],
            int(row["sourceT"] if "sourceT" in row else row["sourceLabel"][3:]),
            row["sourceCoefficientSha256"],
        )
    )
    return occurrences, {
        "freshSourceHashesConsidered": len(sources),
        "ambiguousActionSourceHashesRejected": ambiguous_sources,
        "unanimousActionSourcesOutsideAllBoundaries": no_target_source,
        "exactRationalSturmChecks": exact_checks,
        "runtimeSeconds": time.monotonic() - started,
        "sourceToNegativeSignatureDistribution": {
            f"{left}->{right}": count
            for (left, right), count in sorted(signature_distribution.items())
        },
        "sturmSequenceLengthDistribution": dict(sorted(sturm_lengths.items())),
    }


def scalar_prefilter(
    rows: list[dict], boundaries: dict[str, dict]
) -> tuple[dict, list[dict]]:
    by_action = {}
    for row in rows:
        action_id = str(row["actionIdSha256"])
        if action_id in by_action and by_action[action_id] != row:
            raise ValueError(f"conflicting scalar action {action_id}")
        by_action[action_id] = row
    flip_absent = [row for row in by_action.values() if not row["flipInAction"]]
    summaries = {}
    heavy_rows = []
    for name, boundary in boundaries.items():
        raw_labels = {label for label, _r in boundary["pairs"]}
        eligible_by_label = defaultdict(list)
        for label, r in boundary["eligiblePairs"]:
            eligible_by_label[label].append(r)
        raw_actions = [
            row for row in flip_absent if str(row["closureLabel"]) in raw_labels
        ]
        eligible_actions = [
            row
            for row in flip_absent
            if str(row["closureLabel"]) in eligible_by_label
        ]
        exact_known = []
        unresolved = []
        reachable_pairs = set()
        for row in eligible_actions:
            mapping = row.get("twistSignatureMap")
            if mapping is None:
                unresolved.append(row)
                continue
            possible = {
                int(value)
                for values in mapping.values()
                for value in values
            }
            pairs = {
                (str(row["closureLabel"]), r)
                for r in eligible_by_label[str(row["closureLabel"])]
                if r in possible
            }
            if pairs:
                exact_known.append(row)
                reachable_pairs.update(pairs)
        summaries[name] = {
            "flipAbsentActions": len(flip_absent),
            "actionsWhoseClosureLabelOccurs": len(raw_actions),
            "distinctRawClosureLabels": len(
                {str(row["closureLabel"]) for row in raw_actions}
            ),
            "actionsWhoseClosureLabelHasFinalEligiblePair": len(eligible_actions),
            "distinctEligibleClosureLabels": len(
                {str(row["closureLabel"]) for row in eligible_actions}
            ),
            "actionsWithCachedExactSignatureHit": len(exact_known),
            "cachedExactReachablePairs": [
                [label, r] for label, r in sorted(reachable_pairs)
            ],
            "actionsRequiringFreshExactSignatureAlignment": len(unresolved),
            "status": (
                "exact_zero_closure_label_routes"
                if not eligible_actions
                else "heavy_signature_alignment_required"
            ),
        }
        if name == "top10000":
            for row in eligible_actions:
                heavy_rows.append(
                    {
                        "actionIdSha256": str(row["actionIdSha256"]),
                        "closureLabel": str(row["closureLabel"]),
                        "closureT": int(row["closureT"]),
                        "eligibleTargetR": sorted(
                            eligible_by_label[str(row["closureLabel"])]
                        ),
                        "family": str(row["family"]),
                        "flipInAction": False,
                        "quotientT12": (
                            int(row["quotientT12"])
                            if row.get("quotientT12") is not None
                            else None
                        ),
                        "sourceLabel": str(row["sourceLabel"]),
                        "sourceT": int(row["sourceT"]),
                        "targetLabel": str(row["targetLabel"]),
                        "targetT": int(row["targetT"]),
                        "twistSignatureMapCached": row.get("twistSignatureMap"),
                    }
                )
    heavy_rows.sort(
        key=lambda row: (
            row["closureT"],
            row["targetT"],
            row["sourceT"],
            row["actionIdSha256"],
        )
    )
    return summaries, heavy_rows


def compositum_pilot_audit(
    document: dict,
    boundaries: dict[str, dict],
    snapshot: dict,
    receipt_pairs: set[tuple[str, int]],
    outbox_pairs: set[tuple[str, int]],
    excluded_hashes: set[str],
) -> dict:
    pilot = document.get("pilot") or []
    result = {}
    for name, boundary in boundaries.items():
        raw = []
        final = []
        for row in pilot:
            pair = (str(row["targetLabel"]), int(row["targetR"]))
            placement = boundary["byPair"].get(pair)
            if placement is None:
                continue
            state = gate_state(
                pair, placement, snapshot, receipt_pairs, outbox_pairs
            )
            item = {
                "pilotIndex": int(row["pilotIndex"]),
                "coefficientSha256": str(row["coefficientSha256"]),
                "targetLabel": pair[0],
                "targetR": pair[1],
                "gate": state,
                "candidateHashExcluded": (
                    str(row["coefficientSha256"]) in excluded_hashes
                ),
            }
            raw.append(item)
            if (
                state["survivesAllPairExclusions"]
                and not item["candidateHashExcluded"]
            ):
                final.append(item)
        result[name] = {
            "certifiedPilotFields": len(pilot),
            "rawPlacementHits": len(raw),
            "finalFreshHits": len(final),
            "hits": raw,
        }
    return result


def main() -> int:
    started = time.monotonic()
    outputs = (AUDIT, ROUTES, HEAVY_TARGETS)
    if any(path.exists() for path in outputs):
        raise FileExistsError("refusing to overwrite fresh T00134 twist artifacts")
    input_hashes = verify_pins()
    checkpoints = {
        "top5000": validate_checkpoint(TOP5000_CHECKPOINT, 5000),
        "top10000": validate_checkpoint(TOP10000_CHECKPOINT, 10000),
    }
    sole_rows = validate_placements(SOLE, 180)
    top5000_rows = validate_placements(TOP5000, 5000)
    top10000_rows = validate_placements(TOP10000, 10000)
    top5000_map = {
        (str(row["label"]), int(row["r"])): row for row in top5000_rows
    }
    top10000_map = {
        (str(row["label"]), int(row["r"])): row for row in top10000_rows
    }
    def placement_payload(row: dict | None) -> dict | None:
        if row is None:
            return None
        return {key: value for key, value in row.items() if key != "fetchedAt"}

    if any(
        placement_payload(top10000_map.get(pair)) != placement_payload(row)
        for pair, row in top5000_map.items()
    ):
        raise ValueError("top-5,000 snapshot is not an exact top-10,000 subset")

    action_rows = read_jsonl(ACTION_MAP)
    actions = {}
    for row in action_rows:
        label = str(row["sourceLabel"])
        if label in actions:
            raise ValueError(f"duplicate even-twist action label {label}")
        actions[label] = row
    scalar_rows = read_jsonl(SCALAR_ACTIONS)
    compositum = json.loads(COMPOSITUM_PILOT.read_text(encoding="utf-8"))

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        exact_started = time.monotonic()
        exact_pool, single_rows, exact_meta, pair_index = raid.exact_corpus(
            connection
        )
        receipt_hashes, receipt_pairs, receipt_meta = (
            exact_single.receipt_exclusions(
                lane.RECEIPTS, DATA, connection, pair_index
            )
        )
        outbox_hashes, outbox_pairs, outbox_meta = (
            outbox_audit.outbox_exclusions(pair_index, connection)
        )
        snapshot = pair_routes.load_ledger_snapshot(connection)
        ledger_hashes = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT coefficient_hash FROM polynomials"
            )
        }
        exact_runtime = time.monotonic() - exact_started
        tested_sources, tested_meta = tested_twist_sources(single_rows)

        boundaries = {
            "sole180": placement_boundary(
                sole_rows, snapshot, receipt_pairs, outbox_pairs
            ),
            "top5000": placement_boundary(
                top5000_rows, snapshot, receipt_pairs, outbox_pairs
            ),
            "top10000": placement_boundary(
                top10000_rows, snapshot, receipt_pairs, outbox_pairs
            ),
        }
        all_labels = {
            label
            for boundary in boundaries.values()
            for label, _r in boundary["pairs"]
        }
        relevant_source_labels = {
            label
            for label, action in actions.items()
            if unanimous_target(action) in all_labels
        }
        sources, source_meta = accepted_even_sources(
            connection, relevant_source_labels, tested_sources
        )
        twist_routes, twist_meta = even_twist_routes(
            sources,
            actions,
            boundaries,
            snapshot,
            receipt_pairs,
            outbox_pairs,
        )
    finally:
        connection.close()

    excluded_hashes = ledger_hashes | receipt_hashes | outbox_hashes
    scalar_summary, scalar_heavy_rows = scalar_prefilter(
        scalar_rows, boundaries
    )
    pilot_summary = compositum_pilot_audit(
        compositum,
        boundaries,
        snapshot,
        receipt_pairs,
        outbox_pairs,
        excluded_hashes,
    )

    retained_routes = [
        row
        for row in twist_routes
        if any(
            state["survivesLedgerPairBoundary"]
            for state in row["boundaries"].values()
        )
    ]
    route_text = "".join(
        canonical_json(row) + "\n" for row in retained_routes
    )

    heavy_targets = {
        "schemaVersion": "fresh-t00134-twist-heavy-targets-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "coefficient_free_exact_prefilter",
        "top10000EligiblePairs": [
            {
                "label": label,
                "r": r,
                "kTeams": int(boundaries["top10000"]["byPair"][(label, r)]["kTeams"]),
                "opponentPoints": float(
                    boundaries["top10000"]["byPair"][(label, r)]["points"]
                ),
                "scoreBounds": score_bounds(
                    boundaries["top10000"]["byPair"][(label, r)]
                ),
            }
            for label, r in sorted(boundaries["top10000"]["eligiblePairs"])
        ],
        "scalarActions": scalar_heavy_rows,
        "compositumFactorDegreeFamilies": [[3, 8], [4, 6]],
        "candidateHashExclusion": {
            "ledgerHashes": len(ledger_hashes),
            "receiptHashes": len(receipt_hashes),
            "outboxHashes": len(outbox_hashes),
            "unionCount": len(excluded_hashes),
            "unionSha256": canonical_digest(sorted(excluded_hashes)),
        },
        "coefficientMaterialIncluded": False,
        "networkCalls": 0,
        "submissionCalls": 0,
    }

    boundary_summaries = {}
    for name, boundary in boundaries.items():
        route_summary = summarize_routes(twist_routes, name)
        route_summary["freshSourceHitRateAfterAllPairExclusions"] = (
            route_summary["routeOccurrencesAfterAllPairExclusions"]
            / len(sources)
            if sources
            else 0.0
        )
        boundary_summaries[name] = {
            **boundary["summary"],
            "evenTwist": route_summary,
            "scalarTwist": scalar_summary[name],
            "simpleCompositumPilot": pilot_summary[name],
        }

    audit = {
        "schemaVersion": "fresh-t00134-deterministic-twist-audit-v1",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": (
            "heavy_structural_census_required"
            if scalar_heavy_rows
            else "light_routes_fully_resolved"
        ),
        "scope": {
            "opponentTeamId": TEAM_ID,
            "opponentTeamNumber": TEAM_NUMBER,
            "soleHolderCrawlId": 5,
            "mechanisms": [
                "positive quadratic scalar of accepted even source",
                "negative quadratic scalar of accepted even source",
                "aligned global-flip scalar of saved F5/F6 induced action",
                "linearly-disjoint simple 3x8/4x6 compositum",
            ],
        },
        "inputSha256": input_hashes,
        "checkpoints": checkpoints,
        "exactCorpus": {
            "uniqueCandidateHashes": len(exact_pool),
            "pairIndexHashes": len(pair_index),
            "runtimeSeconds": exact_runtime,
            "meta": exact_meta,
        },
        "exclusions": {
            "baselinePairs": len(snapshot["baseline"]),
            "acceptedScoreablePairs": len(snapshot["owned"]),
            "knownVerificationPairs": len(snapshot["knownPairs"]),
            "ledgerCoefficientHashes": len(ledger_hashes),
            "receiptPairs": len(receipt_pairs),
            "receiptHashes": len(receipt_hashes),
            "outboxPairs": len(outbox_pairs),
            "outboxHashes": len(outbox_hashes),
            "candidateHashUnion": len(excluded_hashes),
            "candidateHashUnionSha256": canonical_digest(
                sorted(excluded_hashes)
            ),
            "receiptAudit": receipt_meta,
            "outboxAudit": outbox_meta,
            "testedTwistSources": tested_meta,
        },
        "evenTwistSourceCensus": {
            "actionMapRows": len(action_rows),
            "relevantUnanimousSourceLabels": len(relevant_source_labels),
            **source_meta,
            **twist_meta,
        },
        "boundaries": boundary_summaries,
        "routeOutput": {
            "path": str(ROUTES.relative_to(ROOT)),
            "rows": len(retained_routes),
            "definition": (
                "exact even-twist occurrences surviving the baseline/"
                "accepted/known-verification pair union in at least one "
                "placement boundary; reservation vetoes remain annotated"
            ),
        },
        "heavyTargetOutput": {
            "path": str(HEAVY_TARGETS.relative_to(ROOT)),
            "top10000EligiblePairs": len(
                heavy_targets["top10000EligiblePairs"]
            ),
            "scalarActions": len(scalar_heavy_rows),
        },
        "compositumBankBoundary": {
            "distinctRecoveredFactorHashes": int(
                compositum["bankDistinctCoefficientHashes"]
            ),
            "linearlyDisjointSourceDiverseCombinations": int(
                compositum["linearlyDisjointSourceDiverseCombinations"]
            ),
            "distinctExactProductGroupPairs": int(
                compositum["distinctExactProductGroupPairs"]
            ),
            "distinctPredictedTargetPairs": int(
                compositum["distinctPredictedTargetPairs"]
            ),
            "certifiedPilotFields": int(compositum["pilotCertifiedDegree24"]),
            "fullProductActionIntersection": "deferred_to_one_bounded_heavy_census",
        },
        "proofRules": {
            "evenTwistLabel": (
                "accepted exact source label plus unanimous cached global-flip "
                "action target over every exact 12x2 block system"
            ),
            "negativeSignature": (
                "exact rational Sturm count for Q and "
                "r(-twist)=2*r(Q)-r(P)"
            ),
            "genericFreshness": (
                "a constructed twist must use a fresh squarefree ramification "
                "prime where P is squarefree; no construction was attempted "
                "by this light worker"
            ),
            "compositumLabel": (
                "coprime factor field discriminants imply linearly disjoint "
                "Galois closures; exact Cartesian-product action"
            ),
        },
        "runtimeSeconds": time.monotonic() - started,
        "coefficientMaterialIncluded": False,
        "credentialMaterialIncluded": False,
        "ledgerWrites": 0,
        "networkCalls": 0,
        "submissionCalls": 0,
    }

    # Write only after every computation and invariant check has succeeded.
    exclusive_text(ROUTES, route_text)
    exclusive_text(
        HEAVY_TARGETS,
        json.dumps(heavy_targets, indent=2, sort_keys=True) + "\n",
    )
    exclusive_text(AUDIT, json.dumps(audit, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "audit": str(AUDIT.relative_to(ROOT)),
                "auditSha256": sha256_path(AUDIT),
                "routeRows": len(retained_routes),
                "routesSha256": sha256_path(ROUTES),
                "heavyTargetsSha256": sha256_path(HEAVY_TARGETS),
                "top10000EvenTwist": boundary_summaries["top10000"][
                    "evenTwist"
                ],
                "top10000Scalar": boundary_summaries["top10000"][
                    "scalarTwist"
                ],
                "runtimeSeconds": audit["runtimeSeconds"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
