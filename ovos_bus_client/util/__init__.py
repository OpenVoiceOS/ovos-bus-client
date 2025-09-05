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
import json
try:
    import orjson
except ImportError:
    orjson = None

from ovos_config.config import read_mycroft_config
from ovos_config.locale import get_default_lang
from ovos_utils.json_helper import merge_dict
from ovos_utils.lang import standardize_lang_tag
from ovos_bus_client import MessageBusClient
from ovos_bus_client.message import dig_for_message, Message
from ovos_bus_client.session import SessionManager
from ovos_bus_client.util.scheduler import EventScheduler
from typing import Dict, Any


_DEFAULT_WS_CONFIG = {"host": "0.0.0.0",
                      "port": 8181,
                      "route": "/core",
                      "ssl": False}



def json_dumps(payload: Dict[str, Any]) -> str:
    """
    Serialize a JSON-serializable mapping to a JSON string, using orjson when available.
    
    Uses orjson.dumps for faster serialization if the orjson module is present; otherwise falls back to the standard library json.dumps. Returns a UTF-8 string (orjson output is decoded from bytes).
    """
    if orjson is None:
        return json.dumps(payload)
    else:
        return orjson.dumps(payload).decode("utf-8")

def json_loads(payload: str) ->  Dict[str, Any]:
    """
    Deserialize a JSON string into a Python dictionary, preferring orjson when available.
    
    If orjson is installed it will be used for parsing for better performance; otherwise the standard library json.loads is used.
    
    Parameters:
        payload (str): JSON-formatted string to parse.
    
    Returns:
        Dict[str, Any]: Parsed JSON as a Python dictionary (or other JSON-equivalent structure).
    """
    if orjson is None:
        return json.loads(payload)
    else:
        return orjson.loads(payload)

def get_message_lang(message=None):
    """
    Return the BCP-47 language tag for a message.
    
    If no message is provided, attempts to locate one via dig_for_message(). Checks legacy locations first (message.data["lang"] or message.context["lang"]); if present returns the standardized tag. If the message contains a session reference ("session_id" or "session"), returns the language from the session (SessionManager.get(message).lang). Otherwise returns the standardized default language from get_default_lang(). Returns None when no message can be found.
    
    Parameters:
        message: Optional message object. If omitted, dig_for_message() will be used to locate a message.
    
    Returns:
        A standardized language tag string (e.g., "en-US"), or None if no message is available.
    """
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
    """
    Send a message (or message descriptor) and wait for a matching reply.
    
    Accepts a Message instance, a dict with keys "type", optional "data" and "context",
    or a string. If a string is provided, the function will attempt to parse it as JSON;
    on failure the string is treated as a message type. The function sends the resulting
    Message on the bus and waits up to `timeout` seconds for a response. If `reply_type`
    is None, the handler that receives the message will typically respond to
    "<message.type>.response".
    
    Parameters:
        message: Message | dict | str — message to send or a descriptor to construct one from.
        reply_type (str, optional): Specific message type to wait for (defaults to
            the standard "<message.type>.response" convention when None).
        timeout (float, optional): Seconds to wait for a reply (default 3.0).
    
    Returns:
        The received Message instance, or None if the wait timed out.
    
    Raises:
        ValueError: If `message` cannot be converted to a Message.
    
    Side effects:
        If no `bus` is provided, a temporary bus connection is created and closed
        automatically when the call completes.
    """
    auto_close = bus is None
    bus = bus or get_mycroft_bus()
    if isinstance(message, str):
        try:
            message = json_loads(message)
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
    """
    Send a Message to the Mycroft/OVOS message bus, accepting several input forms.
    
    If `message` is a string and `data` or `context` is a dict, they are used to construct a Message. If `message` is a string that parses as JSON, the JSON object is converted to a Message. If `message` is a dict, its "type", optional "data", and optional "context" keys are used to construct a Message. The function emits the resulting Message on the bus and closes the bus when one was not provided.
    
    Parameters:
        message (str | dict | Message): Message to send. May be:
            - a Message instance (sent as-is),
            - a dict with "type" (and optional "data"/"context"),
            - a string containing a message type (optionally parsed as JSON),
            - or a JSON string representing a message object.
        data (dict | None): Optional data payload used when `message` is a plain string representing the type.
        context (dict | None): Optional context used when `message` is a plain string representing the type.
    
    Raises:
        ValueError: If the final value cannot be converted to a Message.
    
    Note:
        The `bus` parameter (if provided) is used for emission; if not provided the function obtains a temporary bus and closes it after sending.
    """
    auto_close = bus is None
    bus = bus or get_mycroft_bus()
    if isinstance(message, str):
        if isinstance(data, dict) or isinstance(context, dict):
            message = Message(message, data, context)
        else:
            try:
                message = json_loads(message)
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
    """
    Decode and return binary data from a message-like input as a bytearray.
    
    Accepts:
    - a JSON string containing either a top-level "binary" key or a nested "data":{"binary": "..."} field,
    - a plain hex string,
    - a dict with "binary" or "data"->"binary",
    - or a message object exposing a .data mapping with a "binary" entry.
    
    The function extracts the hex-encoded binary string from the appropriate location and returns its decoded bytes as a bytearray.
    """
    if isinstance(message, str):
        try:  # json string
            message = json_loads(message)
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
