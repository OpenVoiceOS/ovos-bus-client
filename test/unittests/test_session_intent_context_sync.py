"""OVOS-CONTEXT-1 §5.3 — SessionManager owns the ``ovos.session.sync``
intent_context merge (set + null-delete), and ``intent_context`` round-trips
through Session serialization."""
from types import SimpleNamespace

import pytest

from ovos_bus_client.message import Message
from ovos_bus_client.session import (Session, SessionManager,
                                     HAS_FOLD_INBOUND)


@pytest.fixture(autouse=True)
def _reset_sessions():
    # isolate the singleton between tests
    saved = dict(SessionManager.sessions)
    saved_default = SessionManager.default_session
    SessionManager.sessions = {"default": Session("default")}
    SessionManager.default_session = SessionManager.sessions["default"]
    SessionManager.bus = None  # cls.sync() no-ops without a bus
    yield
    SessionManager.sessions = saved
    SessionManager.default_session = saved_default


def test_intent_context_roundtrips():
    s = Session("s1")
    s.intent_context = {"color": {"value": "red", "turns_remaining": 2}}
    rt = Session.deserialize(s.serialize())
    assert rt.intent_context == {"color": {"value": "red", "turns_remaining": 2}}


def test_merge_set_delete_disjoint():
    # set c, delete b, leave a untouched
    merged = SessionManager.merge_intent_context(
        {"a": {"value": 1}, "b": {"value": 2}},
        {"b": None, "c": {"value": 3}})
    assert merged == {"a": {"value": 1}, "c": {"value": 3}}


def test_merge_ignores_malformed_entry():
    assert SessionManager.merge_intent_context({}, {"x": "notadict"}) == {}


def test_merge_empty_payload_is_noop():
    target = {"a": {"value": 1}}
    assert SessionManager.merge_intent_context(target, None) == {"a": {"value": 1}}


def test_handle_session_sync_merges_onto_the_session_this_process_holds():
    # the session this client owns already holds some context
    held = Session("s1")
    held.intent_context = {"a": {"value": 1}, "b": {"value": 2}}
    SessionManager.bus = SimpleNamespace(session=held)
    try:
        # a producer emits ovos.session.sync carrying a snapshot in context.session
        snap = Session("s1")
        snap.intent_context = {"b": None, "c": {"value": 3}}  # delete b, add c
        SessionManager.handle_session_sync(
            Message("ovos.session.sync", {}, {"session": snap.serialize()}))
        # the held session reflects the entry-by-entry merge
        assert held.intent_context == {"a": {"value": 1}, "c": {"value": 3}}
    finally:
        SessionManager.bus = None


def test_handle_session_sync_ignores_another_clients_session():
    # OVOS-SESSION-2 §2.2/§2.5: a named session this process does not hold is
    # another client's state, and adopting it would be durable cross-utterance
    # state the orchestrator is not allowed to keep
    held = Session("s1")
    SessionManager.bus = SimpleNamespace(session=held)
    try:
        snap = Session("s2")
        snap.intent_context = {"k": {"value": 9}}
        SessionManager.handle_session_sync(
            Message("ovos.session.sync", {}, {"session": snap.serialize()}))
        assert held.intent_context == {}
        if HAS_FOLD_INBOUND:
            assert "s2" not in SessionManager.sessions
    finally:
        SessionManager.bus = None


def test_handle_bare_sync_request_does_not_crash():
    # a bare request (no session carrier) just echoes; must not raise
    SessionManager.handle_session_sync(Message("ovos.session.sync"))
