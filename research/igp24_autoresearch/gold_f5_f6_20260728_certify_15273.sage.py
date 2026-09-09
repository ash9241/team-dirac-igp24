#!/usr/bin/env sage -python
"""Exact certificate for the shifted-derivative 24T15273/r20 candidate."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from sage.all import NumberField, PolynomialRing, QQ, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
SCREEN = DATA / "gold_f5_f6_20260728_shifted_derivative_screen.jsonl"
MANIFEST = ROOT / "outbox" / "gold_f5_f6_20260728_exact_24T15273_r20.txt"
OUTPUT = DATA / "gold_f5_f6_20260728_exact_24T15273_r20_certificate.json"

SOURCE_SUBMISSION = "sub_ce6cc49ade964e15a5389bdf9ddcf674"
SOURCE_INDEX = 54
SOURCE_LABEL = "24T19724"
SOURCE_R = 12
QUOTIENT_T = 109
SHIFT = -1
QUOTIENT_LINE = (
    "2646,68040,-320652,205776,616320,-606240,-192896,143880,"
    "26151,-5908,-264,24,1"
)
TARGET_LABEL = "24T15273"
TARGET_T = 15273
TARGET_R = 20
STAGED_PAIRS = {
    ("24T10482", 8),
    ("24T14293", 16),
    ("24T16948", 16),
    ("24T16949", 16),
    ("24T15337", 20),
    ("24T15043", 4),
    ("24T11787", 12),
    ("24T24877", 14),
}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHARACTER = load_module(
    "gold_f5_f6_character_helper",
    ROOT / "character_kernel_gold_pilot.sage.py",
)
SHARED = CHARACTER.SHARED


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def coefficient_line(polynomial) -> str:
    return ",".join(str(value) for value in polynomial.list())


def atomic_new(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    for path in (MANIFEST, OUTPUT):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")

    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    source = connection.execute(
        """
        SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.t,v.status,
               v.scoreable,v.field_disc_abs
        FROM polynomials AS p JOIN verifications AS v
        USING(submission_id,polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (SOURCE_SUBMISSION, SOURCE_INDEX),
    ).fetchone()
    if source is None:
        raise ValueError("accepted source pin is absent")
    source_values = str(source["coefficients"]).split(",")
    if (
        str(source["label"]) != SOURCE_LABEL
        or int(source["r"]) != SOURCE_R
        or str(source["status"]) != "accepted"
        or int(source["scoreable"]) != 1
        or len(source_values) != 25
        or any(int(source_values[index]) for index in range(1, 25, 2))
        or ",".join(source_values[::2]) != QUOTIENT_LINE
    ):
        raise ValueError("accepted source provenance mismatch")

    ring_u = PolynomialRing(ZZ, "u")
    ring_z = PolynomialRing(ZZ, "z")
    ring_x = PolynomialRing(ZZ, "x")
    u = ring_u.gen()
    x = ring_x.gen()
    q = ring_u([ZZ(value) for value in QUOTIENT_LINE.split(",")])
    if q.degree() != 12 or not q.is_monic() or not q.is_irreducible():
        raise ValueError("bad accepted degree-12 quotient")
    square_ratio_product = ZZ(q(SHIFT) * q(0))
    if square_ratio_product <= 0 or not square_ratio_product.is_square():
        raise ValueError("shift does not preserve the exact norm squareclass")

    radicand = (u - ZZ(SHIFT)) * q.derivative()
    bivariate = PolynomialRing(ZZ, names=("y", "z"))
    y, z = bivariate.gens()
    q_y = sum(q[index] * y**index for index in range(13))
    radicand_y = sum(
        radicand[index] * y**index for index in range(radicand.degree() + 1)
    )
    h = ring_z(q_y.resultant(z - radicand_y, y))
    if h.leading_coefficient() == -1:
        h = -h
    transformed_source = ring_x(h(x**2))
    if (
        h.degree() != 12
        or not h.is_monic()
        or not h.is_irreducible()
        or not transformed_source.is_irreducible()
        or int(transformed_source.number_of_real_roots()) != 12
    ):
        raise ValueError("transformed source failed exact arithmetic gates")

    original_core = abs(ZZ(q[0])).squarefree_part()
    transformed_core = abs(ZZ(h[0])).squarefree_part()
    if original_core != 6 or transformed_core != original_core:
        raise ValueError("derivative transform did not preserve norm core 6")

    # The degree-12 resultant is a separating equivariant image of the roots
    # of q, so its quotient root action is exactly the accepted 12T109 action.
    alignment = CHARACTER.character_alignment(
        h,
        QUOTIENT_T,
        probe_cores=[int(transformed_core)],
    )
    aligned_cores = alignment[
        "labelToUnambiguousSquarefreeNormCores"
    ].get(SOURCE_LABEL, [])
    if int(transformed_core) not in [int(value) for value in aligned_cores]:
        raise ValueError("norm character does not give containment in 24T19724")

    SHARED.TARGET_T = int(SOURCE_LABEL[3:])
    SHARED.TARGET_LABEL = SOURCE_LABEL
    maximal_profiles, maximal_identities = SHARED.maximal_joint_profiles()
    source_maximal_certificate = SHARED.frobenius_maximal_certificate(
        transformed_source,
        h,
        maximal_profiles,
        maximal_identities,
        1000,
    )
    if not source_maximal_certificate["complete"]:
        raise ValueError("failed to exclude every proper transitive maximal")

    action_rows = []
    for path in sorted(
        DATA.glob("agent_f5_full_ledger_pair_product_actions_shard*of4.jsonl")
    ):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                row = json.loads(raw)
                if str(row["sourceLabel"]) == SOURCE_LABEL:
                    action_rows.append(row)
    if (
        len(action_rows) != 1
        or str(action_rows[0]["targetLabel"]) != TARGET_LABEL
        or int(action_rows[0]["sourceBlockKernelOrder"]) != 2**11
    ):
        raise ValueError("source pair-product action is not the unique exact route")
    exact_action = action_rows[0]

    pair_resolvent = h.symmetric_power(2, monic=True)
    factors = [
        (factor, int(exponent)) for factor, exponent in pair_resolvent.factor()
    ]
    selected = [
        (index, factor)
        for index, (factor, exponent) in enumerate(factors)
        if factor.degree() == 12 and exponent == 1
    ]
    if len(selected) != 1:
        raise ValueError("pair resolvent lacks a unique degree-12 factor")
    factor_index, selected_factor = selected[0]
    candidate = ring_x(selected_factor(x**2))
    candidate_line = coefficient_line(candidate)
    candidate_hash = sha_text(candidate_line)
    if (
        candidate.degree() != 24
        or not candidate.is_monic()
        or not candidate.is_irreducible()
        or int(candidate.number_of_real_roots()) != TARGET_R
    ):
        raise ValueError("candidate failed exact degree/signature gates")

    screen_hits = [
        json.loads(raw)
        for raw in SCREEN.read_text(encoding="utf-8").splitlines()
        if raw.strip() and json.loads(raw).get("potentialSameActionHit")
    ]
    if (
        len(screen_hits) != 1
        or screen_hits[0]["arithmetic"]["candidate"]["coefficientSha256"]
        != candidate_hash
    ):
        raise ValueError("candidate differs from isolated screen result")

    target = connection.execute(
        """
        SELECT t.*,
          EXISTS(
            SELECT 1 FROM baseline_pairs AS b
            WHERE b.label=t.label AND b.r=t.r
          ) AS baseline,
          EXISTS(
            SELECT 1 FROM verifications AS v
            WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1
          ) AS owned
        FROM targets AS t WHERE t.label=? AND t.r=?
        """,
        (TARGET_LABEL, TARGET_R),
    ).fetchone()
    duplicate_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?",
            (candidate_hash,),
        ).fetchone()[0]
    )
    line_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM polynomials WHERE coefficients=?",
            (candidate_line,),
        ).fetchone()[0]
    )
    if (
        (TARGET_LABEL, TARGET_R) in STAGED_PAIRS
        or target is None
        or int(target["team_count"]) != 0
        or int(target["discovered"]) != 0
        or int(target["baseline"]) != 0
        or int(target["owned"]) != 0
        or duplicate_count != 0
        or line_count != 0
    ):
        raise ValueError("candidate failed current novelty/target gates")

    manifest_text = candidate_line + "\n"
    atomic_new(MANIFEST, manifest_text)
    field_discriminant = abs(
        ZZ(NumberField(candidate.change_ring(QQ), "alpha").discriminant())
    )
    h_line = coefficient_line(h)
    source_line = coefficient_line(transformed_source)
    selected_line = coefficient_line(selected_factor)
    certificate = {
        "schemaVersion": "gold-f5-f6-20260728-exact-15273-v1",
        "candidate": {
            "coefficientLine": candidate_line,
            "coefficientSha256": candidate_hash,
            "degree": 24,
            "fieldDiscriminantAbs": str(field_discriminant),
            "irreducible": True,
            "monic": True,
            "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
            "r": TARGET_R,
        },
        "exactAction": exact_action,
        "factorization": {
            "factorDegrees": [
                {
                    "degree": int(factor.degree()),
                    "exponent": exponent,
                    "index": index,
                }
                for index, (factor, exponent) in enumerate(factors)
            ],
            "pairResolventSha256": sha_text(coefficient_line(pair_resolvent)),
            "selectedDegree12FactorIndex": factor_index,
            "selectedDegree12FactorLine": selected_line,
            "selectedDegree12FactorSha256": sha_text(selected_line),
        },
        "liveAndNoveltyGate": {
            "baseline": False,
            "discovered": False,
            "ledgerCoefficientOccurrences": duplicate_count,
            "ledgerLineOccurrences": line_count,
            "owned": False,
            "stagedEightPairCollision": False,
            "targetGeneratedAt": str(target["generated_at"]),
            "teamCount": 0,
        },
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "manifestSha256": sha_text(manifest_text),
        "method": (
            "shifted derivative-radicand F5: g(t)=(t+1)q'(t), "
            "h(z)=Res_t(q(t),z-g(t)), unique pair-orbit factor"
        ),
        "networkCalls": 0,
        "reproduction": {
            "command": (
                "sage -python "
                "gold_f5_f6_20260728_certify_15273.sage.py"
            ),
            "script": str(Path(__file__).resolve().relative_to(ROOT)),
            "scriptSha256": sha_file(Path(__file__).resolve()),
        },
        "sourceClassification": {
            "characterAlignment": alignment,
            "exactLabel": SOURCE_LABEL,
            "maximalSubgroupCertificate": source_maximal_certificate,
            "quotientAction": "12T109",
            "quotientActionJustification": (
                "The irreducible squarefree degree-12 resultant is a "
                "separating equivariant image of q's twelve roots; hence "
                "the accepted 12T109 root action is unchanged."
            ),
            "squarefreeNormCore": int(transformed_core),
        },
        "sourcePin": {
            "acceptedCoefficientSha256": str(source["coefficient_hash"]),
            "acceptedFieldDiscriminantAbs": str(source["field_disc_abs"]),
            "label": SOURCE_LABEL,
            "polynomialIndex": SOURCE_INDEX,
            "quotientLine": QUOTIENT_LINE,
            "quotientSha256": sha_text(QUOTIENT_LINE),
            "r": SOURCE_R,
            "status": "accepted",
            "submissionId": SOURCE_SUBMISSION,
            "t": int(source["t"]),
        },
        "submissionCalls": 0,
        "target": {"label": TARGET_LABEL, "r": TARGET_R, "t": TARGET_T},
        "transform": {
            "a": SHIFT,
            "originalNormCore": int(original_core),
            "resultantQuotientLine": h_line,
            "resultantQuotientSha256": sha_text(h_line),
            "shiftNormProduct": str(square_ratio_product),
            "shiftNormProductIsSquare": True,
            "sourcePolynomialLine": source_line,
            "sourcePolynomialR": 12,
            "sourcePolynomialSha256": sha_text(source_line),
            "transformedNormCore": int(transformed_core),
        },
    }
    rendered = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    atomic_new(OUTPUT, rendered)
    print(
        json.dumps(
            {
                "candidateSha256": candidate_hash,
                "certificate": str(OUTPUT.relative_to(ROOT)),
                "certificateSha256": sha_text(rendered),
                "exactSourceLabel": SOURCE_LABEL,
                "fieldDiscriminantAbs": str(field_discriminant),
                "target": f"{TARGET_LABEL}/r{TARGET_R}",
            },
            sort_keys=True,
        )
    )
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
