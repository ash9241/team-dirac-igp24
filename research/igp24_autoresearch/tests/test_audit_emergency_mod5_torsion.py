import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "audit_emergency_mod5_torsion.py"
SPEC = importlib.util.spec_from_file_location("emergency_mod5", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_gap_atlas_rejects_fixed_points_on_explicit_domain():
    script = MODULE.build_gap_atlas_script()
    assert "Length(Orbits(action,[1..24]))<>1" in script
    assert "Length(determinants)<>4" in script


def test_parse_gap_atlas():
    parsed = MODULE.parse_gap_atlas(
        "ACTION|31|48|C24 : C2|6\n"
        "CYCLE|31|12|24\n"
        "COMPLETE|48|1\n"
    )
    assert parsed["subgroupConjugacyClasses"] == 48
    assert parsed["actions"]["24T31"]["cycleClassSizes"] == {"24": 12}


def test_full_exclusive_cycle_certifies_full_image():
    atlas = {
        "24T31": {"groupOrder": 48, "cycleClassSizes": {"24": 1}},
        "24T1353": {
            "groupOrder": 480,
            "cycleClassSizes": {"24": 1, "1.1.1.1.5.5.5.5": 24},
        },
    }
    candidate = {
        "curveJInvariant": 1,
        "frobeniusObservations": [(7, (1, 1, 1, 1, 5, 5, 5, 5))],
    }
    result = MODULE.classify_candidate(candidate, atlas)
    assert result["exactLabel"] == "24T1353"
    assert result["proof"]["witness"]["prime"] == 7


def test_scoring_gate_enforces_baseline_unlock():
    candidate = {"fieldDiscriminantAbs": "201"}
    state = {
        "owned": False,
        "baselineBestNfdiscAbs": "200",
        "discovered": False,
        "minimumDiscriminantAbs": None,
    }
    gate = MODULE.scoring_gate(candidate, state)
    assert gate["potentiallyScoreable"] is False
    assert gate["ratioToThreshold"] == 1.005
