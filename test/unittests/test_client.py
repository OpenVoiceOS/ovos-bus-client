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

import unittest
from unittest.mock import call, Mock, patch

from pyee import ExecutorEventEmitter

from ovos_bus_client.message import Message
from ovos_bus_client.client.client import MessageBusClient, GUIWebsocketClient
from ovos_bus_client.client import MessageWaiter, MessageCollector

WS_CONF = {"websocket": {"host": "testhost", "port": 1337, "route": "/core", "ssl": False}}


class TestMessageBusClient(unittest.TestCase):
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
