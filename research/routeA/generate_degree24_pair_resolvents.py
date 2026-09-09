#!/usr/bin/env python3
"""Generate exact degree-24 sibling fields from pair-sum resolvents."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence

from routeA.classify_degree24_pair_factors import (
    classify_factor_targets,
    joint_profile_confidence,
    load_profile,
    pari_joint_cycle_samples,
    real_root_sample,
)
from routeA.constructions.compositum_8x3 import DEFAULT_GP
from routeA.ledger import candidate_hash
from routeA.ordered_pair_resolvent import ordered_affine_resolvent
from routeA.pair_sum_resolvent import pair_sum_product_resolvent, pair_sum_resolvent
from routeA.subset_product_resolvent import triple_shift_product_resolvent


def parse_pari_factors(lines: Iterable[str]) -> list[dict[str, Any]]:
    factors: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.strip()
        if not line.startswith("FAC|"):
            continue
        parts = line.split("|", 5)
        if len(parts) != 6:
            raise ValueError(f"malformed PARI factor row: {line[:200]}")
        _, raw_degree, raw_power, raw_roots, raw_disc, raw_coefficients = parts
        coefficients = [int(value) for value in json.loads(raw_coefficients)]
        degree = int(raw_degree)
        if len(coefficients) != degree + 1:
            raise ValueError("PARI factor coefficient count does not match degree")
        factors.append({
            "degree": degree,
            "multiplicity": int(raw_power),
            "real_roots": int(raw_roots),
            "disc_abs": abs(int(raw_disc)),
            "coefficients": coefficients,
        })
    if not factors:
        raise ValueError("PARI returned no factor rows")
    return factors


def pari_factor_resolvent(
    coefficients: Sequence[int],
    *,
    gp: str | Path = DEFAULT_GP,
    timeout: float = 3_600,
) -> list[dict[str, Any]]:
    vector = "[" + ",".join(str(int(value)) for value in coefficients) + "]"
    script = f'''
default(parisizemax, 2000000000);
p=Polrev({vector});
F=factor(p);
for(i=1,matsize(F)[1],q=F[i,1];mm=F[i,2];d=poldegree(q);rr=if(d==24,polsturm(q),-1);dd=if(d==24,iferr(abs(nfdisc(q)),E,0),0);print("FAC|",d,"|",mm,"|",rr,"|",dd,"|",Vecrev(Vec(q))));
quit;
'''
    result = subprocess.run(
        [str(gp), "-q", "-f"],
        input=script,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return parse_pari_factors(result.stdout.splitlines())


def generate_pair_resolvent_candidates(
    source_catalog_path: str | Path,
    orbit_map_path: str | Path,
    output_path: str | Path,
    *,
    source_t: int,
    gp: str | Path = DEFAULT_GP,
    factor_timeout: float = 3_600,
    allow_self_map: bool = False,
    resolvent_kind: str = "pair-sum",
    product_weight: int = 1,
    shift_weight: int = 1,
    joint_profile_path: str | Path | None = None,
    classification_prime_count: int = 256,
    minimum_good_primes: int = 8,
    minimum_classification_confidence: float = 0.99,
    classification_timeout: float = 1_800,
) -> dict[str, Any]:
    sources = [
        row for row in _load_jsonl(source_catalog_path)
        if int(row["source_t"]) == int(source_t)
    ]
    if len(sources) != 1:
        raise ValueError(f"expected one verified source for 24T{source_t}, found {len(sources)}")
    maps = [
        row for row in _load_jsonl(orbit_map_path)
        if int(row["source_t"]) == int(source_t)
    ]
    if len(maps) != 1:
        raise ValueError(f"expected one pair-orbit map for 24T{source_t}, found {len(maps)}")
    source, orbit = sources[0], maps[0]
    target_t = orbit.get("unambiguous_target_t")
    ambiguous = target_t is None
    if ambiguous and resolvent_kind != "pair-sum-product":
        raise ValueError(
            f"24T{source_t} needs a pair-sum-product resolvent and joint profile"
        )
    if not ambiguous:
        target_t = int(target_t)
    if not ambiguous and target_t == int(source_t) and not allow_self_map:
        raise ValueError(f"24T{source_t} pair action is not a new transitive representation")

    confidence_threshold = float(minimum_classification_confidence)
    if not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("minimum_classification_confidence must lie in [0, 1]")
    if int(minimum_good_primes) < 1:
        raise ValueError("minimum_good_primes must be positive")
    if int(classification_prime_count) < int(minimum_good_primes):
        raise ValueError(
            "classification_prime_count cannot be smaller than minimum_good_primes"
        )

    source_coefficients = tuple(int(value) for value in source["coefficients"])
    if len(source_coefficients) != 25 or source_coefficients[-1] == 0:
        raise ValueError("verified source is not a degree-24 polynomial")
    if resolvent_kind == "pair-sum":
        resolvent = pair_sum_resolvent(source_coefficients)
        recipe_family = "degree24_pair_sum_resolvent"
        recipe_prefix = "pair24"
        action_name = "pair action"
    elif resolvent_kind == "pair-sum-product":
        resolvent = pair_sum_product_resolvent(
            source_coefficients,
            product_weight=int(product_weight),
        )
        recipe_family = "degree24_pair_sum_product_resolvent"
        recipe_prefix = f"pair24-w{int(product_weight)}"
        action_name = "generic unordered-pair action"
    elif resolvent_kind == "ordered-affine":
        resolvent = ordered_affine_resolvent(source_coefficients)
        recipe_family = "degree24_ordered_affine_resolvent"
        recipe_prefix = "ordered-affine24"
        action_name = "ordered-pair action"
    elif resolvent_kind == "triple-shift-product":
        resolvent = triple_shift_product_resolvent(
            source_coefficients,
            shift_weight=int(shift_weight),
        )
        recipe_family = "degree24_triple_shift_product_resolvent"
        recipe_prefix = f"triple-shift24-w{int(shift_weight)}"
        action_name = "unordered three-subset action"
    else:
        raise ValueError(
            "resolvent_kind must be pair-sum, pair-sum-product, ordered-affine, "
            "or triple-shift-product"
        )
    factors = pari_factor_resolvent(resolvent, gp=gp, timeout=factor_timeout)
    degree24 = [row for row in factors if int(row["degree"]) == 24]
    multiplicity = sum(int(row["multiplicity"]) for row in degree24)
    expected = int(orbit["orbit_count"])
    if multiplicity != expected:
        raise ValueError(
            f"degree-24 factor multiplicity {multiplicity} does not match "
            f"GAP orbit count {expected}"
        )

    classification: dict[str, Any] | None = None
    factor_targets: list[int]
    if ambiguous:
        if joint_profile_path is None:
            raise ValueError("ambiguous pair actions require a joint-profile file")
        if len(degree24) != expected or any(
            int(row["multiplicity"]) != 1 for row in degree24
        ):
            raise ValueError(
                "ambiguous pair classification requires distinct multiplicity-one "
                "degree-24 factors"
            )
        profile = load_profile(joint_profile_path, int(source_t))
        profile_targets = tuple(int(value) for value in profile["orbit_targets"])
        orbit_targets = tuple(int(value) for value in orbit["orbit_targets"])
        if profile_targets != orbit_targets:
            raise ValueError("joint profile targets do not match the GAP orbit map")
        modular_samples = pari_joint_cycle_samples(
            source_coefficients,
            [row["coefficients"] for row in degree24],
            gp=gp,
            prime_count=int(classification_prime_count),
            timeout=float(classification_timeout),
        )
        samples: list[dict[str, Any]] = [
            real_root_sample(
                int(source["source_r"]),
                [int(row["real_roots"]) for row in degree24],
            ),
            *modular_samples,
        ]
        resolved = classify_factor_targets(profile, samples)
        confidence = joint_profile_confidence(profile, len(modular_samples))
        good_prime_ok = len(modular_samples) >= int(minimum_good_primes)
        confidence_ok = (
            float(confidence["confidence_lower_bound"]) >= confidence_threshold
        )
        classification = {
            **resolved,
            **confidence,
            "classification_evidence": str(resolved["evidence"]),
            "confidence_evidence": str(confidence["evidence"]),
            "sampled_primes": [int(row["prime"]) for row in modular_samples],
            "minimum_good_primes": int(minimum_good_primes),
            "minimum_classification_confidence": confidence_threshold,
            "good_prime_threshold_passed": good_prime_ok,
            "confidence_threshold_passed": confidence_ok,
            "classification_passed": bool(resolved["resolved"])
            and good_prime_ok
            and confidence_ok,
        }
        factor_targets = (
            [int(value) for value in resolved["factor_targets"]]
            if classification["classification_passed"]
            else []
        )
    else:
        factor_targets = [int(target_t)] * len(degree24)

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[int, ...]] = set()
    for index, factor in enumerate(degree24):
        # An unresolved or under-sampled ambiguous task deliberately emits no
        # submission-ready rows while preserving a complete report artifact.
        if ambiguous and not factor_targets:
            break
        coefficients = tuple(int(value) for value in factor["coefficients"])
        if coefficients in seen:
            continue
        seen.add(coefficients)
        if int(factor["disc_abs"]) <= 1:
            raise ValueError("PARI could not certify a nonzero field discriminant")
        line = ",".join(str(value) for value in coefficients)
        candidates.append({
            "candidate_hash": candidate_hash(line),
            "coefficients": line,
            "target_t": factor_targets[index],
            "target_r": int(factor["real_roots"]),
            "local_root_count": int(factor["real_roots"]),
            "local_irreducible": True,
            "label_probability": 1.0,
            "valid_probability": 1.0,
            "submission_ready": True,
            "exact_compatibility_proven": True,
            "estimated_nfdisc_abs": int(factor["disc_abs"]),
            "recipe_family": recipe_family,
            "recipe_lineage": f"{recipe_prefix}:{source['candidate_hash']}",
            "construction_overgroup": f"exact {action_name} of 24T{source_t}",
            "parameters": {
                "source_t": int(source_t),
                "target_t": factor_targets[index],
                "source_candidate_hash": str(source["candidate_hash"]),
                "gap_orbit_count": expected,
                "resolvent_factor_index": index,
                "resolvent_factor_multiplicity": int(factor["multiplicity"]),
                **(
                    {
                        "product_weight": int(product_weight),
                        "good_prime_count": int(classification["good_prime_count"]),
                        "classification_confidence_lower_bound": float(
                            classification["confidence_lower_bound"]
                        ),
                        "joint_profile_evidence": str(
                            classification["classification_evidence"]
                        ),
                        "classification_confidence_evidence": str(
                            classification["confidence_evidence"]
                        ),
                    }
                    if classification is not None
                    else {}
                ),
                **(
                    {"shift_weight": int(shift_weight)}
                    if resolvent_kind == "triple-shift-product"
                    else {}
                ),
            },
        })
    candidates.sort(key=lambda row: (
        int(row["estimated_nfdisc_abs"]),
        int(row["target_r"]),
        str(row["candidate_hash"]),
    ))
    payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in candidates
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(output, payload)
    summary = {
        "source_t": int(source_t),
        "target_t": target_t,
        "orbit_targets": [
            int(value)
            for value in orbit.get(
                "orbit_targets",
                [] if ambiguous else [target_t] * expected,
            )
        ],
        "ambiguous_orbit_map": ambiguous,
        "self_map": (
            not ambiguous and int(target_t) == int(source_t)
        ),
        "resolvent_kind": resolvent_kind,
        "product_weight": int(product_weight) if resolvent_kind == "pair-sum-product" else None,
        "shift_weight": int(shift_weight) if resolvent_kind == "triple-shift-product" else None,
        "orbit_count": expected,
        "factor_degrees": [
            [int(row["degree"]), int(row["multiplicity"])] for row in factors
        ],
        "candidate_count": len(candidates),
        "classification": classification,
        "output": str(output),
        "output_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }
    _atomic_text(
        output.with_suffix(output.suffix + ".report.json"),
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
    )
    return summary


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _atomic_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_catalog", type=Path)
    parser.add_argument("orbit_map", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-t", type=int, required=True)
    parser.add_argument("--gp", type=Path, default=DEFAULT_GP)
    parser.add_argument("--factor-timeout", type=float, default=3_600)
    parser.add_argument("--allow-self-map", action="store_true")
    parser.add_argument(
        "--resolvent-kind",
        choices=(
            "pair-sum",
            "pair-sum-product",
            "ordered-affine",
            "triple-shift-product",
        ),
        default="pair-sum",
    )
    parser.add_argument("--product-weight", type=int, default=1)
    parser.add_argument("--shift-weight", type=int, default=1)
    parser.add_argument("--joint-profile", type=Path)
    parser.add_argument("--classification-prime-count", type=int, default=256)
    parser.add_argument("--minimum-good-primes", type=int, default=8)
    parser.add_argument(
        "--minimum-classification-confidence", type=float, default=0.99
    )
    parser.add_argument("--classification-timeout", type=float, default=1_800)
    args = parser.parse_args()
    summary = generate_pair_resolvent_candidates(
        args.source_catalog,
        args.orbit_map,
        args.output,
        source_t=args.source_t,
        gp=args.gp,
        factor_timeout=args.factor_timeout,
        allow_self_map=args.allow_self_map,
        resolvent_kind=args.resolvent_kind,
        product_weight=args.product_weight,
        shift_weight=args.shift_weight,
        joint_profile_path=args.joint_profile,
        classification_prime_count=args.classification_prime_count,
        minimum_good_primes=args.minimum_good_primes,
        minimum_classification_confidence=args.minimum_classification_confidence,
        classification_timeout=args.classification_timeout,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
