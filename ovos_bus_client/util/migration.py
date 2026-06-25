"""Consumer-side dedup for the bus-namespace migration (legacy <-> ``ovos.*``).

While both the legacy topic and its ``ovos.*`` replacement are on the bus, a
consumer that listens on both would handle the same logical event twice. A
:class:`Deduplicator` drops the second copy of the same content within a short
window. The key is content-derived (e.g. ``hash`` of utterance+lang), so this
adds no per-message id — within OVOS-MSG-1 §5.4.

Backwards-compat aid; delete in the next major once only ``ovos.*`` is emitted.
"""
import time


class Deduplicator:
    """Drop a repeated content key seen within ``window`` seconds.

        dedup = Deduplicator()
        if dedup.is_duplicate(hash((utterance, lang))):
            return  # already handled via the other topic
    """

    def __init__(self, window: float = 1.0):
        self.window = window
        self._seen: dict = {}

    def is_duplicate(self, key) -> bool:
        now = time.monotonic()
        self._seen = {k: t for k, t in self._seen.items() if now - t < self.window}
        if key in self._seen:
            return True
        self._seen[key] = now
        return False
