#!/usr/bin/env sage
"""Finite square-norm K4 gate over every recovered 12T66 realization.

For a depressed S4 quartic

    f(t) = t^4 + p*t^2 + q*t + r,

rational square values f(t)=s^2 are parametrized by

    E: Y^2 = X^3 + p*X^2 - 4*r*X + q^2 - 4*p*r,
    t = (Y-q)/(2*(X+p)).

Only small Mordell--Weil combinations are tested.  A shifted edge resolvent is
then passed through the exact relative-norm gate before any degree-24
polynomial is constructed.  No network calls or submissions are made.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from sage.all import EllipticCurve, PolynomialRing, QQ, ZZ, pari, prime_range


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "data" / "q66_k4_square_norm_multifield_gate.jsonl"
TARGETS = [10381, 12926, 12936, 15051, 15082, 15091, 15398, 15519, 17170]

spec = importlib.util.spec_from_file_location(
    "q66_multifield", ROOT / "q66_k4_multifield_orientation_pilot.sage.py"
)
multifield = importlib.util.module_from_spec(spec)
spec.loader.exec_module(multifield)
shared = multifield.shared


def elliptic_shifts(quartic, coefficient_bound):
    """Return exact square-value shifts from a finite MW coefficient box."""
    if quartic[3] != 0:
        raise ValueError("quartic must be depressed")
    p, q, r = QQ(quartic[2]), QQ(quartic[1]), QQ(quartic[0])
    curve = EllipticCurve([0, p, 0, -4 * r, q**2 - 4 * p * r])
    generators = list(curve.gens(proof=False))
    points = {curve(0)}
    for coefficients in __import__("itertools").product(
        range(-coefficient_bound, coefficient_bound + 1),
        repeat=len(generators),
    ):
        point = curve(0)
        for coefficient, generator in zip(coefficients, generators):
            point += coefficient * generator
        points.add(point)
    shifts = {}
    for point in points:
        if point.is_zero():
            continue
        x_coordinate, y_coordinate = point[0], point[1]
        denominator = 2 * (x_coordinate + p)
        if denominator == 0:
            continue
        shift = QQ((y_coordinate - q) / denominator)
        value = QQ(quartic(shift))
        if not value.is_square():
            raise ArithmeticError("elliptic map produced a nonsquare value")
        shifts[shift] = {
            "shift": str(shift),
            "squareValue": str(value),
            "squareRoot": str(value.sqrt()),
            "ellipticPoint": [str(x_coordinate), str(y_coordinate)],
        }
    return curve, generators, [shifts[key] for key in sorted(shifts)]


def append_row(path, row):
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coefficient-bound", type=int, default=2)
    parser.add_argument("--field-start", type=int, default=0)
    parser.add_argument("--field-stop", type=int)
    parser.add_argument("--prime-limit", type=int, default=5000)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()
    if args.coefficient_bound < 0:
        raise ValueError("coefficient bound must be nonnegative")
    sources = multifield.recovered()
    sources = sources[args.field_start : args.field_stop]
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.fresh and output.exists():
        output.unlink()
    completed = set()
    if output.exists():
        for line in output.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            completed.add(
                (
                    row.get("qSha256"),
                    row.get("quartic"),
                    row.get("shift"),
                    row.get("orientation"),
                )
            )

    target_profiles = {target: shared.group_profiles(target) for target in TARGETS}
    rational_ring = PolynomialRing(QQ, "y")
    integer_ring = PolynomialRing(ZZ, "x")
    x = integer_ring.gen()

    for source in sources:
        quotient = rational_ring(
            [ZZ(value) for value in source["qLine"].split(",")]
        )
        field = shared.NumberField(quotient, "a")
        edge_field, embedding, _ = field.subfields(6)[0]
        relative = field.relativize(embedding(edge_field.gen()), "b")
        base = relative.base_field()
        norm_data = base.pari_rnfnorm_data(relative)
        closure, _ = edge_field.galois_closure(names="w", map=True)
        depressed = {
            str(entry[0].polynomial()): entry[0].polynomial()
            for entry in closure.subfields(4)
            if entry[0].polynomial()[3] == 0
        }
        if not depressed:
            raise ArithmeticError("no depressed quartic realization found")
        closure_quartic = min(
            depressed.values(),
            key=lambda polynomial: (
                max(abs(QQ(value)) for value in polynomial),
                str(polynomial),
            ),
        )
        reduced_quartic = rational_ring(pari(closure_quartic).polredabs())
        depression_shift = -QQ(reduced_quartic[3]) / 4
        quartic = rational_ring(
            reduced_quartic(rational_ring.gen() + depression_shift)
        )
        if quartic[3] != 0:
            raise ArithmeticError("quartic depression failed")
        curve, generators, shifts = elliptic_shifts(
            quartic, args.coefficient_bound
        )
        print(
            json.dumps(
                {
                    "event": "field",
                    "q": source["qSha256"][:12],
                    "closureQuartic": str(closure_quartic),
                    "reducedQuartic": str(reduced_quartic),
                    "quartic": str(quartic),
                    "ellipticConductor": int(curve.conductor()),
                    "ellipticGenerators": [
                        [str(point[0]), str(point[1])] for point in generators
                    ],
                    "squareShifts": len(shifts),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        for shift_row in shifts:
            shift = QQ(shift_row["shift"])
            shifted = rational_ring(quartic(rational_ring.gen() + shift))
            resolvent = shifted.symmetric_power(2, monic=True)
            roots = resolvent.change_ring(base).roots()
            for orientation, (edge_value, multiplicity) in enumerate(roots):
                key = (
                    source["qSha256"],
                    str(quartic),
                    str(shift),
                    orientation,
                )
                if key in completed:
                    continue
                answer = norm_data.rnfisnorm(pari(edge_value))
                normable = str(answer[1]) == "1"
                row = {
                    "schemaVersion": "q66-k4-square-norm-multifield-gate-v1",
                    **source,
                    "closureQuartic": str(closure_quartic),
                    "reducedQuartic": str(reduced_quartic),
                    "depressionShift": str(depression_shift),
                    "quartic": str(quartic),
                    "ellipticCurve": str(curve),
                    "ellipticRankGenerators": [
                        [str(point[0]), str(point[1])] for point in generators
                    ],
                    **shift_row,
                    "shiftedQuartic": str(shifted),
                    "shiftedQuarticConstantIsSquare": bool(
                        QQ(shifted[0]).is_square()
                    ),
                    "edgeResolvent": str(resolvent),
                    "orientation": orientation,
                    "relativeNormObstruction": str(answer[1]),
                    "relativeNormObstructionIsOne": normable,
                    "networkCalls": 0,
                    "submissionCalls": 0,
                }
                if not normable:
                    row["status"] = "not_relative_norm"
                else:
                    relative_value = relative(answer[0])
                    absolute_value = relative.structure()[0](relative_value)
                    _, scale, minimal = shared.integral_square_scale(
                        absolute_value
                    )
                    candidate = integer_ring(minimal(x**2))
                    coefficient_line = shared.canonical_line(candidate)
                    digest = hashlib.sha256(
                        coefficient_line.encode("ascii")
                    ).hexdigest()
                    possible = set(TARGETS)
                    observations = []
                    if candidate.is_irreducible():
                        for prime in prime_range(2, args.prime_limit + 1):
                            profile = shared.cycle_type(candidate, int(prime))
                            if profile is None:
                                continue
                            observations.append((int(prime), profile))
                            possible = {
                                target
                                for target in possible
                                if profile in target_profiles[target]
                            }
                            if not possible:
                                break
                    else:
                        possible.clear()
                    row.update(
                        {
                            "squareScale": scale,
                            "candidateCoefficientLine": coefficient_line,
                            "candidateSha256": digest,
                            "candidateBytes": len(
                                coefficient_line.encode("ascii")
                            ),
                            "irreducible": bool(candidate.is_irreducible()),
                            "realRoots": (
                                int(candidate.number_of_real_roots())
                                if candidate.is_irreducible()
                                else None
                            ),
                            "checkedGoodPrimes": len(observations),
                            "requestedSurvivors": [
                                f"24T{target}" for target in sorted(possible)
                            ],
                            "decisiveExclusion": (
                                None
                                if possible or not observations
                                else {
                                    "prime": observations[-1][0],
                                    "cycleType": list(observations[-1][1]),
                                }
                            ),
                            "status": (
                                "requested_survivor"
                                if possible
                                else "requested_targets_excluded"
                            ),
                        }
                    )
                append_row(output, row)
                completed.add(key)
                print(
                    json.dumps(
                        {
                            "event": "orientation",
                            "q": source["qSha256"][:12],
                            "shift": str(shift),
                            "orientation": orientation,
                            "normable": normable,
                            "r": row.get("realRoots"),
                            "survivors": row.get("requestedSurvivors", []),
                            "status": row["status"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )

    print(json.dumps({"event": "done", "output": str(output)}, sort_keys=True))


if __name__ == "__main__":
    main()
