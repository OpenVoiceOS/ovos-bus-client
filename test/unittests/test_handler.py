"""Tests for ovos_bus_client.handler (framework done-signal helper).

These verify the helper emits in *exactly* the shape ovos-core's
``intent_services.dispatcher`` consumes:

* topics ``<handler_info>.{start,complete,error}``;
* ``context["skill_id"]`` stamped for correlation;
* session/context preserved through ``forward``;
* exception text in ``data["exception"]`` on the error path;
* exceptions re-raised, never suppressed.
"""
import pytest

from ovos_utils.fakebus import FakeBus

from ovos_bus_client.message import Message
from ovos_bus_client.handler import (
    HandlerLifecycle,
    report_handler_lifecycle,
    DEFAULT_HANDLER_INFO,
)


def _recorder(bus):
    """Attach a catch-all recorder to a FakeBus; return the emitted list."""
    emitted = []
    bus.on("message", lambda m: emitted.append(
        m if isinstance(m, Message) else Message.deserialize(m)))
    return emitted


def _trigger():
    return Message("test:intent",
                   {"utterance": "hello"},
                   {"session": {"session_id": "sess-123"},
                    "source": "unit-test"})


def test_success_path_start_then_complete():
    bus = FakeBus()
    emitted = _recorder(bus)
    msg = _trigger()

    with HandlerLifecycle(bus, msg, skill_id="ovos-persona",
                          handler_name="ask_persona"):
        pass

    types = [m.msg_type for m in emitted]
    assert types == ["mycroft.skill.handler.start",
                     "mycroft.skill.handler.complete"]
    for m in emitted:
        assert m.data["handler"] == "ask_persona"
        assert m.context["skill_id"] == "ovos-persona"


def test_exception_path_start_then_error_and_reraise():
    bus = FakeBus()
    emitted = _recorder(bus)
    msg = _trigger()

    with pytest.raises(ValueError, match="boom"):
        with HandlerLifecycle(bus, msg, skill_id="ovos-stop",
                              handler_name="handle_stop"):
            raise ValueError("boom")

    types = [m.msg_type for m in emitted]
    assert types == ["mycroft.skill.handler.start",
                     "mycroft.skill.handler.error"]
    err = emitted[-1]
    # the dispatcher reads message.data["exception"] first
    assert "boom" in err.data["exception"]
    assert err.context["skill_id"] == "ovos-stop"


def test_skill_id_rides_in_context_and_session_preserved():
    bus = FakeBus()
    emitted = _recorder(bus)
    msg = _trigger()

    with HandlerLifecycle(bus, msg, skill_id="ovos-fallback",
                          handler_name="fallback_handler"):
        pass

    for m in emitted:
        # skill_id stamped for correlation
        assert m.context["skill_id"] == "ovos-fallback"
        # forward stamps the LIVE session for this id (refresh, not the bare
        # verbatim input) — the desync-guard: a derived event always carries
        # the current session for sess-123, not a pre-mutation snapshot
        assert m.context["session"]["session_id"] == "sess-123"
        # other context preserved too
        assert m.context["source"] == "unit-test"


def test_original_message_context_not_mutated():
    bus = FakeBus()
    _recorder(bus)
    msg = _trigger()

    assert "skill_id" not in msg.context
    with HandlerLifecycle(bus, msg, skill_id="ovos-persona"):
        pass
    # forward deep-copies context; caller's original is untouched
    assert "skill_id" not in msg.context


def test_custom_handler_info_base():
    bus = FakeBus()
    emitted = _recorder(bus)
    msg = _trigger()

    with HandlerLifecycle(bus, msg, skill_id="x",
                          handler_name="h",
                          handler_info="ovos.converse.handler"):
        pass

    types = [m.msg_type for m in emitted]
    assert types == ["ovos.converse.handler.start",
                     "ovos.converse.handler.complete"]


def test_empty_handler_info_disables_emission():
    bus = FakeBus()
    emitted = _recorder(bus)
    msg = _trigger()

    with HandlerLifecycle(bus, msg, skill_id="x", handler_info=""):
        pass

    assert emitted == []


def test_no_handler_name_omits_handler_field():
    bus = FakeBus()
    emitted = _recorder(bus)
    msg = _trigger()

    with HandlerLifecycle(bus, msg, skill_id="x"):
        pass

    for m in emitted:
        assert "handler" not in m.data


def test_default_handler_info_constant():
    assert DEFAULT_HANDLER_INFO == "mycroft.skill.handler"


def test_decorator_success_path():
    bus = FakeBus()
    emitted = _recorder(bus)

    @report_handler_lifecycle(bus, skill_id="ovos-common-query")
    def handle_query(message):
        return "answered"

    result = handle_query(_trigger())
    assert result == "answered"

    types = [m.msg_type for m in emitted]
    assert types == ["mycroft.skill.handler.start",
                     "mycroft.skill.handler.complete"]
    # handler_name defaults to the wrapped function name
    for m in emitted:
        assert m.data["handler"] == "handle_query"
        assert m.context["skill_id"] == "ovos-common-query"


def test_decorator_exception_path_reraises():
    bus = FakeBus()
    emitted = _recorder(bus)

    @report_handler_lifecycle(bus, skill_id="ovos-stop",
                              handler_name="handle_stop")
    def handle_stop(message):
        raise RuntimeError("nope")

    with pytest.raises(RuntimeError, match="nope"):
        handle_stop(_trigger())

    types = [m.msg_type for m in emitted]
    assert types == ["mycroft.skill.handler.start",
                     "mycroft.skill.handler.error"]
    assert "nope" in emitted[-1].data["exception"]


def test_caller_data_emitted_verbatim_on_start_and_complete():
    """A caller-supplied payload (e.g. workshop's {'name': ...}) is used as-is."""
    bus = FakeBus()
    emitted = _recorder(bus)
    msg = _trigger()

    base = {"name": "get_time", "extra": 1}
    with HandlerLifecycle(bus, msg, skill_id="ovos-date-time", data=base):
        pass

    types = [m.msg_type for m in emitted]
    assert types == ["mycroft.skill.handler.start",
                     "mycroft.skill.handler.complete"]
    for m in emitted:
        assert m.data == {"name": "get_time", "extra": 1}
        # default {"handler": ...} is NOT added when data is supplied
        assert "handler" not in m.data
        assert m.context["skill_id"] == "ovos-date-time"


def test_caller_data_error_merges_exception_without_mutating():
    """On error the exception merges into a COPY; caller's dict is untouched."""
    bus = FakeBus()
    emitted = _recorder(bus)
    msg = _trigger()

    base = {"name": "get_time"}
    with pytest.raises(ValueError, match="kaboom"):
        with HandlerLifecycle(bus, msg, skill_id="x", data=base):
            raise ValueError("kaboom")

    err = emitted[-1]
    assert err.msg_type == "mycroft.skill.handler.error"
    assert err.data["name"] == "get_time"
    assert "kaboom" in err.data["exception"]
    # the caller's original dict was NOT mutated
    assert base == {"name": "get_time"}


def test_caller_data_not_mutated_across_emissions():
    """The same base dict is reused for start+complete without accumulating."""
    bus = FakeBus()
    emitted = _recorder(bus)
    msg = _trigger()

    base = {"name": "h"}
    with HandlerLifecycle(bus, msg, skill_id="x", data=base):
        pass

    assert base == {"name": "h"}
    for m in emitted:
        assert m.data == {"name": "h"}


def test_data_none_keeps_default_handler_payload():
    """data=None falls back to the {'handler': handler_name} default."""
    bus = FakeBus()
    emitted = _recorder(bus)
    msg = _trigger()

    with HandlerLifecycle(bus, msg, skill_id="x", handler_name="h", data=None):
        pass

    for m in emitted:
        assert m.data == {"handler": "h"}


def test_decorator_caller_data_emitted_verbatim():
    bus = FakeBus()
    emitted = _recorder(bus)

    @report_handler_lifecycle(bus, skill_id="ovos-stop",
                              data={"name": "handle_stop"})
    def handle_stop(message):
        return "ok"

    assert handle_stop(_trigger()) == "ok"
    for m in emitted:
        assert m.data == {"name": "handle_stop"}


def test_works_without_real_bus_duck_typed_emit():
    """Any object exposing .emit works (no MessageBusClient required)."""
    class FakeEmitter:
        def __init__(self):
            self.messages = []

        def emit(self, message):
            self.messages.append(message)

    emitter = FakeEmitter()
    msg = _trigger()
    with HandlerLifecycle(emitter, msg, skill_id="x", handler_name="h"):
        pass

    assert [m.msg_type for m in emitter.messages] == [
        "mycroft.skill.handler.start", "mycroft.skill.handler.complete"]
