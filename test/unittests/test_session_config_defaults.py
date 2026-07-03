"""Config-backed fields resolve to the deployment default, not an empty list.

OVOS-SESSION-1 §2.1: an omitted or ``null`` field means "let the orchestrator
decide" — the consumer substitutes its own deployment default at the point of
consumption. For config-backed fields (``pipeline``, ``lang``) that default is
the deployment-configured value, NOT an empty container.

``Session.deserialize`` routes every field through ``Session.__init__``, whose
``x or Configuration().get(...)`` resolution runs BEFORE
``_normalize_empty_containers`` folds still-``None`` fields to ``[]`` / ``{}``.
So a wire dict that omits (or nulls) ``pipeline`` reconstructs with the
configured pipeline, while genuinely-empty-default fields
(``blacklisted_intents``, the ``*_transformers`` override lists) fold to ``[]``.
These tests pin that ordering so a future change to the normalization step
cannot silently blank a config-backed default.
"""
import unittest
from unittest.mock import patch

import ovos_bus_client.session as session_module
from ovos_bus_client.session import Session

_CUSTOM_PIPELINE = ["adapt_high", "fallback_low"]
_CONFIG = {
    "lang": "en-us",
    "intents": {"pipeline": list(_CUSTOM_PIPELINE)},
}


class TestConfigBackedDefaultsOnDeserialize(unittest.TestCase):
    def _deserialize(self, data):
        with patch.object(session_module, "Configuration",
                          return_value=_CONFIG):
            return Session.deserialize(data)

    def test_omitted_pipeline_resolves_to_configured_pipeline(self):
        sess = self._deserialize({"session_id": "abc"})
        self.assertEqual(sess.pipeline, _CUSTOM_PIPELINE)

    def test_null_pipeline_resolves_to_configured_pipeline(self):
        # SESSION-1 §2.1: explicit null MUST be treated exactly as omitted.
        sess = self._deserialize({"session_id": "abc", "pipeline": None})
        self.assertEqual(sess.pipeline, _CUSTOM_PIPELINE)

    def test_empty_pipeline_resolves_to_configured_pipeline(self):
        # §3.4: an empty array on an override field is wire-equivalent to
        # omission, so it too resolves to the deployment default.
        sess = self._deserialize({"session_id": "abc", "pipeline": []})
        self.assertEqual(sess.pipeline, _CUSTOM_PIPELINE)

    def test_explicit_pipeline_is_preserved(self):
        sess = self._deserialize({"session_id": "abc",
                                  "pipeline": ["stop_high"]})
        self.assertEqual(sess.pipeline, ["stop_high"])

    def test_omitted_blacklisted_intents_folds_to_empty_list(self):
        # empty deployment default: no config-backed value to substitute.
        sess = self._deserialize({"session_id": "abc"})
        self.assertEqual(sess.blacklisted_intents, [])

    def test_omitted_transformer_lists_fold_to_empty_list(self):
        # the six active *_transformers are override lists whose empty value
        # defers to the deployment-configured chain at the consuming
        # transformer service (§3.4 case 3); the Session carries [] for them.
        sess = self._deserialize({"session_id": "abc"})
        for name in ("audio_transformers", "utterance_transformers",
                     "metadata_transformers", "intent_transformers",
                     "dialog_transformers", "tts_transformers"):
            self.assertEqual(getattr(sess, name), [], name)


if __name__ == "__main__":
    unittest.main()
