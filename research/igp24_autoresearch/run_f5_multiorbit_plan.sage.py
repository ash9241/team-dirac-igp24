#!/usr/bin/env sage -python
"""Run a sealed exact F5 multi-orbit plan offline, fail-closed, and resumably.

The worker has no API client and makes no network or submission calls.  It
requires a separate positive audit artifact tied to the exact plan, worker,
and shared helper hashes.  Every cached action is independently reconstructed
from the source group's sole literal two-block system before arithmetic starts.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import itertools
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter, defaultdict, deque
from pathlib import Path

from sage.all import GF, NumberField, PolynomialRing, ZZ, libgap, prime_range

from f5_multiorbit_common import (
    action_core,
    action_sha256,
    canonical_quotient_sha256,
    dispatch_kind,
    filter_slot_assignments,
    label_assignments,
    quotient_from_even_coefficients,
    sha256_bytes,
)


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
RECEIPTS = ROOT / "receipts"
# Reuse the repository-wide heavy-worker lock; a lane-local lock would not
# exclude an already running exact arithmetic worker from another frontier.
LOCK = DATA / ".low_contention_sequential.lock"
POINTS_24 = libgap.eval("[1..24]")
PAIR_IN_NAME = re.compile(r"(24T\d+)_r(\d+)")
RESERVED_RESULT_STATUSES = {
    "certified_live_gold",
    "certified_live_gold_even_generic_negative_quadratic_twist",
    "certified_live_gold_staged_negative_quadratic_twist",
    "certified_staged",
    "exact_frozen_gold_hit",
    "exact_live_hit",
    "hit_staged",
}
ALLOWED_CANDIDATE_STATUSES = {
    "resolved_not_planned_gold_signature",
    "resolved_not_current_live_gold",
    "resolved_known_coefficient",
    "resolved_pair_claimed",
    "hit_staged",
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_json_value(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def render_jsonl(rows: list[dict]) -> str:
    return "".join(
        json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n" for row in rows
    )


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_create(path: Path, value: str) -> None:
    """Publish a claim/certificate with O_EXCL semantics."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def validate_artifact(row: dict) -> Path:
    path = ROOT / str(row["path"])
    if not path.is_file() or sha256_path(path) != str(row["sha256"]):
        raise ValueError(f"pinned input changed: {path}")
    return path


def database_boundary(path: Path) -> dict:
    main_stat = path.stat()
    wal_path = path.with_name(path.name + "-wal")
    wal = None
    if wal_path.is_file() and wal_path.stat().st_size > 0:
        stat = wal_path.stat()
        wal = {
            "mtimeNs": int(stat.st_mtime_ns),
            "sha256": sha256_path(wal_path),
            "size": int(stat.st_size),
        }
    return {
        "main": {"mtimeNs": int(main_stat.st_mtime_ns), "size": int(main_stat.st_size)},
        "wal": wal,
    }


def validate_database_sidecars(db_path: Path, expected: dict) -> None:
    path = db_path.with_name(db_path.name + "-wal")
    row = expected.get("wal")
    if row is None:
        if path.exists() and (not path.is_file() or path.stat().st_size > 0):
            raise ValueError("ledger WAL appeared after plan seal")
    elif not path.is_file() or sha256_path(path) != str(row["sha256"]):
        raise ValueError("ledger WAL changed after plan seal")


def polynomial_hash(line: str, path: Path) -> str:
    values = line.strip().split(",")
    try:
        parsed = [int(value) for value in values]
    except ValueError as exc:
        raise ValueError(f"malformed polynomial line: {path}") from exc
    if len(parsed) != 25 or parsed[-1] != 1:
        raise ValueError(f"non-monic degree-24 polynomial line: {path}")
    return sha256_bytes(line.strip().encode("utf-8"))


def walk_coefficient_hashes(value: object, result: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                key in {"coefficientSha256", "candidateSha256"}
                and isinstance(item, str)
                and len(item) == 64
            ):
                result.add(item)
            walk_coefficient_hashes(item, result)
    elif isinstance(value, list):
        for item in value:
            walk_coefficient_hashes(item, result)


def known_external_hashes(
    own_root: Path, own_manifest: Path, connection: sqlite3.Connection
) -> set[str]:
    hashes = {
        str(row[0])
        for row in connection.execute("SELECT DISTINCT coefficient_hash FROM polynomials")
    }
    for path in OUTBOX.glob("*.txt"):
        if path.resolve() == own_manifest.resolve():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                hashes.add(polynomial_hash(line, path))
    for path in RECEIPTS.glob("sub_*.json"):
        receipt = read_json(path)
        manifest_value = receipt.get("manifest")
        if not isinstance(manifest_value, str):
            continue
        manifest_path = Path(manifest_value).expanduser().resolve()
        if (
            manifest_path.is_file()
            and manifest_path != own_manifest.resolve()
            and sha256_path(manifest_path) == str(receipt.get("manifestHash"))
        ):
            for line in manifest_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    hashes.add(polynomial_hash(line, manifest_path))
    for suffix in ("*.json", "*.jsonl"):
        for path in DATA.rglob(suffix):
            if not path.is_file() or own_root.resolve() in path.resolve().parents:
                continue
            if suffix == "*.json":
                walk_coefficient_hashes(read_json_value(path), hashes)
            else:
                for row in read_jsonl(path):
                    walk_coefficient_hashes(row, hashes)
    return hashes


def assert_manifest_unreceipted(manifest: Path) -> None:
    for receipt_path in RECEIPTS.glob("sub_*.json"):
        receipt = read_json(receipt_path)
        value = receipt.get("manifest")
        if not isinstance(value, str):
            continue
        if Path(value).expanduser().resolve() == manifest.resolve():
            raise ValueError(
                f"refusing to create, rewrite, or extend receipted manifest: {receipt_path}"
            )


def result_reserved_pair(row: dict) -> tuple[str, int] | None:
    status = str(row.get("status") or "")
    if status not in RESERVED_RESULT_STATUSES and not row.get("manifest"):
        return None
    target = row.get("target") or {}
    label = target.get("label", row.get("targetLabel"))
    signature = target.get("r", row.get("targetR"))
    if isinstance(label, str) and signature is not None:
        return label, int(signature)
    return None


def external_claimed_pairs(own_root: Path, own_manifest: Path) -> set[tuple[str, int]]:
    pairs: set[tuple[str, int]] = set()
    for path in DATA.rglob("*.json"):
        if not path.is_file() or own_root.resolve() in path.resolve().parents:
            continue
        if not (
            "claim" in path.name.lower()
            or any(
                "claim" in parent.name.lower()
                for parent in path.parents
                if parent != ROOT
            )
        ):
            continue
        row = read_json(path)
        label = row.get("targetLabel", row.get("label"))
        signature = row.get("targetR", row.get("r"))
        if isinstance(label, str) and signature is not None:
            pairs.add((label, int(signature)))
    for path in DATA.rglob("*.jsonl"):
        if not path.is_file() or own_root.resolve() in path.resolve().parents:
            continue
        for row in read_jsonl(path):
            pair = result_reserved_pair(row)
            if pair is not None:
                pairs.add(pair)
    for path in OUTBOX.glob("*.txt"):
        if path.resolve() == own_manifest.resolve():
            continue
        match = PAIR_IN_NAME.search(path.name)
        if match is not None:
            pairs.add((match.group(1), int(match.group(2))))
    return pairs


def block_action_data(
    blocks: list[tuple[int, int]], permutation
) -> tuple[list[int], list[int]]:
    point_to_block = {}
    point_sign = {}
    for index, block in enumerate(blocks):
        if len(block) != 2 or block[0] == block[1]:
            raise ValueError("source block system is not twelve two-point blocks")
        point_to_block[block[0]] = index
        point_to_block[block[1]] = index
        point_sign[block[0]] = 0
        point_sign[block[1]] = 1
    if set(point_to_block) != set(range(1, 25)):
        raise ValueError("source block system does not partition 1..24")
    block_permutation = []
    sign_vector = []
    for block in blocks:
        image = int(libgap.OnPoints(block[0], permutation))
        if image not in point_to_block:
            raise ValueError("source group does not preserve the literal block system")
        block_permutation.append(point_to_block[image])
        sign_vector.append(point_sign[image])
    if sorted(block_permutation) != list(range(12)):
        raise ValueError("induced block map is not a permutation")
    return block_permutation, sign_vector


def pair_orbits(block_generators: list[list[int]]) -> list[list[tuple[int, int]]]:
    remaining = {
        (first, second) for first in range(12) for second in range(first + 1, 12)
    }
    orbits = []
    while remaining:
        start = min(remaining)
        orbit = {start}
        queue = deque([start])
        while queue:
            pair = queue.popleft()
            for permutation in block_generators:
                image = tuple(sorted((permutation[pair[0]], permutation[pair[1]])))
                if image not in orbit:
                    orbit.add(image)
                    queue.append(image)
        remaining.difference_update(orbit)
        orbits.append(sorted(orbit))
    return sorted(orbits, key=lambda orbit: (len(orbit), orbit))


def induced_permutation(
    orbit: list[tuple[int, int]], block_permutation: list[int], sign_vector: list[int]
):
    position = {pair: index for index, pair in enumerate(orbit)}
    images = []
    for pair in orbit:
        image_pair = tuple(
            sorted((block_permutation[pair[0]], block_permutation[pair[1]]))
        )
        if image_pair not in position:
            raise ValueError("pair orbit is not invariant under source generator")
        target = position[image_pair]
        sign = sign_vector[pair[0]] ^ sign_vector[pair[1]]
        images.extend([2 * target + 1 + sign, 2 * target + 2 - sign])
    return libgap.PermList(images)


def signature_map(
    source_group, blocks: list[tuple[int, int]], orbit: list[tuple[int, int]]
) -> dict[str, list[int]]:
    result: dict[int, set[int]] = defaultdict(set)
    for conjugacy_class in libgap.ConjugacyClasses(source_group):
        representative = libgap.Representative(conjugacy_class)
        if int(libgap.Order(representative)) not in (1, 2):
            continue
        source_r = 24 - int(libgap.NrMovedPoints(representative))
        block_permutation, sign_vector = block_action_data(blocks, representative)
        target = induced_permutation(orbit, block_permutation, sign_vector)
        target_r = 24 - int(libgap.NrMovedPoints(target))
        result[source_r].add(target_r)
    return {str(key): sorted(values) for key, values in sorted(result.items())}


EXACT_ACTION_CACHE: dict[str, dict[str, dict]] = {}


def rebuild_exact_actions(source_label: str, raw: dict) -> dict[str, dict]:
    if source_label in EXACT_ACTION_CACHE:
        return EXACT_ACTION_CACHE[source_label]
    systems = list(raw.get("systems") or [])
    if len(systems) != 1:
        raise ValueError(f"{source_label} does not have exactly one literal block system")
    system = systems[0]
    source_t = int(raw["sourceT"])
    if source_label != f"24T{source_t}":
        raise ValueError("source label/T mismatch")
    source_group = libgap.TransitiveGroup(24, source_t)
    source_order = int(libgap.Size(source_group))
    if source_order != int(raw["sourceOrder"]):
        raise ValueError("source group order differs from literal-system map")
    generators = list(libgap.GeneratorsOfGroup(source_group))
    blocks = [tuple(sorted(int(value) for value in block)) for block in system["blocks"]]
    generator_data = [block_action_data(blocks, generator) for generator in generators]
    block_generators = [
        libgap.PermList([value + 1 for value in block_permutation])
        for block_permutation, _ in generator_data
    ]
    block_group = libgap.Group(block_generators)
    block_order = int(libgap.Size(block_group))
    block_t = int(libgap.TransitiveIdentification(block_group))
    flip_images = list(range(1, 25))
    for first, second in blocks:
        flip_images[first - 1] = second
        flip_images[second - 1] = first
    global_flip = libgap.PermList(flip_images)
    flip_in_source = bool(global_flip in source_group)
    if (
        block_order != int(system["blockActionOrder"])
        or block_t != int(system["blockActionT12"])
        or flip_in_source != bool(system["flipInSource"])
        or source_order % block_order
    ):
        raise ValueError("independently rebuilt block action differs from cached system")
    size_twelve = [
        orbit for orbit in pair_orbits([row[0] for row in generator_data]) if len(orbit) == 12
    ]
    if len(size_twelve) < 2:
        raise ValueError("source is not an exact multi-orbit source")
    rebuilt: dict[str, dict] = {}
    for orbit in size_twelve:
        target_generators = [
            induced_permutation(orbit, block_permutation, sign_vector)
            for block_permutation, sign_vector in generator_data
        ]
        target_group = libgap.Group(target_generators)
        if not bool(libgap.IsTransitive(target_group, POINTS_24)):
            raise ValueError("rebuilt signed-pair action is not transitive")
        target_t = int(libgap.TransitiveIdentification(target_group))
        target_order = int(libgap.Size(target_group))
        if target_order % block_order:
            raise ValueError("target order is incompatible with rebuilt block action")
        row = {
            "pairOrbit": [list(pair) for pair in orbit],
            "quotientT12": block_t,
            "sourceBlockSystem": [list(block) for block in blocks],
            "sourceLabel": source_label,
            "sourceSignatureToPossibleTargetSignatures": signature_map(
                source_group, blocks, orbit
            ),
            "sourceT": source_t,
            "targetKernelOrder": target_order // block_order,
            "targetLabel": f"24T{target_t}",
            "targetOrder": target_order,
            "targetT": target_t,
        }
        digest = action_sha256(row)
        if digest in rebuilt:
            raise ValueError("independent action reconstruction produced a duplicate")
        rebuilt[digest] = row
    EXACT_ACTION_CACHE[source_label] = rebuilt
    return rebuilt


def cycle_type(permutation) -> tuple[int, ...]:
    return tuple(sorted(int(value) for value in libgap.CycleLengths(permutation, POINTS_24)))


JOINT_CACHE: dict[tuple, tuple[dict, dict]] = {}


def joint_profile_census(source_label: str, actions: list[dict]) -> tuple[dict, dict]:
    key = (
        source_label,
        tuple(tuple(tuple(pair) for pair in action["pairOrbit"]) for action in actions),
    )
    if key in JOINT_CACHE:
        return JOINT_CACHE[key]
    block_systems = {
        tuple(tuple(block) for block in action["sourceBlockSystem"]) for action in actions
    }
    if len(block_systems) != 1:
        raise ValueError("joint dispatcher crossed literal source block systems")
    blocks = list(next(iter(block_systems)))
    source_group = libgap.TransitiveGroup(24, int(actions[0]["sourceT"]))
    profiles_by_source: dict[tuple[int, ...], set[tuple[tuple[int, ...], ...]]] = (
        defaultdict(set)
    )
    serializable = []
    classes = list(libgap.ConjugacyClasses(source_group))
    for conjugacy_class in classes:
        representative = libgap.Representative(conjugacy_class)
        source_pattern = cycle_type(representative)
        block_permutation, sign_vector = block_action_data(blocks, representative)
        profile = tuple(
            cycle_type(
                induced_permutation(
                    [tuple(pair) for pair in action["pairOrbit"]],
                    block_permutation,
                    sign_vector,
                )
            )
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
    metadata = {
        "jointCycleProfileCount": sum(len(value) for value in profiles_by_source.values()),
        "jointCycleProfileSetSha256": sha256_bytes(
            json.dumps(serializable, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            )
        ),
        "sourceConjugacyClassCount": len(classes),
        "sourceCycleTypeCount": len(profiles_by_source),
    }
    JOINT_CACHE[key] = profiles_by_source, metadata
    return profiles_by_source, metadata


def modular_pattern(coefficients: list[int], prime: int) -> tuple[int, ...] | None:
    ring = PolynomialRing(GF(prime), "z")
    factorization = list(ring(coefficients).factor())
    if any(int(exponent) != 1 for _, exponent in factorization):
        return None
    return tuple(sorted(int(factor.degree()) for factor, _ in factorization))


def resolve_labels(
    source_label: str,
    source_coefficients: list[int],
    candidate_coefficients: list[list[int]],
    actions: list[dict],
) -> dict:
    count = len(actions)
    if count < 2 or len(candidate_coefficients) != count:
        raise ValueError("multi-orbit candidate/action counts differ")
    target_labels = [str(action["targetLabel"]) for action in actions]
    assignments = list(itertools.permutations(range(count)))
    if len(set(target_labels)) == 1:
        return {
            "assignmentMethod": "identical-exact-target-label-multiset",
            "candidateTargetLabels": [target_labels[0]] * count,
            "jointProfileCensus": None,
            "labelAssignmentCount": 1,
            "remainingSlotAssignments": [list(value) for value in assignments],
            "slotAssignmentRequiredForLabels": False,
            "usablePrimes": 0,
            "eliminatingObservations": [],
        }

    profiles_by_source, census = joint_profile_census(source_label, actions)
    evidence = []
    usable = 0
    examined = 0
    nonsquarefree = 0
    for prime_value in prime_range(2, 5000):
        prime = int(prime_value)
        examined += 1
        source_pattern = modular_pattern(source_coefficients, prime)
        observed = [modular_pattern(coefficients, prime) for coefficients in candidate_coefficients]
        if source_pattern is None or any(pattern is None for pattern in observed):
            nonsquarefree += 1
            continue
        usable += 1
        before = list(assignments)
        assignments = filter_slot_assignments(
            assignments, observed, profiles_by_source.get(source_pattern, set())
        )
        if len(assignments) < len(before):
            evidence.append(
                {
                    "afterLabelAssignments": len(label_assignments(assignments, target_labels)),
                    "afterSlotAssignments": len(assignments),
                    "beforeLabelAssignments": len(label_assignments(before, target_labels)),
                    "beforeSlotAssignments": len(before),
                    "candidateFactorDegrees": [list(pattern) for pattern in observed],
                    "prime": prime,
                    "sourceFactorDegrees": list(source_pattern),
                }
            )
        if not assignments or len(label_assignments(assignments, target_labels)) == 1:
            break
    if not assignments:
        raise ArithmeticError("joint modular profiles contradict every action assignment")
    remaining_labels = label_assignments(assignments, target_labels)
    if len(remaining_labels) != 1:
        raise ArithmeticError(
            "UNRESOLVED_LABEL_ASSIGNMENT:"
            + json.dumps(
                {
                    "candidateTargetLabelAssignments": [
                        list(value) for value in sorted(remaining_labels)
                    ],
                    "eliminatingObservations": evidence,
                    "jointProfileCensus": census,
                    "nonsquarefreePrimes": nonsquarefree,
                    "primesExamined": examined,
                    "remainingSlotAssignments": [list(value) for value in assignments],
                    "usablePrimes": usable,
                },
                sort_keys=True,
            )
        )
    return {
        "assignmentMethod": "joint-source-and-sibling-modular-action-profiles",
        "candidateTargetLabels": list(next(iter(remaining_labels))),
        "eliminatingObservations": evidence,
        "jointProfileCensus": census,
        "labelAssignmentCount": 1,
        "nonsquarefreePrimes": nonsquarefree,
        "primesExamined": examined,
        "remainingSlotAssignments": [list(value) for value in assignments],
        "slotAssignmentRequiredForLabels": True,
        "usablePrimes": usable,
    }


def validate_source_plan(
    selected: dict, raw_by_label: dict[str, dict], cached_actions: dict[str, dict]
) -> list[dict]:
    source = selected["source"]
    label = str(source["label"])
    if label != str(selected["sourceLabel"]):
        raise ValueError("selected source label fields disagree")
    raw = raw_by_label.get(label)
    if raw is None or len(raw.get("systems") or []) != 1:
        raise ValueError("selected source fails strict one-literal-block-system gate")
    digests = [str(value) for value in selected["actionSha256s"]]
    if len(digests) < 2 or len(digests) != len(set(digests)):
        raise ValueError("selected multi-orbit action list is malformed")
    actions = []
    for digest in digests:
        action = cached_actions.get(digest)
        if action is None or str(action["sourceLabel"]) != label:
            raise ValueError("selected cached action is absent or belongs to another source")
        if action["sourceBlockSystem"] != raw["systems"][0]["blocks"]:
            raise ValueError("selected action differs from sole literal source block system")
        actions.append(action)
    actions.sort(key=lambda row: (str(row["targetLabel"]), row["pairOrbit"], action_sha256(row)))
    if (
        len(actions) != int(selected["factorCount"])
        or dispatch_kind(actions) != selected["dispatchKind"]
        or sorted(str(action["targetLabel"]) for action in actions)
        != sorted(str(value) for value in selected["targetLabels"])
    ):
        raise ValueError("selected exact action multiset differs from plan")
    rebuilt = rebuild_exact_actions(label, raw)
    if set(digests) != set(rebuilt):
        raise ValueError("cached actions differ from independent sole-system reconstruction")
    for digest in digests:
        if action_core(cached_actions[digest]) != action_core(rebuilt[digest]):
            raise ValueError("cached action metadata differs from independent reconstruction")
    return actions


def derive_source(
    selected: dict,
    position: int,
    connection: sqlite3.Connection,
    actions: list[dict],
) -> tuple[list[object], dict]:
    source = selected["source"]
    row = connection.execute(
        "SELECT p.coefficients,p.coefficient_hash,v.label,v.t,v.r,v.status,v.scoreable "
        "FROM polynomials p JOIN verifications v USING(submission_id,polynomial_index) "
        "WHERE p.submission_id=? AND p.polynomial_index=?",
        (source["submissionId"], int(source["polynomialIndex"])),
    ).fetchone()
    if row is None:
        raise ValueError("sealed source disappeared from ledger")
    coefficient_text = str(row["coefficients"])
    quotient_text = quotient_from_even_coefficients(coefficient_text)
    if (
        quotient_text is None
        or str(row["coefficient_hash"]) != str(source["coefficientSha256"])
        or str(row["label"]) != str(source["label"])
        or int(row["t"]) != int(source["t"])
        or int(row["r"]) != int(source["r"])
        or str(row["status"]) != "accepted"
        or int(row["scoreable"]) != 1
        or canonical_quotient_sha256(quotient_text)
        != str(selected["canonicalQuotientSha256"])
    ):
        raise ValueError("sealed source provenance or canonical quotient changed")
    source_coefficients = [int(value) for value in coefficient_text.split(",")]
    ring_y = PolynomialRing(ZZ, f"y{position}")
    quotient = ring_y([ZZ(value) for value in quotient_text.split(",")])
    if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
        raise ValueError("source quotient is not monic irreducible degree 12")
    pair_resolvent = quotient.symmetric_power(2, monic=True)
    factors = [(factor, int(exponent)) for factor, exponent in pair_resolvent.factor()]
    if any(factor.degree() == 12 and exponent != 1 for factor, exponent in factors):
        raise ValueError("degree-12 pair-resolvent factor is not simple")
    degree_twelve = [factor for factor, exponent in factors if factor.degree() == 12]
    if len(degree_twelve) != len(actions) or len(degree_twelve) != int(
        selected["factorCount"]
    ):
        raise ValueError("degree-12 factor count differs from exact action count")
    ring_x = PolynomialRing(ZZ, f"x{position}")
    x = ring_x.gen()
    candidates = [ring_x(factor)(x**2) for factor in degree_twelve]
    if any(
        candidate.degree() != 24
        or not candidate.is_monic()
        or not candidate.is_irreducible()
        for candidate in candidates
    ):
        raise ValueError("factor lift is not monic irreducible degree 24")
    candidate_coefficients = [
        [int(value) for value in candidate.list()] for candidate in candidates
    ]
    assignment = resolve_labels(
        str(source["label"]), source_coefficients, candidate_coefficients, actions
    )
    if int(assignment["labelAssignmentCount"]) != 1:
        raise ArithmeticError("dispatcher did not prove a unique target-label assignment")
    result = {
        "assignmentCertificate": assignment,
        "candidateResults": [],
        "exactActionSha256s": [action_sha256(action) for action in actions],
        "factorDegrees": [
            {"degree": int(factor.degree()), "exponent": exponent}
            for factor, exponent in factors
        ],
        "pairResolventSha256": sha256_bytes(
            ",".join(str(value) for value in pair_resolvent.list()).encode("utf-8")
        ),
        "source": source,
        "sourceCanonicalQuotientSha256": str(selected["canonicalQuotientSha256"]),
        "sourcePosition": position,
    }
    labels = list(assignment["candidateTargetLabels"])
    if len(labels) != len(candidates):
        raise ValueError("dispatcher returned the wrong number of candidate labels")
    for factor_index, (candidate, target_label) in enumerate(zip(candidates, labels)):
        matching = [
            action for action in actions if str(action["targetLabel"]) == str(target_label)
        ]
        if not matching:
            raise ValueError("assigned target label is absent from exact actions")
        target_t = {int(action["targetT"]) for action in matching}
        target_order = {int(action["targetOrder"]) for action in matching}
        target_kernel = {int(action["targetKernelOrder"]) for action in matching}
        if len(target_t) != 1 or len(target_order) != 1 or len(target_kernel) != 1:
            raise ValueError("same target label has inconsistent exact action metadata")
        signature = int(candidate.number_of_real_roots())
        coefficient_line = ",".join(str(value) for value in candidate.list())
        result["candidateResults"].append(
            {
                "coefficientLine": coefficient_line,
                "coefficientSha256": sha256_bytes(coefficient_line.encode("utf-8")),
                "exactTarget": {
                    "kernelOrder": next(iter(target_kernel)),
                    "label": str(target_label),
                    "order": next(iter(target_order)),
                    "r": signature,
                    "t": next(iter(target_t)),
                },
                "factorIndex": factor_index,
                "irreducible": True,
            }
        )
    return candidates, result


def live_target(
    connection: sqlite3.Connection,
    pair: tuple[str, int],
    frozen: dict[tuple[str, int], dict],
) -> tuple[bool, dict | None]:
    row = connection.execute(
        "SELECT t.*,"
        "EXISTS(SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r) baseline,"
        "EXISTS(SELECT 1 FROM verifications v WHERE v.label=t.label AND v.r=t.r "
        "AND v.status='accepted' AND v.scoreable=1) owned "
        "FROM targets t WHERE t.label=? AND t.r=?",
        pair,
    ).fetchone()
    if row is None:
        return False, None
    snapshot = {
        "baseline": bool(row["baseline"]),
        "discovered": bool(row["discovered"]),
        "generatedAt": row["generated_at"],
        "minimumDiscAbs": row["minimum_disc_abs"],
        "owned": bool(row["owned"]),
        "teamCount": int(row["team_count"]),
    }
    return bool(
        pair in frozen
        and not snapshot["baseline"]
        and not snapshot["owned"]
        and not snapshot["discovered"]
        and snapshot["teamCount"] == 0
    ), snapshot


def load_own_claims(claims_dir: Path, wave_id: str) -> dict[tuple[str, int], dict]:
    claims = {}
    if not claims_dir.is_dir():
        return claims
    for path in sorted(claims_dir.glob("*.json")):
        row = read_json(path)
        pair = (str(row.get("targetLabel") or ""), int(row.get("targetR", -1)))
        if (
            row.get("schemaVersion") != "f5-multiorbit-claim-v1"
            or row.get("owner") != wave_id
            or not pair[0]
            or pair[1] < 0
            or path.name != f"{pair[0]}_r{pair[1]}.json"
            or pair in claims
            or len(str(row.get("candidateSha256") or "")) != 64
            or len(str(row.get("sourceCanonicalQuotientSha256") or "")) != 64
        ):
            raise ValueError("own atomic claim is malformed or duplicated")
        claims[pair] = row
    return claims


def validate_manifest_checkpoint(
    manifest: Path, claims: dict[tuple[str, int], dict]
) -> None:
    assert_manifest_unreceipted(manifest)
    if not manifest.exists():
        return
    if not claims:
        raise ValueError("manifest exists without an atomic claim")
    ordered = sorted(
        claims.values(),
        key=lambda row: (int(row["sourcePosition"]), int(row["factorIndex"])),
    )
    claim_lines = [str(row["coefficientLine"]) for row in ordered]
    manifest_lines = [
        line.strip()
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(manifest_lines) != len(set(manifest_lines)):
        raise ValueError("checkpoint manifest contains a duplicate polynomial")
    positions = []
    for line in manifest_lines:
        if line not in claim_lines:
            raise ValueError("checkpoint manifest contains an unclaimed polynomial")
        positions.append(claim_lines.index(line))
    if positions != sorted(positions):
        raise ValueError("checkpoint manifest order differs from atomic claim order")


def rebuild_manifest(manifest: Path, claims: dict[tuple[str, int], dict]) -> None:
    assert_manifest_unreceipted(manifest)
    if not claims:
        if manifest.exists():
            raise ValueError("manifest exists without an atomic claim")
        return
    ordered = sorted(
        claims.values(),
        key=lambda row: (int(row["sourcePosition"]), int(row["factorIndex"])),
    )
    lines = [str(row["coefficientLine"]) for row in ordered]
    if len(lines) != len(set(lines)):
        raise ValueError("atomic claims contain duplicate candidate polynomials")
    for row, line in zip(ordered, lines):
        if polynomial_hash(line, manifest) != str(row["candidateSha256"]):
            raise ValueError("atomic claim coefficient hash mismatch")
    atomic_text(manifest, "".join(line + "\n" for line in lines))


def expected_certificate(
    plan: dict,
    plan_sha256: str,
    audit_path: Path,
    audit_sha256: str,
    source_result: dict,
    candidate_result: dict,
) -> dict:
    return {
        "schemaVersion": "f5-multiorbit-hit-certificate-v1",
        "method": plan["mechanism"],
        "waveId": plan["waveId"],
        "plan": str((ROOT / plan["artifacts"]["results"]).parent.joinpath("plan.json").relative_to(ROOT)),
        "planSha256": plan_sha256,
        "independentAudit": str(audit_path.relative_to(ROOT)),
        "independentAuditSha256": audit_sha256,
        "source": source_result["source"],
        "sourceCanonicalQuotientSha256": source_result[
            "sourceCanonicalQuotientSha256"
        ],
        "sourcePosition": source_result["sourcePosition"],
        "factorIndex": candidate_result["factorIndex"],
        "assignmentCertificate": source_result["assignmentCertificate"],
        "exactActionSha256s": source_result["exactActionSha256s"],
        "candidate": candidate_result,
        "sideEffects": {"networkCalls": 0, "submissionCalls": 0, "ledgerWrites": 0},
    }


def validate_claim_certificate(
    claim: dict,
    plan: dict,
    plan_sha256: str,
    audit_path: Path,
    audit_sha256: str,
    source_result: dict,
    candidate_result: dict,
) -> None:
    certificate_path = ROOT / str(claim["certificate"])
    expected = expected_certificate(
        plan,
        plan_sha256,
        audit_path,
        audit_sha256,
        source_result,
        candidate_result,
    )
    expected_text = json.dumps(expected, indent=2, sort_keys=True) + "\n"
    if (
        not certificate_path.is_file()
        or sha256_path(certificate_path) != str(claim["certificateSha256"])
        or certificate_path.read_text(encoding="utf-8") != expected_text
    ):
        raise ValueError("atomic claim certificate differs from exact recomputation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--independent-audit", type=Path, required=True)
    parser.add_argument("--expected-audit-sha256", required=True)
    parser.add_argument("--maximum-sources", type=int, required=True)
    parser.add_argument("--continuation-audit", type=Path)
    parser.add_argument("--expected-continuation-audit-sha256")
    args = parser.parse_args()
    if args.maximum_sources <= 0:
        raise ValueError("maximum source count must be positive")

    plan_path = args.plan.resolve()
    plan_text = plan_path.read_text(encoding="utf-8")
    plan_sha256 = sha256_bytes(plan_text.encode("utf-8"))
    if plan_sha256 != args.expected_plan_sha256 or sha256_path(plan_path) != plan_sha256:
        raise ValueError("plan SHA256 differs from explicit launch pin")
    if '"quotientLine"' in plan_text or '"coefficientLine"' in plan_text:
        raise ValueError("sealed plan contains coefficient-bearing fields")
    plan = json.loads(plan_text)
    execution = plan.get("execution") or {}
    if (
        plan.get("schemaVersion") != "f5-multiorbit-frontier-plan-v1"
        or plan.get("status") != "awaiting_independent_audit"
        or plan.get("coefficientMaterialIncluded") is not False
        or execution.get("independentAuditRequired") is not True
        or execution.get("heavyWorkerLaunched") is not False
        or execution.get("oneWorkerAtATimeLockRequired") is not True
        or execution.get("submissionAuthorized") is not False
        or execution.get("continuationAuditRequiredAfterPilot") is not True
    ):
        raise ValueError("plan is not a sealed audit-gated multi-orbit handoff")

    audit_path = args.independent_audit.resolve()
    if audit_path in {plan_path, Path(__file__).resolve()}:
        raise ValueError("independent audit must be a separate artifact")
    audit_sha256 = sha256_path(audit_path)
    if audit_sha256 != args.expected_audit_sha256:
        raise ValueError("independent-audit SHA256 differs from explicit launch pin")
    audit = read_json(audit_path)
    required_true = (
        "reviewedFullFrontier",
        "reviewedStrictSingleBlockSystemGate",
        "reviewedAllPriorExclusions",
        "reviewedCoefficientFreeBoundary",
    )
    reviewer = str(audit.get("reviewer") or "")
    if (
        audit.get("schemaVersion") != "f5-multiorbit-independent-audit-v1"
        or audit.get("decision") != "pass"
        or audit.get("planSha256") != plan_sha256
        or audit.get("workerSha256") != plan["artifacts"]["worker"]["sha256"]
        or audit.get("commonSha256") != plan["artifacts"]["common"]["sha256"]
        or not all(audit.get(key) is True for key in required_true)
        or not reviewer
        or reviewer.startswith("<")
    ):
        raise ValueError("independent audit is absent, incomplete, or not tied to this code")

    artifacts = plan["artifacts"]
    for name in (
        "actionMap",
        "blockedLabels",
        "claimedPairIndex",
        "common",
        "frozenGold",
        "frontier",
        "ledger",
        "worker",
    ):
        validate_artifact(artifacts[name])
    for name in (
        "actionShards",
        "priorF5Results",
        "priorK2Results",
        "explicitExtraExclusionCertificates",
    ):
        for artifact_row in artifacts[name]:
            validate_artifact(artifact_row)
    if sha256_path(Path(__file__).resolve()) != artifacts["worker"]["sha256"]:
        raise ValueError("worker changed after plan seal")
    common_path = ROOT / artifacts["common"]["path"]
    if sha256_path(common_path) != artifacts["common"]["sha256"]:
        raise ValueError("shared dispatcher helpers changed after plan seal")
    db_path = ROOT / artifacts["ledger"]["path"]
    validate_database_sidecars(db_path, artifacts["databaseSidecars"])

    selected = list(plan["sources"])
    if len(selected) != 39 or args.maximum_sources > len(selected):
        raise ValueError("sealed frontier or maximum source count is invalid")
    selected_hashes = [str(row["canonicalQuotientSha256"]) for row in selected]
    if len(selected_hashes) != len(set(selected_hashes)):
        raise ValueError("sealed frontier contains duplicate canonical sources")
    frontier_rows = read_jsonl(ROOT / artifacts["frontier"]["path"])
    if frontier_rows != selected:
        raise ValueError("plan sources differ from pinned ranked frontier")
    exclusions = plan["priorExactExclusions"]
    excluded_hashes = list(exclusions["excludedCanonicalSha256"])
    if (
        len(excluded_hashes) != 434
        or len(set(excluded_hashes)) != 434
        or set(selected_hashes) & set(excluded_hashes)
        or int(exclusions["priorF5Rows"]) != 428
        or int(exclusions["priorK2Rows"]) != 78
        or len(artifacts["priorF5Results"]) != 75
        or sum(len(read_jsonl(ROOT / row["path"])) for row in artifacts["priorF5Results"])
        != 428
        or sum(len(read_jsonl(ROOT / row["path"])) for row in artifacts["priorK2Results"])
        != 78
    ):
        raise ValueError("sealed 428+78 exact-source exclusion boundary is inconsistent")

    output = ROOT / artifacts["results"]
    summary_path = ROOT / artifacts["summary"]
    claims_dir = ROOT / artifacts["claimsDirectory"]
    certificates_dir = ROOT / artifacts["certificatesDirectory"]
    manifest = ROOT / artifacts["manifest"]
    own_root = output.parent
    frozen = {
        (str(row["label"]), int(row["r"])): row
        for row in read_jsonl(ROOT / artifacts["frozenGold"]["path"])
    }
    claimed_index = read_json(ROOT / artifacts["claimedPairIndex"]["path"])
    for source_artifact in claimed_index["sourceArtifacts"]:
        validate_artifact(source_artifact)
    sealed_claimed = {
        (str(row["label"]), int(row["r"]))
        for row in claimed_index["pairs"]
    }
    completed = read_jsonl(output)
    completed_file_sha256 = sha256_path(output) if output.is_file() else None
    pilot_sources = int(plan["pilotSources"])
    continuation_audit_path = None
    continuation_audit_sha256 = None
    if args.maximum_sources > pilot_sources:
        if (
            args.continuation_audit is None
            or not args.expected_continuation_audit_sha256
            or len(completed) < pilot_sources
        ):
            raise ValueError(
                "continuation beyond the sealed pilot requires its completed prefix and a separate audit"
            )
        continuation_audit_path = args.continuation_audit.resolve()
        continuation_audit_sha256 = sha256_path(continuation_audit_path)
        if continuation_audit_sha256 != args.expected_continuation_audit_sha256:
            raise ValueError("continuation-audit SHA256 differs from explicit launch pin")
        continuation = read_json(continuation_audit_path)
        continuation_reviewer = str(continuation.get("reviewer") or "")
        checkpoint_sha256 = sha256_bytes(
            render_jsonl(completed[:pilot_sources]).encode("utf-8")
        )
        if (
            continuation.get("schemaVersion")
            != "f5-multiorbit-continuation-audit-v1"
            or continuation.get("decision") != "pass"
            or continuation.get("planSha256") != plan_sha256
            or continuation.get("initialAuditSha256") != audit_sha256
            or int(continuation.get("checkpointCompletedSources", -1))
            != pilot_sources
            or continuation.get("checkpointResultsSha256") != checkpoint_sha256
            or int(continuation.get("maximumSourcesAuthorized", 0))
            < args.maximum_sources
            or continuation.get("reviewedPilotOutcomes") is not True
            or not continuation_reviewer
            or continuation_reviewer.startswith("<")
        ):
            raise ValueError("continuation audit is incomplete or tied to another checkpoint")
    elif args.continuation_audit is not None or args.expected_continuation_audit_sha256:
        raise ValueError("continuation-audit arguments are only valid beyond the pilot")

    lock_descriptor = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another heavy arithmetic worker holds the global lock") from exc

        validate_artifact(artifacts["ledger"])
        validate_database_sidecars(db_path, artifacts["databaseSidecars"])
        db_state = database_boundary(db_path)
        if (sha256_path(output) if output.is_file() else None) != completed_file_sha256:
            raise ValueError("results checkpoint changed before the heavy-worker lock was acquired")

        def validate_db_boundary() -> None:
            if database_boundary(db_path) != db_state:
                raise ValueError("ledger/WAL changed during exact arithmetic")

        if external_claimed_pairs(own_root, manifest) != sealed_claimed:
            raise ValueError("external claimed-pair boundary changed after plan seal")
        connection = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")

        raw_rows = read_jsonl(ROOT / artifacts["actionMap"]["path"])
        raw_by_label = {str(row["sourceLabel"]): row for row in raw_rows}
        if len(raw_by_label) != len(raw_rows):
            raise ValueError("literal block-system map contains duplicate labels")
        cached_actions = {}
        for shard in artifacts["actionShards"]:
            for row in read_jsonl(ROOT / shard["path"]):
                digest = action_sha256(row)
                if digest in cached_actions and cached_actions[digest] != row:
                    raise ValueError("conflicting cached action digest")
                cached_actions[digest] = row
        actions_by_position = [
            validate_source_plan(row, raw_by_label, cached_actions) for row in selected
        ]

        if len(completed) > args.maximum_sources:
            raise ValueError("checkpoint is beyond requested maximum source count")
        for position, row in enumerate(completed, 1):
            expected = selected[position - 1]
            if (
                row.get("sourceCanonicalQuotientSha256")
                != expected["canonicalQuotientSha256"]
                or row.get("source") != expected["source"]
                or int(row.get("sourcePosition", -1)) != position
            ):
                raise ValueError("results checkpoint is not an exact frontier prefix")
            if any(
                candidate.get("status") not in ALLOWED_CANDIDATE_STATUSES
                for candidate in row.get("candidateResults") or []
            ):
                raise ValueError("results checkpoint contains an invalid candidate status")

        known = known_external_hashes(own_root, manifest, connection)
        own_claims = load_own_claims(claims_dir, str(plan["waveId"]))
        selected_position = {digest: index for index, digest in enumerate(selected_hashes, 1)}
        for pair, claim in own_claims.items():
            source_position = selected_position.get(
                str(claim["sourceCanonicalQuotientSha256"])
            )
            factor_index = int(claim.get("factorIndex", -1))
            expected_certificate_name = (
                f"s{int(source_position or 0):02d}_f{factor_index}_{pair[0]}_r{pair[1]}.json"
            )
            certificate_path = ROOT / str(claim.get("certificate") or "")
            if (
                source_position is None
                or source_position > len(completed) + 1
                or int(claim.get("sourcePosition", -1)) != source_position
                or claim.get("planSha256") != plan_sha256
                or factor_index < 0
                or factor_index >= int(selected[source_position - 1]["factorCount"])
                or polynomial_hash(str(claim.get("coefficientLine") or ""), manifest)
                != str(claim["candidateSha256"])
                or len(str(claim.get("certificateSha256") or "")) != 64
                or certificate_path.parent.resolve() != certificates_dir.resolve()
                or certificate_path.name != expected_certificate_name
                or int(claim.get("fieldDiscriminantAbs", 0)) <= 0
            ):
                raise ValueError("orphan claim is not in the sole next checkpoint window")
        validate_manifest_checkpoint(manifest, own_claims)

        processed_known = set(known)
        processed_targets: set[tuple[str, int]] = set()
        validated_claim_pairs: set[tuple[str, int]] = set()

        def classify_candidate(
            selected_row: dict,
            source_result: dict,
            candidate: object,
            candidate_result: dict,
            external_claims: set[tuple[str, int]],
            allow_stage: bool,
        ) -> str:
            target = candidate_result["exactTarget"]
            pair = (str(target["label"]), int(target["r"]))
            planned_pairs = {
                (str(row["label"]), int(row["r"]))
                for row in selected_row["possibleGoldPairs"]
            }
            live, local = live_target(connection, pair, frozen)
            candidate_result["localLiveRecheck"] = local
            digest = str(candidate_result["coefficientSha256"])
            own_claim = own_claims.get(pair)
            if pair not in planned_pairs:
                status = "resolved_not_planned_gold_signature"
            elif not live:
                status = "resolved_not_current_live_gold"
            elif digest in processed_known:
                status = "resolved_known_coefficient"
            elif pair in external_claims or pair in processed_targets:
                status = "resolved_pair_claimed"
            elif own_claim is not None:
                if (
                    own_claim["candidateSha256"] != digest
                    or own_claim["sourceCanonicalQuotientSha256"]
                    != source_result["sourceCanonicalQuotientSha256"]
                    or int(own_claim["factorIndex"]) != int(candidate_result["factorIndex"])
                    or int(own_claim["sourcePosition"]) != int(source_result["sourcePosition"])
                ):
                    raise ValueError("own orphan/checkpoint claim differs from exact candidate")
                candidate_result["fieldDiscriminantAbs"] = str(
                    own_claim["fieldDiscriminantAbs"]
                )
                status = "hit_staged"
            elif allow_stage:
                field_discriminant = str(
                    abs(
                        ZZ(
                            NumberField(
                                candidate,
                                f"a{source_result['sourcePosition']}_{candidate_result['factorIndex']}",
                            ).absolute_discriminant()
                        )
                    )
                )
                candidate_result["fieldDiscriminantAbs"] = field_discriminant
                validate_db_boundary()
                if external_claimed_pairs(own_root, manifest) != sealed_claimed:
                    raise ValueError("claimed-pair boundary changed during discriminant work")
                refreshed_known = known_external_hashes(own_root, manifest, connection)
                validate_db_boundary()
                if external_claimed_pairs(own_root, manifest) != sealed_claimed:
                    raise ValueError("claimed-pair boundary changed before atomic publication")
                live_after, local_after = live_target(connection, pair, frozen)
                candidate_result["localLiveRecheck"] = local_after
                if not live_after:
                    status = "resolved_not_current_live_gold"
                elif digest in refreshed_known or digest in processed_known:
                    status = "resolved_known_coefficient"
                else:
                    certificate_path = certificates_dir / (
                        f"s{int(source_result['sourcePosition']):02d}_"
                        f"f{int(candidate_result['factorIndex'])}_{pair[0]}_r{pair[1]}.json"
                    )
                    candidate_result["status"] = "hit_staged"
                    certificate = expected_certificate(
                        plan,
                        plan_sha256,
                        audit_path,
                        audit_sha256,
                        source_result,
                        candidate_result,
                    )
                    certificate_text = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
                    if certificate_path.exists():
                        if certificate_path.read_text(encoding="utf-8") != certificate_text:
                            raise ValueError("orphan certificate differs from exact recomputation")
                    else:
                        atomic_create(certificate_path, certificate_text)
                    certificate_digest = sha256_path(certificate_path)
                    claim_path = claims_dir / f"{pair[0]}_r{pair[1]}.json"
                    claim = {
                        "schemaVersion": "f5-multiorbit-claim-v1",
                        "candidateSha256": digest,
                        "certificate": str(certificate_path.relative_to(ROOT)),
                        "certificateSha256": certificate_digest,
                        "coefficientLine": candidate_result["coefficientLine"],
                        "factorIndex": int(candidate_result["factorIndex"]),
                        "fieldDiscriminantAbs": field_discriminant,
                        "owner": plan["waveId"],
                        "planSha256": plan_sha256,
                        "sourceCanonicalQuotientSha256": source_result[
                            "sourceCanonicalQuotientSha256"
                        ],
                        "sourcePosition": int(source_result["sourcePosition"]),
                        "targetLabel": pair[0],
                        "targetR": pair[1],
                    }
                    claim_text = json.dumps(claim, indent=2, sort_keys=True) + "\n"
                    if claim_path.exists():
                        if claim_path.read_text(encoding="utf-8") != claim_text:
                            raise ValueError("atomic claim collision")
                    else:
                        atomic_create(claim_path, claim_text)
                    own_claims[pair] = claim
                    validated_claim_pairs.add(pair)
                    processed_targets.add(pair)
                    status = "hit_staged"
            else:
                status = "hit_staged"
            candidate_result["status"] = status
            if status == "hit_staged" and own_claim is not None:
                validate_claim_certificate(
                    own_claim,
                    plan,
                    plan_sha256,
                    audit_path,
                    audit_sha256,
                    source_result,
                    candidate_result,
                )
                validated_claim_pairs.add(pair)
            if status == "hit_staged":
                processed_targets.add(pair)
            processed_known.add(digest)
            return status

        # Recompute the complete prefix before trusting checkpointed arithmetic.
        for position, checkpoint in enumerate(completed, 1):
            candidates, recomputed = derive_source(
                selected[position - 1], position, connection, actions_by_position[position - 1]
            )
            stable_checkpoint = dict(checkpoint)
            stable_recomputed = dict(recomputed)
            checkpoint_candidates = stable_checkpoint.pop("candidateResults")
            recomputed_candidates = stable_recomputed.pop("candidateResults")
            if stable_checkpoint != stable_recomputed or len(checkpoint_candidates) != len(
                recomputed_candidates
            ):
                raise ValueError("checkpoint source arithmetic differs from exact recomputation")
            for candidate, prior, current in zip(
                candidates, checkpoint_candidates, recomputed_candidates
            ):
                for key in (
                    "coefficientLine",
                    "coefficientSha256",
                    "exactTarget",
                    "factorIndex",
                    "irreducible",
                ):
                    if prior.get(key) != current.get(key):
                        raise ValueError("checkpoint candidate differs from exact recomputation")
                current["fieldDiscriminantAbs"] = prior.get("fieldDiscriminantAbs")
                status = classify_candidate(
                    selected[position - 1],
                    recomputed,
                    candidate,
                    current,
                    sealed_claimed,
                    False,
                )
                if status != prior["status"]:
                    raise ValueError("checkpoint status differs from current exact gates")
                if status == "hit_staged":
                    if current.get("fieldDiscriminantAbs") is None:
                        raise ValueError("staged checkpoint lacks a field discriminant")
                    pair = (
                        str(current["exactTarget"]["label"]),
                        int(current["exactTarget"]["r"]),
                    )
                    claim = own_claims.get(pair)
                    if claim is None:
                        raise ValueError("staged checkpoint lacks its atomic claim")
                    validate_claim_certificate(
                        claim,
                        plan,
                        plan_sha256,
                        audit_path,
                        audit_sha256,
                        recomputed,
                        current,
                    )

        unvalidated_claims = {
            pair: claim
            for pair, claim in own_claims.items()
            if pair not in validated_claim_pairs
        }
        if any(
            int(claim["sourcePosition"]) <= len(completed)
            for claim in unvalidated_claims.values()
        ):
            raise ValueError("completed checkpoint contains an unmatched atomic claim")
        if unvalidated_claims and len(completed) + 1 > args.maximum_sources:
            raise ValueError("next-source orphan claim lies beyond requested execution bound")
        if not unvalidated_claims:
            rebuild_manifest(manifest, own_claims)

        validate_db_boundary()
        if external_claimed_pairs(own_root, manifest) != sealed_claimed:
            raise ValueError("external claim boundary changed before continuation")

        maximum = min(args.maximum_sources, len(selected))
        for position in range(len(completed) + 1, maximum + 1):
            selected_row = selected[position - 1]
            candidates, source_result = derive_source(
                selected_row, position, connection, actions_by_position[position - 1]
            )
            validate_db_boundary()
            current_external_claims = external_claimed_pairs(own_root, manifest)
            if current_external_claims != sealed_claimed:
                raise ValueError("external claimed-pair boundary changed after arithmetic")
            for candidate, candidate_result in zip(
                candidates, source_result["candidateResults"]
            ):
                classify_candidate(
                    selected_row,
                    source_result,
                    candidate,
                    candidate_result,
                    current_external_claims,
                    True,
                )
                print(
                    json.dumps(
                        {
                            "event": "candidate_resolved",
                            "sourcePosition": position,
                            "factorIndex": candidate_result["factorIndex"],
                            "sourceSha256": source_result[
                                "sourceCanonicalQuotientSha256"
                            ],
                            "status": candidate_result["status"],
                            "targetLabel": candidate_result["exactTarget"]["label"],
                            "targetR": candidate_result["exactTarget"]["r"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            if any(
                int(claim["sourcePosition"]) <= position
                and pair not in validated_claim_pairs
                for pair, claim in own_claims.items()
            ):
                raise ValueError("source checkpoint left an unmatched atomic claim")
            completed.append(source_result)
            atomic_text(output, render_jsonl(completed))
            rebuild_manifest(manifest, own_claims)
            print(
                json.dumps(
                    {
                        "event": "source_checkpointed",
                        "completedSources": len(completed),
                        "sourceSha256": source_result[
                            "sourceCanonicalQuotientSha256"
                        ],
                        "frontierSources": len(selected),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        if set(own_claims) != validated_claim_pairs:
            raise ValueError("atomic claim set differs from exactly recomputed staged hits")
        validate_db_boundary()
        connection.close()
    finally:
        os.close(lock_descriptor)

    candidate_rows = [
        candidate for row in completed for candidate in row["candidateResults"]
    ]
    histogram = Counter(str(row["status"]) for row in candidate_rows)
    exact_hits = int(histogram["hit_staged"])
    if len(completed) == len(selected):
        recommendation = "frontier_exhausted"
    elif len(completed) >= int(plan["pilotSources"]) and exact_hits == 0:
        recommendation = "stall_pending_review_zero_hit_canary"
    else:
        recommendation = "independent_review_before_resume"
    results_text = render_jsonl(completed)
    summary = {
        "schemaVersion": "f5-multiorbit-worker-summary-v1",
        "waveId": plan["waveId"],
        "plan": str(plan_path.relative_to(ROOT)),
        "planSha256": plan_sha256,
        "independentAudit": str(audit_path.relative_to(ROOT)),
        "independentAuditSha256": audit_sha256,
        "continuationAudit": (
            str(continuation_audit_path.relative_to(ROOT))
            if continuation_audit_path is not None
            else None
        ),
        "continuationAuditSha256": continuation_audit_sha256,
        "completedSources": len(completed),
        "frontierSources": len(selected),
        "candidatePolynomials": len(candidate_rows),
        "exactHits": exact_hits,
        "mechanismExhausted": len(completed) == len(selected),
        "pilotComplete": len(completed) >= int(plan["pilotSources"]),
        "continuationRecommendation": recommendation,
        "results": str(output.relative_to(ROOT)),
        "resultsSha256": sha256_bytes(results_text.encode("utf-8")),
        "manifest": str(manifest.relative_to(ROOT)) if manifest.exists() else None,
        "statusHistogram": dict(sorted(histogram.items())),
        "sideEffects": {
            "networkCalls": 0,
            "submissionCalls": 0,
            "ledgerWrites": 0,
        },
    }
    atomic_text(summary_path, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
