#!/usr/bin/env sage -python
"""Exact q242 gate restricted to the core-5 routes for 24T23282.

The frozen broad-local artifact has eighteen locally passing routes, but its
character alignment is deliberately conservative.  This driver keeps only
the second canonical field and core 5 at r=16,20,24.  Before any Selmer work
it independently proves:

* the quotient Galois group is exactly 12T242 of order 2592;
* the quotient polynomial factors 6+6 over Q(sqrt(5));
* every intransitive index-2 character of 12T242 gives the lift 24T23282;
* among the proper transitive maximals of 24T23282, only 24T12494 has the
  full 12T242 block quotient.

The expensive exact Selmer reconstruction remains explicitly bounded by the
CLI arguments.  This script performs no network call or submission.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sqlite3
from collections import Counter
from pathlib import Path

from sage.all import (
    NumberField,
    PolynomialRing,
    QQ,
    QuadraticField,
    ZZ,
    libgap,
    pari,
    proof,
)


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
STRUCTURES = DATA / "agent_non12_tower_structures.jsonl"
LOCAL = DATA / "broad_structural_character_gate_q242_local_postsync_20260730.json"
LOCAL_SHA256 = (
    "9ddc115835b5a3eb6b2c910bcac53a70c95cda6ffbd28eb311f4fe90c5549c76"
)
FIELD_SHA256 = (
    "d0f6e233631516b114f9b67441037ab699f87383014edccbc9a199a542475594"
)
QUOTIENT_T = 242
QUOTIENT_ORDER = 2592
TARGET_CORE = 5
TARGET_LABEL = "24T23282"
TARGET_RS = (16, 20, 24)
ORIGINAL_POSSIBLE_LABELS = (
    "24T23282",
    "24T23283",
    "24T23284",
)
TRIVIAL_CHARACTER_LABEL = "24T23285"
COMPATIBLE_MAXIMAL_LABEL = "24T12494"
COMPATIBLE_MAXIMAL_ORDER = 20736
EXPECTED_TARGET_ORDER = 5308416
EXPECTED_TARGET_BLOCK_KERNEL_ORDER = 2048
STRUCTURAL_SOURCE_KERNELS = {
    "24T14529": 16,
    "24T23788": 4096,
}

EXPECTED_MAXIMAL_ROWS = Counter(
    {
        ("24T22543", 2654208, 2048, "12T210", 1296): 4,
        ("24T22547", 2654208, 2048, "12T210", 1296): 2,
        ("24T22560", 2654208, 2048, "12T214", 1296): 4,
        ("24T22562", 2654208, 2048, "12T214", 1296): 2,
        (COMPATIBLE_MAXIMAL_LABEL, COMPATIBLE_MAXIMAL_ORDER, 8, "12T242", 2592): 1,
    }
)


def load_broad_driver():
    path = ROOT / "broad_structural_character_gate_20260730.sage.py"
    spec = importlib.util.spec_from_file_location(
        "q242_core5_broad_driver", path
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
            f"strict q242 live-pair state changed: {actual}, "
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
        raise ValueError("q242 local artifact byte digest changed")
    payload = json.loads(LOCAL.read_text(encoding="utf-8"))
    if (
        int(payload.get("quotientT12", -1)) != QUOTIENT_T
        or payload.get("targetLabels") != list(ORIGINAL_POSSIBLE_LABELS)
        or payload.get("audit", {}).get("phase") != "local"
        or len(payload.get("fields", [])) != 2
        or int(
            payload.get("summary", {}).get(
                "localPassingPairFieldRoutes", -1
            )
        )
        != 18
    ):
        raise ValueError("q242 frozen local artifact identity changed")
    fields = [
        row
        for row in payload.get("fields", [])
        if row.get("fieldCanonicalSha256") == FIELD_SHA256
    ]
    if len(fields) != 1:
        raise ValueError("selected q242 local field is absent or duplicated")
    source = copy.deepcopy(fields[0])
    polynomial_text = str(source.get("canonicalPolynomial", ""))
    if hashlib.sha256(polynomial_text.encode()).hexdigest() != FIELD_SHA256:
        raise ValueError("selected q242 canonical polynomial hash changed")
    alignment = source.get("alignment", {})
    if (
        int(source.get("quotientT12", -1)) != QUOTIENT_T
        or source.get("family") != f"12T{QUOTIENT_T}"
        or alignment.get("coreToPossibleLabels", {}).get(str(TARGET_CORE))
        != list(ORIGINAL_POSSIBLE_LABELS)
        or alignment.get("targetNormCores", {}).get(TARGET_LABEL)
        != [1, 5, 21, 105]
    ):
        raise ValueError("selected q242 quotient/alignment identity changed")

    selected = [
        copy.deepcopy(pair)
        for pair in source.get("localPairGates", [])
        if (
            int(pair.get("core", -1)) == TARGET_CORE
            and pair.get("possibleLabelsForCore")
            == list(ORIGINAL_POSSIBLE_LABELS)
            and pair.get("label") == TARGET_LABEL
            and int(pair.get("r", -1)) in TARGET_RS
        )
    ]
    selected.sort(key=lambda pair: int(pair["r"]))
    if [int(pair["r"]) for pair in selected] != list(TARGET_RS):
        raise ValueError("q242 core-5/24T23282 local route set changed")
    for pair in selected:
        expected_negative = 12 - int(pair["r"]) // 2
        if (
            pair.get("localPass") is not True
            or pair.get("exactCharacterMatch") is not True
            or pair.get("finalGroupDisambiguationRequired") is not True
            or pair.get("realSignatureBoundPass") is not True
            or pair.get("signedNormParityPass") is not True
            or int(pair.get("negativeRealEmbeddingsRequired", -1))
            != expected_negative
            or int(pair.get("teamCount", -1)) != 0
        ):
            raise ValueError(f"invalid frozen q242 core-5 route: {pair}")

    core_gate = source.get("localCoreGates", {}).get(str(TARGET_CORE))
    expected_prime_tests = [
        {
            "desiredRationalNormValuationParity": 1,
            "prime": TARGET_CORE,
            "primeIdealCount": 3,
            "residueDegrees": [1, 2, 3],
            "valuationParityLocallyPossible": True,
        }
    ]
    if (
        not isinstance(core_gate, dict)
        or core_gate.get("rationalNormParityLocallyPossible") is not True
        or core_gate.get("rationalPrimeSupport") != [TARGET_CORE]
        or core_gate.get("rationalPrimeTests") != expected_prime_tests
        or source.get("exactSelmerSignGate", {}).get(
            "locallyPassingCores"
        )
        != [1, TARGET_CORE]
    ):
        raise ValueError("q242 frozen core-5 local gate changed")
    source["localCoreGates"] = {str(TARGET_CORE): core_gate}
    source["localPairGates"] = selected
    source["exactSelmerSignGate"] = {
        "locallyPassingCores": [TARGET_CORE],
        "pairs": [],
        "status": "selected_for_exact",
    }
    source["selection"] = {
        "broadLocalPossibleLabelsForCore": list(
            ORIGINAL_POSSIBLE_LABELS
        ),
        "core": TARGET_CORE,
        "resolvedLabel": TARGET_LABEL,
        "rValues": list(TARGET_RS),
        "rule": "exact_quadratic_character_factorization",
    }
    return source, payload


def pinned_structural_quotient_certificate(source: dict) -> dict:
    """Certify q242 from accepted parents' unique 12x2 block actions."""

    source_rows = source.get("sourceRows", [])
    source_labels = {str(row.get("label")) for row in source_rows}
    if not source_rows or source_labels != set(STRUCTURAL_SOURCE_KERNELS):
        raise ValueError("q242 structural source-label pins changed")
    if not all(row.get("scoreable") is True for row in source_rows):
        raise ValueError("q242 structural source is not scoreable/accepted")
    rows_by_label = {}
    with STRUCTURES.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            label = str(row.get("label"))
            if label in STRUCTURAL_SOURCE_KERNELS:
                if label in rows_by_label:
                    raise ValueError("duplicate q242 structural parent row")
                rows_by_label[label] = row
    if set(rows_by_label) != set(STRUCTURAL_SOURCE_KERNELS):
        raise ValueError("q242 structural parent row is absent")
    for label, expected_kernel in STRUCTURAL_SOURCE_KERNELS.items():
        systems = [
            system
            for system in rows_by_label[label].get("blockSystems", [])
            if system.get("shape") == "12x2"
        ]
        if len(systems) != 1:
            raise ValueError(
                f"q242 parent {label} no longer has a unique 12x2 system"
            )
        system = systems[0]
        if (
            system.get("quotientActionLabel") != f"12T{QUOTIENT_T}"
            or int(system.get("blockKernelOrder", -1)) != expected_kernel
        ):
            raise ValueError(
                f"q242 pinned block-action certificate changed for {label}"
            )
    return {
        "algorithm": (
            "verifier-accepted parent labels plus unique exact 12x2 "
            "block actions"
        ),
        "degree": 12,
        "order": QUOTIENT_ORDER,
        "parentBlockKernelOrders": STRUCTURAL_SOURCE_KERNELS,
        "parentLabels": sorted(STRUCTURAL_SOURCE_KERNELS),
        "status": "exact_quotient_group_certified",
        "transitiveLabel": f"12T{QUOTIENT_T}",
        "transitiveNumber": QUOTIENT_T,
        "uniqueParentBlockSystems": True,
    }


def parse_coset_schedule(text: str | None) -> list[int] | None:
    if not text:
        return None
    return [int(value.strip(), 0) for value in text.split(",") if value.strip()]


def exact_core5_character_certificate(
    quotient,
    quotient_certificate: dict,
) -> dict:
    """Resolve core 5 by exact factorization and the 12T242 character table."""
    if (
        quotient_certificate.get("status")
        != "exact_quotient_group_certified"
        or int(quotient_certificate.get("transitiveNumber", -1)) != QUOTIENT_T
        or int(quotient_certificate.get("order", -1)) != QUOTIENT_ORDER
    ):
        raise ValueError(
            "q242 character resolution requires exact 12T242/order-2592"
        )

    quadratic = QuadraticField(TARGET_CORE, "s")
    extension_ring = PolynomialRing(quadratic, "u")
    quotient_over_quadratic = extension_ring(quotient)
    factorization = list(quotient_over_quadratic.factor())
    factor_degrees = sorted(int(factor.degree()) for factor, _ in factorization)
    reconstructed = extension_ring.one()
    for factor, exponent in factorization:
        reconstructed *= factor**int(exponent)
    if (
        factor_degrees != [6, 6]
        or any(int(exponent) != 1 for _factor, exponent in factorization)
        or reconstructed != quotient_over_quadratic
    ):
        raise ValueError(
            "q242 quotient over Q(sqrt(5)) is not the exact squarefree "
            f"6+6 factorization: degrees={factor_degrees}, "
            f"exponents={[int(exponent) for _factor, exponent in factorization]}"
        )

    quotient_group = libgap.TransitiveGroup(12, QUOTIENT_T)
    if int(libgap.Size(quotient_group)) != QUOTIENT_ORDER:
        raise ValueError("standard 12T242 order changed")
    generators = list(libgap.GeneratorsOfGroup(quotient_group))
    kernels = [
        subgroup
        for subgroup in libgap.NormalSubgroups(quotient_group)
        if int(libgap.Size(quotient_group))
        == 2 * int(libgap.Size(subgroup))
    ]
    if len(kernels) != 15:
        raise ValueError(
            f"12T242 has {len(kernels)} index-2 kernels, expected 15"
        )

    flips = [
        libgap.eval(f"({2 * index - 1},{2 * index})")
        for index in range(1, 13)
    ]
    even_flips = [flips[0] * flips[index] for index in range(1, 12)]

    def lifted(permutation):
        images = []
        for point in range(1, 13):
            image = int(libgap.OnPoints(point, permutation))
            images.extend([2 * image - 1, 2 * image])
        return libgap.PermList(images)

    lifted_generators = [lifted(generator) for generator in generators]

    def lift_label(kernel) -> tuple[str, int]:
        action_generators = list(even_flips)
        for generator, lifted_generator in zip(
            generators, lifted_generators
        ):
            action_generators.append(
                lifted_generator
                * (
                    flips[0]
                    if generator not in kernel
                    else libgap.One(flips[0])
                )
            )
        action = libgap.Group(action_generators)
        return (
            f"24T{int(libgap.TransitiveIdentification(action))}",
            int(libgap.Size(action)),
        )

    points_12 = libgap.eval("[1..12]")
    rows = []
    observed = Counter()
    for kernel in kernels:
        if libgap.IsTransitive(kernel, points_12):
            kernel_action = (
                f"12T{int(libgap.TransitiveIdentification(kernel))}"
            )
            orbit_sizes = [12]
        else:
            orbit_sizes = sorted(
                int(libgap.Length(orbit))
                for orbit in libgap.Orbits(kernel, points_12)
            )
            if orbit_sizes != [6, 6]:
                raise ValueError(
                    f"unexpected intransitive q242 character orbits "
                    f"{orbit_sizes}"
                )
            kernel_action = "intransitive_6_plus_6"
        label, action_order = lift_label(kernel)
        if action_order != EXPECTED_TARGET_ORDER:
            raise ValueError(
                f"q242 character lift {label} has order {action_order}, "
                f"expected {EXPECTED_TARGET_ORDER}"
            )
        observed[(kernel_action, label)] += 1
        rows.append(
            {
                "kernelAction": kernel_action,
                "kernelOrder": int(libgap.Size(kernel)),
                "liftLabel": label,
                "orbitSizes": orbit_sizes,
            }
        )

    expected = Counter(
        {
            ("intransitive_6_plus_6", TARGET_LABEL): 3,
            ("12T214", "24T23283"): 6,
            ("12T210", "24T23284"): 6,
        }
    )
    if observed != expected:
        raise ValueError(
            f"q242 exact character table changed: {observed}, "
            f"expected {expected}"
        )
    trivial_label, trivial_order = lift_label(quotient_group)
    if (
        trivial_label != TRIVIAL_CHARACTER_LABEL
        or trivial_order != EXPECTED_TARGET_ORDER
    ):
        raise ValueError(
            "q242 trivial character no longer gives 24T23285"
        )

    return {
        "arithmeticCharacter": {
            "factorDegreesOverQuadraticField": factor_degrees,
            "quadraticField": str(quadratic.defining_polynomial()),
            "squarefreeNormCore": TARGET_CORE,
        },
        "exactQuotientOrder": QUOTIENT_ORDER,
        "exactQuotientTransitiveLabel": f"12T{QUOTIENT_T}",
        "groupCharacterTable": {
            "indexTwoKernelCount": len(kernels),
            "rows": sorted(
                rows,
                key=lambda row: (
                    row["kernelAction"],
                    row["liftLabel"],
                    row["orbitSizes"],
                ),
            ),
            "trivialCharacterLiftLabel": trivial_label,
        },
        "method": (
            "exact factorization over Q(sqrt(5)) plus exhaustive "
            "12T242 index-2 character-lift identification"
        ),
        "originalPossibleLabels": list(ORIGINAL_POSSIBLE_LABELS),
        "resolvedLabel": TARGET_LABEL,
        "status": "exact_character_label_certified",
    }


def configure_quotient_compatible_maximal_certifier(
    base,
    quotient_certificate: dict,
) -> dict:
    """Retain only transitive maximals with the exact full 12T242 image."""
    if (
        quotient_certificate.get("status")
        != "exact_quotient_group_certified"
        or int(quotient_certificate.get("transitiveNumber", -1)) != QUOTIENT_T
        or int(quotient_certificate.get("order", -1)) != QUOTIENT_ORDER
    ):
        raise ValueError(
            "q242 maximal filter requires exact 12T242/order-2592"
        )

    target_t = int(TARGET_LABEL[3:])
    target = libgap.TransitiveGroup(24, target_t)
    if int(libgap.Size(target)) != EXPECTED_TARGET_ORDER:
        raise ValueError("standard 24T23282 order changed")
    block_actions = []
    for block in libgap.AllBlocks(target):
        if int(libgap.Length(block)) != 2:
            continue
        blocks = libgap.Orbit(target, block, libgap.OnSets)
        action = libgap.ActionHomomorphism(target, blocks, libgap.OnSets)
        image = libgap.Image(action)
        if (
            int(libgap.DegreeAction(image)) == 12
            and int(libgap.TransitiveIdentification(image)) == QUOTIENT_T
        ):
            block_actions.append(action)
    if len(block_actions) != 1:
        raise ValueError(
            f"24T23282 has {len(block_actions)} exact q242 block actions, "
            "expected one"
        )
    block_action = block_actions[0]
    if (
        int(libgap.Size(libgap.Kernel(block_action)))
        != EXPECTED_TARGET_BLOCK_KERNEL_ORDER
        or int(libgap.Size(libgap.Image(block_action))) != QUOTIENT_ORDER
    ):
        raise ValueError("24T23282 exact block structure changed")

    points_24 = libgap.eval("[1..24]")
    maximal_rows = []
    observed = Counter()
    for subgroup in libgap.MaximalSubgroupClassReps(target):
        if not libgap.IsTransitive(subgroup, points_24):
            continue
        image = libgap.Image(block_action, subgroup)
        restricted = libgap.RestrictedMapping(block_action, subgroup)
        row = {
            "blockKernelOrder": int(libgap.Size(libgap.Kernel(restricted))),
            "blockQuotientOrder": int(libgap.Size(image)),
            "blockQuotientTransitiveLabel": (
                f"12T{int(libgap.TransitiveIdentification(image))}"
            ),
            "label": (
                f"24T{int(libgap.TransitiveIdentification(subgroup))}"
            ),
            "order": int(libgap.Size(subgroup)),
        }
        observed[
            (
                row["label"],
                row["order"],
                row["blockKernelOrder"],
                row["blockQuotientTransitiveLabel"],
                row["blockQuotientOrder"],
            )
        ] += 1
        maximal_rows.append(row)
    if observed != EXPECTED_MAXIMAL_ROWS:
        raise ValueError(
            f"24T23282 maximal block images changed: {observed}, "
            f"expected {EXPECTED_MAXIMAL_ROWS}"
        )

    compatible_rows = [
        row
        for row in maximal_rows
        if (
            row["blockQuotientOrder"] == QUOTIENT_ORDER
            and row["blockQuotientTransitiveLabel"]
            == f"12T{QUOTIENT_T}"
        )
    ]
    if (
        len(compatible_rows) != 1
        or compatible_rows[0]["label"] != COMPATIBLE_MAXIMAL_LABEL
        or compatible_rows[0]["order"] != COMPATIBLE_MAXIMAL_ORDER
    ):
        raise ValueError(
            f"q242 compatible maximal class changed: {compatible_rows}"
        )

    shared = base.HELPER.SHARED
    unfiltered_profiles = shared.maximal_joint_profiles

    def quotient_compatible_profiles():
        if (
            int(shared.TARGET_T) != target_t
            or shared.TARGET_LABEL != TARGET_LABEL
        ):
            raise ValueError("q242 maximal filter used for the wrong target")
        profiles, identities = unfiltered_profiles()
        if len(profiles) != len(identities):
            raise ValueError("q242 maximal profile/identity length mismatch")
        identity_counter = Counter(
            (str(identity.get("label")), int(identity.get("order", -1)))
            for identity in identities
        )
        expected_identity_counter = Counter(
            {
                (label, order): count
                for (
                    label,
                    order,
                    _kernel_order,
                    _quotient_label,
                    _quotient_order,
                ),
                count in EXPECTED_MAXIMAL_ROWS.items()
            }
        )
        if identity_counter != expected_identity_counter:
            raise ValueError(
                f"q242 maximal profile identities changed: "
                f"{identity_counter}, expected {expected_identity_counter}"
            )
        retained = [
            (
                profile,
                {
                    **identity,
                    "quotientCompatibility": {
                        "blockQuotientOrder": QUOTIENT_ORDER,
                        "blockQuotientTransitiveLabel": (
                            f"12T{QUOTIENT_T}"
                        ),
                        "status": "compatible_with_exact_quotient",
                    },
                },
            )
            for profile, identity in zip(profiles, identities)
            if (
                identity.get("label") == COMPATIBLE_MAXIMAL_LABEL
                and int(identity.get("order", -1))
                == COMPATIBLE_MAXIMAL_ORDER
            )
        ]
        if len(retained) != 1:
            raise ValueError("q242 compatible maximal profile is not unique")
        return (
            [profile for profile, _identity in retained],
            [identity for _profile, identity in retained],
        )

    shared.maximal_joint_profiles = quotient_compatible_profiles
    return {
        "enabled": True,
        "exactQuotientOrder": QUOTIENT_ORDER,
        "exactQuotientTransitiveLabel": f"12T{QUOTIENT_T}",
        "maximalClassAudit": sorted(
            maximal_rows,
            key=lambda row: (
                row["blockQuotientOrder"],
                row["blockQuotientTransitiveLabel"],
                row["label"],
            ),
        ),
        "profileFilter": "exact_block_quotient_compatibility",
        "prunedMaximalClassCount": len(maximal_rows) - 1,
        "requiredWitnessMaximalClasses": compatible_rows,
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
        raise ValueError(
            "coset mask start/stride must be nonnegative/positive"
        )
    if args.pari_stack_bytes < 0 or args.pari_stack_max_bytes < 0:
        raise ValueError("PARI stack byte limits must be nonnegative")
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
        raise ValueError("selected q242 quotient is not irreducible degree 12")

    print(
        json.dumps(
            {
                "event": "q242_exact_start",
                "fieldCanonicalSha256": FIELD_SHA256,
                "rValues": list(TARGET_RS),
                "selectedCore": TARGET_CORE,
                "targetLabel": TARGET_LABEL,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    quotient_certificate = pinned_structural_quotient_certificate(source)
    character_certificate = exact_core5_character_certificate(
        quotient, quotient_certificate
    )
    maximal_certifier = configure_quotient_compatible_maximal_certifier(
        base, quotient_certificate
    )
    for pair in source["localPairGates"]:
        pair["broadLocalPossibleLabelsForCore"] = list(
            pair["possibleLabelsForCore"]
        )
        pair["possibleLabelsForCore"] = [TARGET_LABEL]
        pair["finalGroupDisambiguationRequired"] = False
        pair["characterDisambiguation"] = {
            "method": character_certificate["method"],
            "resolvedLabel": TARGET_LABEL,
            "status": character_certificate["status"],
        }

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
            f"q242 exact route set changed: {actual_routes}, "
            f"expected {expected_routes}"
        )
    for pair in exact_pairs:
        expected_certified_status = (
            f"certified_{TARGET_LABEL}_r{int(pair['r'])}"
        )
        for reconstruction in pair.get(
            "testedExactSelmerReconstructions", []
        ):
            status = str(reconstruction.get("candidateStatus", ""))
            if status.startswith("certified_") and (
                status != expected_certified_status
            ):
                raise ValueError(
                    f"q242 reconstruction certified an unexpected route: "
                    f"{status}, expected {expected_certified_status}"
                )

    final_live = strict_live_pairs()
    if final_live != live:
        raise ValueError("q242 strict live snapshot changed during exact run")

    source["characterDisambiguationCertificate"] = character_certificate
    source["exactSelmerSignGate"] = exact_gate
    source["quotientGaloisCertificate"] = quotient_certificate
    source["maximalSubgroupCertification"] = {
        **maximal_certifier,
        "status": "quotient_compatible_filter_installed",
    }
    routes = broad.certified_routes([source])
    certified_route_tuples = [
        (int(route["core"]), str(route["label"]), int(route["r"]))
        for route in routes
    ]
    if (
        len(certified_route_tuples) != len(set(certified_route_tuples))
        or any(route not in expected_routes for route in certified_route_tuples)
    ):
        raise ValueError(
            f"q242 certified-route output escaped the selected routes: "
            f"{certified_route_tuples}"
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
            "phase": "selected_exact_core5",
            "submissionCalls": 0,
            "targetSnapshotGeneratedAt": sorted(
                {
                    pair["generatedAt"]
                    for pair in final_live[TARGET_LABEL]
                    if pair["generatedAt"]
                }
            ),
            "witnessPrimeCount": int(args.witness_primes),
        },
        "fields": [source],
        "livePairs": final_live,
        "quotientT12": QUOTIENT_T,
        "selection": {
            "broadLocalPossibleLabelsForCore": list(
                ORIGINAL_POSSIBLE_LABELS
            ),
            "core": TARGET_CORE,
            "fieldCanonicalSha256": FIELD_SHA256,
            "resolvedLabel": TARGET_LABEL,
            "rValues": list(TARGET_RS),
        },
        "sourceCensusSummary": local_payload.get("sourceCensusSummary"),
        "summary": {
            "auditedFields": 1,
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
