# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import secrets
import unittest
from multiprocessing import Process, Event
from threading import Thread

from time import sleep, time
from unittest.mock import call, Mock, patch

from pyee import ExecutorEventEmitter

import ovos_messagebus.__main__
from ovos_bus_client.message import Message
from ovos_bus_client.client.client import MessageBusClient, GUIWebsocketClient
from ovos_bus_client.client import MessageWaiter, MessageCollector

WS_CONF = {"websocket": {"host": "testhost", "port": 1337, "route": "/core", "ssl": False}}


class TestClient(unittest.TestCase):
    def test_echo(self):
        from ovos_bus_client.client.client import echo

        # TODO

    def test_inheritance(self):
        from mycroft_bus_client.client import MessageBusClient as _Client

        self.assertTrue(issubclass(MessageBusClient, _Client))


class TestMessageBusClient(unittest.TestCase):
    from ovos_bus_client.client.client import MessageBusClient

    client = MessageBusClient()

    def test_build_url(self):
        url = MessageBusClient.build_url("localhost", 1337, "/core", False)
        self.assertEqual(url, "ws://localhost:1337/core")
        ssl_url = MessageBusClient.build_url("sslhost", 443, "/core", True)
        self.assertEqual(ssl_url, "wss://sslhost:443/core")

    def test_create_client(self):
        self.assertEqual(self.client.client.url, "ws://127.0.0.1:8181/core")
        self.assertIsInstance(self.client.emitter, ExecutorEventEmitter)

        mock_emitter = Mock()
        mc = MessageBusClient(emitter=mock_emitter)
        self.assertEqual(mc.emitter, mock_emitter)

    def test_on_open(self):
        # TODO
        pass

    def test_on_close(self):
        # TODO
        pass

    def test_on_error(self):
        # TODO
        pass

    def test_on_message(self):
        # TODO
        pass

    def test_emit(self):
        # TODO
        pass

    def test_collect_responses(self):
        # TODO
        pass

    def test_on_collect(self):
        # TODO
        pass

    @patch("ovos_bus_client.client.client.MessageWaiter")
    def test_wait_for_message_str(self, mock_message_waiter):
        # Arrange
        test_message = Message("test.message")
        self.client.emit = Mock()
        # Act
        self.client.wait_for_response(test_message)
        # Assert
        mock_message_waiter.assert_called_once_with(self.client, ["test.message.response"])

    @patch("ovos_bus_client.client.client.MessageWaiter")
    def test_wait_for_message_list(self, mock_message_waiter):
        # Arrange
        test_message = Message("test.message")
        self.client.emit = Mock()
        # Act
        self.client.wait_for_response(test_message, ["test.message.response", "test.message.response2"])
        # Assert
        mock_message_waiter.assert_called_once_with(self.client, ["test.message.response", "test.message.response2"])

    def test_wait_for_response(self):
        # TODO
        pass

    def test_on(self):
        # TODO
        pass

    def test_once(self):
        # TODO
        pass

    def test_remove(self):
        # TODO
        pass

    def test_remove_all_listeners(self):
        # TODO
        pass

    def test_run_forever(self):
        # TODO
        pass

    def test_close(self):
        # TODO
        pass

    def test_run_in_thread(self):
        # TODO
        pass


class TestGuiWebsocketClient(unittest.TestCase):
    client = GUIWebsocketClient()

    def test_gui_client_init(self):
        self.assertIsInstance(self.client, MessageBusClient)
        self.assertIsInstance(self.client.gui_id, str)

    def test_emit(self):
        # TODO
        pass

    def test_on_open(self):
        # TODO
        pass

    def test_on_message(self):
        # TODO
        pass


class TestMessageWaiter:
    def test_message_wait_success(self):
        bus = Mock()
        waiter = MessageWaiter(bus, "delayed.message")
        bus.once.assert_called_with("delayed.message", waiter._handler)

        test_msg = Mock(name="test_msg")
        waiter._handler(test_msg)  # Inject response

        assert waiter.wait() == test_msg

    def test_message_wait_timeout(self):
        bus = Mock()
        waiter = MessageWaiter(bus, "delayed.message")
        bus.once.assert_called_with("delayed.message", waiter._handler)

        assert waiter.wait(0.3) is None

    def test_message_converts_to_list(self):
        bus = Mock()
        waiter = MessageWaiter(bus, "test.message")
        assert isinstance(waiter.msg_type, list)
        bus.once.assert_called_with("test.message", waiter._handler)

    def test_multiple_messages(self):
        bus = Mock()
        waiter = MessageWaiter(bus, ["test.message", "test.message2"])
        bus.once.assert_has_calls([call("test.message", waiter._handler), call("test.message2", waiter._handler)])


class TestMessageCollector:
    def test_message_wait_success(self):
        bus = Mock()
        collector = MessageCollector(bus, Message("delayed.message"), min_timeout=0.0, max_timeout=2.0)

        test_register = Mock(name="test_register")
        test_register.data = {"query": collector.collect_id, "timeout": 5, "handler": "test_handler1"}
        collector._register_handler(test_register)  # Inject response

        test_response = Mock(name="test_register")
        test_response.data = {"query": collector.collect_id, "handler": "test_handler1"}
        collector._receive_response(test_response)

        assert collector.collect() == [test_response]

    def test_message_drop_invalid(self):
        bus = Mock()
        collector = MessageCollector(bus, Message("delayed.message"), min_timeout=0.0, max_timeout=2.0)

        valid_register = Mock(name="valid_register")
        valid_register.data = {"query": collector.collect_id, "timeout": 5, "handler": "test_handler1"}
        invalid_register = Mock(name="invalid_register")
        invalid_register.data = {"query": "asdf", "timeout": 5, "handler": "test_handler1"}
        collector._register_handler(valid_register)  # Inject response
        collector._register_handler(invalid_register)  # Inject response

        valid_response = Mock(name="valid_register")
        valid_response.data = {"query": collector.collect_id, "handler": "test_handler1"}
        invalid_response = Mock(name="invalid_register")
        invalid_response.data = {"query": "asdf", "handler": "test_handler1"}
        collector._receive_response(valid_response)
        collector._receive_response(invalid_response)
        assert collector.collect() == [valid_response]


class TestClientConnections(unittest.TestCase):
    service_proc: Process = None
    num_clients = 128
    clients = []

    @classmethod
    def setUpClass(cls):
        from ovos_messagebus.__main__ import main
        ovos_messagebus.__main__.reset_sigint_handler = Mock()
        ready_event = Event()

        def ready():
            ready_event.set()

        cls.service_proc = Process(target=main, args=(ready,))
        cls.service_proc.start()
        if not ready_event.wait(10):
            raise TimeoutError("Timed out waiting for bus service to start")

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.clients = []

    @classmethod
    def tearDownClass(cls):
        cls.service_proc.terminate()
        cls.service_proc.join(timeout=5)
        cls.service_proc.kill()

    def test_create_clients(self):
        for i in range(self.num_clients):
            client = MessageBusClient()
            self.clients.append(client)
            client.run_in_thread()
            self.assertTrue(client.connected_event.wait(5))

        self.assertEqual(len(self.clients), self.num_clients)
        self.assertTrue(all((client.connected_event.is_set()
                             for client in self.clients)))

        for client in self.clients:
            client.close()
            self.assertFalse(client.connected_event.is_set())
        self.clients = []

    def test_handle_messages(self):
        handled = []
        test_messages = []

        def handler(message):
            self.assertIsInstance(message, Message)
            self.assertIsInstance(message.data['test'], str)
            self.assertIsInstance(message.context['test'], str)
            handled.append(message)

        for i in range(self.num_clients):
            client = MessageBusClient()
            self.clients.append(client)
            client.run_in_thread()
            self.assertTrue(client.connected_event.wait(5))
            client.on("test.message", handler)
            client.on(f"test.message{i}", handler)
            test_messages.append(Message(f"test.message{i}",
                                         {"test": secrets.token_hex(1024)},
                                         {"test": secrets.token_hex(512)}))

        sender = MessageBusClient()
        sender.run_in_thread()
        self.assertTrue(sender.connected_event.wait(5))

        # Send one message to many handlers
        test_message = Message("test.message", {"test": ""}, {"test": ""})
        sender.emit(test_message)
        timeout = time() + 10
        while len(handled) < self.num_clients and time() < timeout:
            sleep(1)
        self.assertEqual(len(handled), self.num_clients)

        # Send many messages to many handlers
        handled = []
        for message in test_messages:
            Thread(target=sender.emit, args=(message,)).start()
        timeout = time() + 30
        while len(handled) < self.num_clients and time() < timeout:
            sleep(1)
        self.assertEqual(len(handled), self.num_clients)

        sender.close()
        for client in self.clients:
            client.close()
        self.clients = []
