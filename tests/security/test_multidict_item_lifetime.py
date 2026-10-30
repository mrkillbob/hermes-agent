"""Item-set operations must not retain request-header keys or values."""

import gc
import sys

import pytest
from multidict import CIMultiDict, MultiDict


@pytest.mark.parametrize("mapping_type", [MultiDict, CIMultiDict])
@pytest.mark.parametrize("operation", ["reflected_union", "subtraction"])
def test_item_set_operations_release_operand_values(mapping_type, operation):
    value = object()
    mapping = mapping_type([("left", value)])
    operand = {("right", value)}
    expected = {("left", value)}
    if operation == "reflected_union":
        expected |= operand
    before = sys.getrefcount(value)

    for _ in range(20):
        if operation == "reflected_union":
            result = operand | mapping.items()
        else:
            result = mapping.items() - operand
        assert result == expected
        del result

    gc.collect()
    assert sys.getrefcount(value) == before
