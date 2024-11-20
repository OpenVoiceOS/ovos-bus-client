# Copyright 2019 Mycroft AI Inc.
#
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
#
"""
Tools and constructs that are useful together with the messagebus.
"""
import orjson

from ovos_config.config import read_mycroft_config
from ovos_config.locale import get_default_lang
from ovos_utils.json_helper import merge_dict
from ovos_utils.lang import standardize_lang_tag
from ovos_bus_client import MessageBusClient
from ovos_bus_client.message import dig_for_message, Message
from ovos_bus_client.session import SessionManager
from ovos_bus_client.util.scheduler import EventScheduler


_DEFAULT_WS_CONFIG = {"host": "0.0.0.0",
                      "port": 8181,
                      "route": "/core",
                      "ssl": False}


def get_message_lang(message=None):
    message = message or dig_for_message()
    if not message:
        return None
    # old style lang param
    lang = message.data.get("lang") or message.context.get("lang")
    if lang:
        return standardize_lang_tag(lang)

    # new style session lang
    if "session_id" in message.context or "session" in message.context:
        sess = SessionManager.get(message)
        return sess.lang

    return standardize_lang_tag(get_default_lang())


def get_websocket(host, port, route='/', ssl=False, threaded=True):
    """
    Returns a connection to a websocket
    """

    client = MessageBusClient(host, port, route, ssl)
    if threaded:
        client.run_in_thread()
    return client


def get_mycroft_bus(host: str = None, port: int = None, route: str = None,
                    ssl: bool = None):
    """
    Returns a connection to the mycroft messagebus
    """
    config = read_mycroft_config().get('websocket') or dict()
    host = host or config.get('host') or _DEFAULT_WS_CONFIG['host']
    port = port or config.get('port') or _DEFAULT_WS_CONFIG['port']
    route = route or config.get('route') or _DEFAULT_WS_CONFIG['route']
    if ssl is None:
        ssl = config.get('ssl') if 'ssl' in config else \
            _DEFAULT_WS_CONFIG['ssl']
    return get_websocket(host, port, route, ssl)


def listen_for_message(msg_type, handler, bus=None):
    """
    Continuously listens and reacts to a specific messagetype on the mycroft messagebus

    NOTE: when finished you should call bus.remove(msg_type, handler)
    """
    bus = bus or get_mycroft_bus()
    bus.on(msg_type, handler)
    return bus


def listen_once_for_message(msg_type, handler, bus=None):
    """
    listens and reacts once to a specific messagetype on the mycroft messagebus
    """
    auto_close = bus is None
    bus = bus or get_mycroft_bus()

    def _handler(message):
        handler(message)
        if auto_close:
            bus.close()

    bus.once(msg_type, _handler)
    return bus


def wait_for_reply(message, reply_type=None, timeout=3.0, bus=None):
    """Send a message and wait for a response.

    Args:
        message (FakeMessage or str or dict): message object or type to send
        reply_type (str): the message type of the expected reply.
                          Defaults to "<message.type>.response".
        timeout: seconds to wait before timeout, defaults to 3
    Returns:
        The received message or None if the response timed out
    """
    auto_close = bus is None
    bus = bus or get_mycroft_bus()
    if isinstance(message, str):
        try:
            message = orjson.loads(message)
        except:
            pass
    if isinstance(message, str):
        message = Message(message)
    elif isinstance(message, dict):
        message = Message(message["type"],
                          message.get("data"),
                          message.get("context"))
    elif not isinstance(message, Message):
        raise ValueError
    response = bus.wait_for_response(message, reply_type, timeout)
    if auto_close:
        bus.close()
    return response


def send_message(message, data=None, context=None, bus=None):
    auto_close = bus is None
    bus = bus or get_mycroft_bus()
    if isinstance(message, str):
        if isinstance(data, dict) or isinstance(context, dict):
            message = Message(message, data, context)
        else:
            try:
                message = orjson.loads(message)
            except:
                message = Message(message)
    if isinstance(message, dict):
        message = Message(message["type"],
                          message.get("data"),
                          message.get("context"))
    if not isinstance(message, Message):
        raise ValueError
    bus.emit(message)
    if auto_close:
        bus.close()


def send_binary_data_message(binary_data, msg_type="mycroft.binary.data",
                             msg_data=None, msg_context=None, bus=None):
    msg_data = msg_data or {}
    msg = {
        "type": msg_type,
        "data": merge_dict(msg_data, {"binary": binary_data.hex()}),
        "context": msg_context or None
    }
    send_message(msg, bus=bus)


def send_binary_file_message(filepath, msg_type="mycroft.binary.file",
                             msg_context=None, bus=None):
    with open(filepath, 'rb') as f:
        binary_data = f.read()
    msg_data = {"path": filepath}
    send_binary_data_message(binary_data, msg_type=msg_type, msg_data=msg_data,
                             msg_context=msg_context, bus=bus)


def decode_binary_message(message):
    if isinstance(message, str):
        try:  # json string
            message = orjson.loads(message)
            binary_data = message.get("binary") or message["data"]["binary"]
        except:  # hex string
            binary_data = message
    elif isinstance(message, dict):
        # data field or serialized message
        binary_data = message.get("binary") or message["data"]["binary"]
    else:
        # message object
        binary_data = message.data["binary"]
    # decode hex string
    return bytearray.fromhex(binary_data)
