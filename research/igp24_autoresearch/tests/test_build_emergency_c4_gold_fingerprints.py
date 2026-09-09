import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "build_emergency_c4_gold_fingerprints.py"
SPEC = importlib.util.spec_from_file_location("emergency_c4_fingerprints", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_parse_gap_output_preserves_kernel_and_lift_profiles():
    output = "\n".join(
        [
            "GROUP|2025|768|G|128|D|2|12|2,3",
            "KERNEL|2025|32|C4 x C4 x C2|2,4,4|4|1|4,4,4,4,4,4|C4,C4,C4,C4,C4,C4|6,0,0=1;4,2,0=3;2,0,4=24",
            "LIFTS|2025|1,1,2,2/2|2=16;4=48",
        ]
    )
    row = MODULE.parse_gap_output(output)["24T2025"]
    assert row["groupOrder"] == 768
    assert row["abelianizationInvariants"] == [2, 3]
    assert row["blockKernel"]["abelianInvariants"] == [2, 4, 4]
    assert row["blockKernel"]["blockProjectionOrders"] == [4] * 6
    assert row["blockKernel"]["restrictionOrderProfile"][2] == {
        "identityBlockRestrictions": 2,
        "involutionBlockRestrictions": 0,
        "orderFourBlockRestrictions": 4,
        "kernelElements": 24,
    }
    assert row["quotientLiftOrderProfiles"]["1,1,2,2/2"] == {"2": 16, "4": 48}


def test_gap_script_requests_every_action_once():
    script = MODULE.build_gap_script([2025, 4254])
    assert "for t in [2025,4254]" in script
    assert "TransitiveGroup(24,t)" in script
    assert "network" not in script.lower()
