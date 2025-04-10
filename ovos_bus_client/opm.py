from threading import Event
from typing import Optional, Iterable
from uuid import uuid4

from ovos_bus_client import MessageBusClient
from ovos_bus_client.message import Message
from ovos_bus_client.session import Session
from pyee import EventEmitter
from ovos_plugin_manager.templates.language import LanguageDetector, LanguageTranslator
from ovos_plugin_manager.templates.solvers import QuestionSolver
from ovos_utils.log import LOG


class OVOSMessagebusSolver(QuestionSolver):
    def __init__(self, config=None,
                 translator: Optional[LanguageTranslator] = None,
                 detector: Optional[LanguageDetector] = None,
                 priority: int = 70,
                 enable_tx: bool = False,
                 enable_cache: bool = False,
                 internal_lang: Optional[str] = None):
        super().__init__(config=config, translator=translator,
                         detector=detector, priority=priority,
                         enable_tx=enable_tx, enable_cache=enable_cache,
                         internal_lang=internal_lang)
        self.bus = None
        self._response = Event()
        self._responses = []
        if self.config.get("autoconnect"):
            ovos_bus_address = self.config.get("host") or "127.0.0.1"
            ovos_bus_port = self.config.get("port") or 8181
            self.bus = MessageBusClient(
                host=ovos_bus_address,
                port=ovos_bus_port,
                emitter=EventEmitter(),
            )
            self.bus.run_in_thread()
            self.bus.connected_event.wait()
            self.bind(self.bus)
        self._extend_timeout = False
        self.session = Session(session_id=str(uuid4()))
        self._stream = False

    def bind(self, bus: MessageBusClient):
        """if you want to re-use a open connection"""
        self.bus = bus
        self.bus.on("speak", self._receive_answer)
        self.bus.on("ovos.utterance.handled", self._end_of_response)

    def _end_of_response(self, message):
        self._response.set()
        self._extend_timeout = False

    def _receive_answer(self, message):
        utt = message.data["utterance"]
        self._responses.append(utt)
        self._extend_timeout = True

    def _ask_ovos(self, query: str,
                  lang: Optional[str] = None,
                  units: Optional[str] = None,):
        if self.bus is None:
            LOG.error("not connected to OVOS")
            return

        self.session.lang = lang or self.session.lang
        self.session.system_unit = units or self.session.system_unit

        self._response.clear()
        self._responses = []
        self._extend_timeout = False
        mycroft_msg = Message("recognizer_loop:utterance",
                              {"utterances": [query],
                               "lang": self.session.lang},
                              {"session": self.session.serialize()})
        self.bus.emit(mycroft_msg)
        LOG.debug("waiting for end of intent handling...")

    ############################
    # abstract methods
    def get_spoken_answer(self, query: str,
                          lang: Optional[str] = None,
                          units: Optional[str] = None,
                          timeout: int = 5) -> Optional[str]:
        """
        Obtain the spoken answer for a given query.

        Args:
            query (str): The query text.
            lang (Optional[str]): Optional language code. Defaults to None.
            units (Optional[str]): Optional units for the query. Defaults to None.

        Returns:
            str: The spoken answer as a text response.
        """
        self._ask_ovos(query, lang, units)

        self._response.wait(timeout=timeout)
        while self._extend_timeout:
            self._extend_timeout = False
            self._response.wait(timeout=5)
        if self._responses:
            # merge multiple speak messages into one
            return "\n".join(self._responses)
        return None  # let next solver attempt


    def stream_utterances(self, query: str,
                          lang: Optional[str] = None,
                          units: Optional[str] = None) -> Iterable[str]:
        """
        Stream utterances for the given query as they become available.

        Args:
            query (str): The query text.
            lang (Optional[str]): Optional language code. Defaults to None.
            units (Optional[str]): Optional units for the query. Defaults to None.

        Returns:
            Iterable[str]: An iterable of utterances.
        """
        self._ask_ovos(query, lang, units)
        while not self._response.is_set():
            if self._responses:
                yield self._responses.pop(0)
            self._response.wait(timeout=0.1)

        while self._responses:
            yield self._responses.pop(0)

if __name__ == "__main__":
    cfg = {
        "autoconnect": True
    }
    bot = OVOSMessagebusSolver(config=cfg)
    for a in bot.stream_utterances("what is the speed of light?"):
        print(a)
