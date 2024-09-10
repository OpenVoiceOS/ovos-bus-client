#!/usr/bin/env python3
# each method here is a console_script defined in setup.py
# each corresponds to a cli util
from ovos_bus_client import MessageBusClient, Message
from ovos_config import Configuration
import sys
import time


def ovos_speak():
    args_count = len(sys.argv)
    if args_count == 2:
        utt = sys.argv[1]
        lang = Configuration().get("lang", "en-us")
    elif args_count == 3:
        utt = sys.argv[1]
        lang = sys.argv[2]
    else:
        print("USAGE: ovos-speak {utterance} [lang]")
        raise SystemExit(2)
    client = MessageBusClient()
    client.run_in_thread()
    if not client.connected_event.is_set():
        client.connected_event.wait()
    client.emit(Message("speak", {"utterance": utt, "lang": lang}))
    time.sleep(0.5)  # avoids crash in c++ bus server
    client.close()


def ovos_say_to():
    args_count = len(sys.argv)
    if args_count == 2:
        utt = sys.argv[1]
        lang = Configuration().get("lang", "en-us")
    elif args_count == 3:
        utt = sys.argv[1]
        lang = sys.argv[2]
    else:
        print("USAGE: ovos-say-to {utterance} [lang]")
        raise SystemExit(2)
    client = MessageBusClient()
    client.run_in_thread()
    if not client.connected_event.is_set():
        client.connected_event.wait()
    client.emit(Message("recognizer_loop:utterance", {"utterances": [utt], "lang": lang}))
    time.sleep(0.5)  # avoids crash in c++ bus server
    client.close()


def ovos_listen():
    client = MessageBusClient()
    client.run_in_thread()
    if not client.connected_event.is_set():
        client.connected_event.wait()
    client.emit(Message("mycroft.mic.listen"))
    time.sleep(0.5)  # avoids crash in c++ bus server
    client.close()


def simple_cli():
    args_count = len(sys.argv)
    if args_count == 1:
        lang = Configuration().get("lang", "en-us")
    elif args_count == 2:
        lang = sys.argv[1]
    else:
        print("USAGE: ovos-simple-cli [lang]")
        return

    client = MessageBusClient()
    client.run_in_thread()
    if not client.connected_event.is_set():
        client.connected_event.wait()
    lang = lang or Configuration().get("lang", "en-us")

    from ovos_bus_client.session import SessionManager, Session
    sess = SessionManager.default_session

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
