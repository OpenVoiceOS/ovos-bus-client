from os import walk
from os.path import join, splitext, isfile
from typing import List, Union, Optional, Callable

from ovos_utils.file_utils import resolve_ovos_resource_file, resolve_resource_file
from ovos_utils.log import LOG, log_deprecation
from ovos_bus_client.util import get_mycroft_bus
from ovos_utils.gui import can_use_gui
from ovos_config import Configuration
from ovos_bus_client.message import Message


def extend_about_data(about_data: Union[list, dict],
                      bus=None):
    """
    Add more information to the "About" section in the GUI.
    @param about_data: list of dict key, val information to add to the GUI
    @param bus: MessageBusClient object to emit update on
    """
    bus = bus or get_mycroft_bus()
    if isinstance(about_data, list):
        bus.emit(Message("smartspeaker.extension.extend.about",
                         {"display_list": about_data}))
    elif isinstance(about_data, dict):
        display_list = [about_data]
        bus.emit(Message("smartspeaker.extension.extend.about",
                         {"display_list": display_list}))
    else:
        LOG.error("about_data is not a list or dictionary")


class GUIWidgets:
    def __init__(self, bus=None):
        self.bus = bus or get_mycroft_bus()

    def show_widget(self, widget_type, widget_data):
        LOG.debug("Showing widget: " + widget_type)
        self.bus.emit(Message("ovos.widgets.display", {"type": widget_type, "data": widget_data}))

    def remove_widget(self, widget_type, widget_data):
        LOG.debug("Removing widget: " + widget_type)
        self.bus.emit(Message("ovos.widgets.remove", {"type": widget_type, "data": widget_data}))

    def update_widget(self, widget_type, widget_data):
        LOG.debug("Updating widget: " + widget_type)
        self.bus.emit(Message("ovos.widgets.update", {"type": widget_type, "data": widget_data}))


class _GUIDict(dict):
    """
    This is a helper dictionary subclass. It ensures that values changed
    in it are propagated to the GUI service in real time.
    """

    def __init__(self, gui, **kwargs):
        self.gui = gui
        super().__init__(**kwargs)

    def __setitem__(self, key, value):
        old = self.get(key)
        if old != value:
            super(_GUIDict, self).__setitem__(key, value)
            self.gui._sync_data()


class GUIInterface:
    """
    Interface to the Graphical User Interface, allows interaction with
    the mycroft-gui from anywhere

    Values set in this class are synced to the GUI, accessible within QML
    via the built-in sessionData mechanism.  For example, in Python you can
    write in a skill:
        self.gui['temp'] = 33
        self.gui.show_page('Weather.qml')
    Then in the Weather.qml you'd access the temp via code such as:
        text: sessionData.time
    """

    def __init__(self, skill_id: str, bus=None,
                 remote_server: str = None, config: dict = None,
                 ui_directories: dict = None):
        """
        Create an interface to the GUI module. Values set here are exposed to
        the GUI client as sessionData
        @param skill_id: ID of this interface
        @param bus: MessagebusClient object to connect to
        @param remote_server: Optional URL of a remote GUI server
        @param config: dict gui Configuration
        @param ui_directories: dict framework to directory containing resources
            `all` key should reference a `gui` directory containing all
            specific resource subdirectories
        """
        config = config or Configuration().get("gui", {})
        self.config = config
        if remote_server:
            self.config["remote-server"] = remote_server
        self._bus = bus
        self.__session_data = {}  # synced to GUI for use by this skill's pages
        self._pages = []
        self.current_page_idx = -1
        self._skill_id = skill_id
        self.on_gui_changed_callback = None
        self._events = []
        self.ui_directories = ui_directories or dict()
        if bus:
            self.set_bus(bus)

    @property
    def remote_url(self) -> Optional[str]:
        """Returns configuration value for url of remote-server."""
        return self.config.get('remote-server')

    @remote_url.setter
    def remote_url(self, val: str):
        self.config["remote-server"] = val

    def set_bus(self, bus=None):
        self._bus = bus or get_mycroft_bus()
        self.setup_default_handlers()

    @property
    def bus(self):
        """
        Return the attached MessageBusClient
        """
        return self._bus

    @bus.setter
    def bus(self, val):
        self.set_bus(val)

    @property
    def skill_id(self) -> str:
        """
        Return the ID of the module implementing this interface
        """
        return self._skill_id

    @skill_id.setter
    def skill_id(self, val: str):
        self._skill_id = val

    @property
    def page(self) -> Optional[str]:
        """
        Return the active GUI page name to show
        """
        if not len(self._pages) or self.current_page_idx >= len(self._pages):
            return None
        return self._pages[self.current_page_idx]

    @property
    def connected(self) -> bool:
        """
        Returns True if at least 1 remote gui is connected or if gui is
        installed and running locally, else False
        """
        if not self.bus:
            return False
        return can_use_gui(self.bus)

    @property
    def pages(self) -> List[str]:
        """
        Get a list of the active page ID's managed by this interface
        """
        return self._pages

    def build_message_type(self, event: str) -> str:
        """
        Ensure the specified event prepends this interface's `skill_id`
        """
        if not event.startswith(f'{self.skill_id}.'):
            event = f'{self.skill_id}.' + event
        return event

    # events
    def setup_default_handlers(self):
        """
        Sets the handlers for the default messages.
        """
        msg_type = self.build_message_type('set')
        self.bus.on(msg_type, self.gui_set)
        self._events.append((msg_type, self.gui_set))
        self.bus.on("gui.request_page_upload", self.upload_gui_pages)
        if self.ui_directories:
            LOG.debug("Volunteering gui page upload")
            self.bus.emit(Message("gui.volunteer_page_upload",
                                  {'skill_id': self.skill_id},
                                  {'source': self.skill_id, "destination": ["gui"]}))

    def upload_gui_pages(self, message: Message):
        """
        Emit a response Message with all known GUI files managed by
        this interface for the requested infrastructure
        @param message: `gui.request_page_upload` Message requesting pages
        """
        if not self.ui_directories:
            LOG.debug("No UI resources to upload")
            return

        requested_skill = message.data.get("skill_id") or self._skill_id
        if requested_skill != self._skill_id:
            # GUI requesting a specific skill to upload other than this one
            return

        request_res_type = message.data.get("framework") or "all" if "all" in \
                                                                     self.ui_directories else "qt5"
        # Note that ui_directory "all" is a special case that will upload all
        # gui files, including all framework subdirectories
        if request_res_type not in self.ui_directories:
            LOG.warning(f"Requested UI files not available: {request_res_type}")
            return
        LOG.debug(f"Requested upload resources for: {request_res_type}")
        pages = dict()
        # `pages` keys are unique identifiers in the scope of this interface;
        # if ui_directory is "all", then pages are prefixed with `<framework>/`
        res_dir = self.ui_directories[request_res_type]
        for path, _, files in walk(res_dir):
            for file in files:
                try:
                    full_path: str = join(path, file)
                    page_name = full_path.replace(f"{res_dir}/", "", 1)
                    with open(full_path, 'rb') as f:
                        file_bytes = f.read()
                    pages[page_name] = file_bytes.hex()
                except Exception as e:
                    LOG.exception(f"{file} not uploaded: {e}")
        # Note that `pages` in this context include file extensions
        self.bus.emit(message.forward("gui.page.upload",
                                      {"__from": self.skill_id,
                                       "framework": request_res_type,
                                       "pages": pages}))

    def register_handler(self, event: str, handler: Callable):
        """
        Register a handler for GUI events.

        will be prepended with self.skill_id.XXX if missing in event

        When using the triggerEvent method from Qt
        triggerEvent("event", {"data": "cool"})

        Args:
            event (str):    event to catch
            handler:        function to handle the event
        """
        if not self.bus:
            raise RuntimeError("bus not set, did you call self.bind() ?")
        event = self.build_message_type(event)
        self._events.append((event, handler))
        self.bus.on(event, handler)

    def set_on_gui_changed(self, callback: Callable):
        """
        Registers a callback function to run when a value is
        changed from the GUI.

        Arguments:
            callback:   Function to call when a value is changed
        """
        self.on_gui_changed_callback = callback

    # internals
    def gui_set(self, message: Message):
        """
        Handler catching variable changes from the GUI.

        Arguments:
            message: Messagebus message
        """
        for key in message.data:
            self[key] = message.data[key]
        if self.on_gui_changed_callback:
            self.on_gui_changed_callback()

    def _sync_data(self):
        if not self.bus:
            raise RuntimeError("bus not set, did you call self.bind() ?")
        data = self.__session_data.copy()
        data.update({'__from': self.skill_id})
        self.bus.emit(Message("gui.value.set", data))

    def __setitem__(self, key, value):
        """Implements set part of dict-like behaviour with named keys."""
        old = self.__session_data.get(key)
        if old == value:  # no need to sync
            return

        # cast to helper dict subclass that syncs data
        if isinstance(value, dict) and not isinstance(value, _GUIDict):
            value = _GUIDict(self, **value)

        self.__session_data[key] = value

        # emit notification (but not needed if page has not been shown yet)
        if self.page:
            self._sync_data()

    def __getitem__(self, key):
        """Implements get part of dict-like behaviour with named keys."""
        return self.__session_data[key]

    def get(self, *args, **kwargs):
        """Implements the get method for accessing dict keys."""
        return self.__session_data.get(*args, **kwargs)

    def __contains__(self, key):
        """
        Implements the "in" operation.
        """
        return self.__session_data.__contains__(key)

    def clear(self):
        """
        Reset the value dictionary, and remove namespace from GUI.

        This method does not close the GUI for a Skill. For this purpose see
        the `release` method.
        """
        self.__session_data = {}
        self._pages = []
        self.current_page_idx = -1
        if not self.bus:
            raise RuntimeError("bus not set, did you call self.bind() ?")
        self.bus.emit(Message("gui.clear.namespace",
                              {"__from": self.skill_id}))

    def send_event(self, event_name: str,
                   params: Union[dict, list, str, int, float, bool] = None):
        """
        Trigger a gui event.

        Arguments:
            event_name (str): name of event to be triggered
            params: json serializable object containing any parameters that
                    should be sent along with the request.
        """
        params = params or {}
        if not self.bus:
            raise RuntimeError("bus not set, did you call self.bind() ?")
        self.bus.emit(Message("gui.event.send",
                              {"__from": self.skill_id,
                               "event_name": event_name,
                               "params": params}))

    def _pages2uri(self, page_names: List[str]) -> List[str]:
        """
        Get a list of resolved URIs from a list of string page names.
        @param page_names: List of GUI resource names (file basenames) to locate
        @return: List of resolved paths to the requested pages
        """
        # TODO: This method resolves absolute file paths. These will no longer
        #       be used with the implementation of `ovos-gui`
        page_urls = []
        extra_dirs = list(self.ui_directories.values()) or list()
        for name in page_names:
            # Prefer plugin-specific resources first, then fallback to core
            page = resolve_ovos_resource_file(name, extra_dirs) or \
                   resolve_ovos_resource_file(join('ui', name), extra_dirs) or \
                   resolve_resource_file(name, config=self.config) or \
                   resolve_resource_file(join('ui', name), config=self.config)

            if page:
                if self.remote_url:
                    page_urls.append(self.remote_url + "/" + page)
                elif page.startswith("file://"):
                    page_urls.append(page)
                else:
                    page_urls.append("file://" + page)
            else:
                # This is expected; with `ovos-gui`, pages are referenced by ID
                # rather than filename in order to support multiple frameworks
                LOG.debug(f"Requested page not resolved to a file: {page}")
        LOG.debug(f"Resolved pages: {page_urls}")
        return page_urls

    @staticmethod
    def _normalize_page_name(page_name: str) -> str:
        """
        Normalize a requested GUI resource
        @param page_name: string name of a GUI resource
        @return: normalized string name (`.qml` removed for other GUI support)
        """
        if isfile(page_name):
            log_deprecation("GUI resources should specify a resource name and "
                            "not a file path.", "0.1.0")
            return page_name
        file, ext = splitext(page_name)
        if ext == ".qml":
            log_deprecation("GUI resources should exclude gui-specific file "
                            f"extensions. This call should probably pass "
                            f"`{file}`, instead of `{page_name}`", "0.1.0")
            return file

        return page_name

    # base gui interactions
    def show_page(self, name: str, override_idle: Union[bool, int] = None,
                  override_animations: bool = False, index: int = 0,
                   remove_others=False):
        """
        Request to show a page in the GUI.
        @param name: page resource requested
        @param override_idle: number of seconds to override display for;
            if True, override display indefinitely
        @param override_animations: if True, disables all GUI animations
        """
        self.show_pages([name], index, override_idle, override_animations, remove_others)

    def show_pages(self, page_names: List[str], index: int = 0,
                   override_idle: Union[bool, int] = None,
                   override_animations: bool = False,
                   remove_others=False):
        """
        Request to show a list of pages in the GUI.
        @param page_names: list of page resources requested
        @param index: position to insert pages at (default 0)
        @param override_idle: number of seconds to override display for;
            if True, override display indefinitely
        @param override_animations: if True, disables all GUI animations
        """
        if not self.bus:
            raise RuntimeError("bus not set, did you call self.bind() ?")
        if isinstance(page_names, str):
            page_names = [page_names]
        if not isinstance(page_names, list):
            raise ValueError('page_names must be a list')

        if index > len(page_names):
            LOG.error('Default index is larger than page list length')
            index = len(page_names) - 1

        # TODO: deprecate sending page_urls after ovos_gui implementation
        page_urls = self._pages2uri(page_names)
        page_names = [self._normalize_page_name(n) for n in page_names]

        if remove_others:
            self.remove_all_pages(except_pages=page_names)

        self._pages = page_names
        self.current_page_idx = index

        # First sync any data...
        data = self.__session_data.copy()
        data.update({'__from': self.skill_id})
        LOG.debug(f"Updating gui data: {data}")
        self.bus.emit(Message("gui.value.set", data))

        # finally tell gui what to show
        self.bus.emit(Message("gui.page.show",
                              {"page": page_urls,
                               "page_names": page_names,
                               "ui_directories": self.ui_directories,
                               "index": index,
                               "__from": self.skill_id,
                               "__idle": override_idle,
                               "__animations": override_animations}))

    def remove_page(self, page: str):
        """
        Remove a single page from the GUI.
        @param page: Name of page to remove
        """
        self.remove_pages([page])

    def remove_pages(self, page_names: List[str]):
        """
        Request to remove a list of pages from the GUI.
        @param page_names: list of page resources requested
        """
        if not self.bus:
            raise RuntimeError("bus not set, did you call self.bind() ?")
        if isinstance(page_names, str):
            page_names = [page_names]
        if not isinstance(page_names, list):
            raise ValueError('page_names must be a list')
        # TODO: deprecate sending page_urls after ovos_gui implementation
        page_urls = self._pages2uri(page_names)
        page_names = [self._normalize_page_name(n) for n in page_names]
        self.bus.emit(Message("gui.page.delete",
                              {"page": page_urls,
                               "page_names": page_names,
                               "__from": self.skill_id}))

    def remove_all_pages(self, except_pages=None):
        """
        Request to remove all pages from the GUI.
        @param except_pages: list of optional page resources to keep
        """
        if not self.bus:
            raise RuntimeError("bus not set, did you call self.bind() ?")
        self.bus.emit(Message("gui.page.delete.all",
                              {"__from": self.skill_id,
                               "except": except_pages or []}))

    # Utils / Templates

    # backport - PR https://github.com/MycroftAI/mycroft-core/pull/2862
    def show_notification(self, content: str, duration: int = 10,
                          action: str = None, noticetype: str = "transient",
                          style: str = "info",
                          callback_data: Optional[dict] = None):
        """Display a Notification on homepage in the GUI.
        Arguments:
            content (str): Main text content of a notification, Limited
            to two visual lines.
            duration (int): seconds to display notification for
            action (str): Callback to any event registered by the skill
            to perform a certain action when notification is clicked.
            noticetype (str):
                transient: 'Default' displays a notification with a timeout.
                sticky: displays a notification that sticks to the screen.
            style (str):
                info: 'Default' displays a notification with information styling
                warning: displays a notification with warning styling
                success: displays a notification with success styling
                error: displays a notification with error styling
            callback_data (dict): data dictionary available to use with action
        """
        # TODO: Define enums for style and noticetype
        if not self.bus:
            raise RuntimeError("bus not set, did you call self.bind() ?")
        # GUI does not accept NONE type, send an empty dict
        # Sending NONE will corrupt entries in the model
        callback_data = callback_data or dict()
        self.bus.emit(Message("ovos.notification.api.set",
                              data={
                                  "duration": duration,
                                  "sender": self.skill_id,
                                  "text": content,
                                  "action": action,
                                  "type": noticetype,
                                  "style": style,
                                  "callback_data": callback_data
                              }))

    def show_controlled_notification(self, content: str, style: str = "info"):
        """
        Display a controlled Notification in the GUI.
        Arguments:
            content (str): Main text content of a notification, Limited
            to two visual lines.
            style (str):
                info: 'Default' displays a notification with information styling
                warning: displays a notification with warning styling
                success: displays a notification with success styling
                error: displays a notification with error styling
        """
        # TODO: Define enum for style
        if not self.bus:
            raise RuntimeError("bus not set, did you call self.bind() ?")
        self.bus.emit(Message("ovos.notification.api.set.controlled",
                              data={
                                  "sender": self.skill_id,
                                  "text": content,
                                  "style": style
                              }))

    def remove_controlled_notification(self):
        """
        Remove a controlled Notification in the GUI.
        """
        if not self.bus:
            raise RuntimeError("bus not set, did you call self.bind() ?")
        self.bus.emit(Message("ovos.notification.api.remove.controlled"))

    def show_text(self, text: str, title: Optional[str] = None,
                  override_idle: Union[int, bool] = None,
                  override_animations: bool = False):
        """
        Display a GUI page for viewing simple text.

        Arguments:
            text (str): Main text content.  It will auto-paginate
            title (str): A title to display above the text content.
            override_idle (boolean, int):
                True: Takes over the resting page indefinitely
                (int): Delays resting page for the specified number of
                       seconds.
            override_animations (boolean):
                True: Disables showing all platform skill animations.
                False: 'Default' always show animations.
        """
        self["text"] = text
        self["title"] = title
        self.show_page("SYSTEM_TextFrame.qml", override_idle,
                       override_animations)

    def show_image(self, url: str, caption: Optional[str] = None,
                   title: Optional[str] = None,
                   fill: str = None, background_color: str = None,
                   override_idle: Union[int, bool] = None,
                   override_animations: bool = False):
        """
        Display a GUI page for viewing an image.

        Arguments:
            url (str): Pointer to the image
            caption (str): A caption to show under the image
            title (str): A title to display above the image content
            fill (str): Fill type supports 'PreserveAspectFit',
            'PreserveAspectCrop', 'Stretch'
            background_color (str): A background color for
            the page in hex i.e. #000000
            override_idle (boolean, int):
                True: Takes over the resting page indefinitely
                (int): Delays resting page for the specified number of
                       seconds.
            override_animations (boolean):
                True: Disables showing all platform skill animations.
                False: 'Default' always show animations.
        """
        self["image"] = url
        self["title"] = title
        self["caption"] = caption
        self["fill"] = fill
        self["background_color"] = background_color
        self.show_page("SYSTEM_ImageFrame.qml", override_idle,
                       override_animations)

    def show_animated_image(self, url: str, caption: Optional[str] = None,
                            title: Optional[str] = None,
                            fill: str = None, background_color: str = None,
                            override_idle: Union[int, bool] = None,
                            override_animations: bool = False):
        """
        Display a GUI page for viewing an image.

        Args:
            url (str): Pointer to the .gif image
            caption (str): A caption to show under the image
            title (str): A title to display above the image content
            fill (str): Fill type supports 'PreserveAspectFit',
            'PreserveAspectCrop', 'Stretch'
            background_color (str): A background color for
            the page in hex i.e. #000000
            override_idle (boolean, int):
                True: Takes over the resting page indefinitely
                (int): Delays resting page for the specified number of
                       seconds.
            override_animations (boolean):
                True: Disables showing all platform skill animations.
                False: 'Default' always show animations.
        """
        self["image"] = url
        self["title"] = title
        self["caption"] = caption
        self["fill"] = fill
        self["background_color"] = background_color
        self.show_page("SYSTEM_AnimatedImageFrame.qml", override_idle,
                       override_animations)

    def show_html(self, html: str, resource_url: Optional[str] = None,
                  override_idle: Union[int, bool] = None,
                  override_animations: bool = False):
        """
        Display an HTML page in the GUI.

        Args:
            html (str): HTML text to display
            resource_url (str): Pointer to HTML resources
            override_idle (boolean, int):
                True: Takes over the resting page indefinitely
                (int): Delays resting page for the specified number of
                       seconds.
            override_animations (boolean):
                True: Disables showing all platform skill animations.
                False: 'Default' always show animations.
        """
        self["html"] = html
        self["resourceLocation"] = resource_url
        self.show_page("SYSTEM_HtmlFrame.qml", override_idle,
                       override_animations)

    def show_url(self, url: str, override_idle: Union[int, bool] = None,
                 override_animations: bool = False):
        """
        Display an HTML page in the GUI.

        Args:
            url (str): URL to render
            override_idle (boolean, int):
                True: Takes over the resting page indefinitely
                (int): Delays resting page for the specified number of
                       seconds.
            override_animations (boolean):
                True: Disables showing all platform skill animations.
                False: 'Default' always show animations.
        """
        self["url"] = url
        self.show_page("SYSTEM_UrlFrame.qml", override_idle,
                       override_animations)

    def show_input_box(self, title: Optional[str] = None,
                       placeholder: Optional[str] = None,
                       confirm_text: Optional[str] = None,
                       exit_text: Optional[str] = None,
                       override_idle: Union[int, bool] = None,
                       override_animations: bool = False):
        """
        Display a fullscreen UI for a user to enter text and confirm or cancel
        @param title: title of input UI should describe what the input is
        @param placeholder: default text hint to show in an empty entry box
        @param confirm_text: text to display on the submit/confirm button
        @param exit_text: text to display on the cancel/exit button
        @param override_idle: if True, takes over the resting page indefinitely
            else Delays resting page for the specified number of seconds.
        @param override_animations: disable showing all platform animations
        """
        self["title"] = title
        self["placeholder"] = placeholder
        self["skill_id_handler"] = self.skill_id
        if not confirm_text:
            self["confirm_text"] = "Confirm"
        else:
            self["confirm_text"] = confirm_text

        if not exit_text:
            self["exit_text"] = "Exit"
        else:
            self["exit_text"] = exit_text

        self.show_page("SYSTEM_InputBox.qml", override_idle,
                       override_animations)

    def remove_input_box(self):
        """
        Remove an input box shown by `show_input_box`
        """
        LOG.info(f"GUI pages length {len(self._pages)}")
        if len(self._pages) > 1:
            self.remove_page("SYSTEM_InputBox.qml")
        else:
            self.release()

    def release(self):
        """
        Signal that this skill is no longer using the GUI,
        allow different platforms to properly handle this event.
        Also calls self.clear() to reset the state variables
        Platforms can close the window or go back to previous page
        """
        if not self.bus:
            raise RuntimeError("bus not set, did you call self.bind() ?")
        self.clear()
        self.bus.emit(Message("mycroft.gui.screen.close",
                              {"skill_id": self.skill_id}))

    def shutdown(self):
        """
        Shutdown gui interface.

        Clear pages loaded through this interface and remove the bus events
        """
        if self.bus:
            self.release()
            for event, handler in self._events:
                self.bus.remove(event, handler)
