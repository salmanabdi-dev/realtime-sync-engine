import uuid

import pytest

from apps.sync import protocol as p

DOC = str(uuid.uuid4())


def _mutation(**overrides):
    msg = {
        "type": "mutation",
        "document_id": DOC,
        "expected_version": 3,
        "mutation_id": str(uuid.uuid4()),
        "client_id": str(uuid.uuid4()),
        "ops": [{"kind": "insert", "pos": 0, "text": "x"}],
    }
    msg.update(overrides)
    return msg


def test_parses_valid_mutation():
    msg = p.parse_client_message(_mutation())
    assert isinstance(msg, p.MutationMessage)
    assert msg.expected_version == 3
    assert msg.document_id == uuid.UUID(DOC)
    assert msg.ops == [{"kind": "insert", "pos": 0, "text": "x"}]


def test_strips_unknown_op_fields():
    msg = p.parse_client_message(_mutation(ops=[{"kind": "delete", "pos": 1, "length": 2, "evil": 1}]))
    assert msg.ops == [{"kind": "delete", "pos": 1, "length": 2}]


@pytest.mark.parametrize(
    "overrides",
    [
        {"expected_version": True},
        {"expected_version": -1},
        {"expected_version": "3"},
        {"document_id": "not-a-uuid"},
        {"ops": []},
        {"ops": [{"kind": "insert", "pos": 0, "text": ""}]},
        {"ops": [{"kind": "delete", "pos": 0, "length": 0}]},
        {"ops": [{"kind": "replace", "pos": 0}]},
        {"ops": [{"kind": "insert", "pos": 0, "text": "\ud800"}]},
        {"ops": [{"kind": "insert", "pos": 0, "text": "a\x00b"}]},
        {"ops": [{"kind": "insert", "pos": 0, "text": "x"}] * (p.MAX_OPS_PER_MUTATION + 1)},
    ],
)
def test_rejects_invalid_mutations(overrides):
    data = _mutation(**overrides)
    with pytest.raises(p.ProtocolError) as exc:
        p.parse_client_message(data)
    assert exc.value.mutation_id == data["mutation_id"]


def test_resync_and_ping():
    assert p.parse_client_message({"type": "resync", "since_version": 7}) == p.ResyncMessage(7)
    assert p.parse_client_message({"type": "ping", "ts": 12.5}) == p.PingMessage(12.5)


def test_unknown_type():
    with pytest.raises(p.ProtocolError) as exc:
        p.parse_client_message({"type": "launch_missiles"})
    assert exc.value.code == "unknown_type"
