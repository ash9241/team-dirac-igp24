#!/usr/bin/env sage -python
"""Construct exact low-holder C3 module lifts from regular degree-8 fields.

For a totally real Galois field K and u in K, put

    z(u) = (u+i)/(u-i).

An F_3 group-ring vector e gives the norm-one radicand
``prod_g z(g(u))^e_g``.  Hilbert 90 recovers H in K with radicand
``(H+i)/(H-i)``.  The associated cyclic cubic

    Y^3 - 3(H^2+1)^2 Y - 2(H^2-1)(H^2+1)^2

is totally real at every real place.  A totally real source therefore gives
signature 24, while a totally imaginary source gives signature 0.  The orbit
span of e is the requested Kummer kernel.  Frobenius witnesses against every
proper transitive maximal subgroup certify equality with the exact degree-24
target action.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from sage.all import GF, NumberField, PolynomialRing, QQ, ZZ, libgap, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
SOURCES = DATA / "agent_non12_all_degree8_subfields.jsonl"


def load_certifier():
    path = ROOT / "agent_gold_b_asymmetric_composition_certify.sage.py"
    spec = importlib.util.spec_from_file_location("c3_group_ring_certifier", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def perm_from_images(images):
    return libgap.PermList(libgap([int(value) for value in images]))


def permute_vector(vector, permutation):
    ambient = GF(3) ** 8
    output = [GF(3).zero()] * 8
    for index in range(1, 9):
        image = int(libgap.OnPoints(index, permutation))
        output[image - 1] = vector[index - 1]
    return ambient(output)


def orbit_span(vector, permutations):
    ambient = vector.parent()
    space = ambient.subspace([vector])
    while True:
        images = [
            permute_vector(row, permutation)
            for row in space.basis()
            for permutation in permutations
        ]
        expanded = ambient.subspace(list(space.basis()) + images)
        if expanded.dimension() == space.dimension():
            return space
        space = expanded


def subspace_key(space):
    return tuple(
        tuple(int(value) for value in row)
        for row in space.basis_matrix().rows()
    )


def lifted_group(source_permutations, space):
    generators = []
    for vector in space.basis():
        images = []
        for block in range(8):
            shift = int(vector[block])
            for fiber in range(3):
                images.append(3 * block + ((fiber + shift) % 3) + 1)
        generators.append(perm_from_images(images))
    for source in source_permutations:
        images = []
        for block in range(1, 9):
            image_block = int(libgap.OnPoints(block, source))
            for fiber in range(3):
                images.append(3 * (image_block - 1) + fiber + 1)
        generators.append(perm_from_images(images))
    images = []
    for block in range(8):
        images.extend([3 * block + 1, 3 * block + 3, 3 * block + 2])
    generators.append(perm_from_images(images))
    return libgap.Group(generators)


def target_profile(certifier, target_t):
    target = libgap.TransitiveGroup(24, int(target_t))
    points = libgap.eval("[1..24]")

    def cycle_type(permutation):
        return tuple(
            sorted(int(value) for value in libgap.CycleLengths(permutation, points))
        )

    maximals = [
        subgroup
        for subgroup in libgap.MaximalSubgroupClassReps(target)
        if bool(libgap.IsTransitive(subgroup, points))
    ]
    profiles = []
    identities = []
    for subgroup in maximals:
        profiles.append(
            {
                cycle_type(libgap.Representative(conjugacy_class))
                for conjugacy_class in libgap.ConjugacyClasses(subgroup)
            }
        )
        identities.append(
            {
                "label": f"24T{int(libgap.TransitiveIdentification(subgroup))}",
                "order": int(libgap.Size(subgroup)),
            }
        )
    return {
        "groupOrder": int(libgap.Size(target)),
        "identities": identities,
        "profiles": profiles,
        "targetLabel": f"24T{target_t}",
        "targetT": int(target_t),
    }


def source_row(label, real_roots, source_index=0):
    rows = []
    with SOURCES.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["galoisGroup"]["label"] == label and int(row["realRoots"]) == real_roots:
                rows.append(row)
    if not rows:
        raise ValueError(f"no signature-r{real_roots} source for {label}")
    ordered = sorted(
        rows,
        key=lambda row: (
            int(row.get("coefficientBytes", 10**9)),
            len(str(row["fieldDiscriminantAbs"])),
            row["coefficientSha256"],
        ),
    )
    if source_index < 0 or source_index >= len(ordered):
        raise ValueError(
            f"source index {source_index} outside 0..{len(ordered) - 1} "
            f"for {label} r={real_roots}"
        )
    return ordered[source_index]


def automorphism_action(field):
    generator = field.gen()
    automorphisms = list(field.automorphisms())
    if len(automorphisms) != 8:
        raise ArithmeticError("degree-8 source is not Galois")
    images = [automorphism(generator) for automorphism in automorphisms]
    permutations = []
    for left in automorphisms:
        permuted = []
        for image in images:
            composed = left(image)
            matches = [index for index, value in enumerate(images) if value == composed]
            if len(matches) != 1:
                raise ArithmeticError("automorphism composition lookup failed")
            permuted.append(matches[0] + 1)
        permutations.append(perm_from_images(permuted))
    group = libgap.Group(permutations)
    if not bool(libgap.IsTransitive(group)):
        raise ArithmeticError("automorphism regular action is not transitive")
    return automorphisms, images, permutations, group


def module_targets(permutations, desired):
    ambient = GF(3) ** 8
    modules = {}
    for coordinates in ambient:
        if not coordinates:
            continue
        first = next(value for value in coordinates if value)
        if first != 1:
            continue
        space = orbit_span(coordinates, permutations)
        dimension = int(space.dimension())
        if dimension not in (5, 6):
            continue
        key = subspace_key(space)
        rank = (
            sum(1 for value in coordinates if value),
            tuple(min(int(value), 3 - int(value)) for value in coordinates),
        )
        incumbent = modules.get(key)
        if incumbent is None or rank < incumbent[0]:
            modules[key] = (rank, coordinates, space)
    targets = {}
    for key, (_rank, vector, space) in modules.items():
        group = lifted_group(permutations, space)
        target_t = int(libgap.TransitiveIdentification(group))
        target_label = f"24T{target_t}"
        if target_label not in desired:
            continue
        value = {
            "basis": [list(row) for row in key],
            "dimension": int(space.dimension()),
            "groupOrder": int(libgap.Size(group)),
            "targetLabel": target_label,
            "targetT": target_t,
            "vector": [int(item) for item in vector],
        }
        incumbent = targets.get(target_label)
        if incumbent is None or tuple(value["vector"]) < tuple(incumbent["vector"]):
            targets[target_label] = value
    return targets, len(modules)


def pair_multiply(left, right):
    a, b = left
    c, d = right
    return a * c - b * d, a * d + b * c


def hilbert90_h(conjugates, vector):
    one = conjugates[0].parent().one()
    beta = (one, conjugates[0].parent().zero())
    for value, exponent in zip(conjugates, vector):
        if exponent == 0:
            continue
        denominator = value**2 + 1
        z_pair = ((value**2 - 1) / denominator, (2 * value) / denominator)
        if exponent == 2:
            z_pair = (z_pair[0], -z_pair[1])
        beta = pair_multiply(beta, z_pair)
    p, q = beta
    denominator = (p - 1) ** 2 + q**2
    if not denominator or not q:
        raise ArithmeticError("degenerate Hilbert-90 radicand")
    h = (2 * q) / denominator
    # Exact reconstruction of beta=(H+i)/(H-i).
    reconstructed = ((h**2 - 1) / (h**2 + 1), (2 * h) / (h**2 + 1))
    if reconstructed != beta:
        raise ArithmeticError("Hilbert-90 reconstruction failed")
    return h


def construct_candidate(source_polynomial, h):
    y_ring = PolynomialRing(QQ, "y")
    y = y_ring.gen()
    z_ring = PolynomialRing(y_ring, "z")
    z = z_ring.gen()
    h_polynomial = h.polynomial()
    h_z = sum(
        QQ(h_polynomial[index]) * z**index
        for index in range(int(h_polynomial.degree()) + 1)
    )
    a_value = h_z**2 + 1
    b_value = h_z**2 - 1
    relative = y**3 - 3 * a_value**2 * y - 2 * b_value * a_value**2
    outer_z = z_ring([QQ(source_polynomial[index]) for index in range(9)])
    candidate = y_ring(outer_z.resultant(relative))
    if candidate.degree() != 24 or not candidate.is_irreducible():
        raise ArithmeticError("raw candidate is reducible")
    reduced = y_ring(pari(candidate).polredbest())
    if reduced.degree() != 24 or not reduced.is_monic() or not reduced.is_irreducible():
        raise ArithmeticError("candidate failed exact degree/irreducibility gate")
    return reduced


def coefficient_line(polynomial):
    return ",".join(str(int(polynomial[index])) for index in range(25))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-label", required=True)
    parser.add_argument("--source-r", type=int, choices=(0, 8), default=8)
    parser.add_argument("--source-index", type=int, default=0)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--prime-count", type=int, default=5000)
    parser.add_argument("--shift-bound", type=int, default=3)
    parser.add_argument("--seed-degree", type=int, default=1)
    parser.add_argument("--max-team-count", type=int, default=3)
    args = parser.parse_args()
    output = DATA / f"current_c3_group_ring_hybrid_{args.tag}_20260813.json"
    manifest = DATA / f"current_c3_group_ring_hybrid_{args.tag}_20260813.txt"
    if output.exists() or manifest.exists():
        raise FileExistsError("refusing to overwrite C3 group-ring artifacts")

    source = source_row(args.source_label, args.source_r, args.source_index)
    target_r = 24 if args.source_r == 8 else 0
    integer_ring = PolynomialRing(ZZ, "x")
    source_polynomial = integer_ring(
        [ZZ(value) for value in source["coefficientLine"].split(",")]
    )
    field = NumberField(source_polynomial, "a")
    automorphisms, images, permutations, source_group = automorphism_action(field)
    source_t = int(args.source_label.removeprefix("8T"))
    if int(libgap.TransitiveIdentification(source_group)) != source_t:
        raise ArithmeticError("automorphism action label mismatch")

    connection = sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)
    baseline = set(connection.execute("SELECT label,r FROM baseline_pairs"))
    owned = set(
        connection.execute(
            "SELECT DISTINCT label,r FROM verifications WHERE status='accepted'"
        )
    )
    desired = {
        str(label): int(team_count)
        for label, r, team_count in connection.execute(
            "SELECT label,r,team_count FROM targets WHERE r=? AND team_count<=?",
            (target_r, args.max_team_count),
        )
        if (str(label), int(r)) not in baseline and (str(label), int(r)) not in owned
    }
    connection.close()
    targets, enumerated_modules = module_targets(permutations, desired)
    certifier = load_certifier()
    profiles = {}
    results = []
    lines = []
    for target_label, target in sorted(
        targets.items(), key=lambda item: (desired[item[0]], item[1]["targetT"])
    ):
        record = {
            **target,
            "sourceLabel": args.source_label,
            "targetR": target_r,
            "teamCount": desired[target_label],
            "status": "construction_exhausted",
            "attempts": [],
        }
        seed_parameters = []
        for degree in range(1, args.seed_degree + 1):
            for shift in range(-args.shift_bound, args.shift_bound + 1):
                seed_parameters.append((degree, shift))
        for degree, shift in seed_parameters:
            attempt = {"seedDegree": degree, "shift": shift}
            try:
                conjugates = [value**degree + shift for value in images]
                h = hilbert90_h(conjugates, target["vector"])
                candidate = construct_candidate(source_polynomial, h)
                candidate_r = int(candidate.number_of_real_roots())
                attempt["candidateR"] = candidate_r
                if candidate_r != target_r:
                    attempt["status"] = "signature_miss"
                    record["attempts"].append(attempt)
                    continue
                if target["targetT"] not in profiles:
                    profiles[target["targetT"]] = target_profile(
                        certifier, target["targetT"]
                    )
                certificate = certifier.certify(
                    candidate, profiles[target["targetT"]], args.prime_count
                )
                attempt["certificate"] = certificate
                if not certificate["complete"]:
                    attempt["status"] = "certificate_incomplete"
                    record["attempts"].append(attempt)
                    continue
                line = coefficient_line(candidate)
                attempt["candidateCoefficientLine"] = line
                attempt["candidateSha256"] = hashlib.sha256(
                    line.encode("ascii")
                ).hexdigest()
                attempt["status"] = "exact_hit"
                record["attempts"].append(attempt)
                record["status"] = "exact_hit"
                record["selectedShift"] = shift
                record["selectedSeedDegree"] = degree
                record["candidateCoefficientLine"] = line
                record["candidateSha256"] = attempt["candidateSha256"]
                record["containment"] = {
                    "sourceRegularAction": args.source_label,
                    "kernelBasis": target["basis"],
                    "kernelDimension": target["dimension"],
                    "kummerRadicand": "group-ring orbit product of (u+i)/(u-i)",
                    "hilbert90": "exact beta=(H+i)/(H-i)",
                    "relativeCubic": "Y^3-3(H^2+1)^2Y-2(H^2-1)(H^2+1)^2",
                    "relativeDiscriminant": "3*(12*H*(H^2+1)^2)^2",
                    "targetAction": target_label,
                }
                lines.append(line)
                break
            except Exception as error:
                attempt["status"] = "error"
                attempt["error"] = f"{type(error).__name__}: {error}"
                record["attempts"].append(attempt)
        results.append(record)
        print(
            json.dumps(
                {
                    "event": "target_complete",
                    "source": args.source_label,
                    "target": target_label,
                    "teamCount": desired[target_label],
                    "status": record["status"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

    payload = {
        "schemaVersion": "current-c3-group-ring-hybrid-v1",
        "source": source,
        "sourceActionLabel": f"8T{int(libgap.TransitiveIdentification(source_group))}",
        "sourceR": args.source_r,
        "targetR": target_r,
        "enumeratedCyclicRank5Or6Modules": enumerated_modules,
        "eligibleTargets": len(targets),
        "exactHits": sum(row["status"] == "exact_hit" for row in results),
        "results": results,
        "networkCalls": 0,
        "submissionCalls": 0,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    manifest.write_text("".join(line + "\n" for line in lines))
    print(
        json.dumps(
            {
                "output": str(output),
                "manifest": str(manifest),
                "eligibleTargets": len(targets),
                "exactHits": payload["exactHits"],
            },
            sort_keys=True,
        )
    )
    return 0 if lines else 2


if __name__ == "__main__":
    raise SystemExit(main())
