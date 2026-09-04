"""OVOS-SESSION-2 §5.1 — the arrival fold is orchestrator-intake-only.

The §5.1 merge happens exactly once per utterance, at the process that owns
the default-session store on intake (see core#915). A bus-client instance is
a consumer -- a listener, a satellite, a skill container, or the orchestrator
itself reading its own bus -- and observes far more default-session traffic
than one fold per utterance (replies, handled-acks, forwarded frames all
carry a session). Folding every observed message would re-merge stale field
values into the live store on each one and silently clobber whatever the
orchestrator's own intake fold just wrote (§2.6: mutation only at lifecycle
boundaries, not on every observation). So ``on_message`` never folds the
default session; only an explicit ``SessionManager.fold_inbound`` call, made
by whoever owns intake, does.
"""
import unittest
from unittest.mock import MagicMock

from ovos_bus_client.client.client import MessageBusClient
from ovos_bus_client.message import Message
from ovos_bus_client.session import SessionManager, HAS_FOLD_INBOUND


def _inbound(carrier):
    return Message("recognizer_loop:utterance", {"utterances": ["hello"]},
                   {"session": carrier}).serialize()


class TestObservedMessagesNeverFoldTheDefaultStore(unittest.TestCase):
    """The client only *resolves* sessions for handlers; it never folds."""

    def setUp(self):
        SessionManager.reset_default_session()
        self.bus = MessageBusClient()
        self.bus.client = MagicMock()
        self.bus.connected_event.set()

    def tearDown(self):
        SessionManager.reset_default_session()

    def test_observed_default_session_traffic_leaves_the_store_alone(self):
        stored = SessionManager.get_default_session()
        stored.lang = "pt-PT"
        stored.site_id = "kitchen"

        # A message an orchestrator-side skill/handler emitted (e.g. the
        # ovos.utterance.handled ack) can legitimately carry a STALE default
        # carrier from earlier in the pipeline. Observing it must not wipe
        # fields the live store has moved on from.
        self.bus.on_message(_inbound({"session_id": "default"}))
        self.assertIs(SessionManager.get_default_session(), stored)
        self.assertEqual(stored.lang, "pt-PT")
        self.assertEqual(stored.site_id, "kitchen")

        # nor does a fully-populated observed carrier overwrite the store --
        # that fold belongs to the orchestrator's intake alone.
        self.bus.on_message(_inbound({"session_id": "default",
                                      "site_id": "bedroom"}))
        self.assertEqual(stored.site_id, "kitchen")

    def test_absent_carrier_still_dispatches_without_folding_the_store(self):
        # An absent carrier IS the default session (SESSION-1 §2.1/§3.1), so
        # it must still dispatch normally -- but observing it is not intake,
        # so it must not touch the store either.
        stored = SessionManager.get_default_session()
        stored.site_id = "kitchen"
        raw = Message("recognizer_loop:utterance",
                      {"utterances": ["hello"]}).serialize()
        self.bus.on_message(raw)
        self.assertIs(SessionManager.get_default_session(), stored)
        self.assertEqual(stored.site_id, "kitchen")

    def test_named_session_arrival_leaves_the_default_store_alone(self):
        # a named-session arrival still goes through `update` (#313), which
        # is a §2.2 no-op for a non-default id on this spec-tools release --
        # what matters here is that observing it never touches the default
        # store, the same as any other observed message.
        stored = SessionManager.get_default_session()
        stored.site_id = "kitchen"
        self.bus.on_message(_inbound({"session_id": "kitchen-sat",
                                      "site_id": "bedroom"}))
        self.assertIs(SessionManager.get_default_session(), stored)
        self.assertEqual(stored.site_id, "kitchen")


@unittest.skipUnless(HAS_FOLD_INBOUND,
                     "spec-tools registry has no fold_inbound arrival point")
class TestExplicitIntakeFoldStillWorksThroughTheSameClient(unittest.TestCase):
    """The orchestrator's own explicit §5.1 fold at intake is untouched.

    This is core's job now (core#915), but it still goes through the same
    bus-client Message plumbing -- so an explicit ``fold_inbound`` call made
    by whoever owns intake must still merge correctly.
    """

    def setUp(self):
        SessionManager.reset_default_session()

    def tearDown(self):
        SessionManager.reset_default_session()

    def test_explicit_fold_inbound_merges_field_by_field(self):
        first = Message.deserialize(_inbound({"session_id": "default",
                                              "lang": "pt-pt",
                                              "site_id": "kitchen"}))
        SessionManager.fold_inbound(first)
        stored = SessionManager.get_default_session()
        self.assertEqual(stored.lang, "pt-PT")
        self.assertEqual(stored.site_id, "kitchen")

        # a minimal second intake carries no lang and no site_id, so §5.1
        # leaves both stored values alone
        second = Message.deserialize(_inbound({"session_id": "default"}))
        SessionManager.fold_inbound(second)
        self.assertIs(SessionManager.get_default_session(), stored)
        self.assertEqual(stored.lang, "pt-PT")
        self.assertEqual(stored.site_id, "kitchen")

    def test_absent_carrier_at_intake_is_the_default_session(self):
        first = Message.deserialize(_inbound({"session_id": "default",
                                              "site_id": "kitchen"}))
        SessionManager.fold_inbound(first)
        second = Message.deserialize(
            Message("recognizer_loop:utterance",
                   {"utterances": ["hello"]}).serialize())
        SessionManager.fold_inbound(second)
        self.assertEqual(SessionManager.get_default_session().site_id,
                         "kitchen")


if __name__ == "__main__":
    unittest.main()
