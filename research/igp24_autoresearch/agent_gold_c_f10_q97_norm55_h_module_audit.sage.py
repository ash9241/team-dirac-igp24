#!/usr/bin/env sage -python
"""Exact no-stage audit for the second strict qT97 Selmer representative.

The representative has rational norm 55/4.  Its conjugate-squareclass orbit
is certified by exclusion of every proper transitive maximal subgroup of the
full imprimitive wreath action 24T20725.  The resulting signature is r=16,
which is not a frozen-live pair, so this program deliberately writes no
outbox manifest and performs no submission or network call.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from sage.all import NumberField, PolynomialRing, QQ, RealIntervalField, ZZ, libgap, proof


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
FIELD_HASH = "852262fa15e2c469ab3a1a0be2f63eab26471b8cf43aa3a1ae682e466a8d4978"
DEFAULT_CACHE = Path("/private/tmp/igp24_f10_exact_selmer_cache_v2")
DEFAULT_LIVE = DATA / "live_undiscovered_signatures.jsonl"
DEFAULT_OUTPUT = DATA / "agent_gold_c_f10_q97_norm55_h_module_audit.json"
FULL_WREATH_T = 20725
QUOTIENT_T = 97


def load_helper():
    path = ROOT / "character_kernel_gold_pilot.sage.py"
    spec = importlib.util.spec_from_file_location("f10_character_helper", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import the exact character helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HELPER = load_helper()
SHARED = HELPER.SHARED


def rendered(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def exact_signs(field, value):
    for precision in (128, 256, 512, 1024):
        evaluations = [embedding(value) for embedding in field.embeddings(RealIntervalField(precision))]
        if all(not result.contains_zero() for result in evaluations):
            bits = [int(result < 0) for result in evaluations]
            return bits, precision
    raise ValueError("could not certify all real signs")


def minimal_integral_square_scale(value):
    denominator = ZZ(1)
    for coefficient in value.list():
        denominator = denominator.lcm(QQ(coefficient).denominator())
    for scale in range(1, int(denominator) + 1):
        scaled = value * scale**2
        if scaled.is_integral():
            return scaled, scale
    raise ValueError("no integral rational-square scale")


def full_wreath_structure():
    quotient = libgap.TransitiveGroup(12, QUOTIENT_T)
    quotient_generators = list(libgap.GeneratorsOfGroup(quotient))
    flips = [libgap.eval(f"({2 * index - 1},{2 * index})") for index in range(1, 13)]

    def lifted(permutation):
        images = []
        for point in range(1, 13):
            image = int(libgap.OnPoints(point, permutation))
            images.extend([2 * image - 1, 2 * image])
        return libgap.PermList(images)

    group = libgap.Group(flips + [lifted(generator) for generator in quotient_generators])
    label = int(libgap.TransitiveIdentification(group))
    order = int(libgap.Size(group))
    if label != FULL_WREATH_T or order != 2**12 * 192:
        raise ValueError("unexpected full qT97 wreath action")
    return {
        "blockCount": 12,
        "blockKernelOrder": 2**12,
        "groupOrder": order,
        "quotientOrder": 192,
        "quotientT": QUOTIENT_T,
        "targetLabel": f"24T{label}",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--live", type=Path, default=DEFAULT_LIVE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--witness-primes", type=int, default=1000)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError(f"refusing to overwrite frozen audit: {args.output}")
    proof.number_field(False)

    cache_path = args.cache_dir / f"24_{FIELD_HASH}.json"
    cache = json.loads(cache_path.read_text())
    strict = cache["strictNewQuotientRepresentatives"]
    if cache["strictNewQuotientDimension"] != 2 or len(strict) != 2:
        raise ValueError("unexpected strict quotient cache")

    quotient_ring = PolynomialRing(QQ, "y")
    quotient = quotient_ring([ZZ(value) for value in cache["canonicalPolynomial"].split(",")])
    field = NumberField(quotient, "a")
    selected_row = strict[0]
    value = field([QQ(coefficient) for coefficient in selected_row["coordinatesInCanonicalPowerBasis"]])
    if QQ(value.norm()) != QQ(55) / 4:
        raise ValueError("the selected strict representative is not norm 55/4")
    scaled, square_scale = minimal_integral_square_scale(value)
    minimal = scaled.minpoly()
    integer_ring = PolynomialRing(ZZ, "z")
    minimal_zz = integer_ring([ZZ(coefficient) for coefficient in minimal])
    candidate_ring = PolynomialRing(ZZ, "x")
    x = candidate_ring.gen()
    candidate = candidate_ring(minimal_zz(x**2))
    coefficient_line = SHARED.coefficient_line(candidate)
    candidate_sha = hashlib.sha256(coefficient_line.encode()).hexdigest()
    signs, sign_precision = exact_signs(field, scaled)
    real_roots = int(candidate.number_of_real_roots())
    if not candidate.is_irreducible() or real_roots != 16:
        raise ValueError("norm-55 candidate failed exact irreducibility/signature")

    structure = full_wreath_structure()
    SHARED.TARGET_T = FULL_WREATH_T
    profiles, identities = SHARED.maximal_joint_profiles()
    maximal_certificate = SHARED.frobenius_maximal_certificate(
        candidate, quotient, profiles, identities, args.witness_primes
    )
    if not maximal_certificate["complete"]:
        raise ValueError("proper transitive maximal exclusion is incomplete")

    ramified_primes = [int(prime) for prime, _ in ZZ(quotient.discriminant()).factor()]
    if 5 in ramified_primes or 11 in ramified_primes:
        raise ValueError("unexpected ramification at a norm-55 prime")
    live_matches = [
        row
        for row in read_jsonl(args.live)
        if row.get("label") == "24T20725"
        and int(row.get("r", -1)) == real_roots
        and int(row.get("teamCount", -1)) == 0
    ]
    if live_matches:
        raise ValueError("24T20725/r16 unexpectedly became frozen-live")

    payload = {
        "candidate": {
            "candidateCoefficientLine": coefficient_line,
            "candidateSha256": candidate_sha,
            "fieldDiscriminantAbs": str(abs(ZZ(NumberField(candidate.change_ring(QQ), "b").discriminant()))),
            "hOrbitSubmoduleCertificate": {
                "blockKernelOrder": structure["blockKernelOrder"],
                "certification": (
                    "joint Frobenius witnesses exclude every proper transitive maximal "
                    "subgroup of the full qT97 wreath action"
                ),
                "hOrbitSquareclassSpanDimension": 12,
                "status": "certified_full_rank",
            },
            "irreducible": True,
            "maximalSubgroupCertificate": maximal_certificate,
            "minimalPolynomial": str(minimal_zz),
            "norm": str(scaled.norm()),
            "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
            "realRoots": real_roots,
            "signBits": signs,
            "signPrecisionBits": sign_precision,
            "squareScale": square_scale,
            "status": "certified_24T20725_r16_not_live",
            "strictQuotientRepresentative": selected_row,
        },
        "exactNoStageDecision": {
            "frozenLiveMatches": [],
            "reason": "24T20725/r16 is absent from the frozen live-undiscovered census",
            "staged": False,
        },
        "fieldCanonicalSha256": FIELD_HASH,
        "inputHashes": {
            "frozenLiveSha256": sha256_path(args.live),
            "selmerCacheRowSha256": sha256_path(cache_path),
        },
        "networkCalls": 0,
        "normCharacterObstruction": {
            "conclusion": (
                "Q(sqrt(55)) is not a quadratic subfield of the qT97 normal closure; "
                "there is no all-conjugates squareclass relation"
            ),
            "normCore": 55,
            "quotientDiscriminantRamifiedPrimes": ramified_primes,
            "unramifiedNormCorePrimes": [5, 11],
        },
        "outboxManifestWritten": False,
        "submissionCalls": 0,
        "targetStructure": structure,
    }
    text = rendered(payload)
    write_atomic(args.output, text)
    print(
        json.dumps(
            {
                "candidateSha256": candidate_sha,
                "hOrbitSquareclassSpanDimension": 12,
                "output": str(args.output.resolve()),
                "outputSha256": hashlib.sha256(text.encode()).hexdigest(),
                "staged": False,
                "status": payload["candidate"]["status"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
