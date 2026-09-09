import pytest

from routeA.build_general_quartic_expansion import (
    BASES,
    expansion_bases,
    generate_expansion,
)
from routeA.build_general_quartic_pilot import SELECTED_BASES


def test_expansion_bases_are_unused_and_complete() -> None:
    selected = {(group, index) for group, _, index, _ in SELECTED_BASES}
    expansion = expansion_bases()
    assert len(expansion) == sum(map(len, BASES.values())) - len(selected) == 24
    assert not selected & {(group, index) for group, index, _ in expansion}
    assert len({(group, index) for group, index, _ in expansion}) == len(expansion)


def test_custom_base_identity_must_be_unique() -> None:
    with pytest.raises(ValueError, match="must be unique"):
        generate_expansion(
            bases=[("6T16", 0, [1, 0, 0, 0, 0, 0, 1])] * 2,
            attempts_per_cell=1,
        )
