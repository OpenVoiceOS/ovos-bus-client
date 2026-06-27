"""OVOS-CONTEXT-1 §5.3 — SessionManager owns the ``ovos.session.sync``
intent_context merge (set + null-delete), and ``intent_context`` round-trips
through Session serialization."""
import pytest

from ovos_bus_client.message import Message
from ovos_bus_client.session import Session, SessionManager


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


def test_handle_session_sync_merges_onto_managed_session():
    # a tracked session already holds some context
    seeded = Session("s1")
    seeded.intent_context = {"a": {"value": 1}, "b": {"value": 2}}
    SessionManager.update(seeded)

    # a producer emits ovos.session.sync carrying a snapshot in context.session
    snap = Session("s1")
    snap.intent_context = {"b": None, "c": {"value": 3}}  # delete b, add c
    msg = Message("ovos.session.sync", {}, {"session": snap.serialize()})

    SessionManager.handle_session_sync(msg)

    # the singleton's managed session reflects the entry-by-entry merge
    assert SessionManager.sessions["s1"].intent_context == {
        "a": {"value": 1}, "c": {"value": 3}}


def test_handle_session_sync_adopts_unseen_session():
    snap = Session("s2")
    snap.intent_context = {"k": {"value": 9}}
    msg = Message("ovos.session.sync", {}, {"session": snap.serialize()})

    SessionManager.handle_session_sync(msg)

    assert SessionManager.sessions["s2"].intent_context == {"k": {"value": 9}}


def test_handle_bare_sync_request_does_not_crash():
    # a bare request (no session carrier) just echoes; must not raise
    SessionManager.handle_session_sync(Message("ovos.session.sync"))
