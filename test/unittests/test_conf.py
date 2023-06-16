import unittest
from os.path import dirname, join
from unittest.mock import patch

CONF_PATH = join(dirname(__file__), 'test.conf')


class TestConfigLoader(unittest.TestCase):
    def test_config_objects(self):
        from ovos_bus_client.conf import MessageBusConfig, MessageBusClientConf
        self.assertEqual(MessageBusConfig, MessageBusClientConf)

    @patch("ovos_bus_client.conf.Configuration")
    def test_load_messagebus_config(self, configuration):
        from ovos_bus_client.conf import load_message_bus_config
        test_config = {"host": "test_host", "port": 8080, "route": "/test",
                       "ssl": True}
        # Test values from configuration
        configuration.return_value = {"websocket": test_config}
        config = load_message_bus_config()
        self.assertEqual(config.host, test_config['host'])
        self.assertEqual(config.port, test_config['port'])
        self.assertEqual(config.route, test_config['route'])
        self.assertEqual(config.ssl, test_config['ssl'])

        # Test overrides
        config = load_message_bus_config(host='test', port=8181, route='/new',
                                         ssl=False)
        self.assertEqual(config.host, 'test')
        self.assertEqual(config.port, 8181)
        self.assertEqual(config.route, '/new')
        self.assertEqual(config.ssl, False)

        # Test defaults
        configuration.return_value = {"websocket": {}}
        config = load_message_bus_config()
        self.assertIsInstance(config.host, str)
        self.assertIsInstance(config.port, int)
        self.assertIsInstance(config.route, str)
        self.assertIsInstance(config.ssl, bool)

        # Test invalid config
        with self.assertRaises(KeyError):
            configuration.return_value = {}
            load_message_bus_config()

    @patch("ovos_bus_client.conf.Configuration")
    def test_load_gui_message_bus_config(self, configuration):
        from ovos_bus_client.conf import load_gui_message_bus_config
        test_config = {"host": "test_host", "port": 8080, "route": "/test",
                       "ssl": True}
        # Test values from configuration
        configuration.return_value = {"gui": test_config}
        config = load_gui_message_bus_config()
        self.assertEqual(config.host, test_config['host'])
        self.assertEqual(config.port, test_config['port'])
        self.assertEqual(config.route, test_config['route'])
        self.assertEqual(config.ssl, test_config['ssl'])

        # Test overrides
        config = load_gui_message_bus_config(host='test', port=8181,
                                             route='/new', ssl=False)
        self.assertEqual(config.host, 'test')
        self.assertEqual(config.port, 8181)
        self.assertEqual(config.route, '/new')
        self.assertEqual(config.ssl, False)

        # Test defaults
        configuration.return_value = {"gui": {}}
        config = load_gui_message_bus_config()
        self.assertIsInstance(config.host, str)
        self.assertIsInstance(config.port, int)
        self.assertIsInstance(config.route, str)
        self.assertIsInstance(config.ssl, bool)

        # Test invalid config
        with self.assertRaises(KeyError):
            configuration.return_value = {}
            load_gui_message_bus_config()

    def test_client_from_config(self):
        from ovos_bus_client.conf import client_from_config
        # Default config
        client = client_from_config(file_path=CONF_PATH)
        self.assertTrue(client.config.ssl)
        self.assertEqual(client.config.port, 4242)
        self.assertEqual(client.config.route, 'hitchhike')
        self.assertEqual(client.config.host, 'meaning.com')
        # Alternate config
        client = client_from_config('alt', file_path=CONF_PATH)
        self.assertFalse(client.config.ssl)
        self.assertEqual(client.config.port, 666)
        self.assertEqual(client.config.route, 'of_evil')
        self.assertEqual(client.config.host, 'evil.com')
