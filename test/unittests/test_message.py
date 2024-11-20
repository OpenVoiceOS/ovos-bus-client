import unittest

from time import time
from unittest import TestCase
import json
from ovos_bus_client import Message
from ovos_bus_client.util import get_message_lang
from ovos_config.locale import setup_locale
from ovos_bus_client.message import dig_for_message
from ovos_bus_client.session import Session, SessionManager


def get_message_standard(message):
    return dig_for_message()


def get_message_alt_name(msg):
    return dig_for_message()


def get_message_no_name(_):
    return dig_for_message()


class TestMessage(TestCase):
    def test_serialize_deserialize(self):
        """Assert that a serized message is recreated when deserialized."""
        source = Message('test_type',
                         data={'robot': 'marvin', 'android': 'data'},
                         context={'origin': 'earth'})
        msg_string = source.serialize()
        self.assertIsInstance(msg_string, str)

        reassembled = Message.deserialize(msg_string)
        self.assertEqual(source.msg_type, reassembled.msg_type)
        self.assertEqual(source.data, reassembled.data)
        self.assertEqual(source.context, reassembled.context)
        dm = source.as_dict
        self.assertIsInstance(dm, dict)

    def test_session_serialize_deserialize(self):
        """Assert that a serized message is recreated when deserialized."""
        s = Session()

        SessionManager.update(s, make_default=True)

        source = Message('test_type',
                         data={'robot': 'marvin', 'android': 'data'},
                         context={'origin': 'earth', "session": s.serialize()})
        msg_string = source.serialize()

        reassembled = Message.deserialize(msg_string)

        self.assertEqual(source.msg_type, reassembled.msg_type)
        self.assertEqual(source.data, reassembled.data)

        # TODO - why does the dict comparison fail but string not ????
        #self.assertEqual(source.context, reassembled.context)
        self.assertEqual(json.dumps(source.context, sort_keys=True),
                         json.dumps(reassembled.context, sort_keys=True))

    def test_as_dict(self):
        pass
        # TODO

    def test_forward(self):
        pass
        # TODO

    def test_response(self):
        """Assert that the .response is added to the message type for response.
        """
        source = Message('test_type',
                         data={'robot': 'marvin', 'android': 'data'},
                         context={'origin': 'earth'})
        response_msg = source.response()
        self.assertEqual(response_msg.msg_type, "test_type.response")
        self.assertEqual(response_msg.data, {})
        self.assertEqual(response_msg.context, source.context)

    def test_publish(self):
        pass
        # TODO

    def test_reply(self):
        """Assert that the source and destination are swapped"""
        source = Message('test_type',
                         data={'robot': 'marvin', 'android': 'data'},
                         context={'source': 'earth',
                                  'destination': 'alpha centauri'})

        reply_msg = source.reply('reply_type')
        self.assertEqual(reply_msg.context["source"],
                         source.context["destination"])
        self.assertEqual(reply_msg.context["destination"],
                         source.context["source"])

        # assert that .response calls .reply internally as stated in docstrings
        response_msg = source.response()
        self.assertEqual(response_msg.context, reply_msg.context)


class TestFunctions(unittest.TestCase):
    def test_encrypt_decrypt(self):
        from ovos_bus_client.message import encrypt_as_dict, decrypt_from_dict
        # TODO

    def test_dig_for_message_simple(self):
        test_msg = Message("test message", {"test": "data"}, {"time": time()})
        self.assertEqual(test_msg, get_message_standard(test_msg))
        test_msg = Message("test message", {"test": "data"}, {"time": time()})
        self.assertEqual(test_msg, get_message_alt_name(test_msg))
        test_msg = Message("test message", {"test": "data"}, {"time": time()})
        self.assertEqual(test_msg, get_message_no_name(test_msg))

    def test_dig_for_message_nested(self):
        message = Message("test message", {"test": "data"}, {"time": time()})

        def simple_wrapper():
            return get_message_no_name(message)

        self.assertEqual(simple_wrapper(), message)

        message = Message("test message", {"test": "data"}, {"time": time()})

        def get_message():
            return dig_for_message()

        def wrapper_method(msg):
            self.assertEqual(msg, get_message())

        wrapper_method(message)

    def test_dig_for_message_invalid_type(self):
        # Message that should be ignored
        _ = Message("test message", {"test": "data"}, {"time": time()})

        def wrapper_method(_):
            return dig_for_message()
        self.assertIsNone(wrapper_method(dict()))

    def test_dig_for_message_no_method_call(self):
        # Message that should be ignored
        _ = Message("test message", {"test": "data"}, {"time": time()})
        self.assertIsNone(dig_for_message())


class TestLanguageExtraction(TestCase):
    def test_no_lang_in_message(self):
        """No lang in message should result in lang from active locale."""
        setup_locale("it-it")
        msg = Message('test msg', data={})
        self.assertEqual(get_message_lang(msg), 'it-IT')
        setup_locale("en-us")
        self.assertEqual(get_message_lang(msg), 'en-US')

    def test_lang_exists(self):
        """Message has a lang code in data, it should be used."""
        msg = Message('test msg', data={'lang': 'de-de'})
        self.assertEqual(get_message_lang(msg), 'de-DE')
        msg = Message('test msg', data={'lang': 'sv-SE'})
        self.assertEqual(get_message_lang(msg), 'sv-SE')


class TestCollectionMessage(unittest.TestCase):
    # TODO
    pass


class TestGUIMessage(unittest.TestCase):
    # TODO
    pass
