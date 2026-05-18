"""Coverage tests for ovos_bus_client.conf — config loaders."""
import unittest
from unittest import TestCase
from unittest.mock import patch

from ovos_bus_client.conf import (MessageBusConfig, client_from_config,
                                  load_gui_message_bus_config,
                                  load_message_bus_config)


class TestLoadMessageBusConfig(TestCase):
    def test_defaults_from_websocket_block(self):
        with patch("ovos_bus_client.conf.Configuration",
                   return_value={"websocket": {"host": "h", "port": 9, "route": "/x", "ssl": True}}):
            cfg = load_message_bus_config()
            self.assertEqual(cfg, MessageBusConfig("h", 9, "/x", True))

    def test_overrides_take_precedence(self):
        with patch("ovos_bus_client.conf.Configuration",
                   return_value={"websocket": {"host": "h", "port": 9, "route": "/x", "ssl": False}}):
            cfg = load_message_bus_config(host="override")
            self.assertEqual(cfg.host, "override")

    def test_missing_websocket_key_raises(self):
        with patch("ovos_bus_client.conf.Configuration", return_value={}):
            with self.assertRaises(KeyError):
                load_message_bus_config()


class TestLoadGuiMessageBusConfig(TestCase):
    def test_returns_gui_config(self):
        cfg = load_gui_message_bus_config()
        # block exists or falls back to defaults — just confirm shape
        self.assertTrue(hasattr(cfg, "host"))
        self.assertTrue(hasattr(cfg, "port"))


class TestClientFromConfig(TestCase):
    def test_reads_file_and_constructs_client(self, tmp_path=None):
        import json, tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
            json.dump({"core": {"host": "h", "port": 9, "route": "/x", "ssl": False}}, f)
            path = f.name
        try:
            with patch("ovos_bus_client.client.MessageBusClient") as MBC:
                client_from_config(subconf="core", file_path=path)
                MBC.assert_called_once()
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
