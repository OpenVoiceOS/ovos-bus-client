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
    def _send(message, frame=None):
        client.sent.append(frame if frame is not None else message.serialize())
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
    topics = [call.args[0] for call in client.emitter.emit.call_args_list]
    assert topics == ["message", ECHO_TOPIC], "local listeners fire at emit time, once"

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

    topic_copy = [item for item in delivered if isinstance(item, Message)][0]
    topic_copy.data["x"] = "mutated by a handler"
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
    client._send = lambda message, frame=None: False  # queue full / closing / socket gone
    message = Message(ECHO_TOPIC, {"x": 1}, {"source": "core"})
    client.emit(message)

    topics = [call.args[0] for call in client.emitter.emit.call_args_list]
    assert topics == ["message", ECHO_TOPIC], "local delivery still happens"
    assert len(client._local_echo_sent) == 0, "no fingerprint for a frame never sent"

    # the identical frame arriving from elsewhere must be delivered
    client.emitter.reset_mock()
    client.on_message(message.serialize())
    assert client.emitter.emit.called


def test_a_send_that_raises_also_releases_the_fingerprint():
    client = _client()

    def explode(message, frame=None):
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


# What the echo must deliver, and what a reconnect must forget ------------------
#
# Live run against a real messagebus (PR review at 0893ea7): a listed topic
# reached the emitting client's 'message' listener 0 of 201 times with the
# echo on (201 with it off), a MIGRATION_MAP counterpart 0 of 201, and after a
# write lost its echo to a reconnect an identical frame from another process
# arrived 0 times in 10 s.


def _bridged_client():
    """A client whose translator bridges ECHO_TOPIC to a legacy spelling."""
    client = _client()
    client._translator.counterpart_topics = (
        lambda topic: ["legacy.handler.complete"] if topic == ECHO_TOPIC else [])
    client._translator.translate_payload = (
        lambda from_topic, to_topic, data: dict(data, bridged=True))
    return client


def test_the_echo_reaches_the_firehose_and_the_counterpart_exactly_once():
    client = _bridged_client()
    client.emit(Message(ECHO_TOPIC, {"x": 1}, {"source": "core"}))
    names = [call.args[0] for call in client.emitter.emit.call_args_list]
    assert names == ["message", ECHO_TOPIC, "legacy.handler.complete"]
    firehose = client.emitter.emit.call_args_list[0].args[1]
    assert firehose == client.sent[0], "the firehose carries the frame that went to the wire"
    counterpart = client.emitter.emit.call_args_list[2].args[1]
    assert counterpart.msg_type == "legacy.handler.complete"
    assert counterpart.data == {"x": 1, "bridged": True}
    # the wire copy comes back and is dropped whole: nothing is delivered twice
    client.on_message(client.sent[0])
    assert client.emitter.emit.call_count == 3


def test_a_topic_not_opted_in_still_gets_its_firehose_from_the_wire():
    client = _bridged_client()
    client.emit(Message("something.else", {"x": 1}, {"source": "core"}))
    client.emitter.emit.assert_not_called()
    client.on_message(client.sent[0])
    names = [call.args[0] for call in client.emitter.emit.call_args_list]
    assert names[0] == "message" and "something.else" in names


def test_a_reconnect_forgets_the_echo_of_a_frame_the_old_connection_took():
    """The echo was lost with the socket; the fingerprint must not outlive it."""
    from ovos_bus_client.client.client import _local_echo_fingerprint

    client = _client()
    frame = Message(ECHO_TOPIC, {"x": 1}, {"source": "core"})
    client.emit(frame)
    client._mark_local_echo_written(_local_echo_fingerprint(frame), client._connection_generation)
    assert client._local_echo_sent[0][2] == client._connection_generation
    client._forget_local_echoes_of_the_previous_connection()  # the socket was replaced
    assert not client._local_echo_sent
    # the same frame from another process on the new connection is delivered
    client.on_message(client.sent[0])
    assert ECHO_TOPIC in [call.args[0] for call in client.emitter.emit.call_args_list[1:]]


def test_a_reconnect_keeps_the_echo_of_a_frame_still_waiting_to_be_written():
    """The async sender writes a queued frame on the new connection; its echo
    will come back there, and must still be recognised as our own."""
    client = _async_sender_client()
    client.emit(Message(ECHO_TOPIC, {"x": 1}, {"source": "core"}))
    assert client._local_echo_sent[0][2] is None, "queued, not written"
    client._forget_local_echoes_of_the_previous_connection()
    assert len(client._local_echo_sent) == 1, "an unwritten frame is not forgotten"
    # the drain thread writes it on the new connection and stamps it
    client._sender_queue.put(client._SENDER_STOP)
    client._drain_sender()
    assert client._local_echo_sent[0][2] == client._connection_generation
    wire = client.client.send.call_args.args[0]
    before = client.emitter.emit.call_count
    client.on_message(wire)
    assert client.emitter.emit.call_count == before, "our own echo, suppressed"


# The review at c1ed6ed --------------------------------------------------------


def test_a_write_that_finished_on_a_replaced_socket_releases_its_fingerprint():
    """run_forever() may swap the socket while a write is in flight. The
    write completes on the old one, whose echo can never arrive; stamped
    with the new generation, the entry would shadow an identical frame from
    another process for the TTL."""
    from ovos_bus_client.client.client import _local_echo_fingerprint

    client = _client()
    frame = Message(ECHO_TOPIC, {"x": 1}, {"source": "core"})
    client.emit(frame)
    captured = client._connection_generation  # read together with the socket, before the write
    client._forget_local_echoes_of_the_previous_connection()  # the socket was replaced meanwhile
    client._mark_local_echo_written(_local_echo_fingerprint(frame), captured)  # the old write returns
    assert not client._local_echo_sent, "released, not stamped with the new generation"
    client.emitter.reset_mock()
    client.on_message(client.sent[0])  # somebody else's identical frame, on the new connection
    assert ECHO_TOPIC in [call.args[0] for call in client.emitter.emit.call_args_list]


def test_the_sync_writer_binds_the_write_to_the_socket_it_used():
    from threading import Lock
    from unittest.mock import Mock

    client = _client()
    del client._send  # the real one
    client._client_lock = Lock()
    client.connected_event = Mock(is_set=lambda: True)
    old_socket, new_socket = Mock(), Mock()
    client.client = old_socket

    def send(msg):
        # the socket is replaced while this write is in flight
        client.client = new_socket
        client._forget_local_echoes_of_the_previous_connection()
    old_socket.send.side_effect = send
    client.emit(Message(ECHO_TOPIC, {"x": 1}, {"source": "core"}))
    old_socket.send.assert_called_once()
    new_socket.send.assert_not_called()
    assert not client._local_echo_sent, "written on the old socket: no echo to wait for"


def test_the_firehose_gets_the_transport_frame_on_an_encrypted_bus(monkeypatch):
    """on_message hands the firehose the frame as received. On an encrypted
    bus that is the envelope; the local echo must not hand it plaintext."""
    import warnings

    import pytest

    pytest.importorskip("Cryptodome")
    from ovos_bus_client.client import client as module

    monkeypatch.setattr(module, "_encryption_keys", lambda: ("0123456789abcdef", False))
    client = _client()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        client.emit(Message(ECHO_TOPIC, {"x": 1}, {"source": "core"}))
    wire = client.sent[0]
    assert "ciphertext" in json.loads(wire), "the wire frame is the envelope"
    firehose = client.emitter.emit.call_args_list[0]
    assert firehose.args == ("message", wire), "the same frame, not plaintext"
    delivered = client.emitter.emit.call_args_list[1].args[1]
    assert delivered.msg_type == ECHO_TOPIC and delivered.data == {"x": 1}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        client.on_message(wire)  # our echo, still recognised through the envelope
    assert client.emitter.emit.call_count == 2
