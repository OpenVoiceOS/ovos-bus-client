"""Deprecated. The HiveMind agent protocol lives in its own package now.

This module is a backwards-compatibility shim. It re-exports the public surface
from `hivemind-ovos-agent-plugin` and emits a DeprecationWarning on import. It
will be removed in a future release.

Migrate your imports:

    # old
    from ovos_bus_client.hpm import OVOSProtocol

    # new
    from hivemind_ovos_agent_plugin import OVOSAgentProtocol  # (OVOSProtocol alias still works)

And install the new package:

    pip install hivemind-ovos-agent-plugin

The HiveMind entry point (`hivemind-ovos-agent-plugin` under
`hivemind.agent.protocol`) is now owned by the new package, so `hivemind-core`
discovers it from there. No config changes are required.
"""

import warnings

warnings.warn(
    "ovos_bus_client.hpm is deprecated and will be removed in a future release. "
    "Install `hivemind-ovos-agent-plugin` and import `OVOSAgentProtocol` from "
    "`hivemind_ovos_agent_plugin` instead.",
    DeprecationWarning,
    stacklevel=2,
)

try:
    from hivemind_ovos_agent_plugin import OVOSAgentProtocol, OVOSProtocol
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "ovos_bus_client.hpm now requires the `hivemind-ovos-agent-plugin` package. "
        "Install it with: pip install hivemind-ovos-agent-plugin"
    ) from e


__all__ = ["OVOSAgentProtocol", "OVOSProtocol"]
