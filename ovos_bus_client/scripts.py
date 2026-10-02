#!/usr/bin/env python3
# each method here is a console_script defined in setup.py
# each corresponds to a cli util
from ovos_bus_client import MessageBusClient, Message
from ovos_config import Configuration
import sys
import time

#: Seconds to wait for the messagebus before giving up. These are one-shot
#: command line tools, so a device with no bus running has to fail fast and
#: say why, instead of blocking forever on a blank terminal.
CONNECT_TIMEOUT = 10


def _help_requested():
    return any(arg in ("-h", "--help") for arg in sys.argv[1:])


def _print_help(description, usage):
    print(f"{description}\n\nUSAGE: {usage}")
    raise SystemExit(0)


def _connect(usage):
    """Return a connected MessageBusClient, or exit with a message.

    `usage` is echoed on failure, so the user learns what the command does as
    well as why it did not run.
    """
    client = MessageBusClient()
    client.run_in_thread()
    if not client.connected_event.is_set():
        client.connected_event.wait(CONNECT_TIMEOUT)
    if not client.connected_event.is_set():
        cfg = Configuration().get("websocket", {})
        host = cfg.get("host", "0.0.0.0")
        port = cfg.get("port", 8181)
        print(f"ERROR: could not reach the messagebus at ws://{host}:{port} "
              f"after {CONNECT_TIMEOUT} seconds.\n"
              f"Is it running? Check with: "
              f"systemctl --user status ovos-messagebus.service\n\n"
              f"USAGE: {usage}", file=sys.stderr)
        client.close()
        raise SystemExit(1)
    return client


def ovos_speak():
    usage = "ovos-speak {utterance} [lang]"
    if _help_requested():
        _print_help("Speak an utterance out loud, via the OVOS TTS service.",
                    usage)
    args_count = len(sys.argv)
    if args_count == 2:
        utt = sys.argv[1]
        lang = Configuration().get("lang", "en-us")
    elif args_count == 3:
        utt = sys.argv[1]
        lang = sys.argv[2]
    else:
        print(f"USAGE: {usage}")
        raise SystemExit(2)
    client = _connect(usage)
    client.emit(Message("speak", {"utterance": utt, "lang": lang}))
    time.sleep(0.5)  # avoids crash in c++ bus server
    client.close()


def ovos_say_to():
    usage = "ovos-say-to {utterance} [lang]"
    if _help_requested():
        _print_help("Inject an utterance as if the user had spoken it, "
                    "skipping the microphone and STT.", usage)
    args_count = len(sys.argv)
    if args_count == 2:
        utt = sys.argv[1]
        lang = Configuration().get("lang", "en-us")
    elif args_count == 3:
        utt = sys.argv[1]
        lang = sys.argv[2]
    else:
        print(f"USAGE: {usage}")
        raise SystemExit(2)
    client = _connect(usage)
    client.emit(Message("recognizer_loop:utterance", {"utterances": [utt], "lang": lang}))
    time.sleep(0.5)  # avoids crash in c++ bus server
    client.close()


def ovos_listen():
    usage = "ovos-listen"
    if _help_requested():
        _print_help("Trigger the listener, as if the wake word had been "
                    "spoken.", usage)
    client = _connect(usage)
    client.emit(Message("mycroft.mic.listen"))
    time.sleep(0.5)  # avoids crash in c++ bus server
    client.close()


def simple_cli():
    usage = "ovos-simple-cli [lang]"
    if _help_requested():
        _print_help("Talk to the assistant from the terminal. Type ':exit' "
                    "to quit.", usage)
    args_count = len(sys.argv)
    if args_count == 1:
        lang = Configuration().get("lang", "en-us")
    elif args_count == 2:
        lang = sys.argv[1]
    else:
        print(f"USAGE: {usage}")
        return

    client = _connect(usage)
    lang = lang or Configuration().get("lang", "en-us")

    from ovos_bus_client.session import SessionManager, Session
    sess = SessionManager.get_default_session()

    while True:
        try:
            utt = input("Say:")
            if utt == ":exit":
                break
            client.emit(Message("recognizer_loop:utterance",
                                {"utterances": [utt], "lang": lang},
                                {"session": sess.serialize()}))
            time.sleep(0.5)  # avoids crash in c++ bus server
        except KeyboardInterrupt:
            break

    client.close()


if __name__ == "__main__":
    simple_cli()
