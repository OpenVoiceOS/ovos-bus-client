# Copyright 2017 Mycroft AI Inc.
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
Classes and functions related to the Mycroft Message.

The Message object is the core construct passed on the message bus
it contains methods for tracking message context and
serializing / deserializing the message for transmission.
"""

import inspect
import orjson
import re
from copy import deepcopy
from typing import Optional
from binascii import hexlify, unhexlify
from ovos_utils.log import LOG, deprecated
from ovos_utils.security import encrypt, decrypt
from ovos_config.config import Configuration

try:
    from lingua_franca.parse import normalize
except ImportError:
    # optional LF import
    def normalize(text, *args, **kwargs):
        return text

try:
    from mycroft_bus_client.message import Message as _MsgBase, \
        CollectionMessage as _CollectionMsgBase

except ImportError:

    # TODO - code in the wild does isinstance checks
    # this conditional subclassing should be removed ASAP, it is only here for the migration period
    # mycroft_bus_client is abandonware until further notice from MycroftAI

    class _MsgBase:
        pass


    class _CollectionMsgBase(_MsgBase):
        pass


class _MessageMeta(type):
    """ To override isinstance checks we need to use a metaclass """

    def __instancecheck__(self, instance):
        try:
            from mycroft_bus_client.message import Message as _MycroftMessage
            return isinstance(instance, _MycroftMessage) or \
                super().__instancecheck__(instance)
        except:
            return super().__instancecheck__(instance)


class Message(_MsgBase, metaclass=_MessageMeta):
    """Holds and manipulates data sent over the websocket

        Message objects will be used to send information back and forth
        between processes of Mycroft.

    Attributes:
        msg_type (str): type of data sent within the message.
        data (dict): data sent within the message
        context: info about the message not part of data such as source,
            destination or domain.
    """
    # if set all messages are AES encrypted
    _secret_key = Configuration().get("websocket", {}).get("secret_key")
    # if set to False, will refuse to deserialize unencrypted messages for processing
    _allow_unencrypted = Configuration().get("websocket", {}).get("allow_unencrypted", _secret_key is None)

    def __init__(self, msg_type: str, data: dict = None, context: dict = None):
        """Used to construct a message object

        Message objects will be used to send information back and forth
        between processes of mycroft service, voice, skill and cli
        """
        data = data or {}
        context = context or {}
        assert isinstance(data, dict)
        assert isinstance(context, dict)
        self.msg_type = msg_type
        self.data = data
        self.context = context

    def __eq__(self, other):
        if not isinstance(other, Message):
            return False
        return other.msg_type == self.msg_type and \
            other.data == self.data and \
            other.context == self.context

    def serialize(self) -> str:
        """This returns a string of the message info.

        This makes it easy to send over a websocket. This uses
        json dumps to generate the string with type, data and context

        Returns:
            str: a json string representation of the message.
        """
        # handle Session and Message objects
        data = self._json_dump(self.data)
        ctxt = self._json_dump(self.context)

        msg = orjson.dumps({'type': self.msg_type, 'data': data, 'context': ctxt}).decode("utf-8")
        if self._secret_key:
            payload = encrypt_as_dict(self._secret_key, msg)
            return orjson.dumps(payload).decode("utf-8")
        return msg

    @property
    def as_dict(self) -> dict:
        return orjson.loads(self.serialize())

    @staticmethod
    def _json_dump(value):

        from ovos_bus_client.apis.gui import _GUIDict

        def serialize_item(x):
            try:
                if hasattr(x, "serialize"):
                    return x.serialize()
            except:
                pass
            if isinstance(x, list):
                for idx, it in enumerate(x):
                    x[idx] = serialize_item(it)
            if isinstance(x, dict) and not isinstance(x, _GUIDict):
                for k, v in x.items():
                    x[k] = serialize_item(v)
            return x
        assert isinstance(value, dict)
        data = {k: serialize_item(v) for k, v in value.items()}
        return data

    @staticmethod
    def _json_load(value):
        if isinstance(value, str):
            obj = orjson.loads(value)
        else:
            obj = value
        assert isinstance(obj, dict)
        if Message._secret_key:
            if 'ciphertext' in obj:
                obj = decrypt_from_dict(Message._secret_key, obj)
            elif not Message._allow_unencrypted:
                raise RuntimeError("got an unencrypted message, configured to refuse")
        return obj

    @staticmethod
    def deserialize(value: str) -> _MsgBase:
        """
        This takes a string and constructs a message object.

        This makes it easy to take strings from the websocket and create
        a message object.  This uses json loads to get the info and generate
        the message object.

        Args:
            value(str): This is the json string received from the websocket

        Returns:
            Message: message object constructed from the json string passed
            int the function.
            value(str): This is the string received from the websocket
        """
        obj = Message._json_load(value)
        return Message(obj.get('type') or '',
                       obj.get('data') or {},
                       obj.get('context') or {})

    def forward(self, msg_type: str, data: dict = None) -> _MsgBase:
        """
        Keep context and forward message

        This will take the same parameters as a message object but use
        the current message object as a reference.  It will copy the context
        from the existing message object.

        Args:
            msg_type (str): type of message
            data (dict): data for message

        Returns:
            Message: Message object to be used on the reply to the message
        """
        data = data or {}
        return Message(msg_type, data, context=self.context)

    def reply(self, msg_type: str, data: dict = None,
              context: dict = None) -> _MsgBase:
        """
        Construct a reply message for a given message

        This will take the same parameters as a message object but use
        the current message object as a reference.  It will copy the context
        from the existing message object and add any context passed in to
        the function.  Check for a destination passed in to the function from
        the data object and add that to the context as a destination.  If the
        context has a source then that will be swapped with the destination
        in the context.  The new message will then have data passed in plus the
        new context generated.

        Args:
            msg_type (str): type of message
            data (dict): data for message
            context: intended context for new message

        Returns:
            Message: Message object to be used on the reply to the message
        """
        data = deepcopy(data) or {}
        context = context or {}

        new_context = deepcopy(self.context)
        for key in context:
            new_context[key] = context[key]
        if 'destination' in data:
            new_context['destination'] = data['destination']
        if 'source' in new_context and 'destination' in new_context:
            s = new_context['destination']
            new_context['destination'] = new_context['source']
            new_context['source'] = s
        return Message(msg_type, data, context=new_context)

    def response(self, data: dict = None, context: dict = None) -> _MsgBase:
        """
        Construct a response message for the message

        Constructs a reply with the data and appends the expected
        ".response" to the message

        Args:
            data (dict): message data
            context (dict): message context
        Returns
            (Message) message with the type modified to match default response
        """
        return self.reply(self.msg_type + '.response', data, context)

    def publish(self, msg_type: str, data: dict,
                context: dict = None) -> _MsgBase:
        """
        Copy the original context and add passed in context.  Delete
        any target in the new context. Return a new message object with
        passed in data and new context.  Type remains unchanged.

        Args:
            msg_type (str): type of message
            data (dict): data to send with message
            context: context added to existing context

        Returns:
            Message: Message object to publish
        """
        context = context or {}
        new_context = self.context.copy()
        for key in context:
            new_context[key] = context[key]

        if 'target' in new_context:
            del new_context['target']

        return Message(msg_type, data, context=new_context)

    @deprecated("This method is deprecated with no replacement", "0.1.0")
    def utterance_remainder(self):
        """
        DEPRECATED - mycroft-core hack, used by some skills in the wild

        For intents get the portion not consumed by Adapt.

        For example: if they say 'Turn on the family room light' and there are
        entity matches for "turn on" and "light", then it will leave behind
        " the family room " which is then normalized to "family room".

        Returns:
            str: Leftover words or None if not an utterance.
        """
        utt = normalize(self.data.get("utterance", ""))
        if utt and "__tags__" in self.data:
            for token in self.data["__tags__"]:
                # Substitute only whole words matching the token
                utt = re.sub(r'\b' + token.get("key", "") + r"\b", "", utt)
        return normalize(utt)


def encrypt_as_dict(key: str, data: str, nonce=None) -> dict:
    ciphertext, tag, nonce = encrypt(key, data, nonce=nonce)
    return {"ciphertext": hexlify(ciphertext).decode('utf-8'),
            "tag": hexlify(tag).decode('utf-8'),
            "nonce": hexlify(nonce).decode('utf-8')}


def decrypt_from_dict(key: str, data: dict) -> str:
    ciphertext = unhexlify(data["ciphertext"])
    if data.get("tag") is None:  # web crypto
        ciphertext, tag = ciphertext[:-16], ciphertext[-16:]
    else:
        tag = unhexlify(data["tag"])
    nonce = unhexlify(data["nonce"])
    return decrypt(key, ciphertext, tag, nonce)


def dig_for_message(max_records: int = 10) -> Optional[Message]:
    """
    Dig Through the stack for message. Looks at the current stack
    for a passed argument of type 'Message'.
    Args:
        max_records (int): Maximum number of stack records to look through

    Returns:
        Message if found in args, else None
    """
    stack = inspect.stack()[1:]  # First frame will be this function call
    stack = stack if len(stack) <= max_records else stack[:max_records]
    for record in stack:
        args = inspect.getargvalues(record.frame)
        if args.args:
            for arg in args.args:
                if isinstance(args.locals[arg], Message):
                    return args.locals[arg]
    return None


class CollectionMessage(Message, _CollectionMsgBase):
    """Extension of the Message class for use with collect handlers.

    The class provides the convenience methods success and failure to report
    these states back to the origin.
    """

    def __init__(self, msg_type, handler_id, query_id, data=None, context=None):
        super().__init__(msg_type, data, context)
        self.handler_id = handler_id
        self.query_id = query_id

    @classmethod
    def from_message(cls, message, handler_id, query_id):
        """Build a CollectionMessage based of a Message object.

        Args:
            message (Message): the original message
            handler_id (str): the handler_id of the recipient
            query_id (str): the query session id

        Returns:
            CollectionMessage based on the original Message object
        """
        return cls(message.msg_type, handler_id, query_id,
                   message.data, message.context)

    def success(self, data=None, context=None):
        """Create a message indicating a successful result.

        The handler could handle the query and created some sort of response.
        The source and destination is switched in the context like when
        sending a normal response message.

            data (dict): message data
            context (dict): message context
        Returns:
            Message
        """
        data = data or {}
        data['query'] = self.query_id
        data['handler'] = self.handler_id
        data['succeeded'] = True
        response_message = self.reply(self.msg_type + '.response',
                                      data,
                                      context or self.context)
        return response_message

    def failure(self):
        """Create a message indicating a failing result.

        The handler could not handle the query.
        The source and destination is switched in the context like when
        sending a normal response message.

            data (dict): message data
            context (dict): message context
        Returns:
            Message
        """
        data = {}
        data['query'] = self.query_id
        data['handler'] = self.handler_id
        data['succeeded'] = False
        response_message = self.reply(self.msg_type + '.response',
                                      data,
                                      self.context)
        return response_message

    def extend(self, timeout):
        """Extend current timeout,

        The timeout provided will be added to the existing timeout.
        The source and destination is switched in the context like when
        sending a normal response message.

        Arguments:
            timeout (int/float): timeout extension

        Returns:
            Extension message.
        """
        data = {}
        data['query'] = self.query_id
        data['handler'] = self.handler_id
        data['timeout'] = timeout
        response_message = self.reply(self.msg_type + '.handling',
                                      data,
                                      self.context)
        return response_message


class GUIMessage(Message):
    def __init__(self, msg_type, **kwargs):
        super().__init__(msg_type, data=kwargs)

    def serialize(self) -> str:
        """This returns a string of the message info.

        This makes it easy to send over a websocket. This uses
        json dumps to generate the string with type, data and context

        Returns:
            str: a json string representation of the message.
        """
        data = self._json_dump(self.data)
        msg = orjson.dumps({'type': self.msg_type, **data}).decode("utf-8")
        if self._secret_key:
            payload = encrypt_as_dict(self._secret_key, msg)
            return orjson.dumps(payload).decode("utf-8")
        return msg

    @staticmethod
    def deserialize(value: str):
        value = Message._json_load(value)
        msg_type = value.pop("type")
        return GUIMessage(msg_type, **value)
