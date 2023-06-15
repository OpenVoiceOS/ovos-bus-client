# Copyright 2021 Mycroft AI Inc.
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
Small utils and tools to use with the Messagebus.
"""

import logging
from typing import Callable, Optional, Union
from ovos_bus_client.message import Message
from ovos_utils.log import deprecated


@deprecated("No direct replacement", "0.1.0")
def create_echo_function(name: Optional[str]) -> \
        Callable[[Union[Message, str]], None]:
    """
    Standard logging mechanism for Mycroft processes.

    Arguments:
        name (str): Reference name of the process

    Returns:
        func: The echo function
    """
    # TODO: Deprecate in 0.1.0
    log = logging.getLogger(name)

    def echo(message: Union[Message, str]):
        try:
            if isinstance(message, str):
                msg = Message.deserialize(message)
            else:
                msg = message
            # do not log tokens from registration messages
            if msg.msg_type == "registration":
                msg.data["token"] = None
                message = msg.serialize()
        except Exception as exc:
            log.info(f"Error: {exc}", exc_info=True)

        # Listen for messages and echo them for logging
        log.info(f"BUS: {message}")
    return echo
