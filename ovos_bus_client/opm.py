import threading
from dataclasses import dataclass
from typing import Optional, Iterable, Dict, List
from uuid import uuid4

from ovos_bus_client import MessageBusClient
from ovos_bus_client.message import Message
from ovos_bus_client.session import Session, SessionManager
from ovos_plugin_manager.templates.language import LanguageDetector, LanguageTranslator
from ovos_plugin_manager.templates.solvers import QuestionSolver
from ovos_utils.log import LOG
from pyee import EventEmitter


@dataclass
class Query:
    """Data structure to track an active query sent to OVOS."""
    utterance: str
    session: Session
    responses: List[str]
    handled: threading.Event
    _extend_timeout: bool = False


class OVOSMessagebusSolver(QuestionSolver):
    """
    A solver plugin that connects to OVOS via the messagebus to query spoken answers.

    Attributes:
        bus (Optional[MessageBusClient]): Active messagebus connection.
        queries (Dict[str, Query]): Tracks active sessions and their responses.
    """

    def __init__(self,
                 config: Optional[dict] = None,
                 translator: Optional[LanguageTranslator] = None,
                 detector: Optional[LanguageDetector] = None,
                 priority: int = 70,
                 enable_tx: bool = False,
                 enable_cache: bool = False,
                 internal_lang: Optional[str] = None):
        """
        Initialize the messagebus solver.

        Args:
            config: Configuration dictionary.
            translator: Optional language translator.
            detector: Optional language detector.
            priority: Solver priority level.
            enable_tx: Whether to enable translation.
            enable_cache: Whether to cache results.
            internal_lang: Internal processing language.
        """
        super().__init__(config=config or {},
                         translator=translator,
                         detector=detector,
                         priority=priority,
                         enable_tx=enable_tx,
                         enable_cache=enable_cache,
                         internal_lang=internal_lang)
        self.bus: Optional[MessageBusClient] = None
        self.queries: Dict[str, Query] = {}

        if self.config.get("autoconnect"):
            ovos_bus_address = self.config.get("host", "127.0.0.1")
            ovos_bus_port = self.config.get("port", 8181)
            self.bus = MessageBusClient(
                host=ovos_bus_address,
                port=ovos_bus_port,
                emitter=EventEmitter(),
            )
            self.bus.run_in_thread()
            self.bus.connected_event.wait()
            self.bind(self.bus)

    def bind(self, bus: MessageBusClient) -> None:
        """
        Bind to an already connected MessageBusClient instance.

        Args:
            bus: The connected bus instance.
        """
        self.bus = bus
        self.bus.on("speak", self._receive_answer)
        self.bus.on("ovos.utterance.handled", self._end_of_response)

    def _end_of_response(self, message: Message) -> None:
        """Callback for end of utterance handling signal."""
        sess = SessionManager.get(message)
        query = self.queries.get(sess.session_id)
        if query:
            query._extend_timeout = False
            query.handled.set()

    def _receive_answer(self, message: Message) -> None:
        """Callback to collect a spoken response."""
        utt = message.data.get("utterance")
        sess = SessionManager.get(message)
        query = self.queries.get(sess.session_id)
        if query and utt:
            query.responses.append(utt)
            query._extend_timeout = True

    def _ask_ovos(self, query: str,
                  lang: Optional[str] = None,
                  units: Optional[str] = None) -> Optional[Query]:
        """
        Send a query to OVOS through the bus.

        Args:
            query: The input query string.
            lang: Optional language code override.
            units: Optional system unit preferences.

        Returns:
            Query: An internal tracking object for the active query.
        """
        if not self.bus:
            LOG.error("Not connected to OVOS messagebus.")
            return None

        sess = Session(str(uuid4()))
        sess.lang = lang or sess.lang
        sess.system_unit = units or sess.system_unit

        ovos_query = Query(
            utterance=query,
            session=sess,
            responses=[],
            handled=threading.Event()
        )
        self.queries[sess.session_id] = ovos_query

        mycroft_msg = Message("recognizer_loop:utterance",
                              {"utterances": [query], "lang": sess.lang},
                              {"session": sess.serialize()})

        self.bus.emit(mycroft_msg)
        LOG.debug(f"Sent query to OVOS: {query}")
        return ovos_query

    ############################
    # abstract methods
    def get_spoken_answer(self, query: str,
                          lang: Optional[str] = None,
                          units: Optional[str] = None,
                          timeout: int = 5) -> Optional[str]:
        """
        Get the final spoken response from OVOS to a query.

        Args:
            query: User query.
            lang: Optional language code.
            units: Optional unit system (e.g. metric).
            timeout: Time in seconds to wait for a response.

        Returns:
            A single string combining all utterances returned, or None if unanswered.
        """
        ovos_query = self._ask_ovos(query, lang, units)
        if not ovos_query:
            return None

        ovos_query.handled.wait(timeout=timeout)

        while ovos_query._extend_timeout:
            ovos_query._extend_timeout = False
            ovos_query.handled.wait(timeout=timeout)

        responses = ovos_query.responses
        self.queries.pop(ovos_query.session.session_id, None)

        return "\n".join(responses) if responses else None

    def stream_utterances(self, query: str,
                          lang: Optional[str] = None,
                          units: Optional[str] = None) -> Iterable[str]:
        """
        Stream responses to a query as they are emitted.

        Args:
            query: The input query text.
            lang: Optional language code.
            units: Optional system unit.

        Yields:
            Individual spoken responses from OVOS.
        """
        ovos_query = self._ask_ovos(query, lang, units)
        if not ovos_query:
            return

        session_id = ovos_query.session.session_id
        query_obj = self.queries[session_id]

        while not query_obj.handled.is_set():
            query_obj.handled.wait(timeout=0.5)
            while query_obj.responses:
                yield query_obj.responses.pop(0)

        while query_obj.responses:
            yield query_obj.responses.pop(0)

        self.queries.pop(session_id, None)


if __name__ == "__main__":
    cfg = {"autoconnect": True}
    bot = OVOSMessagebusSolver(config=cfg)

    print(bot.spoken_answer("what is the speed of light", lang="en"))

    for a in bot.stream_utterances("execute a speed test", lang="en"):
        print(a)
