from types import SimpleNamespace

import pytest

from routeA.classify_degree24_pair_factors import (
    classify_factor_targets,
    involution_cycle,
    joint_profile_confidence,
    parse_pari_joint_samples,
    pari_joint_cycle_samples,
    real_root_sample,
)


PROFILE = {
    "source_t": 164,
    "orbit_count": 2,
    "orbit_targets": [163, 164],
    "profiles": [
        {
            "source_cycle": [24],
            "orbit_cycles": [[12, 12], [8, 8, 8]],
            "class_size": 4,
        },
        {
            "source_cycle": [12, 12],
            "orbit_cycles": [[6, 6, 6, 6], [4, 4, 4, 4, 4, 4]],
            "class_size": 8,
        },
    ],
}


def test_exact_joint_sample_resolves_factor_targets() -> None:
    result = classify_factor_targets(PROFILE, [{
        "source_cycle": [12, 12],
        "factor_cycles": [[6, 6, 6, 6], [4, 4, 4, 4, 4, 4]],
    }])
    assert result["resolved"] is True
    assert result["factor_targets"] == [163, 164]
    assert result["surviving_orbit_permutations"] == 1


def test_incompatible_observation_never_falls_back_to_a_guess() -> None:
    result = classify_factor_targets(PROFILE, [{
        "source_cycle": [12, 12],
        "factor_cycles": [[8, 8, 8], [8, 8, 8]],
    }])
    assert result["resolved"] is False
    assert result["target_assignments"] == []
    assert result["factor_targets"] is None


def test_same_target_orbits_do_not_create_false_label_ambiguity() -> None:
    profile = {
        "orbit_count": 3,
        "orbit_targets": [10, 20, 20],
        "profiles": [{
            "source_cycle": [24],
            "orbit_cycles": [[24], [12, 12], [12, 12]],
        }],
    }
    result = classify_factor_targets(profile, [{
        "source_cycle": [24],
        "factor_cycles": [[24], [12, 12], [12, 12]],
    }])
    assert result["resolved"] is True
    assert result["factor_targets"] == [10, 20, 20]
    assert result["surviving_orbit_permutations"] == 2


def test_real_place_is_encoded_as_complex_conjugation_cycle_type() -> None:
    sample = real_root_sample(8, [0, 16])
    assert sample["source_cycle"] == involution_cycle(8)
    assert len(sample["source_cycle"]) == 16
    assert sample["factor_cycles"][0] == (2,) * 12
    with pytest.raises(ValueError, match="even"):
        involution_cycle(7)


def test_parse_pari_joint_samples_reads_prime_aligned_patterns() -> None:
    rows = parse_pari_joint_samples([
        "noise",
        "JFP|101|[[1, 1, 2, 20], [24], [12, 12]]",
    ])
    assert rows == [{
        "prime": 101,
        "source_cycle": (1, 1, 2, 20),
        "factor_cycles": [(24,), (12, 12)],
    }]


def test_pari_joint_sampler_keeps_nested_loop_on_one_gp_input_line(monkeypatch) -> None:
    captured = {}

    def fake_run(*args, **kwargs):
        captured["script"] = kwargs["input"]
        return SimpleNamespace(
            returncode=0,
            stdout="JFP|101|[[24],[12,12]]\n",
            stderr="",
        )

    monkeypatch.setattr(
        "routeA.classify_degree24_pair_factors.subprocess.run", fake_run
    )
    polynomial = [1] + [0] * 23 + [1]
    samples = pari_joint_cycle_samples(polynomial, [polynomial], prime_count=1)
    loop_lines = [
        line for line in captured["script"].splitlines()
        if line.startswith("for(j=1,#Q,")
    ]
    assert len(loop_lines) == 1
    assert "for(i=1,#P," in loop_lines[0]
    assert samples[0]["prime"] == 101


def test_joint_profile_confidence_uses_exact_class_mass_and_prime_count() -> None:
    profile = {
        "group_order": 4,
        "orbit_count": 2,
        "orbit_targets": [10, 20],
        "profiles": [
            {
                "class_size": 3,
                "source_cycle": [24],
                "orbit_cycles": [[12, 12], [12, 12]],
            },
            {
                "class_size": 1,
                "source_cycle": [12, 12],
                "orbit_cycles": [[24], [8, 8, 8]],
            },
        ],
    }
    one = joint_profile_confidence(profile, 1)
    eight = joint_profile_confidence(profile, 8)
    assert one["maximum_one_prime_confusion"] == 0.75
    assert one["minimum_one_prime_separation"] == 0.25
    assert one["confidence_lower_bound"] == 0.25
    assert eight["confidence_lower_bound"] == pytest.approx(1.0 - 0.75**8)


def test_joint_profile_confidence_rejects_target_swapping_symmetry() -> None:
    profile = {
        "group_order": 1,
        "orbit_count": 2,
        "orbit_targets": [10, 20],
        "profiles": [{
            "class_size": 1,
            "source_cycle": [24],
            "orbit_cycles": [[12, 12], [12, 12]],
        }],
    }
    result = joint_profile_confidence(profile, 256)
    assert result["minimum_one_prime_separation"] == 0.0
    assert result["confidence_lower_bound"] == 0.0
