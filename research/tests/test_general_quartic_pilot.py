import numpy as np

from routeA.build_general_quartic_pilot import (
    REGIMES,
    ROOT_TARGETS,
    SELECTED_BASES,
    all_cells,
    make_job,
    real_embeddings,
)
from routeA.general_quartic_analyzer import rank_mod_2


def test_gq96_cell_layout_and_balanced_first_batch():
    cells = all_cells()
    assert len(SELECTED_BASES) == 8
    assert len(cells) == 96
    first_batch = [cell for cell in cells if cell[1] == 0]
    assert len(first_batch) == 48
    assert {cell[0] for cell in first_batch} == {"6T7", "6T11", "6T6", "6T3"}
    assert {cell[4] for cell in cells} == set(REGIMES)
    assert {cell[5] for cell in cells} == set(ROOT_TARGETS)


def test_selected_bases_are_totally_real():
    for _, _, _, base in SELECTED_BASES:
        roots = real_embeddings(base)
        assert len(roots) == 6
        assert np.all(np.diff(roots) > 0)


def test_each_regime_builds_all_root_targets_for_one_base():
    base_group, cohort, source_index, base = SELECTED_BASES[0]
    index = 0
    for regime in REGIMES:
        for target_r in ROOT_TARGETS:
            job = make_job(
                index=index,
                base_group=base_group,
                cohort=cohort,
                source_index=source_index,
                base=base,
                regime=regime,
                target_r=target_r,
                variant=0,
                seed=240096,
            )
            assert job.metadata["numeric_prescreen_r"] == target_r
            assert "X" in job.quartic_expression
            if regime == "D4_non_even":
                assert len(job.squareclass_expressions) == 2
            index += 1


def test_binary_marker_rank():
    assert rank_mod_2([[0, 1], [1, 0]]) == 2
    assert rank_mod_2([[1, 1], [1, 1]]) == 1
    assert rank_mod_2([]) == 0

