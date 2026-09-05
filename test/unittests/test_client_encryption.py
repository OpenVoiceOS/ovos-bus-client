"""Layer-2 envelope encryption at the websocket transport edge.

OVOS-MSG-1 is transport-agnostic; the encryption scheme tested here is
bolted on top by ``ovos_bus_client.client.client`` and is deprecated.
These tests pin the deprecated-but-supported behaviour: with
``websocket.secret_key`` set, outbound frames are AES-wrapped and
inbound AES-wrapped frames round-trip back. With no key set, the wire
remains plain JSON and no warning fires.
"""
import json
import unittest
import warnings
from unittest import TestCase

from ovos_bus_client.message import Message

try:
    import pycryptodomex  # noqa: F401
    _HAS_CRYPTO = True
except ImportError:  # pragma: no cover
    try:
        import Cryptodome  # noqa: F401
        _HAS_CRYPTO = True
    except ImportError:
        _HAS_CRYPTO = False


@unittest.skipUnless(_HAS_CRYPTO, "pycryptodomex not installed")
class TestTransportEncryption(TestCase):
    """Monkey-patches :func:`_encryption_keys` on the client module to
    avoid touching the global ``Configuration()`` singleton (which does
    not restore cleanly across tests)."""

    def _patch_keys(self, secret, allow_clear):
        from ovos_bus_client.client import client as _cli
        self._orig = _cli._encryption_keys
        _cli._encryption_keys = lambda: (secret, allow_clear)
        self.addCleanup(setattr, _cli, "_encryption_keys", self._orig)

    def test_maybe_encrypt_wraps_when_secret_set(self):
        from ovos_bus_client.client.client import _maybe_encrypt
        self._patch_keys("0123456789abcdef", False)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            wire = _maybe_encrypt('{"type":"t","data":{},"context":{}}')
        env = json.loads(wire)
        self.assertIn("ciphertext", env)
        self.assertIn("nonce", env)
        deps = [w for w in caught
                if issubclass(w.category, DeprecationWarning)
                and "envelope encryption" in str(w.message).lower()]
        self.assertTrue(deps)

    def test_round_trip_through_transport_helpers(self):
        from ovos_bus_client.client.client import (_maybe_encrypt,
                                                    _maybe_decrypt)
        self._patch_keys("0123456789abcdef", False)
        m = Message("ovos.test", {"k": "v"}, {"source": "S"})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            wire = _maybe_encrypt(m.serialize())
            recovered = Message.deserialize(_maybe_decrypt(wire))
        self.assertEqual(recovered.msg_type, "ovos.test")
        self.assertEqual(recovered.data, {"k": "v"})
        self.assertEqual(recovered.context["source"], "S")

    def test_maybe_decrypt_refuses_plaintext_when_disallowed(self):
        from ovos_bus_client.client.client import _maybe_decrypt
        self._patch_keys("0123456789abcdef", False)
        with self.assertRaises(RuntimeError):
            _maybe_decrypt('{"type":"t","data":{},"context":{}}')

    def test_no_secret_is_passthrough_and_silent(self):
        from ovos_bus_client.client.client import (_maybe_encrypt,
                                                    _maybe_decrypt)
        self._patch_keys(None, True)
        plaintext = '{"type":"t","data":{},"context":{}}'
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.assertEqual(_maybe_encrypt(plaintext), plaintext)
            self.assertEqual(_maybe_decrypt(plaintext), plaintext)
        self.assertFalse([w for w in caught
                          if issubclass(w.category, DeprecationWarning)])


if __name__ == "__main__":
    unittest.main()
