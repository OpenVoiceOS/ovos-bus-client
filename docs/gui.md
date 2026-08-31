# GUI interface

`GUIInterface` (`ovos_bus_client/apis/gui.py`) is the skill- and plugin-facing
API for driving the OVOS GUI over the messagebus. Set `gui["key"] = value` to
sync session data to every connected client, register handlers for QML-side
events, and request a screen via the template API.

## Spec conformance (OVOS-GUI-1)

This is the bus-client precursor to the template-based GUI described by the
`OVOS-GUI-1` architecture spec. The surface is being narrowed so that OVOS
components can only request **pre-defined system templates**, never arbitrary
QML resource names:

- **`PageTemplates` enum** — the only pages a skill or plugin may show:
  `IDLE` (reserved for the `ovos-gui` service), `LOADING`, `STATUS`, `TEXT`,
  `ERROR`, `IMAGE`, `ANIMATED_IMAGE`, `HTML`, `URL`, `WEATHER`, `CLOCK`, and
  `FACE` (avatar). `page`/`pages` are now typed as `PageTemplates`.
- **Page management is internal.** `show_page(s)`, `remove_page(s)`,
  `remove_all_pages`, `build_message_type`, and `setup_default_handlers` are now
  private (`_`-prefixed). High-level helpers such as `show_face`,
  `show_loading_animation`, and `show_status_animation` route through these
  templates instead of named `.qml` files.
- **Removed legacy surface** — `GUIWidgets`, `extend_about_data`,
  `ui_directories`/local GUI-file caching, the custom notification helpers, and
  per-skill `.qml` page normalization. GUIs no longer load skill-supplied QML.

## `EnclosureAPI` is deprecated

`EnclosureAPI` (`ovos_bus_client/apis/enclosure.py`) is no longer a core
abstraction. Mark 1 / hardware-specific visual output should move into a
dedicated `ovos-ui-enclosure-protocol-plugin`; all in-core visual output is
done through `GUIInterface`.
