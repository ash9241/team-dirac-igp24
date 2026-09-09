#!/usr/bin/env sage -python
"""Rebuild and stage the first exact lower-Kummer pair-product hit.

This is deliberately a single-candidate, offline certificate.  It binds the
historical source to its unique size-12 orbit of unordered pairs, rebuilds the
pair-product factor exactly, and rechecks coefficient novelty and the target
pair against the frozen local ledger before writing a one-line manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from sage.all import NumberField, PolynomialRing, ZZ


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTBOX = ROOT / "outbox"
ACTIONS = DATA / "agent_gold_c_lower_kummer_pair_product_actions.jsonl"
ROUTES = DATA / "agent_gold_c_lower_kummer_pair_product_live_routes.jsonl"
FROZEN_GOLD = DATA / "live_undiscovered_signatures.jsonl"
SOURCE_MANIFEST = DATA / "agent_gold_b_html_wave_manifests" / "score700_bank_1000.txt"
LEDGER = Path("/private/tmp/igp24_gold_c_stable_20260721_pilot2.sqlite3")
CERTIFICATE = DATA / "agent_gold_c_lower_kummer_pair_product_hit_9187_r16.json"
MANIFEST = OUTBOX / "agent_gold_c_lower_kummer_pair_product_hit_9187_r16.txt"

EXPECTED_INPUT_SHA256 = {
    ACTIONS: "6304b195a109317db89413300a51567c9f7c09c6227616591cf15fd344031f40",
    ROUTES: "bf8588b8b3f433cb99666066232f40a20028a1ab72ac344be80b102f5e1f4704",
    FROZEN_GOLD: "62f04747b3f5add5cc2f2d7c5d611124f56ba72d353a9ed6ce34bc925108e647",
    SOURCE_MANIFEST: "7c83b531a416edb6870c74fd02f9d3fb0feac2c6ea7c489562b558d1c6876212",
}
EXPECTED_SOURCE = {
    "coefficientSha256": "9cf7636b762bbd74f3eb35b755f1af6a79be30ff0d289012a548b3ad4760c64f",
    "label": "24T15639",
    "polynomialIndex": 79,
    "r": 20,
    "submissionId": "sub_43f61dfb464e4f41829f5c417a1a3df5",
}
EXPECTED_QUOTIENT_SHA256 = "865e110497715a5802efac1d832fe505cb330e168999cd428cbcb8837ef543f9"
EXPECTED_TARGET = {"label": "24T9187", "r": 16, "t": 9187}
EXPECTED_CANDIDATE_SHA256 = "6d1c8abdc80fa8e289ed93bacb80cae244f33776d1c5fc7f8716c1c2d0b74abf"


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


def main() -> int:
    if CERTIFICATE.exists() or MANIFEST.exists():
        raise ValueError("refusing to overwrite an existing hit certificate or manifest")

    input_hashes = {str(path.relative_to(ROOT)): sha256_path(path) for path in EXPECTED_INPUT_SHA256}
    for path, expected in EXPECTED_INPUT_SHA256.items():
        if input_hashes[str(path.relative_to(ROOT))] != expected:
            raise ValueError(f"input hash mismatch: {path}")

    actions = [row for row in load_jsonl(ACTIONS) if row["sourceLabel"] == EXPECTED_SOURCE["label"]]
    if len(actions) != 1:
        raise ValueError("source does not have exactly one size-12 pair orbit")
    action = actions[0]
    routes = [
        row
        for row in load_jsonl(ROUTES)
        if all(row["source"][key] == value for key, value in EXPECTED_SOURCE.items())
        and row["goldTarget"]["label"] == EXPECTED_TARGET["label"]
        and int(row["goldTarget"]["r"]) == EXPECTED_TARGET["r"]
    ]
    if len(routes) != 1 or routes[0]["action"] != action:
        raise ValueError("the frozen source/action/target route is not unique")
    route = routes[0]
    if not route["targetSignatureIsUniqueFromSourceSignature"]:
        raise ValueError("source signature does not force the target signature")
    if (
        action["targetLabel"] != EXPECTED_TARGET["label"]
        or int(action["targetT"]) != EXPECTED_TARGET["t"]
        or int(action["targetOrder"]) != 6144
        or int(action["targetKernelOrder"]) != 16
    ):
        raise ValueError("unexpected induced action")

    quotient_line = route["sourceQuotientLine"]
    if sha256_bytes(quotient_line.encode("utf-8")) != EXPECTED_QUOTIENT_SHA256:
        raise ValueError("source quotient hash mismatch")
    ring_y = PolynomialRing(ZZ, "y")
    quotient = ring_y([ZZ(value) for value in quotient_line.split(",")])
    if quotient.degree() != 12 or not quotient.is_monic() or not quotient.is_irreducible():
        raise ValueError("source quotient is not monic irreducible degree 12")

    pair_resolvent = quotient.symmetric_power(2, monic=True)
    factorization = pair_resolvent.factor()
    factors = [(factor, int(exponent)) for factor, exponent in factorization]
    degree_twelve = [factor for factor, exponent in factors if factor.degree() == 12 and exponent == 1]
    if len(degree_twelve) != 1:
        raise ValueError("pair resolvent does not have one unique degree-12 factor")
    ring_x = PolynomialRing(ZZ, "x")
    x = ring_x.gen()
    pair_factor = ring_x(degree_twelve[0])
    candidate = pair_factor(x**2)
    if candidate.degree() != 24 or not candidate.is_monic() or not candidate.is_irreducible():
        raise ValueError("constructed polynomial is not monic irreducible degree 24")
    signature = int(candidate.number_of_real_roots())
    if signature != EXPECTED_TARGET["r"]:
        raise ValueError("constructed signature does not equal the frozen target")
    coefficient_line = ",".join(str(value) for value in candidate.list())
    coefficient_sha256 = sha256_bytes(coefficient_line.encode("utf-8"))
    if coefficient_sha256 != EXPECTED_CANDIDATE_SHA256:
        raise ValueError("constructed coefficient hash mismatch")

    connection = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    source_row = connection.execute(
        """
        SELECT p.coefficients,p.coefficient_hash,v.label,v.r,v.scoreable,v.field_disc_abs
        FROM polynomials AS p JOIN verifications AS v USING (submission_id,polynomial_index)
        WHERE p.submission_id=? AND p.polynomial_index=?
        """,
        (EXPECTED_SOURCE["submissionId"], EXPECTED_SOURCE["polynomialIndex"]),
    ).fetchone()
    if source_row is None:
        raise ValueError("historical source is absent from the stable ledger")
    source_coefficients = [ZZ(value) for value in source_row["coefficients"].split(",")]
    if (
        source_row["coefficient_hash"] != EXPECTED_SOURCE["coefficientSha256"]
        or source_row["label"] != EXPECTED_SOURCE["label"]
        or int(source_row["r"]) != EXPECTED_SOURCE["r"]
        or int(source_row["scoreable"]) != 1
        or source_coefficients[::2] != quotient.list()
        or any(source_coefficients[index] for index in range(1, 25, 2))
    ):
        raise ValueError("historical source provenance mismatch")

    known_hash_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM polynomials WHERE coefficient_hash=?", (coefficient_sha256,)
        ).fetchone()[0]
    )
    target = connection.execute(
        """
        SELECT t.*,
          EXISTS(SELECT 1 FROM baseline_pairs b WHERE b.label=t.label AND b.r=t.r) AS baseline,
          EXISTS(SELECT 1 FROM verifications v WHERE v.label=t.label AND v.r=t.r AND v.scoreable=1) AS owned
        FROM targets t WHERE t.label=? AND t.r=?
        """,
        (EXPECTED_TARGET["label"], EXPECTED_TARGET["r"]),
    ).fetchone()
    connection.close()
    if target is None or int(target["baseline"]) or int(target["owned"]) or known_hash_count:
        raise ValueError("candidate is no longer novel and locally live")

    frozen = {
        (row["label"], int(row["r"])): row for row in load_jsonl(FROZEN_GOLD)
    }.get((EXPECTED_TARGET["label"], EXPECTED_TARGET["r"]))
    if frozen is None:
        raise ValueError("target pair is absent from frozen gold")

    number_field = NumberField(candidate, "a")
    field_discriminant_abs = abs(ZZ(number_field.absolute_discriminant()))
    manifest_text = coefficient_line + "\n"
    certificate = {
        "candidate": {
            "coefficientLine": coefficient_line,
            "coefficientSha256": coefficient_sha256,
            "fieldDiscriminantAbs": str(field_discriminant_abs),
            "irreducible": True,
            "polynomialDiscriminantAbs": str(abs(ZZ(candidate.discriminant()))),
            "r": signature,
        },
        "exactTargetCertificate": {
            "inducedAction": action,
            "pairResolventFactorDegrees": [
                {"degree": int(factor.degree()), "exponent": exponent}
                for factor, exponent in factors
            ],
            "pairResolventSha256": sha256_bytes(
                ",".join(str(value) for value in pair_resolvent.list()).encode("utf-8")
            ),
            "uniqueDegree12FactorCoefficientLine": ",".join(str(value) for value in pair_factor.list()),
            "proof": (
                "The exact source action has one size-12 orbit of unordered pairs. "
                "The exact pair-product resolvent has one degree-12 factor, binding that "
                "factor to the displayed orbit. Adjoining square roots of its roots gives "
                "the displayed induced signed action, identified exactly by GAP as 24T9187."
            ),
        },
        "frozenGoldTarget": frozen,
        "inputSha256": input_hashes,
        "ledger": {
            "path": str(LEDGER),
            "sha256": sha256_path(LEDGER),
        },
        "localLiveRecheck": {
            "baseline": bool(target["baseline"]),
            "coefficientHashOccurrences": known_hash_count,
            "discovered": bool(target["discovered"]),
            "generatedAt": target["generated_at"],
            "minimumDiscAbs": target["minimum_disc_abs"],
            "owned": bool(target["owned"]),
            "teamCount": int(target["team_count"]),
        },
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "manifestSha256": sha256_bytes(manifest_text.encode("utf-8")),
        "method": "exact-conjugate-pair-product-unique-orbit-v1",
        "networkCalls": 0,
        "source": {**route["source"], "sourceQuotientLine": quotient_line},
        "submissionCalls": 0,
        "target": EXPECTED_TARGET,
    }
    certificate_text = json.dumps(certificate, indent=2, sort_keys=True) + "\n"
    atomic_write(MANIFEST, manifest_text)
    atomic_write(CERTIFICATE, certificate_text)
    print(
        json.dumps(
            {
                "certificate": str(CERTIFICATE),
                "certificateSha256": sha256_bytes(certificate_text.encode("utf-8")),
                "coefficientSha256": coefficient_sha256,
                "fieldDiscriminantAbs": str(field_discriminant_abs),
                "manifest": str(MANIFEST),
                "manifestSha256": sha256_bytes(manifest_text.encode("utf-8")),
                "target": EXPECTED_TARGET,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
