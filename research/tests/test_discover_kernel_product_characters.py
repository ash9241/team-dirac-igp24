from __future__ import annotations

from routeA.discover_kernel_product_characters import (
    _mask_xor,
    dependency_basis,
    enumerate_kernel_masks,
)


def test_dependency_basis_finds_unique_three_column_relation() -> None:
    columns = [0b001, 0b010, 0b011]
    basis = dependency_basis(columns)
    assert basis == [0b111]
    assert list(enumerate_kernel_masks(basis)) == [0b111]


def test_dependency_basis_spans_zero_and_repeated_columns() -> None:
    columns = [0b0, 0b101, 0b101]
    basis = dependency_basis(columns)
    relations = set(enumerate_kernel_masks(basis))
    assert relations == {0b001, 0b110, 0b111}
    assert all(_mask_xor(mask, columns) == 0 for mask in relations)


def test_kernel_enumerator_refuses_inexact_truncation() -> None:
    basis = [1, 2, 4, 8]
    try:
        list(enumerate_kernel_masks(basis, maximum_relations=8))
    except ValueError as error:
        assert "above cap" in str(error)
    else:
        raise AssertionError("expected an exact-enumeration cap error")
