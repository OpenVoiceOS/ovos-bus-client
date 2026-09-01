"""SCHEDULER-1 conformance: one test per MUST of §9, plus the edges around
them that a scheduler gets wrong in production rather than in a spec.
"""
import json
from glob import glob
import os
import tempfile
import time
import unittest
from threading import Event, Thread
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from ovos_utils.fakebus import FakeBus

from ovos_bus_client.message import Message
from ovos_bus_client.util.scheduled_events import (
    LEGACY_REMOVAL_VERSION, MAX_DATA_BYTES, MAX_REPORTED, Schedule,
    ScheduledEventService, format_instant, topics, validate_record)


def iso(when: datetime) -> str:
    return when.isoformat()


class Recorder:
    """Captures every message the scheduler emits, in order."""

    def __init__(self, bus):
        self.messages = []
        bus.on("message", self._on_message)

    def _on_message(self, message):
        if isinstance(message, str):
            message = Message.deserialize(message)
        self.messages.append(message)

    def types(self):
        return [message.msg_type for message in self.messages]

    def of(self, msg_type):
        return [m for m in self.messages if m.msg_type == msg_type]

    def last(self, msg_type):
        found = self.of(msg_type)
        return found[-1] if found else None


class SchedulerTestCase(unittest.TestCase):
    """A scheduler with a store of its own and every message it emits kept."""

    def setUp(self):
        self.bus = FakeBus()
        self.recorder = Recorder(self.bus)
        handle, self.store = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        os.unlink(self.store)
        self.service = ScheduledEventService(self.bus, store_path=self.store,
                                             autostart=False)

    def tearDown(self):
        self.service.shutdown()
        for path in (self.store, f"{self.store}.tmp"):
            if os.path.isfile(path):
                os.unlink(path)

    def request(self, topic, data, context=None):
        """Deliver a request to the scheduler and return its answer."""
        self.bus.emit(Message(topic, data, context or {}))
        return self.recorder.last(f"{topic}.response")

    def schedule(self, request_context=None, **data):
        data.setdefault("owner", "skill.a")
        data.setdefault("id", "one")
        data.setdefault("event", "skill.a.ring")
        return self.request(topics.SCHEDULER_SCHEDULE, data, request_context)

    def new_scheduler(self):
        """A second scheduler over the same store, as a restart would be."""
        revived = ScheduledEventService(self.bus, store_path=self.store,
                                        autostart=False)
        self.addCleanup(revived.shutdown)
        return revived

    def store_content(self):
        with open(self.store) as handle:
            return json.load(handle)


class TestValidationAndAnswers(SchedulerTestCase):
    """§9.1 — validate every field and answer every request exactly once."""

    def test_every_request_is_answered_once(self):
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        self.assertEqual(
            len(self.recorder.of(topics.SCHEDULER_SCHEDULE_RESPONSE)), 1)
        self.request(topics.SCHEDULER_GET, {"owner": "skill.a", "id": "one"})
        self.assertEqual(len(self.recorder.of(topics.SCHEDULER_GET_RESPONSE)), 1)

    def test_naive_instant_is_rejected(self):
        answer = self.schedule(at="2031-03-29T07:30:00")
        self.assertFalse(answer.data["ok"])
        self.assertEqual(answer.data["error"], "bad_instant")

    def test_two_timing_fields_are_rejected(self):
        answer = self.schedule(at=iso(datetime.now(timezone.utc)),
                               every={"seconds": 10})
        self.assertEqual(answer.data["error"], "invalid_record")

    def test_missing_timing_is_rejected(self):
        self.assertEqual(self.schedule().data["error"], "invalid_record")

    def test_an_event_with_a_colon_in_the_name_is_rejected(self):
        answer = self.schedule(event="skill.a.ring:now",
                               at=iso(datetime.now(timezone.utc)))
        self.assertEqual(answer.data["error"], "bad_event")

    def test_an_event_that_is_only_the_owner_is_rejected(self):
        answer = self.schedule(event="skill.a.",
                               at=iso(datetime.now(timezone.utc)))
        self.assertEqual(answer.data["error"], "bad_event")

    def test_a_malformed_recurrence_is_rejected(self):
        self.assertEqual(self.schedule(every={"seconds": 0}).data["error"],
                         "bad_recurrence")
        self.assertEqual(
            self.schedule(local={"time": "07:30",
                                 "zone": "Mars/Olympus"}).data["error"],
            "bad_recurrence")

    def test_a_payload_over_the_cap_is_rejected(self):
        answer = self.schedule(
            at=iso(datetime.now(timezone.utc) + timedelta(hours=1)),
            data={"blob": "x" * (MAX_DATA_BYTES + 100)})
        self.assertEqual(answer.data["error"], "payload_too_large")

    def test_until_with_a_one_shot_is_rejected(self):
        answer = self.schedule(at=iso(datetime.now(timezone.utc)),
                               until=iso(datetime.now(timezone.utc)))
        self.assertEqual(answer.data["error"], "invalid_record")

    def test_a_recurrence_bounded_before_its_first_occurrence_is_rejected(self):
        start = datetime.now(timezone.utc) + timedelta(hours=2)
        answer = self.schedule(id="tick", event="skill.a.tick",
                               until=iso(start - timedelta(hours=1)),
                               every={"seconds": 600, "start": iso(start)})
        self.assertEqual(answer.data["error"], "bad_recurrence")
        # nothing retires a schedule that never fires, so it must not be stored
        self.assertEqual(self.service.schedules, {})
        self.assertFalse(os.path.isfile(self.store))

    def test_a_wall_clock_rule_bounded_in_the_past_is_rejected(self):
        answer = self.schedule(
            id="wake", event="skill.a.wake",
            until=iso(datetime.now(timezone.utc) - timedelta(days=1)),
            local={"time": "07:30", "zone": "Europe/Lisbon"})
        self.assertEqual(answer.data["error"], "bad_recurrence")
        self.assertEqual(self.service.schedules, {})

    def test_a_recurrence_bounded_after_its_first_occurrence_is_accepted(self):
        start = datetime.now(timezone.utc) + timedelta(hours=1)
        answer = self.schedule(id="tick", event="skill.a.tick",
                               until=iso(start + timedelta(hours=1)),
                               every={"seconds": 600, "start": iso(start)})
        self.assertTrue(answer.data["ok"])

    def test_cancelling_an_absent_schedule_is_not_an_error(self):
        answer = self.request(topics.SCHEDULER_CANCEL,
                              {"owner": "skill.a", "id": "nope"})
        self.assertTrue(answer.data["ok"])
        self.assertFalse(answer.data["existed"])

    def test_round_trip_schedule_get_list_cancel(self):
        when = datetime.now(timezone.utc) + timedelta(hours=1)
        created = self.schedule(at=iso(when))
        self.assertTrue(created.data["ok"])
        self.assertEqual(created.data["next"], iso(when))

        read = self.request(topics.SCHEDULER_GET,
                            {"owner": "skill.a", "id": "one"})
        self.assertEqual(read.data["record"]["event"], "skill.a.ring")
        self.assertEqual(read.data["state"]["next"], iso(when))
        self.assertIsNone(read.data["state"]["last_fired"])
        self.assertEqual(read.data["state"]["missed"], [])

        listed = self.request(topics.SCHEDULER_LIST, {"owner": "skill.a"})
        self.assertEqual(
            [s["record"]["id"] for s in listed.data["schedules"]], ["one"])

        cancelled = self.request(topics.SCHEDULER_CANCEL,
                                 {"owner": "skill.a", "id": "one"})
        self.assertTrue(cancelled.data["existed"])
        listed = self.request(topics.SCHEDULER_LIST, {"owner": "skill.a"})
        self.assertEqual(listed.data["schedules"], [])


class TestPersistence(SchedulerTestCase):
    """§9.2 — persist before answering and before firing on, atomically."""

    def test_the_store_is_written_before_the_answer(self):
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        self.assertEqual(
            self.store_content()["schedules"][0]["record"]["id"], "one")

    def test_the_due_of_a_fired_occurrence_is_persisted_after_the_event(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=5)
        self.schedule(id="tick", event="skill.a.tick", grace_s=600,
                      every={"seconds": 10, "start": iso(start)})
        self.service._evaluate()
        self.assertEqual(len(self.recorder.of("skill.a.tick")), 1)
        self.assertEqual(
            self.store_content()["schedules"][0]["last_fired"], iso(start))

    def test_an_occurrence_at_or_before_the_persisted_due_never_fires_again(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=5)
        self.schedule(id="tick", event="skill.a.tick", grace_s=600,
                      every={"seconds": 10, "start": iso(start)})
        self.service._evaluate()
        schedule = self.service.schedules["skill.a", "tick"]
        # a replacement scheduler handed a stale cursor must not replay the fire
        schedule.cursor = start - timedelta(seconds=60)
        self.service._evaluate()
        self.assertEqual(len(self.recorder.of("skill.a.tick")), 1)

    def test_a_crash_between_the_temporary_write_and_the_replace_keeps_the_old_state(self):
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        before = open(self.store).read()
        with patch("os.replace", side_effect=OSError("power loss")):
            answer = self.schedule(
                id="two",
                at=iso(datetime.now(timezone.utc) + timedelta(hours=2)))
        self.assertEqual(open(self.store).read(), before)
        self.assertEqual(answer.data["error"], "internal")

    def test_an_unstorable_creation_leaves_no_trace_in_memory(self):
        with patch("os.replace", side_effect=OSError("read-only")):
            answer = self.schedule(
                at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        self.assertEqual(answer.data["error"], "internal")
        # the running process agrees with the store it would restart from
        self.assertEqual(self.service.schedules, {})

    def test_an_unstorable_replacement_keeps_the_previous_schedule(self):
        first = datetime.now(timezone.utc) + timedelta(hours=1)
        self.schedule(at=iso(first))
        with patch("os.replace", side_effect=OSError("read-only")):
            answer = self.schedule(
                at=iso(datetime.now(timezone.utc) + timedelta(hours=5)))
        self.assertEqual(answer.data["error"], "internal")
        self.assertEqual(
            self.service.schedules["skill.a", "one"].record["at"], iso(first))

    def test_an_unstorable_cancel_keeps_the_schedule(self):
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        with patch("os.replace", side_effect=OSError("read-only")):
            answer = self.request(topics.SCHEDULER_CANCEL,
                                  {"owner": "skill.a", "id": "one"})
        self.assertEqual(answer.data["error"], "internal")
        self.assertIn(("skill.a", "one"), self.service.schedules)
        # and it is still cancellable once the store is writable again
        self.assertTrue(self.request(topics.SCHEDULER_CANCEL,
                                     {"owner": "skill.a", "id": "one"}
                                     ).data["existed"])

    def test_an_unstorable_wildcard_cancel_keeps_every_schedule(self):
        when = iso(datetime.now(timezone.utc) + timedelta(hours=1))
        self.schedule(owner="skill.a", event="skill.a.ring", at=when)
        self.schedule(owner="skill.b", event="skill.b.ring", at=when)
        self.service.admins = ["admin.panel"]
        with patch("os.replace", side_effect=OSError("read-only")):
            answer = self.request(topics.SCHEDULER_CANCEL,
                                  {"owner": "*", "id": "one"},
                                  context={"skill_id": "admin.panel"})
        self.assertEqual(answer.data["error"], "internal")
        self.assertEqual(len(self.service.schedules), 2)

    def test_a_corrupt_store_timestamp_does_not_stop_the_scheduler(self):
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        content = self.store_content()
        content["written_at"] = "nonsense"
        with open(self.store, "w") as handle:
            json.dump(content, handle)

        revived = self.new_scheduler()
        self.assertIn(("skill.a", "one"), revived.schedules)
        self.assertTrue(revived.clock_synced)

    def test_a_store_written_by_a_newer_scheduler_is_set_aside_not_read(self):
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        content = self.store_content()
        newer_version = content["version"] + 1
        content["version"] = newer_version
        with open(self.store, "w") as handle:
            json.dump(content, handle)
        original = open(self.store, "rb").read()

        revived = self.new_scheduler()
        self.assertEqual(revived.schedules, {})

        # a downgrade must not cost the schedules the newer scheduler held
        backup = f"{self.store}.v{newer_version}.bak"
        self.addCleanup(os.unlink, backup)
        self.assertEqual(open(backup, "rb").read(), original)

        # and this scheduler writes a store of its own from scratch
        revived._persist()
        self.assertEqual(self.store_content()["schedules"], [])
        self.assertEqual(self.store_content()["version"], newer_version - 1)

    def test_a_store_from_an_older_version_is_read_not_set_aside(self):
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)),
                      request_context={"mine": True})
        content = self.store_content()
        # what an older scheduler wrote: no context field on the record
        content["version"] = content["version"] - 1
        for entry in content["schedules"]:
            entry["record"].pop("context", None)
        with open(self.store, "w") as handle:
            json.dump(content, handle)

        revived = self.new_scheduler()
        restored = revived.schedules["skill.a", "one"]
        self.assertNotIn("context", restored.record)
        self.assertFalse(glob(f"{self.store}.v*.bak"))

        # and it fires clean rather than with something invented for it
        revived.replace_schedule(dict(restored.record,
                                      at=iso(datetime.now(timezone.utc) -
                                             timedelta(seconds=1))))
        revived._evaluate()
        fired = self.recorder.last("skill.a.ring")
        self.assertEqual(set(fired.context) - {"session"}, {"scheduler"})

    def test_a_store_that_will_not_parse_is_quarantined_not_overwritten(self):
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        whole = open(self.store, "rb").read()
        damaged = whole[:len(whole) // 2]
        with open(self.store, "wb") as handle:
            handle.write(damaged)

        revived = self.new_scheduler()
        self.assertEqual(revived.schedules, {})

        quarantined = glob(f"{self.store}.corrupt.*.bak")
        self.assertEqual(len(quarantined), 1)
        self.addCleanup(os.unlink, quarantined[0])
        self.assertEqual(open(quarantined[0], "rb").read(), damaged)
        self.assertFalse(os.path.exists(self.store))

        # and the scheduler is a working one, writing a store of its own
        revived.replace_schedule(
            {"id": "two", "owner": "skill.a", "event": "skill.a.ring",
             "data": {}, "at": iso(datetime.now(timezone.utc) +
                                   timedelta(hours=1)),
             "misfire": "late", "grace_s": 60, "ephemeral": False})
        self.assertEqual(
            [entry["record"]["id"] for entry in
             self.store_content()["schedules"]], ["two"])

    def test_one_fire_is_persisted_before_the_next_is_emitted(self):
        # a kill anywhere inside a backlog may repeat the occurrence that was
        # in flight, and nothing earlier
        start = datetime.now(timezone.utc) - timedelta(seconds=35)
        self.schedule(id="tick", event="skill.a.tick", misfire="all",
                      grace_s=0, every={"seconds": 10, "start": iso(start)})

        kill_points = self._store_after_every_fire()
        self.assertEqual(len(kill_points), 4)

        fired = [due for due, _ in kill_points]
        for position, (due, state) in enumerate(kill_points):
            repeated = self._replay_from(state, already_fired=fired[:position + 1])
            self.assertLessEqual(
                len(repeated), 1,
                f"a kill just after the fire for {due} repeated "
                f"{len(repeated)} already-emitted occurrences")

    def _store_after_every_fire(self):
        """The store as it stands the instant after each event leaves the bus,
        which is where a kill can land."""
        kill_points = []
        emit_one = self.service._fire

        def watched(schedule, due):
            emit_one(schedule, due)
            kill_points.append((format_instant(due), open(self.store).read()))

        self.service._fire = watched
        self.service._evaluate()
        return kill_points

    def _replay_from(self, state, already_fired):
        """Occurrences a fresh scheduler repeats when it starts from ``state``."""
        with open(self.store, "w") as handle:
            handle.write(state)
        bus = FakeBus()
        recorder = Recorder(bus)
        revived = ScheduledEventService(bus, store_path=self.store,
                                        autostart=False)
        revived.replay()
        revived.shutdown()
        return [m for m in recorder.of("skill.a.tick")
                if m.context["scheduler"]["due"] in set(already_fired)]

    def test_ephemeral_schedules_are_never_persisted(self):
        when = iso(datetime.now(timezone.utc) + timedelta(hours=1))
        self.schedule(id="kept", at=when)
        self.schedule(id="gone", ephemeral=True, at=when)
        self.assertEqual(
            [s["record"]["id"] for s in self.store_content()["schedules"]],
            ["kept"])
        self.assertEqual([key[1] for key in self.new_scheduler().schedules],
                         ["kept"])


class TestIdempotency(SchedulerTestCase):
    """§9.3 — replace on identical identity, never duplicate."""

    def test_rescheduling_the_same_identity_replaces(self):
        first = datetime.now(timezone.utc) + timedelta(hours=1)
        second = datetime.now(timezone.utc) + timedelta(hours=2)
        self.assertFalse(self.schedule(at=iso(first)).data["replaced"])
        answer = self.schedule(at=iso(second))
        self.assertTrue(answer.data["replaced"])
        self.assertEqual(answer.data["next"], iso(second))
        self.assertEqual(len(self.service.schedules), 1)

    def test_two_owners_may_use_the_same_id(self):
        when = iso(datetime.now(timezone.utc) + timedelta(hours=1))
        self.schedule(owner="skill.a", event="skill.a.ring", at=when)
        self.schedule(owner="skill.b", event="skill.b.ring", at=when)
        self.assertEqual(len(self.service.schedules), 2)

    def test_an_unchanged_period_keeps_its_anchor(self):
        start = datetime.now(timezone.utc) + timedelta(seconds=90)
        self.schedule(id="tick", event="skill.a.tick",
                      every={"seconds": 600, "start": iso(start)})
        self.schedule(id="tick", event="skill.a.tick", every={"seconds": 600})
        self.assertEqual(
            self.service.schedules["skill.a", "tick"].record["every"]["start"],
            iso(start))

    def test_a_changed_period_is_re_anchored(self):
        start = datetime.now(timezone.utc) + timedelta(seconds=90)
        self.schedule(id="tick", event="skill.a.tick",
                      every={"seconds": 600, "start": iso(start)})
        self.schedule(id="tick", event="skill.a.tick", every={"seconds": 60})
        self.assertNotEqual(
            self.service.schedules["skill.a", "tick"].record["every"]["start"],
            iso(start))


class TestReplacementKeepsWhatFired(SchedulerTestCase):
    """§9.2, §9.3 and §9.9 — a replacement is not a way to fire again."""

    def hourly_since(self, hours: int, **fields):
        start = datetime.now(timezone.utc) - timedelta(hours=hours)
        request = dict(id="hourly", event="skill.a.hourly", misfire="all",
                       grace_s=0, every={"seconds": 3600, "start": iso(start)})
        request.update(fields)
        return request

    def test_an_identical_request_fires_nothing_further(self):
        request = self.hourly_since(5)
        self.schedule(**request)
        self.service._evaluate()
        already = [m.context["scheduler"]["due"]
                   for m in self.recorder.of("skill.a.hourly")]
        self.assertTrue(already)

        # §5.2: an owner re-creating its schedules on every start sends this
        # very request again, and sending it twice must be sending it once
        self.schedule(**request)
        self.service._evaluate()
        again = [m.context["scheduler"]["due"]
                 for m in self.recorder.of("skill.a.hourly")]
        self.assertEqual(again, already)

    def test_an_identical_request_keeps_what_was_consumed(self):
        request = self.hourly_since(5, count=10)
        self.schedule(**request)
        self.service._evaluate()
        spent = self.service.schedules["skill.a", "hourly"].consumed
        self.assertGreater(spent, 0)
        self.schedule(**request)
        self.assertEqual(self.service.schedules["skill.a", "hourly"].consumed,
                         spent)

    def test_an_identical_request_does_not_report_the_past_as_missed(self):
        request = self.hourly_since(5)
        self.schedule(**request)
        self.service._evaluate()
        self.schedule(**request)
        self.service._evaluate()
        self.assertEqual(len(self.recorder.of(topics.SCHEDULER_MISSED)), 1)

    def test_changing_only_the_payload_keeps_the_count_budget(self):
        self.schedule(**self.hourly_since(5, count=20))
        self.service._evaluate()
        spent = self.service.schedules["skill.a", "hourly"].consumed
        self.schedule(**self.hourly_since(5, count=20, data={"k": 2}))
        schedule = self.service.schedules["skill.a", "hourly"]
        self.assertEqual(schedule.consumed, spent)
        self.assertEqual(schedule.remaining(), 20 - spent)
        self.assertEqual(schedule.record["data"], {"k": 2})

    def test_a_new_timing_still_cannot_fire_at_or_before_the_last_fire(self):
        self.schedule(**self.hourly_since(5))
        self.service._evaluate()
        last = self.service.schedules["skill.a", "hourly"].last_fired
        self.assertIsNotNone(last)
        # a different series entirely, anchored well before that fire
        self.schedule(id="hourly", event="skill.a.hourly", misfire="all",
                      grace_s=0,
                      every={"seconds": 600,
                             "start": iso(datetime.now(timezone.utc) -
                                          timedelta(hours=9))})
        schedule = self.service.schedules["skill.a", "hourly"]
        self.assertEqual(schedule.last_fired, last)
        self.assertGreater(schedule.next_from_now(self.service.now()), last)

    def test_a_replacement_survives_a_restart_with_its_history(self):
        request = self.hourly_since(5)
        self.schedule(**request)
        self.service._evaluate()
        self.schedule(**request)
        revived = self.new_scheduler()
        restored = revived.schedules["skill.a", "hourly"]
        self.assertEqual(restored.last_fired,
                         self.service.schedules["skill.a", "hourly"].last_fired)


class TestRearmingFromInsideTheHandler(SchedulerTestCase):
    """§4.3 retires the schedule that fired, not whatever holds its id.

    "When it rings, set the next one" is how an owner builds a chain of
    one-shots, and the replacement it makes is younger than the fire that is
    still being processed.
    """

    def overdue(self) -> str:
        return iso(datetime.now(timezone.utc) - timedelta(seconds=1))

    def arm(self, **fields):
        request = dict(id="alarm", event="skill.a.alarm", at=self.overdue())
        request.update(fields)
        return self.schedule(**request)

    def test_a_one_shot_armed_again_by_its_own_handler_survives(self):
        rings = []

        def ring(message):
            rings.append(message.context["scheduler"]["due"])
            if len(rings) < 3:
                self.arm()

        self.bus.on("skill.a.alarm", ring)
        self.arm()
        for _ in range(3):
            self.service._evaluate()
        self.assertEqual(len(rings), 3)

    def test_the_replacement_is_still_there_after_the_batch(self):
        self.bus.on("skill.a.alarm", lambda m: self.arm(at=iso(
            datetime.now(timezone.utc) + timedelta(hours=1))))
        self.arm()
        self.service._evaluate()
        self.assertIn(("skill.a", "alarm"), self.service.schedules)
        self.assertEqual(
            [entry["record"]["id"] for entry in
             self.store_content()["schedules"]], ["alarm"])

    def test_a_replacement_from_another_thread_is_not_retired(self):
        arrived = Event()

        def replace_from_elsewhere(message):
            worker = Thread(target=lambda: (self.arm(at=iso(
                datetime.now(timezone.utc) + timedelta(hours=1))),
                arrived.set()))
            worker.start()
            worker.join(5)

        self.bus.on("skill.a.alarm", replace_from_elsewhere)
        self.arm()
        self.service._evaluate()
        self.assertTrue(arrived.is_set())
        self.assertIn(("skill.a", "alarm"), self.service.schedules)

    def test_a_spent_recurrence_retires_but_its_replacement_does_not(self):
        started = iso(datetime.now(timezone.utc) - timedelta(seconds=25))
        fires = []

        def rearm(message):
            fires.append(message)
            if len(fires) == 1:
                self.arm(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))

        self.bus.on("skill.a.alarm", rearm)
        # a recurrence with nothing left after this batch
        self.arm(at=None, every={"seconds": 10, "start": started}, count=3,
                 misfire="all", grace_s=0)
        self.service._evaluate()
        self.assertTrue(fires)
        surviving = self.service.schedules[("skill.a", "alarm")]
        self.assertIn("at", surviving.record)
        self.assertNotIn("every", surviving.record)


class TestReplay(SchedulerTestCase):
    """§9.4 and §9.6 — restore, announce ready, then apply the misfire policy."""

    def _persist_and_restart(self, **record_fields):
        """Store one schedule, then start a fresh scheduler over that store."""
        record = validate_record(dict(
            {"id": "one", "owner": "skill.a", "event": "skill.a.ring"},
            **record_fields))
        writer = ScheduledEventService(self.bus, store_path=self.store,
                                       autostart=False)
        writer.schedules[record["owner"], record["id"]] = Schedule(record)
        writer._persist()
        writer.shutdown()
        self.recorder.messages.clear()

        revived = self.new_scheduler()
        revived.replay()
        return revived

    def test_late_fires_the_missed_one_shot_and_reports_it(self):
        due = datetime.now(timezone.utc) - timedelta(minutes=30)
        self._persist_and_restart(misfire="late", at=iso(due))

        missed = self.recorder.last(topics.SCHEDULER_MISSED)
        self.assertEqual(missed.data["missed"], [iso(due)])
        self.assertEqual(missed.data["fired_late"], [iso(due)])
        self.assertEqual(
            self.recorder.last("skill.a.ring").context["scheduler"]["due"],
            iso(due))
        # readiness comes first, so an owner listening for it hears its own
        # late fire
        self.assertEqual(self.recorder.types(),
                         [topics.SCHEDULER_READY, topics.SCHEDULER_MISSED,
                          "skill.a.ring"])
        self.assertEqual(self.recorder.last(topics.SCHEDULER_READY).data,
                         {"schedules": 1, "missed": 1, "clock": "synchronized"})

    def test_skip_reports_but_does_not_fire(self):
        due = datetime.now(timezone.utc) - timedelta(minutes=30)
        self._persist_and_restart(misfire="skip", at=iso(due))
        self.assertEqual(self.recorder.of("skill.a.ring"), [])
        self.assertEqual(
            self.recorder.last(topics.SCHEDULER_MISSED).data["fired_late"], [])
        self.assertIn(topics.SCHEDULER_READY, self.recorder.types())

    def test_a_one_shot_is_deleted_once_it_has_been_reported(self):
        due = datetime.now(timezone.utc) - timedelta(minutes=30)
        revived = self._persist_and_restart(misfire="skip", at=iso(due))
        self.assertEqual(revived.schedules, {})

    def test_all_fires_every_missed_occurrence_oldest_first(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=50)
        self._persist_and_restart(
            id="tick", event="skill.a.tick", misfire="all", grace_s=0, count=4,
            every={"seconds": 10, "start": iso(start)})
        dues = [m.context["scheduler"]["due"]
                for m in self.recorder.of("skill.a.tick")]
        self.assertEqual(dues, sorted(dues))
        self.assertEqual(len(dues), 4)
        self.assertEqual(dues[0], iso(start))

    def test_ready_counts_what_the_store_held_not_what_survives_replay(self):
        now = datetime.now(timezone.utc)
        writer = ScheduledEventService(self.bus, store_path=self.store,
                                       autostart=False)
        for name, when in (("overdue", now - timedelta(minutes=30)),
                           ("later", now + timedelta(hours=1))):
            record = validate_record({"id": name, "owner": "skill.a",
                                      "event": "skill.a.ring", "at": iso(when)})
            writer.schedules["skill.a", name] = Schedule(record)
        writer._persist()
        writer.shutdown()
        self.recorder.messages.clear()

        self.new_scheduler().replay()
        self.assertEqual(
            self.recorder.last(topics.SCHEDULER_READY).data["schedules"], 2)

    def test_a_recurring_schedule_survives_a_restart(self):
        self.schedule(id="tick", event="skill.a.tick",
                      every={"seconds": 3600,
                             "start": iso(datetime.now(timezone.utc) +
                                          timedelta(hours=1))})
        self.service.shutdown()
        self.assertIn(("skill.a", "tick"), self.new_scheduler().schedules)


class TestFiredMessage(SchedulerTestCase):
    """§9.5 — the requester's own context, plus the scheduler block."""

    def requested_with(self):
        """A context of the shape a real request carries."""
        return {"session": {"session_id": "abc", "lang": "pt-pt"},
                "skill_id": "skill.a",
                "source": "audio:0", "destination": ["skill.a"]}

    def test_a_fired_event_carries_the_context_it_was_scheduled_with(self):
        due = datetime.now(timezone.utc) - timedelta(seconds=1)
        given = self.requested_with()
        self.schedule(at=iso(due), data={"key": "value"},
                      request_context=given)
        self.service._evaluate()
        fired = self.recorder.last("skill.a.ring")
        self.assertEqual(fired.data, {"key": "value"})
        # the routing the owner wrote comes back whole: where the request
        # came from, where its answers go, and which session it belongs to
        for field, value in given.items():
            self.assertEqual(fired.context[field], value)

    def test_the_scheduler_block_is_the_only_thing_added(self):
        due = datetime.now(timezone.utc) - timedelta(seconds=1)
        given = self.requested_with()
        self.schedule(at=iso(due), request_context=given)
        self.service._evaluate()
        fired = self.recorder.last("skill.a.ring")
        self.assertEqual(set(fired.context) - set(given), {"scheduler"})
        self.assertEqual(fired.context["scheduler"]["id"], "one")
        self.assertEqual(fired.context["scheduler"]["owner"], "skill.a")
        self.assertEqual(fired.context["scheduler"]["due"], iso(due))

    def test_a_request_with_no_context_fires_with_none_invented(self):
        due = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.schedule(at=iso(due))
        self.service._evaluate()
        fired = self.recorder.last("skill.a.ring")
        # only the scheduler block, and the default session the bus stamps on
        # any message that names none
        self.assertEqual(set(fired.context) - {"session"}, {"scheduler"})

    def test_the_context_survives_a_restart_with_the_store(self):
        given = self.requested_with()
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(seconds=1)),
                      request_context=given)
        revived = self.new_scheduler()
        self.assertEqual(revived.schedules["skill.a", "one"].record["context"],
                         given)
        time.sleep(1.1)
        revived._evaluate()
        fired = self.recorder.last("skill.a.ring")
        self.assertEqual(fired.context["session"]["session_id"], "abc")

    def test_a_context_planted_in_the_request_body_is_ignored(self):
        # §3.5 takes the context from the request message and never from the
        # body, so a component can only schedule a fire into a context it
        # reached the scheduler from
        self.schedule(at=iso(datetime.now(timezone.utc) - timedelta(seconds=1)),
                      context={"source": "ATTACKER",
                               "session": {"session_id": "victim"}},
                      request_context={"source": "real-caller",
                                       "session": {"session_id": "mine"}})
        stored = self.service.schedules["skill.a", "one"].record
        self.assertNotIn("ATTACKER", json.dumps(stored))

        self.service._evaluate()
        fired = self.recorder.last("skill.a.ring")
        self.assertEqual(fired.context["source"], "real-caller")
        self.assertEqual(fired.context["session"]["session_id"], "mine")
        self.assertNotIn("ATTACKER", json.dumps(fired.context))

    def test_the_later_of_two_requests_supplies_the_context(self):
        # context is carried, not compared: it is no part of the identity
        when = iso(datetime.now(timezone.utc) + timedelta(hours=1))
        self.schedule(at=when, request_context={"mine": "first"})
        self.schedule(at=when, request_context={"mine": "second"})
        self.assertEqual(len(self.service.schedules), 1)
        self.assertEqual(
            self.service.schedules["skill.a", "one"].record["context"]["mine"],
            "second")

    def test_remaining_counts_down_across_a_late_batch(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=25)
        self.schedule(id="tick", event="skill.a.tick", count=10, misfire="all",
                      grace_s=0, every={"seconds": 10, "start": iso(start)})
        self.service._evaluate()
        remaining = [m.context["scheduler"]["remaining"]
                     for m in self.recorder.of("skill.a.tick")]
        self.assertEqual(remaining, [9, 8, 7])


class TestRecurrence(SchedulerTestCase):
    """§9.7 — anchored periods and wall-clock recurrence with the DST rules."""

    @staticmethod
    def _schedule(**record_fields):
        return Schedule(validate_record(
            dict({"id": "x", "owner": "skill.a", "event": "skill.a.x"},
                 **record_fields)))

    def test_a_late_fire_does_not_shift_the_following_occurrences(self):
        start = datetime(2031, 1, 1, 0, 0, tzinfo=timezone.utc)
        schedule = self._schedule(every={"seconds": 600, "start": iso(start)})
        # the fire for 00:10 happens five minutes late; the next occurrence is
        # still on the schedule's own phase, not five minutes behind it
        schedule.cursor = start + timedelta(minutes=10)
        self.assertEqual(schedule.next_after(start + timedelta(minutes=15)),
                         start + timedelta(minutes=20))

    def test_a_wall_clock_rule_crossing_a_spring_forward_gap(self):
        # Europe/Lisbon jumps 01:00 -> 02:00 on 2031-03-30, so 01:30 does not
        # exist and the occurrence is the first instant after the gap
        schedule = self._schedule(
            local={"time": "01:30", "zone": "Europe/Lisbon"})
        occurrence = schedule.next_after(
            datetime(2031, 3, 29, 12, tzinfo=timezone.utc))
        lisbon = occurrence.astimezone(ZoneInfo("Europe/Lisbon"))
        self.assertEqual(lisbon.date().isoformat(), "2031-03-30")
        self.assertEqual(lisbon.strftime("%H:%M"), "02:00")

    def test_a_wall_clock_rule_crossing_a_fall_back_overlap(self):
        # America/New_York reads 01:30 twice on 2031-11-02; the occurrence is
        # the first of the two, still on daylight time
        schedule = self._schedule(
            local={"time": "01:30", "zone": "America/New_York"})
        occurrence = schedule.next_after(
            datetime(2031, 11, 1, 12, tzinfo=timezone.utc))
        self.assertEqual(occurrence.utcoffset(), timedelta(hours=-4))
        self.assertEqual(
            occurrence.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M"),
            "01:30")

    def test_a_wall_clock_rule_honours_its_day_list(self):
        schedule = self._schedule(
            local={"time": "07:30", "zone": "Europe/Lisbon", "days": ["mon"]})
        # 2031-01-01 is a Wednesday; the next Monday is the 6th
        occurrence = schedule.next_after(
            datetime(2031, 1, 1, 12, tzinfo=timezone.utc))
        self.assertEqual(
            occurrence.astimezone(ZoneInfo("Europe/Lisbon")).date().isoformat(),
            "2031-01-06")

    def test_until_stops_the_recurrence(self):
        start = datetime(2031, 1, 1, tzinfo=timezone.utc)
        schedule = self._schedule(until=iso(start + timedelta(seconds=25)),
                                  every={"seconds": 10, "start": iso(start)})
        schedule.cursor = start + timedelta(seconds=20)
        self.assertIsNone(schedule.next_after(schedule.cursor))

    def test_count_stops_the_recurrence(self):
        start = datetime(2031, 1, 1, tzinfo=timezone.utc)
        schedule = self._schedule(count=2,
                                  every={"seconds": 10, "start": iso(start)})
        schedule.consumed = 2
        self.assertIsNone(schedule.next_after(start))


class TestOwnership(SchedulerTestCase):
    """§9.8 — the event namespace and owner scoping."""

    def test_an_event_outside_the_owner_namespace_is_rejected(self):
        answer = self.schedule(event="mycroft.stop",
                               at=iso(datetime.now(timezone.utc)))
        self.assertEqual(answer.data["error"], "bad_event")

    def test_an_authenticated_identity_may_not_act_for_another_owner(self):
        answer = self.request(
            topics.SCHEDULER_SCHEDULE,
            {"owner": "skill.a", "id": "one", "event": "skill.a.ring",
             "at": iso(datetime.now(timezone.utc) + timedelta(hours=1))},
            context={"skill_id": "skill.b"})
        self.assertEqual(answer.data["error"], "not_owner")

    def test_cancel_and_get_are_scoped_by_owner(self):
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        cancelled = self.request(topics.SCHEDULER_CANCEL,
                                 {"owner": "skill.b", "id": "one"})
        self.assertFalse(cancelled.data["existed"])
        self.assertIn(("skill.a", "one"), self.service.schedules)

        read = self.request(topics.SCHEDULER_GET,
                            {"owner": "skill.b", "id": "one"})
        self.assertIsNone(read.data["record"])
        self.assertFalse(read.data["existed"])

    def test_list_shows_only_the_callers_schedules(self):
        when = iso(datetime.now(timezone.utc) + timedelta(hours=1))
        self.schedule(owner="skill.a", event="skill.a.ring", at=when)
        self.schedule(owner="skill.b", event="skill.b.ring", at=when)
        listed = self.request(topics.SCHEDULER_LIST, {"owner": "skill.b"})
        self.assertEqual(
            [s["record"]["owner"] for s in listed.data["schedules"]],
            ["skill.b"])


class TestAdministrativeOwner(SchedulerTestCase):
    """``*`` reaches every owner, and only for a configured administrator."""

    def populate(self):
        when = iso(datetime.now(timezone.utc) + timedelta(hours=1))
        self.schedule(owner="skill.a", event="skill.a.ring", at=when)
        self.schedule(owner="skill.b", event="skill.b.ring", at=when)

    def test_the_allowlist_is_empty_unless_configured(self):
        self.assertEqual(self.service.admins, [])

    def test_an_anonymous_caller_may_not_use_the_wildcard(self):
        self.populate()
        listed = self.request(topics.SCHEDULER_LIST, {"owner": "*"})
        self.assertEqual(listed.data["error"], "not_owner")
        cancelled = self.request(topics.SCHEDULER_CANCEL,
                                 {"owner": "*", "id": "one"})
        self.assertEqual(cancelled.data["error"], "not_owner")
        self.assertEqual(len(self.service.schedules), 2)

    def test_an_identified_caller_outside_the_allowlist_may_not_either(self):
        self.populate()
        listed = self.request(topics.SCHEDULER_LIST, {"owner": "*"},
                              context={"skill_id": "skill.a"})
        self.assertEqual(listed.data["error"], "not_owner")

    def test_an_allowlisted_administrator_lists_and_cancels_everything(self):
        self.populate()
        self.service.admins = ["admin.panel"]
        listed = self.request(topics.SCHEDULER_LIST, {"owner": "*"},
                              context={"skill_id": "admin.panel"})
        self.assertEqual(len(listed.data["schedules"]), 2)
        self.request(topics.SCHEDULER_CANCEL, {"owner": "*", "id": "one"},
                     context={"skill_id": "admin.panel"})
        self.assertEqual(self.service.schedules, {})

    def test_no_schedule_may_be_created_under_the_wildcard(self):
        answer = self.schedule(owner="*", event="*.ring",
                               at=iso(datetime.now(timezone.utc)))
        self.assertEqual(answer.data["error"], "not_owner")

    def test_an_administrator_may_not_create_under_the_wildcard_either(self):
        self.service.admins = ["admin.panel"]
        answer = self.request(
            topics.SCHEDULER_SCHEDULE,
            {"owner": "*", "id": "one", "event": "*.ring",
             "at": iso(datetime.now(timezone.utc) + timedelta(hours=1))},
            context={"skill_id": "admin.panel"})
        self.assertEqual(answer.data["error"], "not_owner")


class TestClock(SchedulerTestCase):
    """§9.9 — a clock step never double-fires, an unset clock defers."""

    def test_a_forward_step_fires_each_occurrence_once(self):
        start = datetime.now(timezone.utc) + timedelta(seconds=10)
        self.schedule(id="tick", event="skill.a.tick", misfire="all",
                      grace_s=0, every={"seconds": 10, "start": iso(start)})
        self.service._evaluate()
        self.assertEqual(self.recorder.of("skill.a.tick"), [])

        jumped = start + timedelta(seconds=25)
        with patch.object(ScheduledEventService, "now",
                          staticmethod(lambda: jumped)):
            self.service._evaluate()
            after_first = [m.context["scheduler"]["due"]
                           for m in self.recorder.of("skill.a.tick")]
            # a second evaluation at the same instant must add nothing
            self.service._evaluate()
        after_second = [m.context["scheduler"]["due"]
                        for m in self.recorder.of("skill.a.tick")]
        self.assertEqual(len(after_first), 3)
        self.assertEqual(after_first, after_second)

    def test_a_backward_step_does_not_refire_a_consumed_occurrence(self):
        due = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.schedule(at=iso(due))
        self.service._evaluate()
        self.assertEqual(len(self.recorder.of("skill.a.ring")), 1)

        rewound = due - timedelta(hours=2)
        with patch.object(ScheduledEventService, "now",
                          staticmethod(lambda: rewound)):
            self.service._evaluate()
        self.assertEqual(len(self.recorder.of("skill.a.ring")), 1)

    def test_a_step_is_detected_against_the_monotonic_clock(self):
        self.service._wall_reference = time.time() - 3600
        self.service._mono_reference = time.monotonic()
        self.assertGreater(abs(self.service._clock_step()), 2)
        self.assertEqual(self.service._clock_step(), 0.0)

    def test_an_unsynchronized_clock_defers_rather_than_drops(self):
        # the wall clock reads before the newest instant a past run recorded
        self.service.newest_past_instant = (datetime.now(timezone.utc) +
                                            timedelta(days=400))
        self.service.clock_synced = False
        due = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertTrue(self.schedule(at=iso(due)).data["ok"])
        self.service._evaluate()
        self.assertEqual(self.recorder.of("skill.a.ring"), [])

        self.bus.emit(Message(topics.CLOCK_SYNCED))
        self.assertEqual(len(self.recorder.of("skill.a.ring")), 1)
        self.assertEqual(
            self.recorder.last(topics.SCHEDULER_READY).data["clock"],
            "synchronized")

    def test_the_clock_syncs_when_the_wall_clock_passes_the_stored_instant(self):
        self.service.newest_past_instant = (datetime.now(timezone.utc) -
                                            timedelta(days=1))
        self.service.clock_synced = False
        self.service.tick()
        self.assertTrue(self.service.clock_synced)
        self.assertIn(topics.SCHEDULER_READY, self.recorder.types())


class TestRelativeDelay(SchedulerTestCase):
    """``in`` runs off the monotonic clock, so a wall step cannot move it."""

    def test_a_wall_clock_step_does_not_fire_a_relative_delay_early(self):
        self.schedule(**{"in": {"seconds": 300}})
        jumped = datetime.now(timezone.utc) + timedelta(hours=5)
        with patch.object(ScheduledEventService, "now",
                          staticmethod(lambda: jumped)):
            self.service._evaluate()
        self.assertEqual(self.recorder.of("skill.a.ring"), [])

    def test_a_relative_delay_fires_when_its_monotonic_deadline_passes(self):
        self.schedule(**{"in": {"seconds": 300}})
        self.service.schedules["skill.a", "one"].deadline = time.monotonic() - 1
        self.service._evaluate()
        self.assertEqual(len(self.recorder.of("skill.a.ring")), 1)
        self.assertEqual(self.service.schedules, {})

    def test_a_relative_delay_is_persisted_as_a_wall_clock_estimate(self):
        self.schedule(**{"in": {"seconds": 300}})
        entry = self.store_content()["schedules"][0]
        self.assertIsNotNone(entry["estimate"])
        self.assertEqual(entry["record"]["in"], {"seconds": 300})

    def test_until_and_count_are_refused_for_a_relative_delay(self):
        answer = self.schedule(count=2, **{"in": {"seconds": 300}})
        self.assertEqual(answer.data["error"], "invalid_record")


class TestMisfireBounds(SchedulerTestCase):
    """Occurrences the misfire policy drops still count against ``count``."""

    def test_skipped_occurrences_are_consumed_against_count(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=25)
        self.schedule(id="tick", event="skill.a.tick", misfire="skip",
                      grace_s=0, count=3,
                      every={"seconds": 10, "start": iso(start)})
        self.service._evaluate()
        self.assertEqual(self.recorder.of("skill.a.tick"), [])
        # three occurrences passed; the bound is spent and the record is gone
        self.assertEqual(self.service.schedules, {})

    def test_all_respects_until(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=55)
        self.schedule(id="tick", event="skill.a.tick", misfire="all", grace_s=0,
                      until=iso(start + timedelta(seconds=25)),
                      every={"seconds": 10, "start": iso(start)})
        self.service._evaluate()
        self.assertEqual(len(self.recorder.of("skill.a.tick")), 3)

    def test_a_long_downtime_caps_and_flags_the_missed_report(self):
        backlog = 5 * MAX_REPORTED
        start = datetime.now(timezone.utc) - timedelta(seconds=backlog)
        self.schedule(id="tick", event="skill.a.tick", misfire="skip",
                      grace_s=0, every={"seconds": 1, "start": iso(start)})
        self.service._evaluate()
        missed = self.recorder.last(topics.SCHEDULER_MISSED)
        self.assertEqual(len(missed.data["missed"]), MAX_REPORTED)
        self.assertTrue(missed.data["truncated"])
        # the whole backlog was consumed even though only part was reported
        self.assertGreaterEqual(
            self.service.schedules["skill.a", "tick"].consumed, backlog)

    def test_a_backlog_inside_the_cap_is_not_flagged(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=5)
        self.schedule(id="tick", event="skill.a.tick", misfire="skip",
                      grace_s=0, every={"seconds": 1, "start": iso(start)})
        self.service._evaluate()
        self.assertFalse(
            self.recorder.last(topics.SCHEDULER_MISSED).data["truncated"])

    def test_missed_dues_show_up_in_the_computed_state(self):
        due = datetime.now(timezone.utc) - timedelta(hours=2)
        self.schedule(id="tick", event="skill.a.tick", misfire="skip",
                      every={"seconds": 86400, "start": iso(due)})
        self.service._evaluate()
        read = self.request(topics.SCHEDULER_GET,
                            {"owner": "skill.a", "id": "tick"})
        self.assertEqual(read.data["state"]["missed"], [iso(due)])

    def test_a_fire_clears_the_missed_dues_that_precede_it(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=25)
        self.schedule(id="tick", event="skill.a.tick", misfire="late",
                      grace_s=0, every={"seconds": 10, "start": iso(start)})
        self.service._evaluate()
        read = self.request(topics.SCHEDULER_GET,
                            {"owner": "skill.a", "id": "tick"})
        self.assertEqual(read.data["state"]["missed"], [])
        self.assertEqual(read.data["state"]["last_fired"],
                         iso(start + timedelta(seconds=20)))

    def test_a_series_exhausted_in_the_same_evaluation_reports_no_next(self):
        # the report goes out before the fires it announces, so a next read
        # from the pre-batch tally would name an occurrence count forbids
        start = datetime.now(timezone.utc) - timedelta(seconds=25)
        self.schedule(id="tick", event="skill.a.tick", misfire="all",
                      grace_s=0, count=3,
                      every={"seconds": 10, "start": iso(start)})
        self.service._evaluate()
        self.assertEqual(len(self.recorder.of("skill.a.tick")), 3)
        self.assertIsNone(self.recorder.last(topics.SCHEDULER_MISSED).data["next"])
        self.assertEqual(self.service.schedules, {})

    def test_a_series_that_continues_still_reports_its_next(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=25)
        self.schedule(id="tick", event="skill.a.tick", misfire="skip",
                      grace_s=0, count=10,
                      every={"seconds": 10, "start": iso(start)})
        self.service._evaluate()
        self.assertIsNotNone(
            self.recorder.last(topics.SCHEDULER_MISSED).data["next"])

    def test_replay_reports_every_occurrence_it_produces(self):
        # an occurrence emitted just before a crash may have lost its record,
        # so replay reports it even when it is inside the grace period
        start = datetime.now(timezone.utc) - timedelta(seconds=5)
        self.schedule(id="tick", event="skill.a.tick", grace_s=600,
                      every={"seconds": 10, "start": iso(start)})
        self.service.replay()
        missed = self.recorder.last(topics.SCHEDULER_MISSED)
        self.assertEqual(missed.data["missed"], [iso(start)])
        self.assertEqual(missed.data["fired_late"], [])
        self.assertEqual(len(self.recorder.of("skill.a.tick")), 1)


class TestLegacyAdapter(SchedulerTestCase):
    """The pre-specification protocol keeps working for one stable cycle."""

    def emit_legacy_schedule(self, context=None, **data):
        self.bus.emit(Message(topics.LEGACY_SCHEDULE, data, context or {}))

    def test_schedule_and_fire_through_the_legacy_topic(self):
        self.emit_legacy_schedule(event="skill.a:ring", time=time.time() - 1,
                                  data={"k": 1})
        self.service._evaluate()
        self.assertEqual(self.recorder.last("skill.a:ring").data, {"k": 1})

    def test_scheduling_an_existing_name_replaces_instead_of_stacking(self):
        for _ in range(3):
            self.emit_legacy_schedule(event="skill.a:ring",
                                      time=time.time() + 3600)
        self.assertEqual(len(self.service.schedules), 1)
        self.service._evaluate()
        self.assertEqual(self.recorder.of("skill.a:ring"), [])

    def test_a_legacy_schedule_fires_with_the_context_it_was_made_with(self):
        # these owners predate §4.2 and were promised their own context back;
        # the specification path stays fresh, this one does not
        self.emit_legacy_schedule(event="skill.a:ring", time=time.time() - 1,
                                  context={"skill_id": "skill.a",
                                           "session": {"session_id": "abc"}})
        self.service._evaluate()
        fired = self.recorder.last("skill.a:ring")
        self.assertEqual(fired.context["skill_id"], "skill.a")
        self.assertEqual(fired.context["scheduler"]["id"], "skill.a:ring")
        # whole, session and all, as the scheduler this replaced did it and
        # as a schedule made through the specified protocol does it
        self.assertEqual(fired.context["session"]["session_id"], "abc")

    def test_a_legacy_repeat_carries_its_context_to_every_occurrence(self):
        start = time.time() - 25
        self.emit_legacy_schedule(event="skill.a:tick", time=start, repeat=10,
                                  context={"mine": True})
        self.service._evaluate()
        fired = self.recorder.of("skill.a:tick")
        self.assertTrue(fired)
        self.assertTrue(all(m.context["mine"] for m in fired))

    def test_a_legacy_context_survives_a_restart(self):
        self.emit_legacy_schedule(event="skill.a:ring", time=time.time() + 1,
                                  context={"mine": True})
        revived = self.new_scheduler()
        revived._evaluate()
        time.sleep(1.1)
        revived._evaluate()
        self.assertTrue(self.recorder.last("skill.a:ring").context["mine"])

    def test_a_stored_legacy_context_keeps_its_session(self):
        self.emit_legacy_schedule(event="skill.a:ring", time=time.time() + 3600,
                                  context={"mine": True,
                                           "session": {"session_id": "abc"}})
        record = self.service.schedules["skill.a", "skill.a:ring"].record
        self.assertEqual(record["context"]["mine"], True)
        self.assertEqual(record["context"]["session"]["session_id"], "abc")

    def test_the_get_reply_carries_the_stored_context(self):
        self.emit_legacy_schedule(event="skill.a:ring", time=time.time() + 3600,
                                  context={"mine": True})
        self.bus.emit(Message(topics.LEGACY_GET, {"name": "skill.a:ring"}))
        reply = self.recorder.last(
            f"{topics.LEGACY_GET_REPLY_PREFIX}skill.a:ring")
        self.assertTrue(reply.data["schedule"][3]["mine"])

    def test_a_legacy_context_is_stored_the_way_any_other_one_is(self):
        # both protocols reach the same field: the adapter is a translation,
        # not a second set of rules about context
        self.emit_legacy_schedule(event="skill.a:ring", time=time.time() + 3600,
                                  context={"mine": True})
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)),
                      request_context={"mine": True})
        for key in (("skill.a", "skill.a:ring"), ("skill.a", "one")):
            self.assertTrue(self.service.schedules[key].record["context"]["mine"])

    def test_remove_event(self):
        self.emit_legacy_schedule(event="skill.a:ring", time=time.time() + 3600)
        self.bus.emit(Message(topics.LEGACY_REMOVE, {"event": "skill.a:ring"}))
        self.assertEqual(self.service.schedules, {})

    def test_update_event_replaces_the_data(self):
        self.emit_legacy_schedule(event="skill.a:ring", time=time.time() + 3600,
                                  data={"k": 1})
        self.bus.emit(Message(topics.LEGACY_UPDATE,
                              {"event": "skill.a:ring", "data": {"k": 2}}))
        self.assertEqual(
            self.service.schedules["skill.a", "skill.a:ring"].record["data"],
            {"k": 2})

    def test_get_event_answers_on_its_callback_topic(self):
        when = time.time() + 3600
        self.emit_legacy_schedule(event="skill.a:ring", time=when)
        self.bus.emit(Message(topics.LEGACY_GET, {"name": "skill.a:ring"}))
        reply = self.recorder.last(
            f"{topics.LEGACY_GET_REPLY_PREFIX}skill.a:ring")
        # the schedule travels under a key: the wire refuses a list payload
        self.assertAlmostEqual(reply.data["schedule"][0], when, places=0)

    def test_list_events_keeps_its_reply_shape(self):
        self.emit_legacy_schedule(event="skill.a:ring", time=time.time() + 3600)
        self.bus.emit(Message(topics.LEGACY_LIST, {},
                              {"source": ["x"], "destination": ["y"]}))
        listed = [m for m in self.recorder.messages if "scheduled_events" in m.data]
        self.assertIn("skill.a:ring", listed[-1].data["scheduled_events"])

    def test_a_repeat_becomes_a_fixed_period_recurrence(self):
        self.emit_legacy_schedule(event="skill.a:tick", time=time.time() + 60,
                                  repeat=30)
        record = self.service.schedules["skill.a", "skill.a:tick"].record
        self.assertEqual(record["every"]["seconds"], 30)

    def test_an_unnamespaced_event_belongs_to_no_component_in_particular(self):
        self.emit_legacy_schedule(event="bare", time=time.time() + 60)
        self.assertIn(("legacy", "bare"), self.service.schedules)

    def test_the_deprecation_notice_names_the_removal_version(self):
        with patch("ovos_bus_client.util.scheduled_events.legacy.LOG") as log:
            self.emit_legacy_schedule(event="skill.a:ring", time=time.time() + 60)
            self.emit_legacy_schedule(event="skill.a:other", time=time.time() + 60)
        notices = [call.args[0] for call in log.warning.call_args_list]
        # warned once for the topic, naming the release that drops it
        self.assertEqual(len(notices), 1)
        self.assertIn(LEGACY_REMOVAL_VERSION, notices[0])

    def test_the_store_moves_out_of_the_configuration_directory(self):
        with tempfile.TemporaryDirectory() as config_dir:
            source = os.path.join(config_dir, "schedule.json")
            with open(source, "w") as handle:
                json.dump({"skill.a:ring": [[time.time() + 3600, None, {}, {}]]},
                          handle)
            if os.path.isfile(self.store):
                os.unlink(self.store)

            with patch("ovos_bus_client.util.scheduled_events.legacy."
                       "get_xdg_config_save_path", return_value=config_dir):
                migrated = self.new_scheduler()
                self.assertIn(("skill.a", "skill.a:ring"), migrated.schedules)
                self.assertTrue(os.path.isfile(self.store))
                # the original stays put so a downgrade still finds it
                self.assertTrue(os.path.isfile(source))
                self.assertTrue(os.path.isfile(f"{source}.migrated"))

                self.assertEqual(len(self.new_scheduler().schedules), 1)


class TestSubscriptions(SchedulerTestCase):
    def test_shutdown_leaves_an_unrelated_observer_subscribed(self):
        seen = []
        self.bus.on(topics.SCHEDULER_SCHEDULE, seen.append)
        self.service.shutdown()
        self.bus.emit(Message(topics.SCHEDULER_SCHEDULE, {}))
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.recorder.of(topics.SCHEDULER_SCHEDULE_RESPONSE), [])


class TestInstants(unittest.TestCase):
    def test_an_instant_round_trips(self):
        when = datetime(2031, 3, 29, 7, 30, tzinfo=timezone.utc)
        self.assertEqual(format_instant(when), "2031-03-29T07:30:00+00:00")

    def test_a_zulu_suffix_is_accepted(self):
        record = validate_record({"id": "x", "owner": "s", "event": "s.e",
                                  "at": "2031-03-29T07:30:00Z"})
        self.assertEqual(record["at"], "2031-03-29T07:30:00+00:00")


if __name__ == "__main__":
    unittest.main()
