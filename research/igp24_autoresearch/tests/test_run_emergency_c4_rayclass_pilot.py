import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "run_emergency_c4_rayclass_pilot.py"
SPEC = importlib.util.spec_from_file_location("emergency_c4_rayclass", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_gp_script_enforces_nonstable_conductor_and_cyclic_quotient():
    script = MODULE.build_gp_base_script(
        1,
        MODULE.BASES[0],
        prime_bound=250,
        component_limit=12,
        support_size=6,
        fields_per_base=2,
    )
    assert "selected<V[s[j]][6]" in script
    assert "subgrouplist(bnr,[4])" in script
    assert "vecmax(matsnf(L[j]))==4" in script
    assert "bnrconductor(bnr,H)" in script
    assert "bnrclassfield(bnr,H,2)" in script
    assert "submit" not in script.lower()


def test_parse_gp_output_reverses_coefficients_and_rejects_diagonal_lane():
    descending = [1] + [0] * 23 + [7]
    output = "\n".join([
        'BASE|1|58383808|6|24|2|2A_4(6) = [2^3]3 = 2 wr 3',
        'SEARCH|1|12|75|2|999|[1, 2, 3, 4, 5, 7]|[4, 4]|2',
        'SUPPORT|1|1|37|37|1|1|1|4',
        'SUPPORT|1|2|37|37|2|1|1|4',
        'SUPPORT|1|3|49|7|2|1|2|3',
        'SUPPORT|1|4|113|113|1|1|1|6',
        'SUPPORT|1|5|113|113|3|1|1|6',
        'SUPPORT|1|6|113|113|5|1|1|6',
        f'FIELD|1|1|[1, 0; 0, 4]|[4, 1]|999|10|12345|24|{descending}',
        f'FIELD|1|2|[4, 0; 0, 1]|[4, 1]|999|11|67890|24|{descending[:-1] + [11]}',
    ])
    base, rows = MODULE.parse_gp_base_output(
        output,
        expected_index=1,
        base_coefficients=MODULE.BASES[0],
    )
    assert base["nonRationalStableConductor"] is True
    assert base["partialRationalPrimeFibers"] == [7, 37, 113]
    assert len(rows) == 2
    assert rows[0]["coefficientsAscending"][0] == 7
    assert rows[0]["coefficientsAscending"][-1] == 1
    assert rows[0]["diagonalKernelOrderFourRejected"] is True


def test_parse_gap_atlas_and_exact_cycle_veto():
    identity = ",".join(["1"] * 24)
    twos = ",".join(["2"] * 12)
    output = "\n".join([
        "STRUCT|2025|4|1|C1||1",
        f"CYCLE|2025|1|{identity}",
        f"CYCLE|2025|3|{twos}",
    ])
    atlas = MODULE.parse_gap_target_output(output)
    atlas["24T2025"]["livePairs"] = [{"r": 24, "teamCount": 0, "targetKind": "gold"}]
    candidate = {"candidateHash": "abc"}
    gate = MODULE.assess_cycle_gate(candidate, [(5, (24,))], atlas)
    assert gate["cycleGate"] == "killed-by-exact-live-cycle-support"
    comparison = gate["liveTargetComparisons"][0]
    assert comparison["cycleSupportCompatible"] is False
    assert comparison["firstVetoWitness"] == {"prime": 5, "cycleType": "24"}


def test_frobenius_parser_maps_rows_to_candidates():
    candidates = [{"candidateHash": "a"}, {"candidateHash": "b"}]
    rows = MODULE.parse_frobenius_output(
        "FROB|1|5|[24]\nFROB|2|7|[12, 12]\n",
        candidates,
    )
    assert rows["a"] == [(5, (24,))]
    assert rows["b"] == [(7, (12, 12))]
