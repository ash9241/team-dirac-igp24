import json

import pytest

from routeA.generate_degree24_pair_resolvents import (
    generate_pair_resolvent_candidates,
    parse_pari_factors,
)


def test_parse_pari_factors_reads_ascending_coefficients() -> None:
    rows = parse_pari_factors([
        "noise",
        "FAC|2|3|2|5|[1,0,1]",
    ])
    assert rows == [{
        "degree": 2,
        "multiplicity": 3,
        "real_roots": 2,
        "disc_abs": 5,
        "coefficients": [1, 0, 1],
    }]


def test_parse_pari_factors_rejects_wrong_coefficient_count() -> None:
    with pytest.raises(ValueError, match="coefficient count"):
        parse_pari_factors(["FAC|2|1|0|5|[1,1]"])


def test_generator_requires_explicit_opt_in_for_self_map(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.jsonl"
    orbit = tmp_path / "orbit.jsonl"
    output = tmp_path / "candidates.jsonl"
    source.write_text(json.dumps({
        "source_t": 3,
        "coefficients": [1] + [0] * 23 + [1],
        "candidate_hash": "source-hash",
    }) + "\n")
    orbit.write_text(json.dumps({
        "source_t": 3,
        "unambiguous_target_t": 3,
        "orbit_count": 1,
    }) + "\n")
    with pytest.raises(ValueError, match="not a new transitive representation"):
        generate_pair_resolvent_candidates(source, orbit, output, source_t=3)

    monkeypatch.setattr(
        "routeA.generate_degree24_pair_resolvents.pair_sum_resolvent",
        lambda coefficients: (1, 0, 1),
    )
    monkeypatch.setattr(
        "routeA.generate_degree24_pair_resolvents.pari_factor_resolvent",
        lambda *args, **kwargs: [{
            "degree": 24,
            "multiplicity": 1,
            "real_roots": 4,
            "disc_abs": 101,
            "coefficients": [1] + [0] * 23 + [1],
        }],
    )
    summary = generate_pair_resolvent_candidates(
        source,
        orbit,
        output,
        source_t=3,
        allow_self_map=True,
    )
    assert summary["self_map"] is True
    candidate = json.loads(output.read_text())
    assert candidate["target_t"] == 3
    assert candidate["target_r"] == 4


def _write_ambiguous_inputs(tmp_path):
    source = tmp_path / "source.jsonl"
    orbit = tmp_path / "orbit.jsonl"
    profile = tmp_path / "profile.jsonl"
    output = tmp_path / "candidates.jsonl"
    source.write_text(json.dumps({
        "source_t": 3,
        "source_r": 0,
        "coefficients": [1] + [0] * 23 + [1],
        "candidate_hash": "source-hash",
    }) + "\n")
    orbit.write_text(json.dumps({
        "source_t": 3,
        "unambiguous_target_t": None,
        "orbit_targets": [10, 20],
        "orbit_count": 2,
    }) + "\n")
    profile.write_text(json.dumps({
        "source_t": 3,
        "group_order": 2,
        "orbit_count": 2,
        "orbit_targets": [10, 20],
        "profiles": [
            {
                "class_size": 1,
                "source_cycle": [2] * 12,
                "orbit_cycles": [[2] * 12, [2] * 12],
            },
            {
                "class_size": 1,
                "source_cycle": [24],
                "orbit_cycles": [[24], [12, 12]],
            },
        ],
    }) + "\n")
    return source, orbit, profile, output


def _patch_ambiguous_pari(monkeypatch, *, prime_count):
    first = [1] + [0] * 23 + [1]
    second = [2] + [0] * 23 + [1]
    monkeypatch.setattr(
        "routeA.generate_degree24_pair_resolvents.pair_sum_product_resolvent",
        lambda *args, **kwargs: (1, 0, 1),
    )
    monkeypatch.setattr(
        "routeA.generate_degree24_pair_resolvents.pari_factor_resolvent",
        lambda *args, **kwargs: [
            {
                "degree": 24,
                "multiplicity": 1,
                "real_roots": 0,
                "disc_abs": 101,
                "coefficients": first,
            },
            {
                "degree": 24,
                "multiplicity": 1,
                "real_roots": 0,
                "disc_abs": 103,
                "coefficients": second,
            },
        ],
    )
    monkeypatch.setattr(
        "routeA.generate_degree24_pair_resolvents.pari_joint_cycle_samples",
        lambda *args, **kwargs: [
            {
                "prime": 101 + index,
                "source_cycle": (24,),
                "factor_cycles": [(24,), (12, 12)],
            }
            for index in range(prime_count)
        ],
    )


def test_ambiguous_generator_classifies_factors_and_records_proof(
    tmp_path, monkeypatch
) -> None:
    source, orbit, profile, output = _write_ambiguous_inputs(tmp_path)
    _patch_ambiguous_pari(monkeypatch, prime_count=8)
    summary = generate_pair_resolvent_candidates(
        source,
        orbit,
        output,
        source_t=3,
        resolvent_kind="pair-sum-product",
        joint_profile_path=profile,
        classification_prime_count=8,
        minimum_good_primes=8,
        minimum_classification_confidence=0.99,
    )
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert summary["ambiguous_orbit_map"] is True
    assert summary["classification"]["classification_passed"] is True
    assert [row["target_t"] for row in rows] == [10, 20]
    assert all(row["exact_compatibility_proven"] for row in rows)
    assert all(row["recipe_family"].endswith("sum_product_resolvent") for row in rows)


def test_ambiguous_generator_fails_closed_below_confidence_threshold(
    tmp_path, monkeypatch
) -> None:
    source, orbit, profile, output = _write_ambiguous_inputs(tmp_path)
    _patch_ambiguous_pari(monkeypatch, prime_count=1)
    summary = generate_pair_resolvent_candidates(
        source,
        orbit,
        output,
        source_t=3,
        resolvent_kind="pair-sum-product",
        joint_profile_path=profile,
        classification_prime_count=1,
        minimum_good_primes=1,
        minimum_classification_confidence=0.99,
    )
    assert output.read_text() == ""
    assert summary["candidate_count"] == 0
    assert summary["classification"]["resolved"] is True
    assert summary["classification"]["confidence_threshold_passed"] is False
    assert summary["classification"]["classification_passed"] is False
