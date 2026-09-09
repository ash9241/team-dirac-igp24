#!/usr/bin/env sage -python
"""Exhaust the final fourteen historical pair-product source polynomials.

The three multi-orbit source labels are handled without guessing.  When orbit
labels differ, simultaneous modular factor degrees of the exact source and all
sibling candidates are intersected with the joint induced-action profiles of
single GAP conjugacy classes until the factor-to-action label assignment is
unique.  When every orbit has the same exact target label, the action multiset
itself already fixes every factor label and no arbitrary slot assignment is
made.  Exact live hits are claimed and staged individually; no network or
submission calls occur here.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from sage.all import GF, NumberField, PolynomialRing, ZZ, libgap, prime_range


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
ACTIONS = DATA / "agent_gold_c_lower_kummer_pair_product_actions.jsonl"
ROUTES = DATA / "agent_gold_c_lower_kummer_pair_product_live_routes.jsonl"
FROZEN_GOLD = DATA / "live_undiscovered_signatures.jsonl"
AUDIT = DATA / "agent_gold_c_lower_kummer_pair_product_wave8_cumulative_audit.json"
LEDGER = Path("/private/tmp/igp24_gold_c_stable_20260721_pilot2.sqlite3")
CLAIMS = DATA / "agent_gold_c_lower_kummer_pair_product_claims"
PLAN = DATA / "agent_gold_c_lower_kummer_pair_product_final_plan.json"
RESULTS = DATA / "agent_gold_c_lower_kummer_pair_product_final_results.jsonl"
SUMMARY = DATA / "agent_gold_c_lower_kummer_pair_product_final_summary.json"

EXPECTED_SHA256 = {
    ACTIONS: "6304b195a109317db89413300a51567c9f7c09c6227616591cf15fd344031f40",
    ROUTES: "bf8588b8b3f433cb99666066232f40a20028a1ab72ac344be80b102f5e1f4704",
    FROZEN_GOLD: "62f04747b3f5add5cc2f2d7c5d611124f56ba72d353a9ed6ce34bc925108e647",
    AUDIT: "83e011b3ea6554d8d4424d0a75b3d0f642e873e17e47a81853c1cd5333357606",
}
PRIORITY_LABELS = {"24T10428": 0, "24T18011": 1, "24T20749": 2}
POINTS_24 = libgap.eval("[1..24]")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def render_jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def cycle_type(permutation) -> tuple[int, ...]:
    return tuple(sorted(int(value) for value in libgap.CycleLengths(permutation, POINTS_24)))


def block_action_data(blocks: list[tuple[int, int]], permutation) -> tuple[list[int], list[int]]:
    point_to_block = {}
    point_sign = {}
    for index, block in enumerate(blocks):
        point_to_block[block[0]] = index
        point_to_block[block[1]] = index
        point_sign[block[0]] = 0
        point_sign[block[1]] = 1
    block_permutation = []
    sign_vector = []
    for block in blocks:
        image = int(libgap.OnPoints(block[0], permutation))
        block_permutation.append(point_to_block[image])
        sign_vector.append(point_sign[image])
    return block_permutation, sign_vector


def induced_permutation(orbit: list[list[int]], block_permutation: list[int], sign_vector: list[int]):
    position = {tuple(pair): index for index, pair in enumerate(orbit)}
    images = []
    for pair_values in orbit:
        pair = tuple(pair_values)
        image_pair = tuple(sorted((block_permutation[pair[0]], block_permutation[pair[1]])))
        target = position[image_pair]
        sign = sign_vector[pair[0]] ^ sign_vector[pair[1]]
        images.extend([2 * target + 1 + sign, 2 * target + 2 - sign])
    return libgap.PermList(images)


def collection_sha256(rows) -> str:
    rendered = json.dumps(rows, separators=(",", ":"), sort_keys=True)
    return sha256_bytes(rendered.encode("utf-8"))


JOINT_CACHE = {}


def joint_profile_census(source_label: str, actions: list[dict]):
    key = (source_label, tuple(tuple(tuple(pair) for pair in action["pairOrbit"]) for action in actions))
    if key in JOINT_CACHE:
        return JOINT_CACHE[key]
    block_systems = {tuple(tuple(block) for block in action["sourceBlockSystem"]) for action in actions}
    if len(block_systems) != 1:
        raise ValueError(f"{source_label} actions do not share one exact source block system")
    blocks = list(next(iter(block_systems)))
    source_group = libgap.TransitiveGroup(24, int(actions[0]["sourceT"]))
    classes = list(libgap.ConjugacyClasses(source_group))
    profiles_by_source = defaultdict(set)
    serializable = []
    for conjugacy_class in classes:
        representative = libgap.Representative(conjugacy_class)
        source_pattern = cycle_type(representative)
        block_permutation, sign_vector = block_action_data(blocks, representative)
        profile = tuple(
            cycle_type(induced_permutation(action["pairOrbit"], block_permutation, sign_vector))
            for action in actions
        )
        profiles_by_source[source_pattern].add(profile)
    for source_pattern in sorted(profiles_by_source):
        serializable.append(
            {
                "sourceCycleType": list(source_pattern),
                "targetProfiles": [
                    [list(pattern) for pattern in profile]
                    for profile in sorted(profiles_by_source[source_pattern])
                ],
            }
        )
    result = (
        profiles_by_source,
        {
            "jointCycleProfileCount": sum(len(value) for value in profiles_by_source.values()),
            "jointCycleProfileSetSha256": collection_sha256(serializable),
            "sourceConjugacyClassCount": len(classes),
            "sourceCycleTypeCount": len(profiles_by_source),
        },
    )
    JOINT_CACHE[key] = result
    return result


def modular_pattern(coefficients: list[int], prime: int):
    ring = PolynomialRing(GF(prime), "z")
    factorization = list(ring(coefficients).factor())
    if any(int(exponent) != 1 for _, exponent in factorization):
        return None
    return tuple(
        sorted(
            int(factor.degree())
            for factor, exponent in factorization
            for _ in range(int(exponent))
        )
    )


def label_assignments(assignments, actions: list[dict]) -> set[tuple[str, ...]]:
    return {
        tuple(actions[action_index]["targetLabel"] for action_index in assignment)
        for assignment in assignments
    }


def resolve_labels(
    source_label: str,
    source_coefficients: list[int],
    candidate_coefficients: list[list[int]],
    actions: list[dict],
) -> dict:
    count = len(actions)
    if len(candidate_coefficients) != count:
        raise ValueError("candidate/action orbit counts differ")
    target_labels = [action["targetLabel"] for action in actions]
    assignments = list(itertools.permutations(range(count)))
    if len(set(target_labels)) == 1:
        labels = tuple(target_labels[0] for _ in range(count))
        return {
            "assignmentMethod": "identical-exact-target-label-multiset",
            "candidateTargetLabels": list(labels),
            "jointProfileCensus": None,
            "labelAssignmentCount": 1,
            "remainingSlotAssignments": [list(value) for value in assignments],
            "slotAssignmentRequiredForLabels": False,
            "usablePrimes": 0,
            "eliminatingObservations": [],
        }
    if count == 1:
        return {
            "assignmentMethod": "unique-size-12-pair-orbit",
            "candidateTargetLabels": target_labels,
            "jointProfileCensus": None,
            "labelAssignmentCount": 1,
            "remainingSlotAssignments": [[0]],
            "slotAssignmentRequiredForLabels": False,
            "usablePrimes": 0,
            "eliminatingObservations": [],
        }

    profiles_by_source, census_metadata = joint_profile_census(source_label, actions)
    evidence = []
    usable_primes = 0
    primes_examined = 0
    nonsquarefree_primes = 0
    for prime_value in prime_range(2, 5000):
        prime = int(prime_value)
        primes_examined += 1
        source_pattern = modular_pattern(source_coefficients, prime)
        observed = [modular_pattern(coefficients, prime) for coefficients in candidate_coefficients]
        if source_pattern is None or any(pattern is None for pattern in observed):
            nonsquarefree_primes += 1
            continue
        usable_primes += 1
        before = assignments
        kept = []
        for assignment in assignments:
            profile_by_action = [None] * count
            for factor_position, action_position in enumerate(assignment):
                profile_by_action[action_position] = observed[factor_position]
            if tuple(profile_by_action) in profiles_by_source.get(source_pattern, set()):
                kept.append(assignment)
        assignments = kept
        if len(assignments) < len(before):
            evidence.append(
                {
                    "afterLabelAssignments": len(label_assignments(assignments, actions)),
                    "afterSlotAssignments": len(assignments),
                    "beforeLabelAssignments": len(label_assignments(before, actions)),
                    "beforeSlotAssignments": len(before),
                    "candidateFactorDegrees": [list(pattern) for pattern in observed],
                    "prime": prime,
                    "sourceFactorDegrees": list(source_pattern),
                }
            )
        if not assignments or len(label_assignments(assignments, actions)) == 1:
            break
    if not assignments:
        raise ArithmeticError(f"joint modular profiles contradict every assignment for {source_label}")
    remaining_labels = label_assignments(assignments, actions)
    if len(remaining_labels) != 1:
        obstruction = {
            "assignmentMethod": "joint-source-and-sibling-modular-action-profiles",
            "candidateTargetLabelAssignments": [list(value) for value in sorted(remaining_labels)],
            "eliminatingObservations": evidence,
            "jointProfileCensus": census_metadata,
            "nonsquarefreePrimes": nonsquarefree_primes,
            "primesExamined": primes_examined,
            "remainingSlotAssignments": [list(value) for value in assignments],
            "usablePrimes": usable_primes,
        }
        raise ArithmeticError("UNRESOLVED_ASSIGNMENT:" + json.dumps(obstruction, sort_keys=True))
    labels = next(iter(remaining_labels))
    return {
        "assignmentMethod": "joint-source-and-sibling-modular-action-profiles",
        "candidateTargetLabels": list(labels),
        "eliminatingObservations": evidence,
        "jointProfileCensus": census_metadata,
        "labelAssignmentCount": 1,
        "nonsquarefreePrimes": nonsquarefree_primes,
        "primesExamined": primes_examined,
        "remainingSlotAssignments": [list(value) for value in assignments],
        "slotAssignmentRequiredForLabels": True,
        "usablePrimes": usable_primes,
    }


def claimed_pairs() -> set[tuple[str, int]]:
    paths = set()
    for candidate in DATA.rglob("*claim*"):
        if candidate.is_file():
            paths.add(candidate)
        elif candidate.is_dir():
            paths.update(path for path in candidate.rglob("*") if path.is_file())
    pairs = set()
    for path in paths:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict):
            continue
        label = row.get("targetLabel", row.get("label"))
        signature = row.get("targetR", row.get("r"))
        if isinstance(label, str) and signature is not None:
            try:
                pairs.add((label, int(signature)))
            except (TypeError, ValueError):
                pass
    return pairs


def outbox_hashes() -> set[str]:
    hashes = set()
    for path in OUTBOX.glob("*.txt"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        hashes.update(sha256_bytes(line.strip().encode("utf-8")) for line in lines if line.strip())
    return hashes


def main() -> int:
    for path in (PLAN, RESULTS, SUMMARY):
        if path.exists():
            raise ValueError(f"refusing to overwrite {path}")
    input_hashes = {str(path.relative_to(ROOT)): sha256_path(path) for path in EXPECTED_SHA256}
    for path, expected in EXPECTED_SHA256.items():
        if input_hashes[str(path.relative_to(ROOT))] != expected:
            raise ValueError(f"input hash mismatch: {path}")

    action_rows = load_jsonl(ACTIONS)
    route_rows = load_jsonl(ROUTES)
    actions_by_source = defaultdict(list)
    for action in action_rows:
        actions_by_source[action["sourceLabel"]].append(action)
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    remaining = []
    for label_row in audit["remainingSources"]["rows"]:
        label = label_row["label"]
        for source in label_row["sourcePolynomials"]:
            matching_routes = [
                row
                for row in route_rows
                if row["source"]["coefficientSha256"] == source["coefficientSha256"]
            ]
            if not matching_routes:
                raise ValueError("remaining audit source has no executable route")
            quotient_lines = {row["sourceQuotientLine"] for row in matching_routes}
            quotient_hashes = {row["sourceQuotientPolynomialSha256"] for row in matching_routes}
            if len(quotient_lines) != 1 or len(quotient_hashes) != 1:
                raise ValueError("remaining source routes disagree on quotient data")
            first = matching_routes[0]
            cost = max(abs(int(value)) for value in first["sourceQuotientLine"].split(",")).bit_length()
            remaining.append(
                {
                    "actions": actions_by_source[label],
                    "coefficientCostBits": cost,
                    "coveragePairs": label_row["livePairs"],
                    "source": first["source"],
                    "sourceQuotientLine": first["sourceQuotientLine"],
                    "sourceQuotientPolynomialSha256": first["sourceQuotientPolynomialSha256"],
                }
            )
    if len(remaining) != 14 or len({row["source"]["coefficientSha256"] for row in remaining}) != 14:
        raise ValueError("cumulative audit does not expose exactly fourteen remaining sources")
    remaining.sort(
        key=lambda row: (
            PRIORITY_LABELS.get(row["source"]["label"], 3),
            row["coefficientCostBits"],
            int(row["source"]["polynomialIndex"]),
        )
    )
    plan = {
        "coverageRule": (
            "all sources for the four wholly untested pairs first (24T3463/r16, "
            "24T14142/r8,r16, 24T17365/r24), then every remaining source polynomial"
        ),
        "inputSha256": input_hashes,
        "mechanism": "exact-conjugate-pair-product-final-exhaustion-v1",
        "networkCalls": 0,
        "sources": remaining,
        "submissionCalls": 0,
    }
    plan_text = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    atomic_write(PLAN, plan_text)
    plan_sha256 = sha256_bytes(plan_text.encode("utf-8"))
    print(json.dumps({"event": "plan_frozen", "path": str(PLAN), "sha256": plan_sha256}), flush=True)

    connection = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    ledger_hashes = {str(row[0]) for row in connection.execute("SELECT coefficient_hash FROM polynomials")}
    preexisting_outbox = outbox_hashes()
    known_hashes = ledger_hashes | preexisting_outbox
    claims = claimed_pairs()
    frozen_gold = {(row["label"], int(row["r"])): row for row in load_jsonl(FROZEN_GOLD)}
    ledger_sha256 = sha256_path(LEDGER)
    results = []
    hit_rows = []
    candidate_count = 0

    for source_position, route in enumerate(remaining, 1):
        source = route["source"]
        actions = route["actions"]
        quotient_line = route["sourceQuotientLine"]
        if sha256_bytes(quotient_line.encode("utf-8")) != route["sourceQuotientPolynomialSha256"]:
            raise ValueError("source quotient hash mismatch")
        source_row = connection.execute(
            """
            SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.scoreable,v.field_disc_abs
            FROM polynomials p JOIN verifications v USING (submission_id,polynomial_index)
            WHERE p.submission_id=? AND p.polynomial_index=?
            """,
            (source["submissionId"], int(source["polynomialIndex"])),
        ).fetchone()
        if source_row is None:
            raise ValueError("source missing from stable ledger")
        source_coefficients = [ZZ(value) for value in source_row["coefficients"].split(",")]
        if (
            source_row["coefficient_hash"] != source["coefficientSha256"]
            or source_row["label"] != source["label"]
            or int(source_row["r"]) != int(source["r"])
            or int(source_row["scoreable"]) != 1
            or any(source_coefficients[index] for index in range(1, 25, 2))
        ):
            raise ValueError("source ledger provenance mismatch")
        ring_y = PolynomialRing(ZZ, f"y{source_position}")
        quotient = ring_y([ZZ(value) for value in quotient_line.split(",")])
        if source_coefficients[::2] != quotient.list() or not quotient.is_irreducible():
            raise ValueError("source quotient reconstruction/irreducibility failed")
        pair_resolvent = quotient.symmetric_power(2, monic=True)
        factors = [(factor, int(exponent)) for factor, exponent in pair_resolvent.factor()]
        degree_twelve = [factor for factor, exponent in factors if factor.degree() == 12 and exponent == 1]
        if len(degree_twelve) != len(actions):
            raise ValueError("degree-12 factor count does not equal exact action-orbit count")
        ring_x = PolynomialRing(ZZ, f"x{source_position}")
        x = ring_x.gen()
        candidates = [ring_x(factor)(x**2) for factor in degree_twelve]
        for candidate in candidates:
            if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
                raise ValueError("candidate is not monic irreducible degree 24")
        candidate_coefficients = [[int(value) for value in candidate.list()] for candidate in candidates]
        assignment = resolve_labels(
            source["label"],
            [int(value) for value in source_coefficients],
            candidate_coefficients,
            actions,
        )
        candidate_labels = assignment["candidateTargetLabels"]
        source_result = {
            "assignmentCertificate": assignment,
            "candidateResults": [],
            "coveragePairs": route["coveragePairs"],
            "exactActions": actions,
            "factorDegrees": [
                {"degree": int(factor.degree()), "exponent": exponent}
                for factor, exponent in factors
            ],
            "pairResolventSha256": sha256_bytes(
                ",".join(str(value) for value in pair_resolvent.list()).encode("utf-8")
            ),
            "source": source,
            "sourcePosition": source_position,
            "sourceQuotientPolynomialSha256": route["sourceQuotientPolynomialSha256"],
        }

        for factor_index, (candidate, target_label) in enumerate(zip(candidates, candidate_labels)):
            candidate_count += 1
            matching_actions = [action for action in actions if action["targetLabel"] == target_label]
            if not matching_actions:
                raise ValueError("assigned target label is absent from exact action multiset")
            target_t_values = {int(action["targetT"]) for action in matching_actions}
            target_order_values = {int(action["targetOrder"]) for action in matching_actions}
            target_kernel_values = {int(action["targetKernelOrder"]) for action in matching_actions}
            if len(target_t_values) != 1 or len(target_order_values) != 1 or len(target_kernel_values) != 1:
                raise ValueError("same target label has inconsistent exact action metadata")
            target_t = next(iter(target_t_values))
            signature = int(candidate.number_of_real_roots())
            coefficient_line = ",".join(str(value) for value in candidate.list())
            coefficient_sha256 = sha256_bytes(coefficient_line.encode("utf-8"))
            field_discriminant_abs = abs(
                ZZ(NumberField(candidate, f"a{source_position}_{factor_index}").absolute_discriminant())
            )
            target_pair = (target_label, signature)
            target = connection.execute(
                """
                SELECT t.*,
                  EXISTS(SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r) AS baseline,
                  EXISTS(SELECT 1 FROM verifications v WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1) AS owned
                FROM targets t WHERE t.label=? AND t.r=?
                """,
                target_pair,
            ).fetchone()
            local_live = bool(
                target is not None
                and not int(target["baseline"])
                and not int(target["owned"])
                and target_pair in frozen_gold
            )
            if not local_live:
                status = "resolved_not_live_signature"
            elif coefficient_sha256 in known_hashes:
                status = "resolved_known_coefficient"
            elif target_pair in claims:
                status = "resolved_pair_claimed"
            else:
                status = "hit_staged"
            candidate_result = {
                "coefficientLine": coefficient_line,
                "coefficientSha256": coefficient_sha256,
                "exactTarget": {
                    "kernelOrder": next(iter(target_kernel_values)),
                    "label": target_label,
                    "order": next(iter(target_order_values)),
                    "r": signature,
                    "t": target_t,
                },
                "factorIndex": factor_index,
                "fieldDiscriminantAbs": str(field_discriminant_abs),
                "frozenGoldTarget": frozen_gold.get(target_pair),
                "irreducible": True,
                "localLiveRecheck": None if target is None else {
                    "baseline": bool(target["baseline"]),
                    "discovered": bool(target["discovered"]),
                    "generatedAt": target["generated_at"],
                    "minimumDiscAbs": target["minimum_disc_abs"],
                    "owned": bool(target["owned"]),
                    "teamCount": int(target["team_count"]),
                },
                "novelty": {
                    "presentInPreexistingOutbox": coefficient_sha256 in preexisting_outbox,
                    "presentInStableLedger": coefficient_sha256 in ledger_hashes,
                    "newAtResolution": coefficient_sha256 not in known_hashes,
                },
                "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
                "status": status,
            }
            if status == "hit_staged":
                stem = (
                    f"agent_gold_c_lower_kummer_pair_product_final_{source_position:02d}_"
                    f"f{factor_index}_{target_label}_r{signature}"
                )
                manifest_path = OUTBOX / f"{stem}.txt"
                certificate_path = DATA / f"{stem}.json"
                claim_path = CLAIMS / f"{target_label}_r{signature}.json"
                if manifest_path.exists() or certificate_path.exists() or claim_path.exists():
                    raise ValueError("refusing to overwrite final-hit artifacts")
                manifest_text = coefficient_line + "\n"
                candidate_result["manifest"] = str(manifest_path.relative_to(ROOT))
                candidate_result["manifestSha256"] = sha256_bytes(manifest_text.encode("utf-8"))
                certificate = {
                    "assignmentCertificate": assignment,
                    "candidate": candidate_result,
                    "exactActions": actions,
                    "inputSha256": input_hashes,
                    "ledger": {"path": str(LEDGER), "sha256": ledger_sha256},
                    "manifest": candidate_result["manifest"],
                    "manifestSha256": candidate_result["manifestSha256"],
                    "method": "exact-conjugate-pair-product-final-exhaustion-v1",
                    "networkCalls": 0,
                    "plan": str(PLAN.relative_to(ROOT)),
                    "planSha256": plan_sha256,
                    "source": source,
                    "sourcePosition": source_position,
                    "submissionCalls": 0,
                }
                certificate_text = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
                claim = {
                    "candidateCoefficientLine": coefficient_line,
                    "candidateFieldDiscriminantAbs": str(field_discriminant_abs),
                    "candidateSha256": coefficient_sha256,
                    "certificate": str(certificate_path.relative_to(ROOT)),
                    "owner": "agent_gold_c_lower_kummer_pair_product_final",
                    "targetLabel": target_label,
                    "targetR": signature,
                }
                atomic_write(claim_path, json.dumps(claim, indent=2, sort_keys=True) + "\n")
                atomic_write(manifest_path, manifest_text)
                atomic_write(certificate_path, certificate_text)
                candidate_result["certificate"] = str(certificate_path.relative_to(ROOT))
                candidate_result["certificateSha256"] = sha256_bytes(certificate_text.encode("utf-8"))
                candidate_result["claim"] = str(claim_path.relative_to(ROOT))
                claims.add(target_pair)
                known_hashes.add(coefficient_sha256)
                hit_rows.append(candidate_result)
            source_result["candidateResults"].append(candidate_result)
            print(
                json.dumps(
                    {
                        "assignmentMethod": assignment["assignmentMethod"],
                        "certificate": candidate_result.get("certificate"),
                        "certificateSha256": candidate_result.get("certificateSha256"),
                        "coefficientSha256": coefficient_sha256,
                        "event": "candidate_resolved",
                        "factorIndex": factor_index,
                        "fieldDiscriminantAbs": str(field_discriminant_abs),
                        "manifest": candidate_result.get("manifest"),
                        "manifestSha256": candidate_result.get("manifestSha256"),
                        "sourceIndex": int(source["polynomialIndex"]),
                        "sourceLabel": source["label"],
                        "sourcePosition": source_position,
                        "status": status,
                        "targetLabel": target_label,
                        "targetR": signature,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        results.append(source_result)
        atomic_write(RESULTS, render_jsonl(results))

    connection.close()
    results_text = render_jsonl(results)
    candidate_results = [candidate for row in results for candidate in row["candidateResults"]]
    status_histogram = Counter(row["status"] for row in candidate_results)
    summary = {
        "candidatePolynomials": candidate_count,
        "exactHits": int(status_histogram["hit_staged"]),
        "inputSha256": input_hashes,
        "mechanismExhausted": True,
        "networkCalls": 0,
        "plan": str(PLAN.relative_to(ROOT)),
        "planSha256": plan_sha256,
        "remainingHistoricalSourcePolynomials": 0,
        "resolvedSourcePolynomials": len(results),
        "results": str(RESULTS.relative_to(ROOT)),
        "resultsSha256": sha256_bytes(results_text.encode("utf-8")),
        "staged": [
            {
                "certificate": row["certificate"],
                "certificateSha256": row["certificateSha256"],
                "manifest": row["manifest"],
                "manifestSha256": row["manifestSha256"],
                "target": row["exactTarget"],
            }
            for row in hit_rows
        ],
        "statusHistogram": dict(sorted(status_histogram.items())),
        "submissionCalls": 0,
    }
    summary_text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    atomic_write(SUMMARY, summary_text)
    print(
        json.dumps(
            {
                "candidatePolynomials": candidate_count,
                "event": "final_wave_complete",
                "exactHits": summary["exactHits"],
                "resolvedSourcePolynomials": len(results),
                "resultsSha256": summary["resultsSha256"],
                "summary": str(SUMMARY),
                "summarySha256": sha256_bytes(summary_text.encode("utf-8")),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
