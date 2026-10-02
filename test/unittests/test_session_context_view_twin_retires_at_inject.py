"""T-2348: the bare twin a pre-CONTEXT-1 writer left must retire when this
view's own write makes it stale.

A writer that predates OVOS-CONTEXT-1 stores the munged spelling as a bare key
beside the private entry the same turn writes, and ``frame_stack`` hides the
duplicate behind the private entry. ``inject_context`` writes the private key
alone, so the inject is itself what makes the two values diverge. From that
moment ``frame_stack``'s divergence rule keeps both, two frames carry the one
registered spelling with two different values, and ``remove_context``'s
equal-value guard can never fire again: a removal leaves the pre-inject value
tagging, with nothing raised and nothing logged.

OVOS-CONTEXT-1 §5 l.619-623 permits the deletion, conditionally:

    A component MAY delete shared entries it did not set only when doing so is
    part of its user-visible purpose (an explicit "forget that" command,
    end-of-conversation cleanup).

Deleting the twin is what makes the context update the user just caused take
effect for the one spelling this view serves, so the condition is met
(architecture, T-7135). The permission turns on the twin being this view's own
prior output, so the gate is ``remove_context``'s existing three conditions,
moved to inject time because the write is what destroys the second one.

Candidate A, writing the new value to the twin as well, is refused: §3 l.266
reads a stored key by its shape "however the writer derived it", so a bare key
carrying a privately intended value is the state §3 forbids a component to
author, whether it minted the key or only updated it.
"""
import time
import unittest

from ovos_bus_client.session import Session

SKILL_ID = "tea.skill"
KEY = "confirming_milk"
PRIVATE = f"{SKILL_ID}:{KEY}"
MUNGED = "tea_skillconfirming_milk"


def _adapt_entity(value, entity_type=MUNGED):
    return {"key": value, "data": [(value, entity_type)], "confidence": 1.0}


class TestTheShadowedTwinRetiresAtInject(unittest.TestCase):
    def _session(self, twin=None, private="milk", expires=120):
        session = Session(session_id="t2348")
        store = {}
        if private is not None:
            store[PRIVATE] = {"value": private,
                              "expires_at": time.time() + expires}
        if twin is not None:
            store[MUNGED] = twin
        session.intent_context = store
        return session

    def _live(self, value, expires=120):
        return {"value": value, "expires_at": time.time() + expires}

    def _values(self, session):
        return {key: (entry or {}).get("value") if isinstance(entry, dict)
                else entry
                for key, entry in (session.intent_context or {}).items()}

    def _spellings(self, view):
        return [entity["data"][0][1]
                for frame, _ts in view.frame_stack
                for entity in frame.entities]

    # --- the defect -----------------------------------------------------
    def test_the_inject_retires_the_twin_it_makes_stale(self):
        """The intermediate state, which is the only place the two candidate
        repairs differ: the twin is tombstoned by the inject itself, and
        exactly one frame carries the registered spelling."""
        session = self._session(twin=self._live("milk"))
        view = session.context
        view.inject_context(_adapt_entity("oatmilk"))

        self.assertEqual(self._values(session),
                         {PRIVATE: "oatmilk", MUNGED: None})
        self.assertEqual(self._spellings(view), [MUNGED],
                         "exactly one frame may carry the registered spelling")

    def test_a_removal_after_an_inject_leaves_nothing_tagging(self):
        """The user-visible defect, end to end: the removal used to leave the
        pre-inject value serving."""
        session = self._session(twin=self._live("milk"))
        view = session.context
        view.inject_context(_adapt_entity("oatmilk"))
        view.remove_context(MUNGED)

        self.assertEqual(self._spellings(view), [])
        self.assertIsNone(self._values(session)[MUNGED])

    def test_two_injects_then_a_removal_leave_nothing_tagging(self):
        session = self._session(twin=self._live("milk"))
        view = session.context
        for value in ("oatmilk", "soymilk"):
            view.inject_context(_adapt_entity(value))
        view.remove_context(MUNGED)
        self.assertEqual(self._spellings(view), [])

    # --- the controls ---------------------------------------------------
    def test_with_no_twin_the_inject_touches_one_key(self):
        """The control. This cell was always correct, so a fix measured only
        here would prove nothing."""
        session = self._session()
        view = session.context
        view.inject_context(_adapt_entity("oatmilk"))
        self.assertEqual(self._values(session), {PRIVATE: "oatmilk"})

    def test_a_removal_with_no_inject_still_tombstones_both(self):
        """The other control: the equal-value guard at removal time is not
        widened and still fires when nothing diverged it."""
        session = self._session(twin=self._live("milk"))
        session.context.remove_context(MUNGED)
        self.assertEqual(self._values(session),
                         {PRIVATE: None, MUNGED: None})

    # --- the gate turns on a change, not on the inject ------------------
    def test_an_inject_of_the_value_already_stored_keeps_the_twin(self):
        """Reviewer finding 1 on @93ef861. The three conditions compared the
        twin with the private entry's value *before* the write and nothing
        compared the value being written, so an inject that changed nothing
        retired the twin anyway.

        ``set_context`` re-sets the same value on every re-entry, which is how a
        skill keeps a context alive across turns, so the first such turn stripped
        a pre-CONTEXT-1 peer's only readable spelling. §5 permits the deletion
        only when it is "part of its user-visible purpose", and the purpose
        claimed is making the update the user just caused take effect: an inject
        that updates nothing has no such update, so the permission does not reach
        it. The twin had not gone stale either — it was still an exact duplicate.
        """
        session = self._session(twin=self._live("milk"))
        view = session.context
        view.inject_context(_adapt_entity("milk"))

        self.assertEqual(self._values(session),
                         {PRIVATE: "milk", MUNGED: "milk"})
        self.assertEqual(self._spellings(view), [MUNGED],
                         "the duplicate is still hidden behind the private "
                         "entry, so one frame carries the spelling")

    def test_a_second_inject_of_the_same_new_value_keeps_the_retired_state(self):
        """The sequence that follows from the cell above: the first inject
        changes the value and retires the twin, and a second inject of that
        same value must not be read as a fresh change."""
        session = self._session(twin=self._live("milk"))
        view = session.context
        view.inject_context(_adapt_entity("oatmilk"))
        self.assertEqual(self._values(session),
                         {PRIVATE: "oatmilk", MUNGED: None})
        view.inject_context(_adapt_entity("oatmilk"))
        self.assertEqual(self._values(session),
                         {PRIVATE: "oatmilk", MUNGED: None})

    def test_a_non_divergent_shared_entry_is_retired_and_cannot_be_told_apart(self):
        """The negative the review found missing from the list, asserted as the
        loss it is rather than left out.

        The three conditions are a proxy for provenance, not a decision about
        it: §3 l.271-273 says "§2 gives the entry no ``scope`` field and no
        ``origin`` field, so a private intention that the key does not spell is
        recorded nowhere and no consumer can act on it". A genuine shared
        fact another component published, whose value happens to equal the
        private entry's, is indistinguishable from this view's own twin and is
        retired with it. That is the accepted cost of decision
        ``context1-twin-tombstone-backcompat``, "no entry beats a wrong one",
        and it is the same proxy ``remove_context`` has always used. This cell
        exists so the cost is visible in the suite and a future reader does not
        mistake the proxy for a provenance check.
        """
        session = self._session(twin=self._live("milk"))
        view = session.context
        view.inject_context(_adapt_entity("oatmilk"))
        self.assertEqual(self._values(session),
                         {PRIVATE: "oatmilk", MUNGED: None},
                         "a coincidentally equal shared entry is retired too; "
                         "nothing at this layer can tell it from the twin")


    # --- what the gate must NOT delete ----------------------------------
    def test_a_divergent_shared_entry_survives_the_inject(self):
        """§5's permission reaches only this view's own prior output. A bare
        entry holding a different value is, or may be, a third party's shared
        entry, and ``frame_stack``'s divergence rule exists to keep it
        visible."""
        session = self._session(twin=self._live("soy"))
        view = session.context
        view.inject_context(_adapt_entity("oatmilk"))
        self.assertEqual(self._values(session),
                         {PRIVATE: "oatmilk", MUNGED: "soy"})

    def test_an_expired_twin_is_left_alone(self):
        session = self._session(twin={"value": "milk",
                                      "expires_at": time.time() - 5})
        view = session.context
        view.inject_context(_adapt_entity("oatmilk"))
        self.assertEqual(self._values(session)[MUNGED], "milk",
                         "a dead entry takes no part in resolution")

    def test_an_already_tombstoned_twin_stays_tombstoned(self):
        session = self._session(twin=None)
        session.intent_context[MUNGED] = None
        view = session.context
        view.inject_context(_adapt_entity("oatmilk"))
        self.assertIsNone(self._values(session)[MUNGED])

    def test_a_bare_write_with_no_private_entry_retires_nothing(self):
        """No private entry means no twin of ours to retire: the bare key is
        not this view's prior output."""
        session = self._session(private=None, twin=self._live("milk"))
        view = session.context
        view.inject_context(_adapt_entity("oatmilk"))
        self.assertEqual(self._values(session), {MUNGED: "oatmilk"})

    def test_another_owners_private_entry_and_an_unrelated_shared_key_are_untouched(self):
        session = self._session(twin=self._live("milk"))
        session.intent_context["other.skill:confirming_milk"] = self._live("milk")
        session.intent_context["weather"] = self._live("sunny")
        view = session.context
        view.inject_context(_adapt_entity("oatmilk"))
        values = self._values(session)
        self.assertEqual(values["other.skill:confirming_milk"], "milk")
        self.assertEqual(values["weather"], "sunny")


if __name__ == "__main__":
    unittest.main()
