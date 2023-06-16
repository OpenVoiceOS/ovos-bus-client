"""Message bus configuration loader.

The message bus event handler and client use basically the same configuration.
This code is re-used in both to load config values.
"""
import json
from collections import namedtuple

from ovos_utils.log import LOG
from ovos_config.config import Configuration

# mycroft-core had this duplicated with both names...
MessageBusConfig = MessageBusClientConf = namedtuple('MessageBusClientConf',
                                                     ['host', 'port', 'route',
                                                      'ssl'])


def load_message_bus_config(**overrides) -> MessageBusConfig:
    """
    Load the bits of device configuration needed to run the message bus.
    @param overrides: Optional config overrides
        host (str): messagebus host
        port (int): messagebus port
        route (str): messagebus route
        ssl (bool): enable SSL on websocket
    @return: MessageBusConfig with valid configuration
    """
    LOG.debug('Loading message bus configs')
    config = Configuration()

    try:
        config = config['websocket']
    except KeyError as ke:
        LOG.error(f'No websocket configs found ({ke})')
        raise ke
    overrides = overrides or {}
    config = config or {}
    mb_config = MessageBusConfig(
        host=overrides.get('host') or config.get('host') or "127.0.0.1",
        port=overrides.get('port') or config.get('port') or 8181,
        route=overrides.get('route') or config.get('route') or "/core",
        ssl=overrides.get('ssl') if 'ssl' in overrides else
        config.get('ssl') if 'ssl' in config else
        False
    )

    return mb_config


def load_gui_message_bus_config(**overrides):
    """
    Load the bits of device configuration needed to run the GUI bus.
    @param overrides: Optional config overrides
        host (str): GUI ebus host
        port (int): GUI bus port
        route (str): GUI bus route
        ssl (bool): enable SSL on websocket
    @return: MessageBusConfig with valid configuration
    """
    LOG.info('Loading GUI bus configs')
    config = Configuration()

    try:
        config = config['gui']
    except KeyError as ke:
        LOG.error(f'No gui configs found ({ke})')
        raise
    overrides = overrides or {}
    config = config or {}
    mb_config = MessageBusConfig(
        host=overrides.get('host') or config.get('host') or "127.0.0.1",
        port=overrides.get('port') or config.get('port') or 18181,
        route=overrides.get('route') or config.get('route') or "/",
        ssl=overrides.get('ssl') if 'ssl' in overrides else
        config.get('ssl') if 'ssl' in config else
        False
    )

    return mb_config


def client_from_config(subconf: str = 'core',
                       file_path: str = '/etc/mycroft/bus.conf'):
    """
    Load messagebus configuration from file.

    The config is a basic json file with a number of "sub configurations"

    Ex:
    {
      "core": {
        "route": "/core",
        "port": "8181"
      }
      "gui": {
        "route": "/gui",
        "port": "8811"
      }
    }

    Arguments:
        subconf:    configuration to choose from the file, defaults to "core"
                    if omitted.
        file_path:  path to the config file, defaults to /etc/mycroft/bus.conf
                    if omitted.
    Returns:
        MessageBusClient instance based on the selected config.
    """
    from ovos_bus_client.client import MessageBusClient

    with open(file_path) as f:
        conf = json.load(f)

    return MessageBusClient(**conf[subconf])
