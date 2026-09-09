#!/usr/bin/env sage -python
"""Exact q174 gate restricted to the unambiguous core-2 local routes.

The frozen broad-local artifact contains both an ambiguous core-1 route and
the singleton core-2 character route for 24T21591.  This driver admits only
the latter at r=12,16,20,24, independently certifies the claimed 12T174
quotient, and invokes the existing exact Selmer/Frobenius audit once.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

from sage.all import NumberField, PolynomialRing, QQ, ZZ, pari, proof


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
STRUCTURES = DATA / "agent_non12_tower_structures.jsonl"
LOCAL = DATA / "broad_structural_character_gate_q174_local_postsync_20260730.json"
LOCAL_SHA256 = (
    "e93ddaed48bf932e58f4a65559e5ab86ef3ea007462c803df5929290fad73f09"
)
FIELD_SHA256 = (
    "d9d7ef149cb1c1967c07a86b3d52ae6002e6acbd85d01b9b4a11b1d81f7280d8"
)
QUOTIENT_T = 174
QUOTIENT_ORDER = 648
TARGET_CORE = 2
TARGET_LABEL = "24T21591"
TARGET_RS = (12, 16, 20, 24)
COMPATIBLE_MAXIMAL_LABEL = "24T7578"
INCOMPATIBLE_MAXIMAL_LABEL = "24T18041"
STRUCTURAL_SOURCE_LABEL = "24T22534"
STRUCTURAL_BLOCK_KERNEL_ORDER = 4096


def load_broad_driver():
    path = ROOT / "broad_structural_character_gate_20260730.sage.py"
    spec = importlib.util.spec_from_file_location(
        "q174_core2_broad_driver", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import broad character gate from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        raise FileExistsError(f"refusing to overwrite {temporary}")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def strict_live_pairs() -> dict[str, list[dict]]:
    connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT t.label,t.r,t.team_count,t.generated_at
            FROM targets AS t
            LEFT JOIN baseline_pairs AS b
              ON b.label=t.label AND b.r=t.r
            LEFT JOIN (
                SELECT DISTINCT label,r
                FROM verifications
                WHERE scoreable=1
            ) AS owned
              ON owned.label=t.label AND owned.r=t.r
            WHERE t.label=?
              AND t.team_count=0
              AND t.discovered=0
              AND b.label IS NULL
              AND owned.label IS NULL
            ORDER BY t.r
            """,
            (TARGET_LABEL,),
        ).fetchall()
    finally:
        connection.close()
    actual = [(str(label), int(r)) for label, r, _tc, _at in rows]
    expected = [(TARGET_LABEL, r) for r in TARGET_RS]
    if actual != expected:
        raise ValueError(
            f"strict q174 live-pair state changed: {actual}, "
            f"expected {expected}"
        )
    return {
        TARGET_LABEL: [
            {
                "baseline": False,
                "discovered": False,
                "generatedAt": (
                    str(generated_at) if generated_at else None
                ),
                "locallyOwned": False,
                "r": int(r),
                "teamCount": int(team_count),
            }
            for _label, r, team_count, generated_at in rows
        ]
    }


def selected_source() -> tuple[dict, dict]:
    if sha256_path(LOCAL) != LOCAL_SHA256:
        raise ValueError("q174 local artifact byte digest changed")
    payload = json.loads(LOCAL.read_text(encoding="utf-8"))
    if (
        int(payload.get("quotientT12", -1)) != QUOTIENT_T
        or payload.get("targetLabels") != [TARGET_LABEL]
        or payload.get("audit", {}).get("phase") != "local"
    ):
        raise ValueError("q174 frozen local artifact identity changed")
    fields = [
        row
        for row in payload.get("fields", [])
        if row.get("fieldCanonicalSha256") == FIELD_SHA256
    ]
    if len(fields) != 1:
        raise ValueError("selected q174 local field is absent or duplicated")
    source = copy.deepcopy(fields[0])
    polynomial_text = str(source.get("canonicalPolynomial", ""))
    if hashlib.sha256(polynomial_text.encode()).hexdigest() != FIELD_SHA256:
        raise ValueError("selected q174 canonical polynomial hash changed")
    if (
        int(source.get("quotientT12", -1)) != QUOTIENT_T
        or source.get("family") != f"12T{QUOTIENT_T}"
        or source.get("alignment", {}).get("coreToPossibleLabels", {}).get(
            str(TARGET_CORE)
        )
        != [TARGET_LABEL]
    ):
        raise ValueError("selected q174 quotient/alignment identity changed")

    selected = [
        copy.deepcopy(pair)
        for pair in source.get("localPairGates", [])
        if (
            int(pair.get("core", -1)) == TARGET_CORE
            and pair.get("possibleLabelsForCore") == [TARGET_LABEL]
            and pair.get("label") == TARGET_LABEL
            and int(pair.get("r", -1)) in TARGET_RS
        )
    ]
    selected.sort(key=lambda pair: int(pair["r"]))
    if [int(pair["r"]) for pair in selected] != list(TARGET_RS):
        raise ValueError("q174 core-2 local route set changed")
    for pair in selected:
        expected_negative = 12 - int(pair["r"]) // 2
        if (
            pair.get("localPass") is not True
            or pair.get("exactCharacterMatch") is not True
            or pair.get("finalGroupDisambiguationRequired") is not False
            or pair.get("realSignatureBoundPass") is not True
            or pair.get("signedNormParityPass") is not True
            or int(pair.get("negativeRealEmbeddingsRequired", -1))
            != expected_negative
            or int(pair.get("teamCount", -1)) != 0
        ):
            raise ValueError(f"invalid frozen q174 core-2 route: {pair}")

    core_gate = source.get("localCoreGates", {}).get(str(TARGET_CORE))
    if (
        not isinstance(core_gate, dict)
        or core_gate.get("rationalNormParityLocallyPossible") is not True
        or core_gate.get("rationalPrimeSupport") != [TARGET_CORE]
    ):
        raise ValueError("q174 frozen core-2 local gate changed")
    source["localCoreGates"] = {str(TARGET_CORE): core_gate}
    source["localPairGates"] = selected
    source["exactSelmerSignGate"] = {
        "locallyPassingCores": [TARGET_CORE],
        "pairs": [],
        "status": "selected_for_exact",
    }
    source["selection"] = {
        "core": TARGET_CORE,
        "possibleLabelsForCore": [TARGET_LABEL],
        "rValues": list(TARGET_RS),
        "rule": "singleton_character_core_only",
    }
    return source, payload


def pinned_structural_quotient_certificate(source: dict) -> dict:
    """Certify q174 from the verifier-labelled parent's unique block action.

    This is the same exact structural certificate used by the productive
    q70/q77/q136/q138/q210 lift drivers: an accepted degree-24 parent with a
    unique 12x2 block system has quotient equal to that block action.
    """

    source_rows = source.get("sourceRows", [])
    if not source_rows or {
        str(row.get("label")) for row in source_rows
    } != {STRUCTURAL_SOURCE_LABEL}:
        raise ValueError("q174 structural source-label pin changed")
    if not all(row.get("scoreable") is True for row in source_rows):
        raise ValueError("q174 structural source is not scoreable/accepted")

    structural_rows = []
    with STRUCTURES.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("label") == STRUCTURAL_SOURCE_LABEL:
                structural_rows.append(row)
    if len(structural_rows) != 1:
        raise ValueError("q174 structural parent row is absent or duplicated")
    systems = [
        system
        for system in structural_rows[0].get("blockSystems", [])
        if system.get("shape") == "12x2"
    ]
    if len(systems) != 1:
        raise ValueError("q174 parent no longer has a unique 12x2 system")
    system = systems[0]
    if (
        system.get("quotientActionLabel") != f"12T{QUOTIENT_T}"
        or int(system.get("blockKernelOrder", -1))
        != STRUCTURAL_BLOCK_KERNEL_ORDER
    ):
        raise ValueError("q174 pinned block-action certificate changed")
    return {
        "algorithm": (
            "verifier-accepted parent label plus unique exact 12x2 "
            "block action"
        ),
        "blockKernelOrder": STRUCTURAL_BLOCK_KERNEL_ORDER,
        "degree": 12,
        "order": QUOTIENT_ORDER,
        "parentLabel": STRUCTURAL_SOURCE_LABEL,
        "status": "exact_quotient_group_certified",
        "transitiveLabel": f"12T{QUOTIENT_T}",
        "transitiveNumber": QUOTIENT_T,
        "uniqueParentBlockSystem": True,
    }


def parse_coset_schedule(text: str | None) -> list[int] | None:
    if not text:
        return None
    return [int(value.strip(), 0) for value in text.split(",") if value.strip()]


def configure_quotient_compatible_maximal_certifier(
    base, quotient_certificate: dict
) -> dict:
    """Install the audited 12T174-compatible maximal-profile filter.

    The target has five proper transitive maximal-subgroup classes.  Four are
    24T18041 classes whose induced 12-block image is 12T47 of order 72, so an
    exact 12T174 quotient excludes them.  The sole compatible class is
    24T7578, whose induced quotient is 12T174 of order 648.
    """
    if (
        quotient_certificate.get("status")
        != "exact_quotient_group_certified"
        or int(quotient_certificate.get("transitiveNumber", -1)) != QUOTIENT_T
        or int(quotient_certificate.get("order", -1)) != QUOTIENT_ORDER
    ):
        raise ValueError(
            "q174 maximal filter requires an exact 12T174/order-648 quotient"
        )

    shared = base.HELPER.SHARED
    unfiltered_profiles = shared.maximal_joint_profiles

    def quotient_compatible_profiles():
        if (
            int(shared.TARGET_T) != int(TARGET_LABEL[3:])
            or shared.TARGET_LABEL != TARGET_LABEL
        ):
            raise ValueError("q174 maximal filter used for the wrong target")
        profiles, identities = unfiltered_profiles()
        if len(profiles) != len(identities):
            raise ValueError("q174 maximal profile/identity length mismatch")
        observed_labels = sorted(
            str(identity.get("label")) for identity in identities
        )
        expected_labels = sorted(
            [INCOMPATIBLE_MAXIMAL_LABEL] * 4
            + [COMPATIBLE_MAXIMAL_LABEL]
        )
        if observed_labels != expected_labels:
            raise ValueError(
                f"q174 proper maximal classes changed: {observed_labels}, "
                f"expected {expected_labels}"
            )
        retained = [
            (
                profile,
                {
                    **identity,
                    "quotientCompatibility": {
                        "blockQuotientOrder": QUOTIENT_ORDER,
                        "blockQuotientTransitiveLabel": f"12T{QUOTIENT_T}",
                        "status": "compatible_with_exact_quotient",
                    },
                },
            )
            for profile, identity in zip(profiles, identities)
            if identity.get("label") == COMPATIBLE_MAXIMAL_LABEL
        ]
        if len(retained) != 1:
            raise ValueError("q174 compatible maximal class is not unique")
        return (
            [profile for profile, _identity in retained],
            [identity for _profile, identity in retained],
        )

    shared.maximal_joint_profiles = quotient_compatible_profiles
    return {
        "enabled": True,
        "exactQuotientOrder": QUOTIENT_ORDER,
        "exactQuotientTransitiveLabel": f"12T{QUOTIENT_T}",
        "profileFilter": "exact_block_quotient_compatibility",
        "prunedMaximalClasses": [
            {
                "classCount": 4,
                "label": INCOMPATIBLE_MAXIMAL_LABEL,
                "quotientImageOrder": 72,
                "quotientImageTransitiveLabel": "12T47",
                "reason": "incompatible_with_exact_12T174_quotient",
            }
        ],
        "requiredWitnessMaximalClasses": [
            {
                "classCount": 1,
                "label": COMPATIBLE_MAXIMAL_LABEL,
                "quotientImageOrder": QUOTIENT_ORDER,
                "quotientImageTransitiveLabel": f"12T{QUOTIENT_T}",
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--witness-primes", type=int, default=1000)
    parser.add_argument("--max-reconstructions", type=int, default=64)
    parser.add_argument(
        "--max-coset-reconstructions-per-sign",
        type=int,
        default=64,
    )
    parser.add_argument("--coset-mask-start", type=int, default=0)
    parser.add_argument("--coset-mask-stride", type=int, default=1)
    parser.add_argument(
        "--coset-mask-list",
        help="comma-separated explicit Selmer-kernel coset masks",
    )
    parser.add_argument("--pari-stack-bytes", type=int, default=0)
    parser.add_argument("--pari-stack-max-bytes", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    if args.witness_primes < 0:
        raise ValueError("--witness-primes must be nonnegative")
    if args.max_reconstructions < 1:
        raise ValueError("--max-reconstructions must be positive")
    if args.max_coset_reconstructions_per_sign < 1:
        raise ValueError(
            "--max-coset-reconstructions-per-sign must be positive"
        )
    if args.coset_mask_start < 0 or args.coset_mask_stride < 1:
        raise ValueError("coset mask start/stride must be nonnegative/positive")
    coset_schedule = parse_coset_schedule(args.coset_mask_list)
    if coset_schedule is not None and any(mask < 0 for mask in coset_schedule):
        raise ValueError("explicit coset masks must be nonnegative")

    source, local_payload = selected_source()
    live = strict_live_pairs()
    broad = load_broad_driver()
    base = broad.load_base(QUOTIENT_T, [TARGET_LABEL])
    ring = PolynomialRing(QQ, "y")
    quotient = ring(
        [ZZ(value) for value in source["canonicalPolynomial"].split(",")]
    )
    if quotient.degree() != 12 or not quotient.is_irreducible():
        raise ValueError("selected q174 quotient is not irreducible degree 12")

    print(
        json.dumps(
            {
                "event": "q174_exact_start",
                "fieldCanonicalSha256": FIELD_SHA256,
                "rValues": list(TARGET_RS),
                "selectedCore": TARGET_CORE,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    quotient_certificate = pinned_structural_quotient_certificate(source)
    maximal_certifier = configure_quotient_compatible_maximal_certifier(
        base, quotient_certificate
    )
    proof.number_field(False)
    if args.pari_stack_bytes:
        pari.allocatemem(
            int(args.pari_stack_bytes),
            int(args.pari_stack_max_bytes),
        )
    field = NumberField(quotient.change_ring(QQ), "a")
    exact_gate = base.exact_selmer_sign_gate(
        field,
        quotient,
        {TARGET_CORE: source["localPairGates"]},
        bool(maximal_certifier["enabled"]),
        int(args.witness_primes),
        int(args.max_reconstructions),
        int(args.max_coset_reconstructions_per_sign),
        int(args.coset_mask_start),
        int(args.coset_mask_stride),
        coset_schedule,
    )
    exact_pairs = exact_gate.get("pairs", [])
    actual_routes = sorted(
        (int(pair["core"]), str(pair["label"]), int(pair["r"]))
        for pair in exact_pairs
    )
    expected_routes = [
        (TARGET_CORE, TARGET_LABEL, r) for r in TARGET_RS
    ]
    if actual_routes != expected_routes:
        raise ValueError(
            f"q174 exact route set changed: {actual_routes}, "
            f"expected {expected_routes}"
        )
    if not maximal_certifier["enabled"]:
        for pair in exact_pairs:
            pair["maximalSubgroupCertification"] = {
                "complete": False,
                "reason": maximal_certifier["reason"],
                "status": "deferred",
            }
            for reconstruction in pair.get(
                "testedExactSelmerReconstructions", []
            ):
                if str(reconstruction.get("candidateStatus", "")).startswith(
                    "certified_"
                ):
                    raise ValueError(
                        "q174 generic maximal certification was not disabled"
                    )

    source["exactSelmerSignGate"] = exact_gate
    source["quotientGaloisCertificate"] = quotient_certificate
    source["maximalSubgroupCertification"] = {
        **maximal_certifier,
        "status": "quotient_compatible_filter_installed",
    }
    routes = (
        broad.certified_routes([source])
        if maximal_certifier["enabled"]
        else []
    )
    payload = {
        "audit": {
            "coefficientSearches": 0,
            "conditionalOnGRH": True,
            "fieldEnumerationCalls": 0,
            "inputLocalGateSha256": LOCAL_SHA256,
            "maximalSubgroupCertification": source[
                "maximalSubgroupCertification"
            ],
            "networkCalls": 0,
            "phase": "selected_exact_core2",
            "submissionCalls": 0,
            "targetSnapshotGeneratedAt": sorted(
                {
                    pair["generatedAt"]
                    for pair in live[TARGET_LABEL]
                    if pair["generatedAt"]
                }
            ),
            "witnessPrimeCount": int(args.witness_primes),
        },
        "fields": [source],
        "livePairs": live,
        "quotientT12": QUOTIENT_T,
        "selection": {
            "core": TARGET_CORE,
            "fieldCanonicalSha256": FIELD_SHA256,
            "possibleLabelsForCore": [TARGET_LABEL],
            "rValues": list(TARGET_RS),
        },
        "sourceCensusSummary": local_payload.get("sourceCensusSummary"),
        "summary": {
            "auditedFields": 1,
            "certificationDeferred": not maximal_certifier["enabled"],
            "certifiedExactRouteCount": len(routes),
            "certifiedExactRoutes": routes,
            "exactSelmerPassingRouteCount": sum(
                pair.get("status") == "exact_selmer_sign_pass"
                for pair in exact_pairs
            ),
            "localPassingPairFieldRoutes": len(source["localPairGates"]),
        },
        "targetLabels": [TARGET_LABEL],
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    write_atomic(output, rendered)
    print(
        json.dumps(
            {
                "event": "artifact_written",
                "output": str(output),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                **payload["summary"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
