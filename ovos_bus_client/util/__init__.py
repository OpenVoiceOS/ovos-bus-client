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
from ovos_bus_client.util.scheduler import EventScheduler
from ovos_bus_client.util.utils import create_echo_function
from ovos_bus_client.message import dig_for_message
from ovos_bus_client.session import SessionManager


def get_message_lang(message=None):
    message = message or dig_for_message()
    if not message:
        return None
    # old style lang param
    lang = message.data.get("lang") or message.context.get("lang")

    # new style session lang
    if not lang and "session_id" in message.context or "session" in message.context:
        sess = SessionManager.get(message)
        lang = sess.lang

    return lang

