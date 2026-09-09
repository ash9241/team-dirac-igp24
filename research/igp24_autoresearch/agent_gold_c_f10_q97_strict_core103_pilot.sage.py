#!/usr/bin/env sage -python
"""One-candidate exact F10 pilot from the qT97 strict Selmer quotient.

The input squareclass is the unique strict-quotient representative with
absolute rational norm core 103 and three positive real embeddings.  The
frozen target is therefore 24T19693/r6.  No S-unit or auxiliary-prime search
is performed.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import importlib.util
import json
from pathlib import Path

from sage.all import (
    NumberField,
    PolynomialRing,
    QQ,
    RealIntervalField,
    ZZ,
    proof,
)


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEFAULT_CENSUS = DATA / "agent_gold_c_character_field_census.jsonl"
DEFAULT_LIVE = DATA / "live_undiscovered_signatures.jsonl"
DEFAULT_CACHE = Path("/private/tmp/igp24_f10_exact_selmer_cache_v2")
DEFAULT_OUTPUT = DATA / "agent_gold_c_f10_q97_core103_r6_exact_pilot.json"
DEFAULT_MANIFEST = ROOT / "outbox" / "agent_gold_c_f10_q97_core103_r6_exact_pilot.txt"
FIELD_HASH = "852262fa15e2c469ab3a1a0be2f63eab26471b8cf43aa3a1ae682e466a8d4978"
TARGET_LABEL = "24T19693"
TARGET_T = 19693
TARGET_R = 6
TARGET_CORE = 103


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


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def absolute_norm_core(value) -> int:
    norm = QQ(value.norm())
    return int(abs(ZZ(norm.numerator() * norm.denominator()).squarefree_part()))


def exact_signs(field, value) -> tuple[list[int], int]:
    for precision in (128, 256, 512, 1024):
        evaluations = [
            embedding(value)
            for embedding in field.embeddings(RealIntervalField(precision))
        ]
        if all(not result.contains_zero() for result in evaluations):
            return [int(result < 0) for result in evaluations], precision
    raise ValueError("could not certify the real signs")


def minimal_integral_square_scale(value) -> tuple[object, int]:
    denominator = ZZ(1)
    for coefficient in value.list():
        denominator = denominator.lcm(QQ(coefficient).denominator())
    for scale in range(1, int(denominator) + 1):
        scaled = value * scale**2
        if scaled.is_integral():
            return scaled, scale
    raise ValueError("failed to find an integral rational-square scale")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    parser.add_argument("--live", type=Path, default=DEFAULT_LIVE)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--witness-primes", type=int, default=1000)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    if args.output.exists() or args.manifest.exists():
        raise ValueError("refusing to overwrite the exact F10 pilot")
    proof.number_field(False)

    census_rows = read_jsonl(args.census)
    census = next(
        row for row in census_rows if row["fieldCanonicalSha256"] == FIELD_HASH
    )
    cache_matches = sorted(args.cache_dir.glob(f"24_{FIELD_HASH}.json"))
    if len(cache_matches) != 1:
        raise ValueError("the exact qT97 Selmer cache row is missing or ambiguous")
    cache_path = cache_matches[0]
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    if cache["strictNewQuotientDimension"] != 2:
        raise ValueError("the frozen strict quotient is not two-dimensional")

    live = [
        row
        for row in read_jsonl(args.live)
        if row["label"] == TARGET_LABEL
        and int(row["r"]) == TARGET_R
        and int(row["teamCount"]) == 0
    ]
    if len(live) != 1:
        raise ValueError("the frozen 24T19693/r6 target is missing or ambiguous")

    quotient = PolynomialRing(QQ, "y")(
        [ZZ(value) for value in census["fieldCanonicalPolynomial"].split(",")]
    )
    field = NumberField(quotient, "a")
    strict = []
    for row in cache["strictNewQuotientRepresentatives"]:
        value = field(
            [QQ(coefficient) for coefficient in row["coordinatesInCanonicalPowerBasis"]]
        )
        signs, sign_precision = exact_signs(field, value)
        strict.append(
            {
                "cacheRow": row,
                "normCore": absolute_norm_core(value),
                "positiveEmbeddings": len(signs) - sum(signs),
                "signBits": signs,
                "signPrecisionBits": sign_precision,
                "value": value,
            }
        )
    eligible = [
        row
        for row in strict
        if row["normCore"] == TARGET_CORE
        and 2 * row["positiveEmbeddings"] == TARGET_R
    ]
    if len(eligible) != 1:
        raise ValueError("expected one strict core-103/r6 squareclass")
    selected = eligible[0]
    value = selected["value"]

    structure = HELPER.target_structure(TARGET_T)
    if (
        structure["quotientT"] != 97
        or structure["blockKernelOrder"] != 2**11
    ):
        raise ValueError("target does not have the required full-rank qT97 action")
    alignment = HELPER.character_alignment(
        quotient,
        structure["quotientT"],
        [TARGET_CORE],
    )
    target_cores = alignment["labelToUnambiguousSquarefreeNormCores"].get(
        TARGET_LABEL, []
    )
    if TARGET_CORE not in target_cores:
        raise ValueError("core 103 does not map uniquely to 24T19693")

    scaled, square_scale = minimal_integral_square_scale(value)
    signs, sign_precision = exact_signs(field, scaled)
    minimal = scaled.minpoly().change_ring(QQ)
    if minimal.degree() != 12 or any(QQ(coefficient).denominator() != 1 for coefficient in minimal):
        raise ValueError("scaled strict representative has no integral degree-12 minpoly")
    minimal_zz = PolynomialRing(ZZ, "z")([ZZ(value) for value in minimal])
    ring = PolynomialRing(ZZ, "x")
    x = ring.gen()
    candidate = ring(minimal_zz(x**2))
    coefficient_line = SHARED.coefficient_line(candidate)
    candidate_sha = hashlib.sha256(coefficient_line.encode()).hexdigest()
    irreducible = bool(candidate.is_irreducible())
    real_roots = int(candidate.number_of_real_roots())
    if not irreducible or real_roots != TARGET_R:
        raise ValueError("strict candidate failed irreducibility or exact signature")

    SHARED.TARGET_T = TARGET_T
    SHARED.TARGET_LABEL = TARGET_LABEL
    profiles, identities = SHARED.maximal_joint_profiles()
    maximal_certificate = SHARED.frobenius_maximal_certificate(
        candidate,
        quotient,
        profiles,
        identities,
        args.witness_primes,
    )
    certified = bool(maximal_certificate["complete"])
    h_rank = 11 if certified else None
    field_discriminant = (
        str(abs(ZZ(NumberField(candidate.change_ring(QQ), "b").discriminant())))
        if certified
        else None
    )
    payload = {
        "audit": {
            "censusFieldCanonicalSha256": FIELD_HASH,
            "frozenLiveTarget": live[0],
            "inputHashes": {
                "censusSha256": sha256_path(args.census),
                "frozenLiveSha256": sha256_path(args.live),
                "selmerCacheRowSha256": sha256_path(cache_path),
            },
            "networkCalls": 0,
            "selmerCacheRow": str(cache_path.resolve()),
            "submissionCalls": 0,
            "targetStructure": structure,
        },
        "candidate": {
            "candidateCoefficientLine": coefficient_line,
            "candidateSha256": candidate_sha,
            "containmentProof": {
                "sameCanonicalDegree12Field": True,
                "targetSquarefreeNormCore": TARGET_CORE,
                "theorem": (
                    "the exact quotient-character alignment places core 103 in "
                    "the 24T19693 character action"
                ),
            },
            "fieldDiscriminantAbs": field_discriminant,
            "hOrbitSubmoduleCertificate": {
                "blockKernelOrder": structure["blockKernelOrder"],
                "certification": (
                    "exact target equality gives conjugate-squareclass span "
                    "dimension log2(blockKernelOrder)=11"
                    if certified
                    else "maximal-subgroup exclusion incomplete"
                ),
                "hOrbitSquareclassSpanDimension": h_rank,
                "status": "certified_full_rank" if certified else "incomplete",
            },
            "irreducible": irreducible,
            "maximalSubgroupCertificate": maximal_certificate,
            "minimalPolynomial": str(minimal_zz),
            "negativeEmbeddingCount": sum(signs),
            "norm": str(scaled.norm()),
            "normOverCoreIsSquare": SHARED.rational_is_square(
                abs(QQ(scaled.norm())) / TARGET_CORE
            ),
            "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
            "realRoots": real_roots,
            "signBits": signs,
            "signPrecisionBits": sign_precision,
            "squareScale": square_scale,
            "status": (
                f"certified_{TARGET_LABEL}_r{TARGET_R}"
                if certified
                else "contained_candidate_incomplete_maximal_certificate"
            ),
            "strictQuotientRepresentative": selected["cacheRow"],
        },
        "pilot": {
            "candidateCount": 1,
            "mechanism": "strict Selmer quotient representative; no S-unit enumeration",
            "splitPrimeAuxiliaryEnumerationCalls": 0,
        },
    }
    if not payload["candidate"]["normOverCoreIsSquare"]:
        raise ValueError("scaled norm does not retain core 103")
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    write_atomic(args.output, rendered)
    if certified:
        manifest_text = coefficient_line + "\n"
        write_atomic(args.manifest, manifest_text)
        payload["staging"] = {
            "candidateSha256": candidate_sha,
            "manifest": str(args.manifest.resolve()),
            "manifestSha256": hashlib.sha256(manifest_text.encode()).hexdigest(),
            "polynomials": 1,
            "submissionCalls": 0,
        }
        # Rewrite once so the result records the frozen local manifest.
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        write_atomic(args.output, rendered)
    print(
        json.dumps(
            {
                "candidateSha256": candidate_sha,
                "hOrbitSquareclassSpanDimension": h_rank,
                "manifest": str(args.manifest.resolve()) if certified else None,
                "output": str(args.output.resolve()),
                "outputSha256": hashlib.sha256(rendered.encode()).hexdigest(),
                "status": payload["candidate"]["status"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if certified else 2


if __name__ == "__main__":
    raise SystemExit(main())
