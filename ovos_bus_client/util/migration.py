"""Helpers for the OVOS bus-namespace migration (legacy <-> ``ovos.*`` topics).

During the migration a producer may emit a logical event on BOTH the legacy
topic and the new ``ovos.*`` topic so nodes on either version interoperate — a
HiveMind satellite does not necessarily upgrade in lockstep with the core. A
consumer that subscribes to both topics would then handle the event twice; the
:class:`TransitionalDeduplicator` drops the duplicate using a content-derived
key within a short time window.

The dedup key is derived from message content (NOT a per-message identifier), so
this adds no new bus field and stays within OVOS-MSG-1 §5.4. These helpers are a
backwards-compatibility aid and are expected to be removed in the next major
release, once every node emits the ``ovos.*`` topics only.
"""
import time
from collections import OrderedDict
from typing import Callable, Hashable, Iterable, Optional, Union


class TransitionalDeduplicator:
    """Drop content-duplicate events seen within a short time window.

    A consumer that listens on both the legacy and the new topic for the same
    logical event registers one of these and guards its handler::

        dedup = TransitionalDeduplicator(window=1.0)

        def handle(message):
            if dedup.is_duplicate(utterance_key(message.data.get("utterances"),
                                                 message.data.get("lang"))):
                return
            ...  # process once

    Args:
        window: seconds during which a repeated key is treated as a duplicate.
        max_keys: hard cap on retained keys (bounds memory if the window is
            never hit, e.g. a flood of distinct events).
        clock: monotonic time source; injectable for testing.
    """

    def __init__(self, window: float = 1.0, max_keys: int = 256,
                 clock: Callable[[], float] = time.monotonic):
        self.window = window
        self.max_keys = max_keys
        self._clock = clock
        self._seen: "OrderedDict[Hashable, float]" = OrderedDict()

    def _purge(self, now: float) -> None:
        cutoff = now - self.window
        while self._seen:
            key, ts = next(iter(self._seen.items()))
            if ts < cutoff:
                self._seen.popitem(last=False)
            else:
                break

    def is_duplicate(self, key: Hashable) -> bool:
        """Return True if ``key`` was seen within ``window`` seconds; else record it.

        A ``None`` key is never treated as a duplicate (legacy emitters that
        carry no identifiable content always pass through).
        """
        if key is None:
            return False
        now = self._clock()
        self._purge(now)
        if key in self._seen:
            return True
        self._seen[key] = now
        if len(self._seen) > self.max_keys:
            self._seen.popitem(last=False)
        return False

    def reset(self) -> None:
        """Forget all recorded keys."""
        self._seen.clear()


def utterance_key(utterances: Optional[Union[str, Iterable[str]]],
                  lang: Optional[str] = None) -> Optional[Hashable]:
    """Content key for an utterance/speak event: ``hash`` of its text and lang.

    Accepts either a single utterance string (``speak``) or an iterable of
    utterances (``recognizer_loop:utterance``). Returns ``None`` when there is
    no text, so the deduplicator lets such messages through.
    """
    if not utterances:
        return None
    if isinstance(utterances, str):
        text = utterances
    else:
        text = "\n".join(utterances)
    if not text:
        return None
    return hash((text, lang or ""))


def emit_migration_pair(bus, message, legacy_type: str, new_type: str,
                        data: Optional[dict] = None) -> None:
    """Emit the same payload on both the legacy and the new topic.

    Context (session, skill_id, …) is preserved via ``Message.forward``. Use on
    the producer side during the migration so consumers on either namespace are
    reached; consumers dedupe the resulting pair via
    :class:`TransitionalDeduplicator`.

    When ``data`` is omitted the inbound ``message.data`` is reused — note that
    ``Message.forward`` defaults the payload to ``{}`` (it does NOT carry the
    source data over), so passing ``data=None`` here without this default would
    emit empty payloads.
    """
    if data is None:
        data = message.data
    bus.emit(message.forward(legacy_type, data))
    bus.emit(message.forward(new_type, data))
