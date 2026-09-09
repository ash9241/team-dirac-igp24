#!/usr/bin/env sage
"""Exact tc0 gate for q(T_d(x)-a), (deg(q),d)=(6,4),(4,6),(3,8).

Only the generic full D_d wr Gal(q) action is considered.  The arithmetic
family is gated by the exact open intervals cut out by a=-alpha-1 and
a=-alpha+1 for the real roots alpha of q.  No polynomial construction,
network call, or submission is performed by this census.
"""

import hashlib
import json
import signal
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from sage.all import AA, PolynomialRing, QQ
from sage.libs.gap.libgap import libgap


ROOT = Path.cwd()
SOURCES = ROOT / "data" / "agent_non12_recoverable_subfields.jsonl"
LEDGER = ROOT / "data" / "ledger.sqlite3"
OUTPUT = (
    ROOT / "data" / "chebyshev_even_dihedral_wreath_census_20260727.json"
)
HARD_WALL_SECONDS = 12 * 60
R = PolynomialRing(QQ, "x")


def alarm_handler(_signum, _frame):
    raise TimeoutError(f"hard wall-clock cap of {HARD_WALL_SECONDS}s reached")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def perm_from_images(images):
    return libgap.PermList(libgap(images))


def fixed_points(permutation, degree):
    return sum(
        int(libgap.OnPoints(point, permutation)) == point
        for point in range(1, degree + 1)
    )


def source_involution_fixed_points(source_degree, source_t):
    """All fixed-point counts of order-one/two elements in the source action."""
    source_group = libgap.TransitiveGroup(source_degree, source_t)
    values = set()
    witnesses = {}
    for conjugacy_class in list(libgap.ConjugacyClasses(source_group)):
        representative = libgap.Representative(conjugacy_class)
        order = int(libgap.Order(representative))
        if order not in (1, 2):
            continue
        count = fixed_points(representative, source_degree)
        values.add(count)
        witnesses.setdefault(
            count,
            {
                "elementOrder": order,
                "sourceCycleStructure": str(
                    libgap.CycleStructurePerm(representative)
                ),
            },
        )
    return sorted(values), witnesses


def sumset(values, terms):
    totals = {0}
    for _index in range(terms):
        totals = {left + right for left in totals for right in values}
    return totals


def full_dihedral_wreath(source_degree, source_t, fiber_degree):
    """Natural degree source_degree*fiber_degree action of D_d wr H."""
    source_group = libgap.TransitiveGroup(source_degree, source_t)
    degree = source_degree * fiber_degree
    generators = []

    # Independent rotation and reflection in every fiber.  On zero-based
    # fiber positions these are k -> k+1 and k -> -k.
    for block in range(source_degree):
        start = fiber_degree * block

        rotation = list(range(1, degree + 1))
        for root_index in range(fiber_degree):
            rotation[start + root_index] = (
                start + ((root_index + 1) % fiber_degree) + 1
            )
        generators.append(perm_from_images(rotation))

        reflection = list(range(1, degree + 1))
        for root_index in range(fiber_degree):
            reflection[start + root_index] = (
                start + ((-root_index) % fiber_degree) + 1
            )
        generators.append(perm_from_images(reflection))

    # The source group permutes the fibers and preserves their coordinates.
    for source_generator in list(libgap.GeneratorsOfGroup(source_group)):
        images = []
        for block in range(1, source_degree + 1):
            image_block = int(libgap.OnPoints(block, source_generator))
            for root_index in range(fiber_degree):
                images.append(
                    fiber_degree * (image_block - 1) + root_index + 1
                )
        generators.append(perm_from_images(images))

    group = libgap.Group(generators)
    expected_order = (
        (2 * fiber_degree) ** source_degree * int(libgap.Size(source_group))
    )
    actual_order = int(libgap.Size(group))
    if actual_order != expected_order:
        raise ArithmeticError(
            f"wreath order mismatch: expected {expected_order}, got {actual_order}"
        )
    if not bool(libgap.IsTransitive(group)):
        raise ArithmeticError("constructed wreath action is not transitive")

    target_t = int(libgap.TransitiveIdentification(group))
    source_fixed, witnesses = source_involution_fixed_points(
        source_degree, source_t
    )
    action_fixed = sorted(
        set().union(
            *(sumset({0, 2, fiber_degree}, count) for count in source_fixed)
        )
    )
    return {
        "targetLabel": f"24T{target_t}",
        "targetOrder": actual_order,
        "expectedWreathOrder": expected_order,
        "sourceInvolutionFixedPoints": source_fixed,
        "sourceInvolutionWitnesses": {
            str(key): value for key, value in sorted(witnesses.items())
        },
        "actionInvolutionFixedPoints": action_fixed,
        "exactChecks": {
            "degree": degree,
            "orderMatchesFullWreath": True,
            "transitive": True,
            "transitiveIdentificationSucceeded": True,
        },
    }


def rational_between(left, right):
    """Return a rational strictly between two distinct real algebraic numbers."""
    if not left < right:
        raise ValueError("rational_between requires left < right")
    for precision in (64, 128, 256, 512, 1024, 2048, 4096):
        midpoint = QQ((left.n(precision) + right.n(precision)) / 2)
        if left < midpoint < right:
            return midpoint
    raise ArithmeticError("failed to isolate a rational interval representative")


def rational_payload(value):
    return {
        "numerator": str(value.numerator()),
        "denominator": str(value.denominator()),
        "text": str(value),
    }


def attainable_signature_intervals(coefficient_line, fiber_degree):
    """Exact generic-a signatures, with a rational witness in every interval."""
    coefficients = [QQ(value) for value in coefficient_line.split(",")]
    polynomial = R(coefficients)
    roots = polynomial.roots(AA)
    if any(multiplicity != 1 for _root, multiplicity in roots):
        raise ArithmeticError("recovered source polynomial is not squarefree")

    events = []
    for root_index, (root, _multiplicity) in enumerate(roots):
        events.append((-root - 1, fiber_degree, root_index, "enter"))
        events.append((-root + 1, 2 - fiber_degree, root_index, "leave"))
    events.sort(key=lambda item: item[0])

    if not events:
        return [
            {
                "signature": 0,
                "representativeA": rational_payload(QQ(0)),
                "leftEventCount": 0,
            }
        ], 0

    grouped = []
    for event in events:
        if not grouped or event[0] != grouped[-1][0]:
            grouped.append([event[0], []])
        grouped[-1][1].append(event)

    first = grouped[0][0]
    representative = QQ(first.floor() - 1)
    intervals = [
        {
            "signature": 0,
            "representativeA": rational_payload(representative),
            "leftEventCount": 0,
        }
    ]
    signature = 0
    processed = 0
    for group_index, (event_value, tied_events) in enumerate(grouped):
        signature += sum(event[1] for event in tied_events)
        processed += len(tied_events)
        if group_index + 1 < len(grouped):
            representative = rational_between(
                event_value, grouped[group_index + 1][0]
            )
        else:
            representative = QQ(event_value.ceil() + 1)
        intervals.append(
            {
                "signature": int(signature),
                "representativeA": rational_payload(representative),
                "leftEventCount": processed,
                "crossedEvents": [
                    {
                        "rootIndex": int(event[2]),
                        "kind": event[3],
                        "signatureDelta": int(event[1]),
                    }
                    for event in tied_events
                ],
            }
        )

    return intervals, len(roots)


def load_ledger_state(connection):
    target_rows = {
        (str(row[0]), int(row[1])): {
            "teamCount": int(row[2]),
            "discovered": int(row[3]),
            "generatedAt": row[4],
        }
        for row in connection.execute(
            "SELECT label,r,team_count,discovered,generated_at FROM targets"
        )
    }
    baseline = {
        (str(row[0]), int(row[1]))
        for row in connection.execute("SELECT label,r FROM baseline_pairs")
    }
    owned = {
        (str(row[0]), int(row[1]))
        for row in connection.execute(
            """
            SELECT DISTINCT label,r
            FROM verifications
            WHERE scoreable=1 AND label IS NOT NULL AND r IS NOT NULL
            """
        )
    }
    live = {
        pair: {
            **target,
            "nonbaseline": True,
            "locallyUnownedScoreable": True,
        }
        for pair, target in target_rows.items()
        if target["teamCount"] == 0
        and target["discovered"] == 0
        and pair not in baseline
        and pair not in owned
    }
    return target_rows, baseline, owned, live


def main():
    started = time.monotonic()
    source_rows = [
        json.loads(line)
        for line in SOURCES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    source_rows = [
        row
        for row in source_rows
        if int(row["subfieldDegree"]) in (3, 4, 6)
    ]

    # A reduced coefficient hash represents one recovered q.  Require all
    # duplicate recoveries to agree on the arithmetic data used by the gate.
    distinct_sources = {}
    recovery_counts = defaultdict(int)
    for row in source_rows:
        source_hash = str(row["coefficientSha256"])
        recovery_counts[source_hash] += 1
        if source_hash in distinct_sources:
            previous = distinct_sources[source_hash]
            required = (
                "coefficientLine",
                "subfieldDegree",
                "realRoots",
            )
            if any(previous[key] != row[key] for key in required):
                raise ArithmeticError(
                    f"inconsistent duplicate source row for {source_hash}"
                )
            if previous["galoisGroup"]["label"] != row["galoisGroup"]["label"]:
                raise ArithmeticError(
                    f"inconsistent source group for {source_hash}"
                )
            continue
        distinct_sources[source_hash] = row

    action_keys = sorted(
        {
            (
                int(row["subfieldDegree"]),
                int(str(row["galoisGroup"]["label"]).split("T", 1)[1]),
                24 // int(row["subfieldDegree"]),
            )
            for row in distinct_sources.values()
        }
    )
    actions = {}
    for source_degree, source_t, fiber_degree in action_keys:
        actions[(source_degree, source_t, fiber_degree)] = (
            full_dihedral_wreath(source_degree, source_t, fiber_degree)
        )

    with sqlite3.connect(LEDGER) as connection:
        target_rows, baseline_pairs, owned_pairs, live_tc0 = (
            load_ledger_state(connection)
        )
        ledger_max_generated_at = connection.execute(
            "SELECT MAX(generated_at) FROM targets"
        ).fetchone()[0]

    rows = []
    support_candidates = []
    for source_hash, row in sorted(distinct_sources.items()):
        source_degree = int(row["subfieldDegree"])
        source_t = int(str(row["galoisGroup"]["label"]).split("T", 1)[1])
        fiber_degree = 24 // source_degree
        action = actions[(source_degree, source_t, fiber_degree)]
        intervals, exact_real_roots = attainable_signature_intervals(
            row["coefficientLine"], fiber_degree
        )
        if exact_real_roots != int(row["realRoots"]):
            raise ArithmeticError(
                f"source real-root mismatch for {source_hash}: "
                f"{exact_real_roots} != {row['realRoots']}"
            )
        signatures = sorted({item["signature"] for item in intervals})
        missing_action_signatures = sorted(
            set(signatures) - set(action["actionInvolutionFixedPoints"])
        )
        if missing_action_signatures:
            raise ArithmeticError(
                f"family signatures absent from wreath involution profile: "
                f"{source_hash} {missing_action_signatures}"
            )
        if exact_real_roots not in action["sourceInvolutionFixedPoints"]:
            raise ArithmeticError(
                f"source signature absent from source group: {source_hash}"
            )

        target_label = action["targetLabel"]
        live_signatures = [
            signature
            for signature in signatures
            if (target_label, signature) in live_tc0
        ]
        for interval in intervals:
            pair = (target_label, interval["signature"])
            if pair not in live_tc0:
                continue
            support_candidates.append(
                {
                    "sourceCoefficientSha256": source_hash,
                    "sourceCoefficientLine": row["coefficientLine"],
                    "sourceCoefficientBytes": int(row["coefficientBytes"]),
                    "sourceDegree": source_degree,
                    "sourceLabel": row["galoisGroup"]["label"],
                    "fiberDegree": fiber_degree,
                    "targetLabel": target_label,
                    "targetR": interval["signature"],
                    "representativeA": interval["representativeA"],
                }
            )
        rows.append(
            {
                "sourceCoefficientSha256": source_hash,
                "sourceCoefficientLine": row["coefficientLine"],
                "sourceCoefficientBytes": int(row["coefficientBytes"]),
                "sourceDegree": source_degree,
                "sourceLabel": row["galoisGroup"]["label"],
                "sourceR": exact_real_roots,
                "recoveryRowCount": recovery_counts[source_hash],
                "fiberDegree": fiber_degree,
                "targetLabel": target_label,
                "targetOrder": action["targetOrder"],
                "attainableR": signatures,
                "signatureIntervals": intervals,
                "currentLiveTc0R": live_signatures,
                "currentLiveTc0": bool(live_signatures),
                "actionSignatureCompatibilityExact": True,
            }
        )

    action_rows = []
    for key in action_keys:
        source_degree, source_t, fiber_degree = key
        action = dict(actions[key])
        relevant = [
            row
            for row in rows
            if row["sourceDegree"] == source_degree
            and row["sourceLabel"] == f"{source_degree}T{source_t}"
        ]
        family_signatures = sorted(
            {
                signature
                for row in relevant
                for signature in row["attainableR"]
            }
        )
        action["sourceDegree"] = source_degree
        action["sourceLabel"] = f"{source_degree}T{source_t}"
        action["fiberDegree"] = fiber_degree
        action["recoveredDistinctSources"] = len(relevant)
        action["recoveredFamilyAttainableR"] = family_signatures
        action["currentLiveTc0R"] = [
            signature
            for signature in family_signatures
            if (action["targetLabel"], signature) in live_tc0
        ]
        action["currentLiveTc0"] = bool(action["currentLiveTc0R"])
        action_rows.append(action)

    support_candidates.sort(
        key=lambda item: (
            item["sourceCoefficientBytes"],
            abs(int(item["representativeA"]["numerator"]))
            + int(item["representativeA"]["denominator"]),
            item["sourceCoefficientSha256"],
            item["targetR"],
        )
    )
    status = (
        "arithmetic_scan_required"
        if support_candidates
        else "hard_blocked_zero_current_live_tc0_support"
    )
    family_pairs = {
        (action["targetLabel"], signature)
        for action in action_rows
        for signature in action["recoveredFamilyAttainableR"]
    }
    target_state_partition = defaultdict(int)
    for pair in family_pairs:
        target = target_rows.get(pair)
        if target is None:
            target_state_partition["absentTargetPair"] += 1
        elif target["teamCount"] != 0:
            target_state_partition["teamCountPositive"] += 1
        elif target["discovered"] != 0:
            target_state_partition["discovered"] += 1
        elif pair in baseline_pairs:
            target_state_partition["baseline"] += 1
        elif pair in owned_pairs:
            target_state_partition["locallyOwnedScoreable"] += 1
        else:
            target_state_partition["liveTc0NonbaselineUnowned"] += 1
    if sum(target_state_partition.values()) != len(family_pairs):
        raise ArithmeticError("target-state partition is not exhaustive")
    if target_state_partition["liveTc0NonbaselineUnowned"] != len(
        {
            (candidate["targetLabel"], candidate["targetR"])
            for candidate in support_candidates
        }
    ):
        raise ArithmeticError("live target partition and source support disagree")

    payload = {
        "schemaVersion": 1,
        "mechanism": (
            "q(T_d(x)-a), (deg(q),d)=(6,4),(4,6),(3,8), "
            "generic full D_d wr Gal(q) natural degree-24 action"
        ),
        "status": status,
        "hardWallSeconds": HARD_WALL_SECONDS,
        "elapsedSeconds": round(time.monotonic() - started, 3),
        "input": {
            "sourcePath": str(SOURCES),
            "sourceSha256": sha256(SOURCES),
            "recoveryRowsDegree3_4_6": len(source_rows),
            "distinctRecoveredSources": len(distinct_sources),
            "ledgerPath": str(LEDGER),
            "ledgerMaxTargetGeneratedAt": ledger_max_generated_at,
            "liveTc0Definition": (
                "team_count=0 AND discovered=0 AND nonbaseline "
                "AND locally unowned by any scoreable verification"
            ),
            "liveTc0Pairs": len(live_tc0),
        },
        "proof": {
            "actionConstruction": (
                "independent rotation/reflection generators in every d-point "
                "fiber, semidirect by the exact transitive source group"
            ),
            "actionChecks": (
                "exact order (2d)^m*|H|, transitivity, and GAP "
                "TransitiveIdentification"
            ),
            "signatureConstruction": (
                "exact AA ordering of all a=-alpha-1 and a=-alpha+1 "
                "breakpoints; generic fiber contributions are 0,d,2"
            ),
            "squarefreeBoundaryPolicy": (
                "breakpoint values are excluded because T_d(x)-(alpha+a) "
                "has a critical multiple root there; each retained open "
                "interval has an exact rational representative"
            ),
            "involutionCompatibility": (
                "every recovered source r is an order-one/two fixed-point "
                "count in H, and every family signature is an involution "
                "fixed-point count in D_d wr H"
            ),
            "failClosed": True,
        },
        "actionClasses": len(action_rows),
        "actions": action_rows,
        "sourceRows": rows,
        "currentLiveTc0ActionClasses": [
            row for row in action_rows if row["currentLiveTc0"]
        ],
        "currentLiveTc0SourceRows": [
            row for row in rows if row["currentLiveTc0"]
        ],
        "arithmeticCandidateCount": len(support_candidates),
        "top20ArithmeticCandidates": support_candidates[:20],
        "distinctRecoveredFamilyTargetPairs": len(family_pairs),
        "targetStatePartition": dict(sorted(target_state_partition.items())),
        "arithmeticCalls": 0,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    if support_candidates:
        # This file is deliberately a gate.  A nonempty result requires a
        # separate exact arithmetic worker; silently treating the generic
        # wreath action as the actual specialized group would not be sound.
        payload["nextStep"] = (
            "run only the listed top20 representatives through an exact "
            "specialized-group certificate; reject every unproved candidate"
        )
    else:
        payload["hardBlockCertificate"] = {
            "recoveredSourcesExhausted": len(distinct_sources),
            "actionClassesExhausted": len(action_rows),
            "allAttainableSignaturesIntersected": True,
            "intersectionCardinality": 0,
            "distinctTargetPairsIntersected": len(family_pairs),
            "targetStatePartition": dict(
                sorted(target_state_partition.items())
            ),
            "arithmeticForbiddenByGate": True,
        }

    OUTPUT.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "status": status,
                "actionClasses": len(action_rows),
                "distinctSources": len(distinct_sources),
                "currentLiveTc0ActionClasses": len(
                    payload["currentLiveTc0ActionClasses"]
                ),
                "currentLiveTc0SourceRows": len(
                    payload["currentLiveTc0SourceRows"]
                ),
                "arithmeticCandidateCount": len(support_candidates),
                "elapsedSeconds": payload["elapsedSeconds"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, alarm_handler)
    signal.alarm(HARD_WALL_SECONDS)
    try:
        main()
    finally:
        signal.alarm(0)
