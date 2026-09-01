"""SCHEDULER-1 conformance suite — one test per MUST of §9."""
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from ovos_utils.fakebus import FakeBus

from ovos_bus_client.message import Message
from ovos_bus_client.util.scheduled_events import (
    LEGACY_REMOVAL_VERSION, MAX_DATA_BYTES, MAX_REPORTED, Schedule,
    ScheduledEventService, format_instant, validate_record)


def iso(when: datetime) -> str:
    return when.isoformat()


class Recorder:
    """Captures every message the service emits, in order."""

    def __init__(self, bus):
        self.messages = []
        bus.on("message", self._on)

    def _on(self, message):
        if isinstance(message, str):
            message = Message.deserialize(message)
        self.messages.append(message)

    def types(self):
        return [m.msg_type for m in self.messages]

    def of(self, msg_type):
        return [m for m in self.messages if m.msg_type == msg_type]

    def last(self, msg_type):
        found = self.of(msg_type)
        return found[-1] if found else None


class SchedulerTestCase(unittest.TestCase):
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

    def request(self, topic, data, context=None, service=None):
        service = service or self.service
        message = Message(topic, data, context or {})
        getattr(service, f"handle_{topic.split('.')[1]}")(message)
        return self.recorder.last(f"{topic}.response")

    def schedule(self, **data):
        data.setdefault("owner", "skill.a")
        data.setdefault("id", "one")
        data.setdefault("event", "skill.a.ring")
        return self.request("scheduler.schedule", data)


class TestValidationAndAnswers(SchedulerTestCase):
    """§9.1 — validate every field and answer every request exactly once."""

    def test_every_request_is_answered_once(self):
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        self.assertEqual(len(self.recorder.of("scheduler.schedule.response")), 1)
        self.request("scheduler.get", {"owner": "skill.a", "id": "one"})
        self.assertEqual(len(self.recorder.of("scheduler.get.response")), 1)

    def test_naive_instant_is_rejected(self):
        response = self.schedule(at="2031-03-29T07:30:00")
        self.assertFalse(response.data["ok"])
        self.assertEqual(response.data["error"], "bad_instant")

    def test_two_timing_fields_are_rejected(self):
        response = self.schedule(at=iso(datetime.now(timezone.utc)),
                                 every={"seconds": 10})
        self.assertEqual(response.data["error"], "invalid_record")

    def test_missing_timing_is_rejected(self):
        self.assertEqual(self.schedule().data["error"], "invalid_record")

    def test_an_event_with_a_colon_in_the_name_is_rejected(self):
        response = self.schedule(event="skill.a.ring:now",
                                 at=iso(datetime.now(timezone.utc)))
        self.assertEqual(response.data["error"], "bad_event")

    def test_an_event_that_is_only_the_owner_is_rejected(self):
        response = self.schedule(event="skill.a.",
                                 at=iso(datetime.now(timezone.utc)))
        self.assertEqual(response.data["error"], "bad_event")

    def test_bad_recurrence_is_rejected(self):
        self.assertEqual(self.schedule(every={"seconds": 0}).data["error"],
                         "bad_recurrence")
        self.assertEqual(
            self.schedule(local={"time": "07:30", "zone": "Mars/Olympus"}).data["error"],
            "bad_recurrence")

    def test_payload_cap(self):
        response = self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)),
                                 data={"blob": "x" * (MAX_DATA_BYTES + 100)})
        self.assertEqual(response.data["error"], "payload_too_large")

    def test_until_with_one_shot_is_rejected(self):
        response = self.schedule(at=iso(datetime.now(timezone.utc)),
                                 until=iso(datetime.now(timezone.utc)))
        self.assertEqual(response.data["error"], "invalid_record")

    def test_cancel_of_absent_schedule_is_not_an_error(self):
        response = self.request("scheduler.cancel",
                                {"owner": "skill.a", "id": "nope"})
        self.assertTrue(response.data["ok"])
        self.assertFalse(response.data["existed"])

    def test_round_trip_schedule_get_list_cancel(self):
        when = datetime.now(timezone.utc) + timedelta(hours=1)
        created = self.schedule(at=iso(when))
        self.assertTrue(created.data["ok"])
        self.assertEqual(created.data["next"], iso(when))

        got = self.request("scheduler.get", {"owner": "skill.a", "id": "one"})
        self.assertEqual(got.data["record"]["event"], "skill.a.ring")
        self.assertEqual(got.data["state"]["next"], iso(when))
        self.assertIsNone(got.data["state"]["last_fired"])
        self.assertEqual(got.data["state"]["missed"], [])

        listed = self.request("scheduler.list", {"owner": "skill.a"})
        self.assertEqual([s["record"]["id"] for s in listed.data["schedules"]],
                         ["one"])

        cancelled = self.request("scheduler.cancel",
                                 {"owner": "skill.a", "id": "one"})
        self.assertTrue(cancelled.data["existed"])
        listed = self.request("scheduler.list", {"owner": "skill.a"})
        self.assertEqual(listed.data["schedules"], [])


class TestPersistence(SchedulerTestCase):
    """§9.2 — persist before answering and before firing, atomically."""

    def test_store_is_written_before_the_response(self):
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        with open(self.store) as handle:
            stored = json.load(handle)
        self.assertEqual(stored["schedules"][0]["record"]["id"], "one")

    def test_the_due_of_a_fired_occurrence_is_persisted_after_the_event(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=5)
        self.schedule(id="tick", event="skill.a.tick", grace_s=600,
                      every={"seconds": 10, "start": iso(start)})
        self.service._evaluate()
        self.assertEqual(len(self.recorder.of("skill.a.tick")), 1)
        with open(self.store) as handle:
            stored = json.load(handle)
        self.assertEqual(stored["schedules"][0]["last_fired"], iso(start))

    def test_an_occurrence_at_or_before_the_persisted_due_never_fires_again(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=5)
        self.schedule(id="tick", event="skill.a.tick", grace_s=600,
                      every={"seconds": 10, "start": iso(start)})
        self.service._evaluate()
        schedule = self.service.schedules["skill.a", "tick"]
        # a replacement store handed a stale cursor must not replay the fire
        schedule.cursor = start - timedelta(seconds=60)
        self.service._evaluate()
        self.assertEqual(len(self.recorder.of("skill.a.tick")), 1)

    def test_a_crash_between_the_temp_write_and_the_replace_keeps_old_state(self):
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        before = open(self.store).read()
        with patch("os.replace", side_effect=OSError("power loss")):
            response = self.schedule(id="two",
                                     at=iso(datetime.now(timezone.utc) +
                                            timedelta(hours=2)))
        self.assertEqual(open(self.store).read(), before)
        self.assertFalse(response.data["ok"])
        self.assertEqual(response.data["error"], "internal")

    def test_an_unwritable_store_still_answers_a_cancel(self):
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        with patch("os.replace", side_effect=OSError("read-only")):
            response = self.request("scheduler.cancel",
                                    {"owner": "skill.a", "id": "one"})
        self.assertEqual(response.data["error"], "internal")

    def test_an_unstorable_creation_leaves_no_trace_in_memory(self):
        with patch("os.replace", side_effect=OSError("read-only")):
            response = self.schedule(
                at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        self.assertEqual(response.data["error"], "internal")
        # the running process agrees with the store it would restart from
        self.assertEqual(self.service.schedules, {})

    def test_an_unstorable_replacement_keeps_the_previous_schedule(self):
        first = datetime.now(timezone.utc) + timedelta(hours=1)
        self.schedule(at=iso(first))
        with patch("os.replace", side_effect=OSError("read-only")):
            response = self.schedule(
                at=iso(datetime.now(timezone.utc) + timedelta(hours=5)))
        self.assertEqual(response.data["error"], "internal")
        self.assertEqual(
            self.service.schedules["skill.a", "one"].record["at"], iso(first))

    def test_an_unstorable_cancel_keeps_the_schedule(self):
        when = iso(datetime.now(timezone.utc) + timedelta(hours=1))
        self.schedule(at=when)
        with patch("os.replace", side_effect=OSError("read-only")):
            response = self.request("scheduler.cancel",
                                    {"owner": "skill.a", "id": "one"})
        self.assertEqual(response.data["error"], "internal")
        self.assertIn(("skill.a", "one"), self.service.schedules)
        # and it is still cancellable once the store is writable again
        self.assertTrue(self.request("scheduler.cancel",
                                     {"owner": "skill.a", "id": "one"}
                                     ).data["existed"])

    def test_an_unstorable_wildcard_cancel_keeps_every_schedule(self):
        when = iso(datetime.now(timezone.utc) + timedelta(hours=1))
        self.schedule(owner="skill.a", event="skill.a.ring", at=when)
        self.schedule(owner="skill.b", event="skill.b.ring", at=when)
        self.service.admins = ["admin.panel"]
        with patch("os.replace", side_effect=OSError("read-only")):
            response = self.request("scheduler.cancel", {"owner": "*", "id": "one"},
                                    context={"skill_id": "admin.panel"})
        self.assertEqual(response.data["error"], "internal")
        self.assertEqual(len(self.service.schedules), 2)

    def test_a_corrupt_store_timestamp_does_not_stop_the_service(self):
        self.schedule(at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        with open(self.store) as handle:
            stored = json.load(handle)
        stored["written_at"] = "nonsense"
        with open(self.store, "w") as handle:
            json.dump(stored, handle)
        revived = ScheduledEventService(self.bus, store_path=self.store,
                                        autostart=False)
        self.addCleanup(revived.shutdown)
        self.assertIn(("skill.a", "one"), revived.schedules)
        self.assertTrue(revived._clock_synced)

    def test_one_fire_is_persisted_before_the_next_is_emitted(self):
        # a kill anywhere inside a backlog may repeat the occurrence that
        # was in flight, and nothing earlier
        start = datetime.now(timezone.utc) - timedelta(seconds=35)
        self.schedule(id="tick", event="skill.a.tick", misfire="all",
                      grace_s=0, every={"seconds": 10, "start": iso(start)})
        # the store as it stands the instant after each event leaves the
        # bus, which is where a kill can land
        kill_points = []
        real_fire = self.service._fire

        def watched(schedule, due):
            real_fire(schedule, due)
            kill_points.append((format_instant(due), open(self.store).read()))

        self.service._fire = watched
        self.service._evaluate()
        fired = [due for due, _ in kill_points]
        self.assertEqual(len(fired), 4)

        for position, (due, state) in enumerate(kill_points):
            with open(self.store, "w") as handle:
                handle.write(state)
            bus = FakeBus()
            recorder = Recorder(bus)
            revived = ScheduledEventService(bus, store_path=self.store,
                                            autostart=False)
            revived.replay()
            already = set(fired[:position + 1])
            repeated = [m for m in recorder.of("skill.a.tick")
                        if m.context["scheduler"]["due"] in already]
            revived.shutdown()
            self.assertLessEqual(
                len(repeated), 1,
                f"a kill just after the fire for {due} repeated "
                f"{len(repeated)} already-emitted occurrences")

    def test_ephemeral_schedules_are_never_persisted(self):
        self.schedule(id="kept",
                      at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        self.schedule(id="gone", ephemeral=True,
                      at=iso(datetime.now(timezone.utc) + timedelta(hours=1)))
        with open(self.store) as handle:
            stored = json.load(handle)
        self.assertEqual([s["record"]["id"] for s in stored["schedules"]], ["kept"])

        revived = ScheduledEventService(self.bus, store_path=self.store,
                                        autostart=False)
        self.assertEqual([k[1] for k in revived.schedules], ["kept"])
        revived.shutdown()


class TestIdempotency(SchedulerTestCase):
    """§9.3 — replace on identical identity, never duplicate."""

    def test_rescheduling_the_same_identity_replaces(self):
        first = datetime.now(timezone.utc) + timedelta(hours=1)
        second = datetime.now(timezone.utc) + timedelta(hours=2)
        self.assertFalse(self.schedule(at=iso(first)).data["replaced"])
        response = self.schedule(at=iso(second))
        self.assertTrue(response.data["replaced"])
        self.assertEqual(response.data["next"], iso(second))
        self.assertEqual(len(self.service.schedules), 1)

    def test_two_owners_may_use_the_same_id(self):
        when = iso(datetime.now(timezone.utc) + timedelta(hours=1))
        self.schedule(owner="skill.a", event="skill.a.ring", at=when)
        self.schedule(owner="skill.b", event="skill.b.ring", at=when)
        self.assertEqual(len(self.service.schedules), 2)


class TestReplay(SchedulerTestCase):
    """§9.4 and §9.6 — restore, apply the misfire policy, announce ready."""

    def _downtime(self, misfire, **extra):
        """Persist a schedule due in the past, then start a fresh service."""
        due = datetime.now(timezone.utc) - timedelta(minutes=30)
        record = validate_record(dict(
            {"id": "one", "owner": "skill.a", "event": "skill.a.ring",
             "misfire": misfire, "at": iso(due)}, **extra))
        service = ScheduledEventService(self.bus, store_path=self.store,
                                        autostart=False)
        service.schedules[record["owner"], record["id"]] = Schedule(record)
        service._persist()
        service.shutdown()
        self.recorder.messages.clear()
        revived = ScheduledEventService(self.bus, store_path=self.store,
                                        autostart=False)
        revived.replay()
        return revived, due

    def test_late_policy_fires_the_missed_one_shot_and_reports_it(self):
        revived, due = self._downtime("late")
        self.addCleanup(revived.shutdown)
        missed = self.recorder.last("scheduler.missed")
        self.assertEqual(missed.data["missed"], [iso(due)])
        self.assertEqual(missed.data["fired_late"], [iso(due)])
        fired = self.recorder.last("skill.a.ring")
        self.assertEqual(fired.context["scheduler"]["due"], iso(due))
        self.assertEqual(
            self.recorder.types(),
            ["scheduler.ready", "scheduler.missed", "skill.a.ring"])
        ready = self.recorder.last("scheduler.ready")
        # the count is what the store held, before replay consumed anything
        self.assertEqual(ready.data, {"schedules": 1, "missed": 1,
                                      "clock": "synchronized"})

    def test_ready_counts_what_the_store_held_not_what_survives(self):
        now = datetime.now(timezone.utc)
        service = ScheduledEventService(self.bus, store_path=self.store,
                                        autostart=False)
        for name, when in (("overdue", now - timedelta(minutes=30)),
                           ("later", now + timedelta(hours=1))):
            record = validate_record({"id": name, "owner": "skill.a",
                                      "event": "skill.a.ring", "at": iso(when)})
            service.schedules["skill.a", name] = Schedule(record)
        service._persist()
        service.shutdown()
        self.recorder.messages.clear()

        revived = ScheduledEventService(self.bus, store_path=self.store,
                                        autostart=False)
        self.addCleanup(revived.shutdown)
        revived.replay()
        self.assertEqual(self.recorder.last("scheduler.ready").data["schedules"], 2)

    def test_skip_policy_reports_but_does_not_fire(self):
        revived, due = self._downtime("skip")
        self.addCleanup(revived.shutdown)
        self.assertEqual(self.recorder.of("skill.a.ring"), [])
        self.assertEqual(self.recorder.last("scheduler.missed").data["fired_late"], [])
        self.assertIn("scheduler.ready", self.recorder.types())

    def test_all_policy_fires_every_missed_occurrence_oldest_first(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=50)
        record = validate_record(
            {"id": "tick", "owner": "skill.a", "event": "skill.a.tick",
             "misfire": "all", "grace_s": 0, "count": 4,
             "every": {"seconds": 10, "start": iso(start)}})
        service = ScheduledEventService(self.bus, store_path=self.store,
                                        autostart=False)
        service.schedules[record["owner"], record["id"]] = Schedule(record)
        service._persist()
        service.shutdown()
        self.recorder.messages.clear()

        revived = ScheduledEventService(self.bus, store_path=self.store,
                                        autostart=False)
        self.addCleanup(revived.shutdown)
        revived.replay()
        dues = [m.context["scheduler"]["due"]
                for m in self.recorder.of("skill.a.tick")]
        self.assertEqual(dues, sorted(dues))
        self.assertEqual(len(dues), 4)
        self.assertEqual(dues[0], iso(start))

    def test_one_shot_is_deleted_after_it_is_reported(self):
        revived, _ = self._downtime("skip")
        self.addCleanup(revived.shutdown)
        self.assertEqual(revived.schedules, {})

    def test_a_recurring_schedule_survives_a_restart(self):
        self.schedule(id="tick", event="skill.a.tick",
                      every={"seconds": 3600,
                             "start": iso(datetime.now(timezone.utc) +
                                          timedelta(hours=1))})
        self.service.shutdown()
        revived = ScheduledEventService(self.bus, store_path=self.store,
                                        autostart=False)
        self.addCleanup(revived.shutdown)
        self.assertIn(("skill.a", "tick"), revived.schedules)


class TestFiredMessage(SchedulerTestCase):
    """§9.5 — a fresh context carrying the scheduler fields, no session."""

    def test_fired_event_carries_only_scheduler_context(self):
        due = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.schedule(at=iso(due), data={"key": "value"},
                      context={"session": {"session_id": "abc"},
                               "skill_id": "skill.a"})
        self.service._evaluate()
        fired = self.recorder.last("skill.a.ring")
        self.assertEqual(fired.data, {"key": "value"})
        self.assertNotEqual(
            fired.context.get("session", {}).get("session_id"), "abc")
        self.assertEqual(fired.context["scheduler"]["id"], "one")
        self.assertEqual(fired.context["scheduler"]["owner"], "skill.a")
        self.assertEqual(fired.context["scheduler"]["due"], iso(due))

    def test_remaining_counts_down_across_a_late_batch(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=25)
        self.schedule(id="tick", event="skill.a.tick", count=10, misfire="all",
                      grace_s=0, every={"seconds": 10, "start": iso(start)})
        self.service._evaluate()
        remaining = [m.context["scheduler"]["remaining"]
                     for m in self.recorder.of("skill.a.tick")]
        self.assertEqual(remaining, [9, 8, 7])


class TestRecurrence(SchedulerTestCase):
    """§9.7 — anchored periods, wall-clock recurrence with the DST rules."""

    def test_a_late_fire_does_not_shift_the_following_occurrences(self):
        start = datetime(2031, 1, 1, 0, 0, tzinfo=timezone.utc)
        record = validate_record(
            {"id": "tick", "owner": "skill.a", "event": "skill.a.tick",
             "every": {"seconds": 600, "start": iso(start)}})
        schedule = Schedule(record)
        # the fire for 00:10 happens five minutes late; the next occurrence
        # is still on the schedule's own phase, not five minutes behind it
        late_fire = start + timedelta(minutes=15)
        schedule.cursor = start + timedelta(minutes=10)
        self.assertEqual(schedule.next_after(late_fire),
                         start + timedelta(minutes=20))

    def test_local_recurrence_across_a_spring_forward_gap(self):
        # Europe/Lisbon jumps 01:00 -> 02:00 on 2031-03-30, so 01:30 does
        # not exist and the occurrence is the first instant after the gap
        record = validate_record(
            {"id": "x", "owner": "skill.a", "event": "skill.a.x",
             "local": {"time": "01:30", "zone": "Europe/Lisbon"}})
        occurrence = Schedule(record).next_after(
            datetime(2031, 3, 29, 12, tzinfo=timezone.utc))
        lisbon = occurrence.astimezone(ZoneInfo("Europe/Lisbon"))
        self.assertEqual(lisbon.date().isoformat(), "2031-03-30")
        self.assertEqual(lisbon.strftime("%H:%M"), "02:00")

    def test_local_recurrence_across_a_fall_back_overlap(self):
        # America/New_York reads 01:30 twice on 2031-11-02; the occurrence
        # is the first of the two, still on daylight time
        record = validate_record(
            {"id": "x", "owner": "skill.a", "event": "skill.a.x",
             "local": {"time": "01:30", "zone": "America/New_York"}})
        occurrence = Schedule(record).next_after(
            datetime(2031, 11, 1, 12, tzinfo=timezone.utc))
        self.assertEqual(occurrence.utcoffset(), timedelta(hours=-4))
        self.assertEqual(
            occurrence.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M"),
            "01:30")

    def test_local_recurrence_honours_the_day_list(self):
        record = validate_record(
            {"id": "x", "owner": "skill.a", "event": "skill.a.x",
             "local": {"time": "07:30", "zone": "Europe/Lisbon",
                       "days": ["mon"]}})
        schedule = Schedule(record)
        # 2031-01-01 is a Wednesday; the next Monday is the 6th
        occurrence = schedule.next_after(
            datetime(2031, 1, 1, 12, tzinfo=timezone.utc))
        self.assertEqual(
            occurrence.astimezone(ZoneInfo("Europe/Lisbon")).date().isoformat(),
            "2031-01-06")

    def test_until_stops_the_recurrence(self):
        start = datetime(2031, 1, 1, tzinfo=timezone.utc)
        record = validate_record(
            {"id": "x", "owner": "skill.a", "event": "skill.a.x",
             "until": iso(start + timedelta(seconds=25)),
             "every": {"seconds": 10, "start": iso(start)}})
        schedule = Schedule(record)
        schedule.cursor = start + timedelta(seconds=20)
        self.assertIsNone(schedule.next_after(schedule.cursor))

    def test_count_stops_the_recurrence(self):
        start = datetime(2031, 1, 1, tzinfo=timezone.utc)
        record = validate_record(
            {"id": "x", "owner": "skill.a", "event": "skill.a.x", "count": 2,
             "every": {"seconds": 10, "start": iso(start)}})
        schedule = Schedule(record)
        schedule.consumed = 2
        self.assertIsNone(schedule.next_after(start))


class TestOwnership(SchedulerTestCase):
    """§9.8 — the event namespace and owner scoping."""

    def test_an_event_outside_the_owner_namespace_is_rejected(self):
        response = self.schedule(event="mycroft.stop",
                                 at=iso(datetime.now(timezone.utc)))
        self.assertEqual(response.data["error"], "bad_event")

    def test_an_authenticated_identity_may_not_act_for_another_owner(self):
        response = self.request(
            "scheduler.schedule",
            {"owner": "skill.a", "id": "one", "event": "skill.a.ring",
             "at": iso(datetime.now(timezone.utc) + timedelta(hours=1))},
            context={"skill_id": "skill.b"})
        self.assertEqual(response.data["error"], "not_owner")

    def test_cancel_and_get_are_scoped_by_owner(self):
        when = iso(datetime.now(timezone.utc) + timedelta(hours=1))
        self.schedule(owner="skill.a", event="skill.a.ring", at=when)
        cancelled = self.request("scheduler.cancel",
                                 {"owner": "skill.b", "id": "one"})
        self.assertFalse(cancelled.data["existed"])
        self.assertIn(("skill.a", "one"), self.service.schedules)
        got = self.request("scheduler.get", {"owner": "skill.b", "id": "one"})
        self.assertIsNone(got.data["record"])
        self.assertFalse(got.data["existed"])

    def test_list_shows_only_the_callers_schedules(self):
        when = iso(datetime.now(timezone.utc) + timedelta(hours=1))
        self.schedule(owner="skill.a", event="skill.a.ring", at=when)
        self.schedule(owner="skill.b", event="skill.b.ring", at=when)
        listed = self.request("scheduler.list", {"owner": "skill.b"})
        self.assertEqual([s["record"]["owner"] for s in listed.data["schedules"]],
                         ["skill.b"])


class TestClock(SchedulerTestCase):
    """§9.9 — clock steps never double-fire; an unsynchronized clock defers."""

    def test_a_forward_step_fires_each_occurrence_once(self):
        start = datetime.now(timezone.utc) + timedelta(seconds=10)
        self.schedule(id="tick", event="skill.a.tick", misfire="all",
                      grace_s=0, every={"seconds": 10, "start": iso(start)})
        self.service._evaluate()
        self.assertEqual(self.recorder.of("skill.a.tick"), [])

        jumped = start + timedelta(seconds=25)
        with patch.object(ScheduledEventService, "_now", staticmethod(lambda: jumped)):
            self.service._evaluate()
            first = [m.context["scheduler"]["due"]
                     for m in self.recorder.of("skill.a.tick")]
            # a second evaluation at the same instant must add nothing
            self.service._evaluate()
        second = [m.context["scheduler"]["due"]
                  for m in self.recorder.of("skill.a.tick")]
        self.assertEqual(len(first), 3)
        self.assertEqual(first, second)

    def test_a_backward_step_does_not_refire_a_consumed_occurrence(self):
        due = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.schedule(at=iso(due))
        self.service._evaluate()
        self.assertEqual(len(self.recorder.of("skill.a.ring")), 1)
        rewound = due - timedelta(hours=2)
        with patch.object(ScheduledEventService, "_now", staticmethod(lambda: rewound)):
            self.service._evaluate()
        self.assertEqual(len(self.recorder.of("skill.a.ring")), 1)

    def test_a_step_is_detected_against_the_monotonic_clock(self):
        self.service._wall_reference = time.time() - 3600
        self.service._mono_reference = time.monotonic()
        self.assertGreater(abs(self.service._clock_stepped()), 2)
        self.assertEqual(self.service._clock_stepped(), 0.0)

    def test_an_unsynchronized_clock_defers_rather_than_drops(self):
        # the wall clock reads before the newest instant a past run wrote
        self.service._written_at = datetime.now(timezone.utc) + timedelta(days=400)
        self.service._clock_synced = False
        due = datetime.now(timezone.utc) - timedelta(seconds=1)
        response = self.schedule(at=iso(due))
        self.assertTrue(response.data["ok"])
        self.service._evaluate()
        self.assertEqual(self.recorder.of("skill.a.ring"), [])

        self.service.handle_clock_synced(Message("system.clock.synced"))
        self.assertEqual(len(self.recorder.of("skill.a.ring")), 1)
        ready = self.recorder.last("scheduler.ready")
        self.assertEqual(ready.data["clock"], "synchronized")

    def test_the_clock_syncs_when_the_wall_clock_passes_the_stored_instant(self):
        self.service._written_at = datetime.now(timezone.utc) - timedelta(days=1)
        self.service._clock_synced = False
        self.service.tick()
        self.assertTrue(self.service._clock_synced)
        self.assertIn("scheduler.ready", self.recorder.types())


class TestLegacyAdapter(SchedulerTestCase):
    """The pre-specification protocol keeps working for one stable cycle."""

    def emit_legacy(self, **data):
        self.bus.emit(Message("mycroft.scheduler.schedule_event", data))

    def test_schedule_and_fire_through_the_legacy_topic(self):
        self.emit_legacy(event="skill.a:ring", time=time.time() - 1,
                         data={"k": 1})
        self.service._evaluate()
        self.assertEqual(self.recorder.last("skill.a:ring").data, {"k": 1})

    def test_legacy_one_shot_with_an_existing_name_replaces(self):
        for _ in range(3):
            self.emit_legacy(event="skill.a:ring", time=time.time() + 3600)
        self.assertEqual(len(self.service.schedules), 1)
        self.service._evaluate()
        self.assertEqual(self.recorder.of("skill.a:ring"), [])

    def test_legacy_remove_event(self):
        self.emit_legacy(event="skill.a:ring", time=time.time() + 3600)
        self.bus.emit(Message("mycroft.scheduler.remove_event",
                              {"event": "skill.a:ring"}))
        self.assertEqual(self.service.schedules, {})

    def test_legacy_update_event_replaces_the_data(self):
        self.emit_legacy(event="skill.a:ring", time=time.time() + 3600,
                         data={"k": 1})
        self.bus.emit(Message("mycroft.scheduler.update_event",
                              {"event": "skill.a:ring", "data": {"k": 2}}))
        self.assertEqual(
            self.service.schedules["skill.a", "skill.a:ring"].record["data"],
            {"k": 2})

    def test_legacy_get_event_answers_on_its_callback_topic(self):
        when = time.time() + 3600
        self.emit_legacy(event="skill.a:ring", time=when)
        self.service.handle_legacy_get(
            Message("mycroft.scheduler.get_event", {"name": "skill.a:ring"}))
        reply = self.recorder.last("mycroft.event_status.callback.skill.a:ring")
        self.assertAlmostEqual(reply.data["schedule"][0], when, places=0)

    def test_legacy_list_events_keeps_its_reply_shape(self):
        self.emit_legacy(event="skill.a:ring", time=time.time() + 3600)
        self.service.handle_legacy_list(
            Message("mycroft.scheduler.list_events", {},
                    {"source": ["x"], "destination": ["y"]}))
        listed = [m for m in self.recorder.messages if "scheduled_events" in m.data]
        self.assertIn("skill.a:ring", listed[-1].data["scheduled_events"])

    def test_a_legacy_repeat_becomes_a_fixed_period_recurrence(self):
        self.emit_legacy(event="skill.a:tick", time=time.time() + 60, repeat=30)
        record = self.service.schedules["skill.a", "skill.a:tick"].record
        self.assertEqual(record["every"]["seconds"], 30)

    def test_an_unnamespaced_legacy_event_is_owned_by_legacy(self):
        self.emit_legacy(event="bare", time=time.time() + 60)
        self.assertIn(("legacy", "bare"), self.service.schedules)

    def test_the_deprecation_notice_names_the_removal_version(self):
        with patch("ovos_bus_client.util.scheduled_events.LOG") as log:
            self.emit_legacy(event="skill.a:ring", time=time.time() + 60)
            self.emit_legacy(event="skill.a:other", time=time.time() + 60)
        notices = [c.args[0] for c in log.warning.call_args_list]
        # warned once for the topic, naming the release that drops it
        self.assertEqual(len(notices), 1)
        self.assertIn(LEGACY_REMOVAL_VERSION, notices[0])

    def test_the_store_moves_out_of_the_configuration_directory(self):
        with tempfile.TemporaryDirectory() as config_dir:
            old = os.path.join(config_dir, "schedule.json")
            with open(old, "w") as handle:
                json.dump({"skill.a:ring": [[time.time() + 3600, None, {}, {}]]},
                          handle)
            if os.path.isfile(self.store):
                os.unlink(self.store)
            with patch("ovos_bus_client.util.scheduled_events."
                       "get_xdg_config_save_path", return_value=config_dir):
                migrated = ScheduledEventService(self.bus, store_path=self.store,
                                                 autostart=False)
            self.addCleanup(migrated.shutdown)
            self.assertIn(("skill.a", "skill.a:ring"), migrated.schedules)
            self.assertTrue(os.path.isfile(self.store))
            # the original stays put so a downgrade still finds it
            self.assertTrue(os.path.isfile(old))
            self.assertTrue(os.path.isfile(f"{old}.migrated"))

            again = ScheduledEventService(self.bus, store_path=self.store,
                                          autostart=False)
            self.addCleanup(again.shutdown)
            self.assertEqual(len(again.schedules), 1)


class TestRelativeDelay(SchedulerTestCase):
    """``in`` runs off the monotonic clock, so a wall step cannot move it."""

    def test_a_wall_clock_step_does_not_fire_a_relative_delay_early(self):
        self.schedule(**{"in": {"seconds": 300}})
        jumped = datetime.now(timezone.utc) + timedelta(hours=5)
        with patch.object(ScheduledEventService, "_now", staticmethod(lambda: jumped)):
            self.service._evaluate()
        self.assertEqual(self.recorder.of("skill.a.ring"), [])

    def test_a_relative_delay_fires_when_the_monotonic_deadline_passes(self):
        self.schedule(**{"in": {"seconds": 300}})
        schedule = self.service.schedules["skill.a", "one"]
        schedule.deadline = time.monotonic() - 1
        self.service._evaluate()
        self.assertEqual(len(self.recorder.of("skill.a.ring")), 1)
        self.assertEqual(self.service.schedules, {})

    def test_a_relative_delay_is_persisted_as_a_wall_clock_estimate(self):
        self.schedule(**{"in": {"seconds": 300}})
        with open(self.store) as handle:
            stored = json.load(handle)
        self.assertIsNotNone(stored["schedules"][0]["estimate"])
        self.assertEqual(stored["schedules"][0]["record"]["in"], {"seconds": 300})

    def test_until_and_count_are_refused_for_a_relative_delay(self):
        response = self.schedule(count=2, **{"in": {"seconds": 300}})
        self.assertEqual(response.data["error"], "invalid_record")


class TestBounds(SchedulerTestCase):
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
        missed = self.recorder.last("scheduler.missed")
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
        self.assertFalse(self.recorder.last("scheduler.missed").data["truncated"])

    def test_missed_dues_show_up_in_the_computed_state(self):
        due = datetime.now(timezone.utc) - timedelta(hours=2)
        self.schedule(id="tick", event="skill.a.tick", misfire="skip",
                      every={"seconds": 86400, "start": iso(due)})
        self.service._evaluate()
        got = self.request("scheduler.get", {"owner": "skill.a", "id": "tick"})
        self.assertEqual(got.data["state"]["missed"], [iso(due)])

    def test_a_fire_clears_the_missed_dues_that_precede_it(self):
        start = datetime.now(timezone.utc) - timedelta(seconds=25)
        self.schedule(id="tick", event="skill.a.tick", misfire="late",
                      grace_s=0, every={"seconds": 10, "start": iso(start)})
        self.service._evaluate()
        got = self.request("scheduler.get", {"owner": "skill.a", "id": "tick"})
        self.assertEqual(got.data["state"]["missed"], [])
        self.assertEqual(got.data["state"]["last_fired"],
                         iso(start + timedelta(seconds=20)))

    def test_replay_reports_every_occurrence_it_produces(self):
        # an occurrence emitted just before a crash may have lost its
        # record, so replay reports it even when it is inside the grace
        start = datetime.now(timezone.utc) - timedelta(seconds=5)
        self.schedule(id="tick", event="skill.a.tick", grace_s=600,
                      every={"seconds": 10, "start": iso(start)})
        self.service.replay()
        missed = self.recorder.last("scheduler.missed")
        self.assertEqual(missed.data["missed"], [iso(start)])
        self.assertEqual(missed.data["fired_late"], [])
        self.assertEqual(len(self.recorder.of("skill.a.tick")), 1)


class TestAdministrativeOwner(SchedulerTestCase):
    """``*`` reaches every owner, and only for a configured administrator."""

    def populate(self):
        when = iso(datetime.now(timezone.utc) + timedelta(hours=1))
        self.schedule(owner="skill.a", event="skill.a.ring", at=when)
        self.schedule(owner="skill.b", event="skill.b.ring", at=when)

    def test_an_anonymous_caller_may_not_use_the_wildcard(self):
        self.populate()
        listed = self.request("scheduler.list", {"owner": "*"})
        self.assertEqual(listed.data["error"], "not_owner")
        cancelled = self.request("scheduler.cancel", {"owner": "*", "id": "one"})
        self.assertEqual(cancelled.data["error"], "not_owner")
        self.assertEqual(len(self.service.schedules), 2)

    def test_an_identified_caller_outside_the_allowlist_may_not_either(self):
        self.populate()
        listed = self.request("scheduler.list", {"owner": "*"},
                              context={"skill_id": "skill.a"})
        self.assertEqual(listed.data["error"], "not_owner")

    def test_an_allowlisted_administrator_lists_and_cancels_everything(self):
        self.populate()
        self.service.admins = ["admin.panel"]
        listed = self.request("scheduler.list", {"owner": "*"},
                              context={"skill_id": "admin.panel"})
        self.assertEqual(len(listed.data["schedules"]), 2)
        self.request("scheduler.cancel", {"owner": "*", "id": "one"},
                     context={"skill_id": "admin.panel"})
        self.assertEqual(self.service.schedules, {})

    def test_the_allowlist_is_empty_unless_configured(self):
        self.assertEqual(self.service.admins, [])

    def test_no_schedule_may_be_created_under_the_wildcard(self):
        response = self.schedule(owner="*", event="*.ring",
                                 at=iso(datetime.now(timezone.utc)))
        self.assertEqual(response.data["error"], "not_owner")

    def test_an_administrator_may_not_create_under_the_wildcard_either(self):
        self.service.admins = ["admin.panel"]
        response = self.request(
            "scheduler.schedule",
            {"owner": "*", "id": "one", "event": "*.ring",
             "at": iso(datetime.now(timezone.utc) + timedelta(hours=1))},
            context={"skill_id": "admin.panel"})
        self.assertEqual(response.data["error"], "not_owner")


class TestListeners(SchedulerTestCase):
    def test_shutdown_leaves_an_unrelated_observer_subscribed(self):
        seen = []
        self.bus.on("scheduler.schedule", seen.append)
        self.service.shutdown()
        self.bus.emit(Message("scheduler.schedule", {}))
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.recorder.of("scheduler.schedule.response"), [])


class TestInstants(unittest.TestCase):
    def test_format_round_trips(self):
        when = datetime(2031, 3, 29, 7, 30, tzinfo=timezone.utc)
        self.assertEqual(format_instant(when), "2031-03-29T07:30:00+00:00")

    def test_zulu_suffix_is_accepted(self):
        record = validate_record({"id": "x", "owner": "s", "event": "s.e",
                                  "at": "2031-03-29T07:30:00Z"})
        self.assertEqual(record["at"], "2031-03-29T07:30:00+00:00")


if __name__ == "__main__":
    unittest.main()
