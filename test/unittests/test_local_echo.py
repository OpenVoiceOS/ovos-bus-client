"""Opt-in same-process local echo (``websocket.local_echo_topics``).

The property that matters is not the speed-up, it is that the wire frame stays
byte-identical to an ordinary emit. An earlier version tagged it with a source
marker and dropped the tagged copy on the way back in. That works only while
every process runs a bus-client that knows the marker: an older one keeps it
(MSG-1 §2.3 ignores unknown context keys) and ``reply()``/``forward()``
preserve context unchanged (§5.1, §5.2), so the answer came back carrying the
originator's own marker and the originator discarded its own reply, silently.
"""
import json
from unittest.mock import Mock

from ovos_bus_client.client.client import MessageBusClient
from ovos_bus_client.message import Message

ECHO_TOPIC = "mycroft.skill.handler.complete"


def _client(topics=(ECHO_TOPIC,)):
    """A client with the wire and the event emitter stubbed out."""
    client = MessageBusClient.__new__(MessageBusClient)
    client.emitter = Mock()
    client._local_echo_topics = frozenset(topics)
    from collections import deque
    from threading import Lock
    from ovos_bus_client.client.client import LOCAL_ECHO_RING_SIZE
    client._local_echo_sent = deque(maxlen=LOCAL_ECHO_RING_SIZE)
    client._local_echo_lock = Lock()
    client.sent = []
    def _send(message):
        client.sent.append(message.serialize())
        return True  # _send reports whether the frame reached the wire
    client._send = _send
    client._send_legacy_intent_twin = lambda message: None
    client._send_legacy_namespace_twin = lambda message: None
    client._own_session = lambda: Mock(serialize=lambda: {"session_id": "test"})
    client._take_inbound_session = lambda message: None
    # on_message walks the namespace bridge; no counterparts in these tests.
    translator = Mock()
    translator.counterpart_topics = lambda topic: []
    client._translator = translator
    return client


def test_the_wire_frame_carries_no_echo_marker():
    """The whole point: an old receiver must see an ordinary frame."""
    client = _client()
    client.emit(Message(ECHO_TOPIC, {"x": 1}, {"source": "core"}))

    assert len(client.sent) == 1
    context = json.loads(client.sent[0])["context"]
    assert not [key for key in context if "echo" in key.lower()], context


def test_a_foreign_reply_is_not_mistaken_for_our_echo():
    """The cross-vintage defect, reproduced end to end.

    An old bus-client cannot pop a marker it does not know, and reply()
    preserves context -- so its answer arrives carrying whatever the wire
    frame carried. The originator must still deliver it.
    """
    client = _client()
    original = Message(ECHO_TOPIC, {"x": 1}, {"source": "core"})
    client.emit(original)
    client.emitter.reset_mock()

    # what an old process received, and what its reply() produces from it
    on_the_wire = Message.deserialize(client.sent[0])
    foreign_reply = on_the_wire.reply(f"{ECHO_TOPIC}.response", {"ok": True})

    client.on_message(foreign_reply.serialize())

    assert client.emitter.emit.called, "the originator dropped a valid reply"


def test_our_own_echo_is_suppressed_once():
    client = _client()
    message = Message(ECHO_TOPIC, {"x": 1}, {"source": "core"})
    client.emit(message)
    assert client.emitter.emit.call_count == 1, "local listeners fire at emit time"

    client.emitter.reset_mock()
    client.on_message(client.sent[0])
    assert not client.emitter.emit.called, "the echo must not double-deliver"


def test_an_identical_frame_from_elsewhere_is_delivered_after_the_echo():
    """The ring entry is consumed, so it shadows exactly one frame."""
    client = _client()
    message = Message(ECHO_TOPIC, {"x": 1}, {"source": "core"})
    client.emit(message)

    client.on_message(client.sent[0])  # our echo, suppressed
    client.emitter.reset_mock()
    client.on_message(client.sent[0])  # someone else's identical frame
    assert client.emitter.emit.called


def test_a_topic_not_opted_in_is_never_echoed():
    client = _client(topics=())
    client.emit(Message(ECHO_TOPIC, {"x": 1}))
    assert not client.emitter.emit.called
    assert len(client.sent) == 1


def test_the_wire_copy_is_isolated_from_local_handler_mutation():
    client = _client()
    delivered = []
    client.emitter.emit = lambda topic, message: delivered.append(message)

    message = Message(ECHO_TOPIC, {"x": 1})
    client.emit(message)

    delivered[0].data["x"] = "mutated by a handler"
    assert json.loads(client.sent[0])["data"]["x"] == 1


def test_an_instance_without_init_never_echoes():
    """Partial constructions must skip the feature, not raise."""
    bare = MessageBusClient.__new__(MessageBusClient)
    assert bare._local_echo_topics == frozenset()
    assert bare._local_echo_sent is None


def test_reply_preserves_unknown_context_keys():
    """Why the marker cannot ride the wire, stated as an executable fact.

    MSG-1 §2.3 has a consumer ignore unknown context keys, and §5.1/§5.2 have
    reply()/forward() preserve context unchanged. An older bus-client
    therefore hands a marker it does not understand straight back to the
    originator on the answer -- which is precisely how a source marker turns
    into silent message loss.
    """
    original = Message(ECHO_TOPIC, {"x": 1},
                       {"source": "core", "__some_unknown_marker": "ORIGINATOR"})

    assert original.reply(f"{ECHO_TOPIC}.response", {"ok": True}) \
        .context["__some_unknown_marker"] == "ORIGINATOR"
    assert original.forward("some.other.topic", {}) \
        .context["__some_unknown_marker"] == "ORIGINATOR"


def test_a_dropped_frame_leaves_no_fingerprint_behind():
    """A frame the sender rejected never reaches the wire, so nothing will
    come back for it -- and a lingering fingerprint would suppress the next
    identical frame from another process as if it were our echo."""
    client = _client()
    client._send = lambda message: False  # queue full / closing / socket gone
    message = Message(ECHO_TOPIC, {"x": 1}, {"source": "core"})
    client.emit(message)

    assert client.emitter.emit.call_count == 1, "local delivery still happens"
    assert len(client._local_echo_sent) == 0, "no fingerprint for a frame never sent"

    # the identical frame arriving from elsewhere must be delivered
    client.emitter.reset_mock()
    client.on_message(message.serialize())
    assert client.emitter.emit.called


def test_a_send_that_raises_also_releases_the_fingerprint():
    client = _client()

    def explode(message):
        raise ValueError("not connected")

    client._send = explode
    import pytest
    with pytest.raises(ValueError):
        client.emit(Message(ECHO_TOPIC, {"x": 1}))
    assert len(client._local_echo_sent) == 0



def _async_sender_client(send_raises=None):
    """A client whose `_send` is the real one, backed by the async sender queue."""
    import queue as _queue
    from unittest.mock import Mock

    client = _client()
    del client._send  # use the real MessageBusClient._send
    client._sender_queue = _queue.Queue()
    client._sender_closing = False
    client._sender_dropped = 0
    client._sender_dropped_logged_at = 0.0
    client.connected_event = Mock(is_set=lambda: True, wait=lambda *a, **k: True)
    client.client = Mock()
    if send_raises is not None:
        client.client.send.side_effect = send_raises
    return client


def test_an_async_sender_write_failure_releases_the_fingerprint():
    """The async sender accepts the frame and fails later, on its own thread.

    `_send` returns True because the frame was queued, so nothing on the emit
    path releases the fingerprint. Only the drain loop learns the write failed,
    and it never sees the Message -- so the fingerprint has to travel with the
    queued frame. Left behind it would suppress the next byte-identical frame
    from another process for the whole echo TTL.
    """
    client = _async_sender_client(ValueError("socket went away mid-write"))
    message = Message(ECHO_TOPIC, {"x": 1}, {"source": "core"})

    client.emit(message)
    assert len(client._local_echo_sent) == 1, "a queued frame is still tracked"

    client._sender_queue.put(client._SENDER_STOP)
    MessageBusClient._drain_sender(client)

    assert len(client._local_echo_sent) == 0, (
        "a frame that never reached the wire must not keep its fingerprint")

    client.emitter.reset_mock()
    client.on_message(message.serialize())
    assert client.emitter.emit.called, "the identical foreign frame is delivered"


def test_an_async_sender_that_succeeds_keeps_the_fingerprint():
    """The counterpart: a frame that did reach the wire still owes an echo,
    so its fingerprint must survive the drain."""
    client = _async_sender_client()
    client.emit(Message(ECHO_TOPIC, {"x": 1}, {"source": "core"}))

    client._sender_queue.put(client._SENDER_STOP)
    MessageBusClient._drain_sender(client)

    assert len(client._local_echo_sent) == 1
