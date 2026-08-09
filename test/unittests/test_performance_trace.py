"""Request-correlated trace coverage for the shared bus emission boundary."""

from unittest.mock import MagicMock

import pytest

from ovos_bus_client.client import MessageBusClient
from ovos_bus_client.message import Message
from ovos_bus_client.performance import (
    message_request_id,
    trace_performance_stage,
)


def test_message_request_id_finds_nested_metadata():
    message = Message(
        "speak",
        {},
        {"metadata": {"qa_query_id": "request-nested"}},
    )

    assert message_request_id(message) == "request-nested"


@pytest.mark.parametrize("message_type", ("speak", "ovos.utterance.speak"))
def test_speech_emit_traces_before_single_wire_send(monkeypatch, message_type):
    monkeypatch.setenv("OVOS_PERFORMANCE_TRACE", "true")
    monkeypatch.setattr(
        "ovos_bus_client.performance.time.time_ns",
        lambda: 456_000_000,
    )
    emitted = []
    monkeypatch.setattr(
        "ovos_bus_client.performance._LOG.info",
        lambda template, payload: emitted.append(template % payload),
    )
    client = MessageBusClient()
    client._send = MagicMock(
        side_effect=lambda _message: emitted.append("wire_send")
    )

    client.emit(Message(
        message_type,
        {"utterance": "Hello"},
        {"query_id": "request-speech"},
    ))

    assert len(emitted) == 2
    assert '"stage":"skill_reply_emit"' in emitted[0]
    assert '"request_id":"request-speech"' in emitted[0]
    assert '"at_unix_ns":456000000' in emitted[0]
    assert emitted[1] == "wire_send"
    client._send.assert_called_once()


def test_non_speech_emit_does_not_trace(monkeypatch):
    monkeypatch.setenv("OVOS_PERFORMANCE_TRACE", "true")
    log_info = MagicMock()
    monkeypatch.setattr("ovos_bus_client.performance._LOG.info", log_info)
    client = MessageBusClient()
    client._send = MagicMock()

    client.emit(Message(
        "ovos.utterance.handled",
        {},
        {"query_id": "request-handled"},
    ))

    log_info.assert_not_called()
    client._send.assert_called_once()


def test_disabled_trace_does_not_extract_request_id(monkeypatch):
    monkeypatch.delenv("OVOS_PERFORMANCE_TRACE", raising=False)
    monkeypatch.setattr(
        "ovos_bus_client.performance.message_request_id",
        lambda _message: pytest.fail("disabled trace extracted request ID"),
    )

    trace_performance_stage("skill_reply_emit", message=Message("speak"))
