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
                                  ['host', 'port', 'route', 'ssl'])


def load_message_bus_config(**overrides):
    """Load the bits of device configuration needed to run the message bus."""
    LOG.info('Loading message bus configs')
    config = Configuration()

    try:
        websocket_configs = config['websocket']
    except KeyError as ke:
        LOG.error('No websocket configs found ({})'.format(repr(ke)))
        raise
    else:
        mb_config = MessageBusConfig(
            host=overrides.get('host') or websocket_configs.get('host') or "127.0.0.1",
            port=overrides.get('port') or websocket_configs.get('port') or 8181,
            route=overrides.get('route') or websocket_configs.get('route') or "/core",
            ssl=overrides.get('ssl') or config.get('ssl') or False
        )
        if not all([mb_config.host, mb_config.port, mb_config.route]):
            error_msg = 'Missing one or more websocket configs'
            LOG.error(error_msg)
            raise ValueError(error_msg)

    return mb_config


def client_from_config(subconf='core', file_path='/etc/mycroft/bus.conf'):
    """Load messagebus configuration from file.

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
