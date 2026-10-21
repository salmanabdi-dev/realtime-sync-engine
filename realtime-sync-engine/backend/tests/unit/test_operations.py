import pytest

from apps.sync.operations import InvalidOperation, apply_operation, apply_operations


def test_insert_in_middle():
    assert apply_operation("Hello world", {"kind": "insert", "pos": 5, "text": ","}) == "Hello, world"


def test_insert_at_end_and_start():
    assert apply_operation("abc", {"kind": "insert", "pos": 3, "text": "d"}) == "abcd"
    assert apply_operation("abc", {"kind": "insert", "pos": 0, "text": "_"}) == "_abc"


def test_delete_range():
    assert apply_operation("Hello world", {"kind": "delete", "pos": 5, "length": 6}) == "Hello"


def test_ops_apply_sequentially():
    ops = [
        {"kind": "delete", "pos": 0, "length": 5},
        {"kind": "insert", "pos": 0, "text": "Howdy"},
    ]
    assert apply_operations("Hello world", ops) == "Howdy world"


def test_positions_count_code_points_not_utf16_units():
    # "😀" is one code point (two UTF-16 units). Position 1 is right after it.
    assert apply_operation("😀b", {"kind": "insert", "pos": 1, "text": "a"}) == "😀ab"
    assert apply_operation("😀😀", {"kind": "delete", "pos": 1, "length": 1}) == "😀"


@pytest.mark.parametrize(
    "op",
    [
        {"kind": "insert", "pos": 4, "text": "x"},
        {"kind": "delete", "pos": 2, "length": 2},
        {"kind": "delete", "pos": 3, "length": 1},
    ],
)
def test_out_of_range_is_rejected(op):
    with pytest.raises(InvalidOperation):
        apply_operation("abc", op)


def test_max_document_size_enforced():
    with pytest.raises(InvalidOperation):
        apply_operations("abc", [{"kind": "insert", "pos": 3, "text": "def"}], max_chars=5)
