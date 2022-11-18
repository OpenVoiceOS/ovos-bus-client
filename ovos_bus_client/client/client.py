from ovos_bus_client.message import Message, CollectionMessage
from ovos_bus_client.util import create_echo_function
from ovos_bus_client.client.collector import MessageCollector
from ovos_bus_client.client.waiter import MessageWaiter

from mycroft_bus_client.client.client import MessageBusClient as _MycroftBusClient, MessageBusClientConf
from ovos_bus_client.session import Session, SessionManager


class MessageBusClient(_MycroftBusClient):
    """The Mycroft Messagebus Client

    The Messagebus client connects to the Mycroft messagebus service
    and allows communication with the system. It has been extended to work
    like the pyee EventEmitter and tries to offer as much convenience as
    possible to the developer.
    """

    def emit(self, message):
        """Send a message onto the message bus.

        This will both send the message to the local process using the
        event emitter and onto the Mycroft websocket for other processes.

        Args:
            message (Message): Message to send
        """
        if "session" not in message.context:
            message.context["session"] = SessionManager.get(message)
        return super().emit(message)


def echo():
    """Echo function repeating all input from a user."""
    message_bus_client = MessageBusClient()

    def repeat_utterance(message):
        message.msg_type = 'speak'
        message_bus_client.emit(message)

    message_bus_client.on('message', create_echo_function(None))
    message_bus_client.on('recognizer_loop:utterance', repeat_utterance)
    message_bus_client.run_forever()


if __name__ == "__main__":
    echo()
