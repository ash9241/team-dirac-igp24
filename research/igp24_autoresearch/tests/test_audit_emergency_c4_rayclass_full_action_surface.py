import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "audit_emergency_c4_rayclass_full_action_surface.py"
)
SPEC = importlib.util.spec_from_file_location("emergency_c4_full_audit", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_gap_census_is_exhaustive_and_wreath_bounded():
    script = MODULE.build_gap_full_action_script()
    assert "[1..NrTransitiveGroups(24)]" in script
    assert "98304 mod sz<>0" in script
    assert "TransitiveIdentification(q)<>6" in script
    assert "TransitiveIdentification(fiber)=1" in script
    assert "ConjugacyClasses(g)" in script


def test_empirical_classifier_requires_small_distance_and_separation():
    rows = [
        {"label": "24T1", "cycleSupportCompatible": True, "empiricalTotalVariation": 0.01},
        {"label": "24T2", "cycleSupportCompatible": True, "empiricalTotalVariation": 0.05},
    ]
    result = MODULE.classify(rows)
    assert result["status"] == "unique-high-confidence-empirical-match"
    assert result["authoritativeExactLabel"] is None
    assert result["nearBest"] == [rows[0]]


def test_empirical_classifier_fails_closed_when_near_tied():
    rows = [
        {"label": "24T1", "cycleSupportCompatible": True, "empiricalTotalVariation": 0.01},
        {"label": "24T2", "cycleSupportCompatible": True, "empiricalTotalVariation": 0.02},
    ]
    result = MODULE.classify(rows)
    assert result["status"] == "empirically-ambiguous"
    assert [row["label"] for row in result["nearBest"]] == ["24T1", "24T2"]


def test_histogram_reconstruction_preserves_sample_size_and_cycles():
    candidate = {
        "frobeniusGate": {
            "cycleHistogram": {"24": 2, "12.12": 3}
        }
    }
    rows = MODULE.observations_from_histogram(candidate)
    assert len(rows) == 5
    assert sum(cycle == (24,) for _, cycle in rows) == 2
    assert sum(cycle == (12, 12) for _, cycle in rows) == 3
