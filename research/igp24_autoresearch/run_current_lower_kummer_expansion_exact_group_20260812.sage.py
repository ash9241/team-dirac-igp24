#!/usr/bin/env sage -python
"""Extract every exact degree-12 factor for one expanded Kummer route group."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from sage.all import PolynomialRing, QQ, ZZ, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
ASSIGNMENT_WORKER = ROOT / "agent_f9_k4_incidence_pilot.sage.py"


def load_assignment_worker():
    spec = importlib.util.spec_from_file_location(
        "current_lower_kummer_assignment", ASSIGNMENT_WORKER
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load exact factor/action assignment worker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def coefficient_line(polynomial) -> str:
    values = [ZZ(value) for value in polynomial.list()]
    values += [ZZ(0)] * (25 - len(values))
    if len(values) != 25 or values[-1] != 1:
        raise ValueError("candidate is not monic degree 24")
    return ",".join(str(value) for value in values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--group-ordinal", type=int, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--assignment-prime-bound", type=int, default=2000)
    args = parser.parse_args()
    plan_path = DATA / Path(args.plan).name
    plan = json.loads(plan_path.read_text())
    group = next(item for item in plan["groups"] if int(item["groupOrdinal"]) == args.group_ordinal)
    result_path = DATA / f"current_lower_kummer_expansion_{args.tag}_group{args.group_ordinal}_20260812.json"
    manifest_path = DATA / f"current_lower_kummer_expansion_{args.tag}_group{args.group_ordinal}_20260812.txt"
    if result_path.exists() or manifest_path.exists():
        raise FileExistsError("refusing to overwrite expanded Kummer outputs")
    ring_q = PolynomialRing(ZZ, "q")
    quotient = ring_q([ZZ(value) for value in group["quotientLine"].split(",")])
    if hashlib.sha256(group["quotientLine"].encode()).hexdigest() != group["quotientSha256"]:
        raise ValueError("quotient hash mismatch")
    if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
        raise ValueError("quotient failed exact checks")
    subset_size = int(group["subsetSize"])
    computation_size = min(subset_size, 12 - subset_size)
    factorization = list(quotient.symmetric_power(computation_size, monic=True).factor())
    factors = sorted(
        [factor for factor, exponent in factorization if factor.degree() == 12 and int(exponent) == 1],
        key=lambda value: ",".join(str(ZZ(item)) for item in value.list()),
    )
    if subset_size > 6:
        # A k-subset product is Norm(beta) divided by the complementary
        # (12-k)-subset product.  Reciprocal-transform the small symmetric
        # power factors instead of constructing a degree C(12,k) polynomial.
        rational_ring = PolynomialRing(QQ, "z")
        z = rational_ring.gen()
        norm_beta = ZZ(quotient[0])
        complemented = []
        for factor in factors:
            degree = int(factor.degree())
            transformed = sum(
                QQ(factor[index]) * QQ(norm_beta) ** index * z ** (degree - index)
                for index in range(degree + 1)
            ) / QQ(factor[0])
            if not all(value.denominator() == 1 for value in transformed.list()):
                raise ValueError("complement factor is not integral")
            complemented.append(ring_q([ZZ(value) for value in transformed.list()]))
        factors = sorted(
            complemented,
            key=lambda value: ",".join(str(ZZ(item)) for item in value.list()),
        )
    if len(factors) != len(group["actions"]):
        raise ValueError(f"factor/action mismatch: {len(factors)} != {len(group['actions'])}")
    if len(group["actions"]) == 1 and len(factors) == 1:
        # Pair-orbit plans can have a single exact degree-12 action whose
        # schema uses ``pairOrbit`` rather than ``subsetOrbit``.  With one
        # action and one factor the bijection is forced, so no Frobenius
        # disambiguation is necessary (or even meaningful).
        assignment = {0: 0}
        assignment_certificate = {
            "assignmentCount": 1,
            "factorActionIndexOptions": {"0": [0]},
            "proof": "One degree-12 factor and one exact action force the unique assignment.",
            "targetLabelAssignmentCount": 1,
        }
    else:
        assignment_worker = load_assignment_worker()
        assignment, assignment_certificate = assignment_worker.assign_degree_twelve_factors(
            quotient,
            factors,
            group["actions"],
            int(group["source"]["r"]),
            args.assignment_prime_bound,
        )
    candidates = []
    for index, factor in enumerate(factors):
        ring_x = PolynomialRing(ZZ, f"x{index}")
        x = ring_x.gen()
        candidate = ring_x(factor)(x**2)
        candidate = ring_x(pari(candidate).polredbest())
        if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
            raise ValueError("candidate failed exact polynomial checks")
        line = coefficient_line(candidate)
        action_index = int(assignment[index])
        action = group["actions"][action_index]
        candidates.append(
            {
                "actionIndex": action_index,
                "coefficientLine": line,
                "coefficientSha256": hashlib.sha256(line.encode()).hexdigest(),
                "fieldDiscriminantAbs": str(abs(int(pari(candidate).nfdisc()))),
                "r": int(candidate.number_of_real_roots()),
                "targetLabel": str(action["targetLabel"]),
                "targetT": int(action["targetT"]),
            }
        )
    manifest_path.write_text("".join(item["coefficientLine"] + "\n" for item in candidates))
    payload = {
        "candidates": candidates,
        "factorActionAssignmentCertificate": assignment_certificate,
        "factorDegrees": sorted(int(factor.degree()) for factor, exponent in factorization for _ in range(int(exponent))),
        "groupOrdinal": args.group_ordinal,
        "routes": group["routes"],
        "source": group["source"],
        "subsetSize": int(group["subsetSize"]),
        "symmetricPowerComputationSize": computation_size,
    }
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"candidates": len(candidates), "event": "complete", "group": args.group_ordinal, "signatures": [item["r"] for item in candidates]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
