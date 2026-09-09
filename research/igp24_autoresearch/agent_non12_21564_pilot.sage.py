#!/usr/bin/env sage -python
"""Exact local pilot for the 8T42 cubic-augmentation tower to 24T21564."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

from sage.all import PolynomialRing, ZZ, libgap, pari


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
OUTPUT = DATA / "agent_non12_21564_pilot.json"
MANIFEST = DATA / "agent_non12_21564_pilot.txt"
OUTER_LINE = "-1,-24,-124,-34,71,18,-14,-2,1"
OUTER_SOURCE = {"label": "24T692", "submissionId": "sub_2fe8e857d8fa4920ad52292814c7bcd1", "polynomialIndex": 477}


def load_certifier():
    path = ROOT / "agent_gold_b_asymmetric_composition_certify.sage.py"
    spec = importlib.util.spec_from_file_location("composition_certifier", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def augmentation_group():
    block_count, block_size = 8, 3
    outer = libgap.TransitiveGroup(8, 42)
    generators = []
    for block in range(block_count - 1):
        shifts = [0] * block_count
        shifts[block] = 1
        shifts[-1] = -1
        images = [
            position * block_size + ((point + shifts[position]) % block_size) + 1
            for position in range(block_count)
            for point in range(block_size)
        ]
        generators.append(libgap.PermList(images))
    for generator in list(libgap.GeneratorsOfGroup(outer)):
        images = []
        for block in range(1, block_count + 1):
            image_block = int(libgap.OnPoints(block, generator))
            for point in range(block_size):
                images.append((image_block - 1) * block_size + point + 1)
        generators.append(libgap.PermList(images))
    generators.append(
        libgap.PermList(
            [
                block * block_size + ((-point) % block_size) + 1
                for block in range(block_count)
                for point in range(block_size)
            ]
        )
    )
    return libgap.Group(generators)


def target_profile(certifier, target):
    points = libgap.eval("[1..24]")

    def group_cycle_type(permutation):
        return tuple(
            sorted(int(value) for value in libgap.CycleLengths(permutation, points))
        )

    maximals = [
        subgroup
        for subgroup in list(libgap.MaximalSubgroupClassReps(target))
        if bool(libgap.IsTransitive(subgroup, points))
    ]
    profiles, identities = [], []
    for subgroup in maximals:
        profile = {
            group_cycle_type(libgap.Representative(conjugacy_class))
            for conjugacy_class in list(libgap.ConjugacyClasses(subgroup))
        }
        profiles.append(profile)
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
        "properTransitiveMaximalCount": len(maximals),
        "targetLabel": "24T21564",
        "targetT": 21564,
    }


def main() -> int:
    certifier = load_certifier()
    ring = PolynomialRing(ZZ, "x")
    x = ring.gen()
    outer = ring([int(value) for value in OUTER_LINE.split(",")])
    outer_group = pari(outer).polgalois()
    if int(outer_group[2]) != 42 or int(outer_group[0]) != 288:
        raise ArithmeticError("outer polynomial is not exactly 8T42")
    if int(outer[0]) != -1:
        raise ArithmeticError("outer constant is not the certified cube -1")
    candidate = outer(x**3)
    if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
        raise ArithmeticError("tower candidate failed exact degree/irreducibility checks")

    constructed = augmentation_group()
    constructed_t = int(libgap.TransitiveIdentification(constructed))
    if constructed_t != 21564:
        raise ArithmeticError(f"augmentation action is 24T{constructed_t}, not 24T21564")
    target = libgap.TransitiveGroup(24, 21564)
    if int(libgap.Size(constructed)) != int(libgap.Size(target)):
        raise ArithmeticError("constructed/target group order mismatch")
    profile = target_profile(certifier, target)
    certificate = certifier.certify(candidate, profile, prime_count=5000)

    line = ",".join(str(int(value)) for value in candidate.list())
    digest = hashlib.sha256(line.encode("ascii")).hexdigest()
    r = int(candidate.number_of_real_roots())
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    state = connection.execute(
        "SELECT team_count,discovered,minimum_disc_abs,generated_at FROM targets WHERE label='24T21564' AND r=?",
        (r,),
    ).fetchone()
    ledger_rows = int(connection.execute("SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?", (digest,)).fetchone()[0])
    connection.close()
    result = {
        "status": "certified_24T21564" if certificate["complete"] else "certificate_incomplete",
        "candidateCoefficientLine": line,
        "candidateSha256": digest,
        "candidateFieldDiscriminantAbs": str(abs(int(pari(candidate).nfdisc()))),
        "candidateRealRoots": r,
        "candidateIrreducibleDegree24": True,
        "absentFromLedger": ledger_rows == 0,
        "outerInput": {
            **OUTER_SOURCE,
            "coefficientLine": OUTER_LINE,
            "coefficientSha256": hashlib.sha256(OUTER_LINE.encode("ascii")).hexdigest(),
            "galoisGroup": {"label": "8T42", "order": 288, "pariName": str(outer_group[3])},
            "realRoots": int(outer.number_of_real_roots()),
            "constantTerm": -1,
            "constantTermCubeRoot": -1,
        },
        "containmentProof": {
            "relation": "candidate(x)=outer(x^3)",
            "blocks": "eight fibers of size three",
            "baseKernel": "C3^7: product of the eight radicals is -1, so rotation exponents sum to zero",
            "diagonalInversion": "global complex conjugation on zeta_3 inverts every C3 fiber simultaneously",
            "outerQuotient": "8T42",
            "constructedOvergroup": "C3^7 semidirect (8T42 x C2_diagonal)",
            "constructedOrder": int(libgap.Size(constructed)),
            "constructedTransitiveIdentification": "24T21564",
        },
        "maximalSubgroupCertificate": certificate,
        "targetState": {
            "teamCount": int(state[0]) if state else None,
            "discovered": int(state[1]) if state else None,
            "minimumDiscAbs": str(state[2]) if state and state[2] else None,
            "generatedAt": str(state[3]) if state else None,
        },
        "liveGoldHit": bool(state and int(state[0]) == 0 and int(state[1]) == 0),
        "goldSignatureObstruction": {
            "liveGoldSignatures": [12, 24],
            "pilotSignature": r,
            "reason": "each real pure-cubic fiber x^3=beta has exactly one real root, so r equals the outer field's r and cannot be 12 or 24",
        },
        "networkCalls": 0,
        "submissionCalls": 0,
        "staged": False,
    }
    atomic = json.dumps(result, indent=2, sort_keys=True) + "\n"
    temporary = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    temporary.write_text(atomic, encoding="utf-8")
    temporary.replace(OUTPUT)
    MANIFEST.write_text(line + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": result["status"],
                "target": "24T21564",
                "r": r,
                "liveGold": result["liveGoldHit"],
                "properTransitiveMaximals": profile["properTransitiveMaximalCount"],
                "certificateComplete": certificate["complete"],
                "output": str(OUTPUT),
            },
            sort_keys=True,
        )
    )
    return 0 if certificate["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
