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
import time
from datetime import timedelta
from os.path import abspath
from threading import Lock
from typing import List, Union

from ovos_utils.gui import is_gui_connected, is_gui_running
from ovos_utils.log import LOG, deprecated

from ovos_bus_client.message import Message
from ovos_bus_client.message import dig_for_message
from ovos_bus_client.util import get_mycroft_bus


def ensure_uri(s: str):
    """
    Interpret paths as file:// uri's.

    Args:
        s: string path to be checked

    Returns:
        if s is uri, s is returned otherwise file:// is prepended
    """
    if isinstance(s, str):
        if '://' not in s:
            return 'file://' + abspath(s)
        else:
            return s
    elif isinstance(s, (tuple, list)):  # Handle (mime, uri) arg
        if '://' not in s[0]:
            return 'file://' + abspath(s[0]), s[1]
        else:
            return s
    else:
        raise ValueError('Invalid track')


class ClassicAudioServiceInterface:
    """AudioService class for interacting with the classic mycroft audio subsystem

    DEPRECATED: only works in ovos-core <= 0.0.8

    it has been removed from ovos-audio with the move to ovos-media
    
    "mycroft.audio.XXX" has been replaced by "ovos.audio.XXX" namespace

    use OCPInterface instead

    Args:
        bus: OpenVoiceOS messagebus connection
    """

    @deprecated("removed from ovos-audio with the adoption of ovos-media service, "
                "use OCPInterface instead", "0.1.0")
    def __init__(self, bus=None):
        self.bus = bus or get_mycroft_bus()

    def queue(self, tracks=None):
        """Queue up a track to playing playlist.

        Args:
            tracks: track uri or list of track uri's
                    Each track can be added as a tuple with (uri, mime)
                    to give a hint of the mime type to the system
        """
        tracks = tracks or []
        if isinstance(tracks, (str, tuple)):
            tracks = [tracks]
        elif not isinstance(tracks, list):
            raise ValueError
        tracks = [ensure_uri(t) for t in tracks]
        self.bus.emit(Message('mycroft.audio.service.queue',
                              data={'tracks': tracks}))

    def play(self, tracks=None, utterance=None, repeat=None):
        """Start playback.

        Args:
            tracks: track uri or list of track uri's
                    Each track can be added as a tuple with (uri, mime)
                    to give a hint of the mime type to the system
            utterance: forward utterance for further processing by the
                        audio service.
            repeat: if the playback should be looped
        """
        repeat = repeat or False
        tracks = tracks or []
        utterance = utterance or ''
        if isinstance(tracks, (str, tuple)):
            tracks = [tracks]
        elif not isinstance(tracks, list):
            raise ValueError
        tracks = [ensure_uri(t) for t in tracks]
        self.bus.emit(Message('mycroft.audio.service.play',
                              data={'tracks': tracks,
                                    'utterance': utterance,
                                    'repeat': repeat}))

    def stop(self):
        """Stop the track."""
        self.bus.emit(Message('mycroft.audio.service.stop'))

    def next(self):
        """Change to next track."""
        self.bus.emit(Message('mycroft.audio.service.next'))

    def prev(self):
        """Change to previous track."""
        self.bus.emit(Message('mycroft.audio.service.prev'))

    def pause(self):
        """Pause playback."""
        self.bus.emit(Message('mycroft.audio.service.pause'))

    def resume(self):
        """Resume paused playback."""
        self.bus.emit(Message('mycroft.audio.service.resume'))

    def get_track_length(self):
        """
        getting the duration of the audio in seconds
        """
        length = 0
        info = self.bus.wait_for_response(
            Message('mycroft.audio.service.get_track_length'),
            timeout=1)
        if info:
            length = info.data.get("length") or 0
        return length / 1000  # convert to seconds

    def get_track_position(self):
        """
        get current position in seconds
        """
        pos = 0
        info = self.bus.wait_for_response(
            Message('mycroft.audio.service.get_track_position'),
            timeout=1)
        if info:
            pos = info.data.get("position") or 0
        return pos / 1000  # convert to seconds

    def set_track_position(self, seconds):
        """Seek X seconds.

        Arguments:
            seconds (int): number of seconds to seek, if negative rewind
        """
        self.bus.emit(Message('mycroft.audio.service.set_track_position',
                              {"position": seconds * 1000}))  # convert to ms

    def seek(self, seconds: Union[int, float, timedelta] = 1):
        """Seek X seconds.

        Args:
            seconds (int): number of seconds to seek, if negative rewind
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        if seconds < 0:
            self.seek_backward(abs(seconds))
        else:
            self.seek_forward(seconds)

    def seek_forward(self, seconds: Union[int, float, timedelta] = 1):
        """Skip ahead X seconds.

        Args:
            seconds (int): number of seconds to skip
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        self.bus.emit(Message('mycroft.audio.service.seek_forward',
                              {"seconds": seconds}))

    def seek_backward(self, seconds: Union[int, float, timedelta] = 1):
        """Rewind X seconds

         Args:
            seconds (int): number of seconds to rewind
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        self.bus.emit(Message('mycroft.audio.service.seek_backward',
                              {"seconds": seconds}))

    def track_info(self):
        """Request information of current playing track.

        Returns:
            Dict with track info.
        """
        info = self.bus.wait_for_response(
            Message('mycroft.audio.service.track_info'),
            reply_type='mycroft.audio.service.track_info_reply',
            timeout=1)
        return info.data if info else {}

    def available_backends(self):
        """Return available audio backends.

        Returns:
            dict with backend names as keys
        """
        msg = Message('mycroft.audio.service.list_backends')
        response = self.bus.wait_for_response(msg)
        return response.data if response else {}

    @property
    def is_playing(self):
        """True if the audioservice is playing, else False."""
        return self.track_info() != {}


class OCPInterface:
    """bus api interface for OCP subsystem
    Args:
        bus: OpenVoiceOS messagebus connection
    """

    def __init__(self, bus=None):
        self.bus = bus or get_mycroft_bus()

    def _format_msg(self, msg_type, msg_data=None):
        # this method ensures all skills are .forward from the utterance
        # that triggered the skill, this ensures proper routing and metadata
        msg_data = msg_data or {}
        msg = dig_for_message()
        if msg:
            msg = msg.forward(msg_type, msg_data)
        else:
            msg = Message(msg_type, msg_data)
        # at this stage source == skills, lets indicate audio service took over
        sauce = msg.context.get("source")
        if sauce == "skills":
            msg.context["source"] = "audio_service"
        return msg

    # OCP bus api
    @staticmethod
    def norm_tracks(tracks: list):
        try:
            from ovos_utils.ocp import Playlist, MediaEntry, PluginStream, dict2entry
        except ImportError as e:
            raise RuntimeError("This method requires ovos-utils ~=0.1") from e

        """ensures a list of tracks contains only MediaEntry or Playlist items"""
        assert isinstance(tracks, list)
        # support Playlist and MediaEntry objects in tracks
        for idx, track in enumerate(tracks):
            if isinstance(track, dict):
                tracks[idx] = dict2entry(track)
            if isinstance(track, PluginStream):
                # TODO - this method will be deprecated
                #  once all SEI parsers can handle the new objects
                #  this module can serialize them just fine,
                #  but we dont know who is listening
                tracks[idx] = track.as_media_entry
            elif isinstance(track, list) and not isinstance(track, Playlist):
                tracks[idx] = OCPInterface.norm_tracks(track)
            elif not isinstance(track, MediaEntry):
                # TODO - support string uris
                # let it fail in next assert
                # log all bad entries before failing
                LOG.error(f"Bad track, invalid type: {track}")
        assert all(isinstance(t, (MediaEntry, Playlist, PluginStream)) for t in tracks)
        return tracks

    def queue(self, tracks: list):
        """Queue up a track to OCP playing playlist.

        Args:
            tracks: track dict or list of track dicts (OCP result style)
        """
        tracks = self.norm_tracks(tracks)
        msg = self._format_msg('ovos.common_play.playlist.queue',
                               {'tracks': tracks})
        self.bus.emit(msg)

    def play(self, tracks: list, utterance=None):
        """Start playback.
        Args:
            tracks: track dict or list of track dicts (OCP result style)
            utterance: forward utterance for further processing by OCP
        """
        tracks = self.norm_tracks(tracks)

        utterance = utterance or ''
        tracks = [t.as_dict for t in tracks]
        msg = self._format_msg('ovos.common_play.play',
                               {"media": tracks[0],
                                "playlist": tracks,
                                "utterance": utterance})
        self.bus.emit(msg)

    def stop(self):
        """Stop the track."""
        msg = self._format_msg("ovos.common_play.stop")
        self.bus.emit(msg)

    def next(self):
        """Change to next track."""
        msg = self._format_msg("ovos.common_play.next")
        self.bus.emit(msg)

    def prev(self):
        """Change to previous track."""
        msg = self._format_msg("ovos.common_play.previous")
        self.bus.emit(msg)

    def pause(self):
        """Pause playback."""
        msg = self._format_msg("ovos.common_play.pause")
        self.bus.emit(msg)

    def resume(self):
        """Resume paused playback."""
        msg = self._format_msg("ovos.common_play.resume")
        self.bus.emit(msg)

    def seek_forward(self, seconds=1):
        """Skip ahead X seconds.
        Args:
            seconds (int): number of seconds to skip
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        msg = self._format_msg('ovos.common_play.seek',
                               {"seconds": seconds})
        self.bus.emit(msg)

    def seek_backward(self, seconds=1):
        """Rewind X seconds
         Args:
            seconds (int): number of seconds to rewind
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        msg = self._format_msg('ovos.common_play.seek',
                               {"seconds": seconds * -1})
        self.bus.emit(msg)

    def get_track_length(self):
        """
        getting the duration of the audio in miliseconds
        """
        length = 0
        msg = self._format_msg('ovos.common_play.get_track_length')
        info = self.bus.wait_for_response(msg, timeout=1)
        if info:
            length = info.data.get("length", 0)
        return length

    def get_track_position(self):
        """
        get current position in miliseconds
        """
        pos = 0
        msg = self._format_msg('ovos.common_play.get_track_position')
        info = self.bus.wait_for_response(msg, timeout=1)
        if info:
            pos = info.data.get("position", 0)
        return pos

    def set_track_position(self, miliseconds):
        """Go to X position.
        Arguments:
           miliseconds (int): position to go to in miliseconds
        """
        msg = self._format_msg('ovos.common_play.set_track_position',
                               {"position": miliseconds})
        self.bus.emit(msg)

    def track_info(self):
        """Request information of current playing track.
        Returns:
            Dict with track info.
        """
        msg = self._format_msg('ovos.common_play.track_info')
        response = self.bus.wait_for_response(msg)
        return response.data if response else {}

    def available_backends(self):
        """Return available audio backends.
        Returns:
            dict with backend names as keys
        """
        msg = self._format_msg('ovos.common_play.list_backends')
        response = self.bus.wait_for_response(msg)
        return response.data if response else {}


class OCPAudioServiceInterface:
    """Internal OCP audio subsystem
    most likely you should use OCPInterface instead
    NOTE: this class operates with uris not with MediaEntry/Playlist/dict entries
    """

    def __init__(self, bus=None):
        self.bus = bus or get_mycroft_bus()

    def play(self, tracks=None, utterance=None, repeat=None):
        """Start playback.

        Args:
            tracks: track uri or list of track uri's
                    Each track can be added as a tuple with (uri, mime)
                    to give a hint of the mime type to the system
            utterance: forward utterance for further processing by the
                        audio service.
            repeat: if the playback should be looped
        """
        repeat = repeat or False
        tracks = tracks or []
        utterance = utterance or ''
        if isinstance(tracks, (str, tuple)):
            tracks = [tracks]
        elif not isinstance(tracks, list):
            raise ValueError
        tracks = [ensure_uri(t) for t in tracks]
        self.bus.emit(Message('ovos.audio.service.play',
                              data={'tracks': tracks,
                                    'utterance': utterance,
                                    'repeat': repeat}))

    def stop(self):
        """Stop the track."""
        self.bus.emit(Message('ovos.audio.service.stop'))

    def next(self):
        """Change to next track."""
        self.bus.emit(Message('ovos.audio.service.next'))

    def prev(self):
        """Change to previous track."""
        self.bus.emit(Message('ovos.audio.service.prev'))

    def pause(self):
        """Pause playback."""
        self.bus.emit(Message('ovos.audio.service.pause'))

    def resume(self):
        """Resume paused playback."""
        self.bus.emit(Message('ovos.audio.service.resume'))

    def get_track_length(self):
        """
        getting the duration of the audio in seconds
        """
        length = 0
        info = self.bus.wait_for_response(
            Message('ovos.audio.service.get_track_length'),
            timeout=1)
        if info:
            length = info.data.get("length") or 0
        return length / 1000  # convert to seconds

    def get_track_position(self):
        """
        get current position in seconds
        """
        pos = 0
        info = self.bus.wait_for_response(
            Message('ovos.audio.service.get_track_position'),
            timeout=1)
        if info:
            pos = info.data.get("position") or 0
        return pos / 1000  # convert to seconds

    def set_track_position(self, seconds):
        """Seek X seconds.

        Arguments:
            seconds (int): number of seconds to seek, if negative rewind
        """
        self.bus.emit(Message('ovos.audio.service.set_track_position',
                              {"position": seconds * 1000}))  # convert to ms

    def seek(self, seconds: Union[int, float, timedelta] = 1):
        """Seek X seconds.

        Args:
            seconds (int): number of seconds to seek, if negative rewind
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        if seconds < 0:
            self.seek_backward(abs(seconds))
        else:
            self.seek_forward(seconds)

    def seek_forward(self, seconds: Union[int, float, timedelta] = 1):
        """Skip ahead X seconds.

        Args:
            seconds (int): number of seconds to skip
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        self.bus.emit(Message('ovos.audio.service.seek_forward',
                              {"seconds": seconds}))

    def seek_backward(self, seconds: Union[int, float, timedelta] = 1):
        """Rewind X seconds

         Args:
            seconds (int): number of seconds to rewind
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        self.bus.emit(Message('ovos.audio.service.seek_backward',
                              {"seconds": seconds}))

    def track_info(self):
        """Request information of current playing track.

        Returns:
            Dict with track info.
        """
        info = self.bus.wait_for_response(
            Message('ovos.audio.service.track_info'),
            reply_type='ovos.audio.service.track_info_reply',
            timeout=1)
        return info.data if info else {}

    def available_backends(self):
        """Return available audio backends.

        Returns:
            dict with backend names as keys
        """
        msg = Message('ovos.audio.service.list_backends')
        response = self.bus.wait_for_response(msg)
        return response.data if response else {}

    @property
    def is_playing(self):
        """True if the audioservice is playing, else False."""
        return self.track_info() != {}


class OCPVideoServiceInterface:
    """Internal OCP video subsystem
    most likely you should use OCPInterface instead
    NOTE: this class operates with uris not with MediaEntry/Playlist/dict entries
    """

    def __init__(self, bus=None):
        self.bus = bus or get_mycroft_bus()

    def play(self, tracks=None, utterance=None, repeat=None):
        """Start playback.

        Args:
            tracks: track uri or list of track uri's
                    Each track can be added as a tuple with (uri, mime)
                    to give a hint of the mime type to the system
            utterance: forward utterance for further processing by the
                        video service.
            repeat: if the playback should be looped
        """
        repeat = repeat or False
        tracks = tracks or []
        utterance = utterance or ''
        if isinstance(tracks, (str, tuple)):
            tracks = [tracks]
        elif not isinstance(tracks, list):
            raise ValueError
        tracks = [ensure_uri(t) for t in tracks]
        self.bus.emit(Message('ovos.video.service.play',
                              data={'tracks': tracks,
                                    'utterance': utterance,
                                    'repeat': repeat}))

    def stop(self):
        """Stop the track."""
        self.bus.emit(Message('ovos.video.service.stop'))

    def next(self):
        """Change to next track."""
        self.bus.emit(Message('ovos.video.service.next'))

    def prev(self):
        """Change to previous track."""
        self.bus.emit(Message('ovos.video.service.prev'))

    def pause(self):
        """Pause playback."""
        self.bus.emit(Message('ovos.video.service.pause'))

    def resume(self):
        """Resume paused playback."""
        self.bus.emit(Message('ovos.video.service.resume'))

    def get_track_length(self):
        """
        getting the duration of the video in seconds
        """
        length = 0
        info = self.bus.wait_for_response(
            Message('ovos.video.service.get_track_length'),
            timeout=1)
        if info:
            length = info.data.get("length") or 0
        return length / 1000  # convert to seconds

    def get_track_position(self):
        """
        get current position in seconds
        """
        pos = 0
        info = self.bus.wait_for_response(
            Message('ovos.video.service.get_track_position'),
            timeout=1)
        if info:
            pos = info.data.get("position") or 0
        return pos / 1000  # convert to seconds

    def set_track_position(self, seconds):
        """Seek X seconds.

        Arguments:
            seconds (int): number of seconds to seek, if negative rewind
        """
        self.bus.emit(Message('ovos.video.service.set_track_position',
                              {"position": seconds * 1000}))  # convert to ms

    def seek(self, seconds=1):
        """Seek X seconds.

        Args:
            seconds (int): number of seconds to seek, if negative rewind
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        if seconds < 0:
            self.seek_backward(abs(seconds))
        else:
            self.seek_forward(seconds)

    def seek_forward(self, seconds: Union[int, float, timedelta] = 1):
        """Skip ahead X seconds.

        Args:
            seconds (int): number of seconds to skip
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        self.bus.emit(Message('ovos.video.service.seek_forward',
                              {"seconds": seconds}))

    def seek_backward(self, seconds: Union[int, float, timedelta] = 1):
        """Rewind X seconds

         Args:
            seconds (int): number of seconds to rewind
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        self.bus.emit(Message('ovos.video.service.seek_backward',
                              {"seconds": seconds}))

    def track_info(self):
        """Request information of current playing track.

        Returns:
            Dict with track info.
        """
        info = self.bus.wait_for_response(
            Message('ovos.video.service.track_info'),
            reply_type='ovos.video.service.track_info_reply',
            timeout=1)
        return info.data if info else {}

    def available_backends(self):
        """Return available video backends.

        Returns:
            dict with backend names as keys
        """
        msg = Message('ovos.video.service.list_backends')
        response = self.bus.wait_for_response(msg)
        return response.data if response else {}

    @property
    def is_playing(self):
        """True if the videoservice is playing, else False."""
        return self.track_info() != {}


class OCPWebServiceInterface:
    """Internal OCP web view subsystem
    most likely you should use OCPInterface instead
    NOTE: this class operates with uris not with MediaEntry/Playlist/dict entries
    """

    def __init__(self, bus=None):
        self.bus = bus or get_mycroft_bus()

    def play(self, tracks=None, utterance=None, repeat=None):
        """Start playback.

        Args:
            tracks: track uri or list of track uri's
                    Each track can be added as a tuple with (uri, mime)
                    to give a hint of the mime type to the system
            utterance: forward utterance for further processing by the
                        web service.
            repeat: if the playback should be looped
        """
        repeat = repeat or False
        tracks = tracks or []
        utterance = utterance or ''
        if isinstance(tracks, (str, tuple)):
            tracks = [tracks]
        elif not isinstance(tracks, list):
            raise ValueError
        tracks = [ensure_uri(t) for t in tracks]
        self.bus.emit(Message('ovos.web.service.play',
                              data={'tracks': tracks,
                                    'utterance': utterance,
                                    'repeat': repeat}))

    def stop(self):
        """Stop the track."""
        self.bus.emit(Message('ovos.web.service.stop'))

    def next(self):
        """Change to next track."""
        self.bus.emit(Message('ovos.web.service.next'))

    def prev(self):
        """Change to previous track."""
        self.bus.emit(Message('ovos.web.service.prev'))

    def pause(self):
        """Pause playback."""
        self.bus.emit(Message('ovos.web.service.pause'))

    def resume(self):
        """Resume paused playback."""
        self.bus.emit(Message('ovos.web.service.resume'))

    def get_track_length(self):
        """
        getting the duration of the web in seconds
        """
        length = 0
        info = self.bus.wait_for_response(
            Message('ovos.web.service.get_track_length'),
            timeout=1)
        if info:
            length = info.data.get("length") or 0
        return length / 1000  # convert to seconds

    def get_track_position(self):
        """
        get current position in seconds
        """
        pos = 0
        info = self.bus.wait_for_response(
            Message('ovos.web.service.get_track_position'),
            timeout=1)
        if info:
            pos = info.data.get("position") or 0
        return pos / 1000  # convert to seconds

    def set_track_position(self, seconds):
        """Seek X seconds.

        Arguments:
            seconds (int): number of seconds to seek, if negative rewind
        """
        self.bus.emit(Message('ovos.web.service.set_track_position',
                              {"position": seconds * 1000}))  # convert to ms

    def seek(self, seconds: Union[int, float, timedelta] = 1):
        """Seek X seconds.

        Args:
            seconds (int): number of seconds to seek, if negative rewind
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        if seconds < 0:
            self.seek_backward(abs(seconds))
        else:
            self.seek_forward(seconds)

    def seek_forward(self, seconds: Union[int, float, timedelta] = 1):
        """Skip ahead X seconds.

        Args:
            seconds (int): number of seconds to skip
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        self.bus.emit(Message('ovos.web.service.seek_forward',
                              {"seconds": seconds}))

    def seek_backward(self, seconds: Union[int, float, timedelta] = 1):
        """Rewind X seconds

         Args:
            seconds (int): number of seconds to rewind
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        self.bus.emit(Message('ovos.web.service.seek_backward',
                              {"seconds": seconds}))

    def track_info(self):
        """Request information of current playing track.

        Returns:
            Dict with track info.
        """
        info = self.bus.wait_for_response(
            Message('ovos.web.service.track_info'),
            reply_type='ovos.web.service.track_info_reply',
            timeout=1)
        return info.data if info else {}

    def available_backends(self):
        """Return available web backends.

        Returns:
            dict with backend names as keys
        """
        msg = Message('ovos.web.service.list_backends')
        response = self.bus.wait_for_response(msg)
        return response.data if response else {}

    @property
    def is_playing(self):
        """True if the webservice is playing, else False."""
        return self.track_info() != {}


class OCPQuery:
    try:
        from ovos_utils.ocp import MediaType
        cast2audio = [
            MediaType.MUSIC,
            MediaType.PODCAST,
            MediaType.AUDIOBOOK,
            MediaType.RADIO,
            MediaType.RADIO_THEATRE,
            MediaType.VISUAL_STORY,
            MediaType.NEWS
        ]
    except ImportError as e:
        from enum import IntEnum

        class MediaType(IntEnum):
            GENERIC = 0  # nothing else matches

        cast2audio = None

    def __init__(self, query, bus, media_type=MediaType.GENERIC, config=None):
        if self.cast2audio is None:
            raise RuntimeError("This class requires ovos-utils ~=0.1")
        LOG.debug(f"Created {media_type.name} query: {query}")
        self.query = query
        self.media_type = media_type
        self.bus = bus
        self.config = config or {}
        self.reset()

    def reset(self):
        try:
            from ovos_utils.ocp import PlaybackMode
        except ImportError as e:
            raise RuntimeError("This method requires ovos-utils ~=0.1") from e
        self.active_skills = {}
        self.active_skills_lock = Lock()
        self.query_replies = []
        self.searching = False
        self.search_start = 0
        self.query_timeouts = self.config.get("min_timeout", 5)
        if self.config.get("playback_mode") in [PlaybackMode.AUDIO_ONLY]:
            self.has_gui = False
        else:
            self.has_gui = is_gui_running() or is_gui_connected(self.bus)

    def send(self, skill_id: str = None):
        self.query_replies = []
        self.query_timeouts = self.config.get("min_timeout", 5)
        self.search_start = time.time()
        self.searching = True
        self.register_events()
        if skill_id:
            self.bus.emit(Message(f'ovos.common_play.query.{skill_id}',
                                  {"phrase": self.query,
                                   "question_type": self.media_type}))
        else:
            self.bus.emit(Message('ovos.common_play.query',
                                  {"phrase": self.query,
                                   "question_type": self.media_type}))

    def wait(self):
        try:
            from ovos_utils.ocp import MediaType
        except ImportError as e:
            raise RuntimeError("This method requires ovos-utils ~=0.1") from e
        # if there is no match type defined, lets increase timeout a bit
        # since all skills need to search
        if self.media_type == MediaType.GENERIC:
            timeout = self.config.get("max_timeout", 15) + 3  # timeout bonus
        else:
            timeout = self.config.get("max_timeout", 15)
        while self.searching and time.time() - self.search_start <= timeout:
            time.sleep(0.1)
        self.searching = False
        self.remove_events()

    @property
    def results(self) -> List[dict]:
        return [s for s in self.query_replies if s.get("results")]

    def register_events(self):
        LOG.debug("Registering Search Bus Events")
        self.bus.on("ovos.common_play.skill.search_start", self.handle_skill_search_start)
        self.bus.on("ovos.common_play.skill.search_end", self.handle_skill_search_end)
        self.bus.on("ovos.common_play.query.response", self.handle_skill_response)

    def remove_events(self):
        LOG.debug("Removing Search Bus Events")
        self.bus.remove_all_listeners("ovos.common_play.skill.search_start")
        self.bus.remove_all_listeners("ovos.common_play.skill.search_end")
        self.bus.remove_all_listeners("ovos.common_play.query.response")

    def handle_skill_search_start(self, message):
        skill_id = message.data["skill_id"]
        LOG.debug(f"{message.data['skill_id']} is searching")
        with self.active_skills_lock:
            if skill_id not in self.active_skills:
                self.active_skills[skill_id] = Lock()

    def handle_skill_response(self, message):
        search_phrase = message.data["phrase"]
        if search_phrase != self.query:
            # not an answer for this search query
            return
        timeout = message.data.get("timeout")
        skill_id = message.data['skill_id']
        # LOG.debug(f"OVOSCommonPlay result: {skill_id}")

        # in case this handler fires before the search start handler
        with self.active_skills_lock:
            if skill_id not in self.active_skills:
                self.active_skills[skill_id] = Lock()
        with self.active_skills[skill_id]:
            if message.data.get("searching"):
                # extend the timeout by N seconds
                if timeout and self.config.get("allow_extensions", True):
                    self.query_timeouts += timeout
                # else -> expired search

            else:
                # Collect replies until the timeout
                if not self.searching and not len(self.query_replies):
                    LOG.debug("  too late!! ignored in track selection process")
                    LOG.warning(f"{skill_id} is not answering fast enough!")
                    return

                # populate search playlist
                res = message.data.get("results", [])
                LOG.debug(f'got {len(res)} results from {skill_id}')
                if res:
                    self.query_replies.append(message.data)

                    # abort searching if we gathered enough results
                    # TODO ensure we have a decent confidence match, if all matches
                    #  are < 50% conf extend timeout instead
                    if time.time() - self.search_start > self.query_timeouts:
                        if self.searching:
                            self.searching = False
                            LOG.debug("common play query timeout, parsing results")

                    elif self.searching:
                        for res in message.data.get("results", []):
                            if res.get("match_confidence", 0) >= \
                                    self.config.get("early_stop_thresh", 85):
                                # got a really good match, dont search further
                                LOG.info(
                                    "Receiving very high confidence match, stopping "
                                    "search early")

                                # allow other skills to "just miss"
                                early_stop_grace = \
                                    self.config.get("early_stop_grace_period", 0.5)
                                if early_stop_grace:
                                    LOG.debug(
                                        f"  - grace period: {early_stop_grace} seconds")
                                    time.sleep(early_stop_grace)
                                self.searching = False
                                return

    def handle_skill_search_end(self, message):
        skill_id = message.data["skill_id"]
        LOG.debug(f"{message.data['skill_id']} finished search")
        with self.active_skills_lock:
            if skill_id in self.active_skills:
                with self.active_skills[skill_id]:
                    del self.active_skills[skill_id]

        # if this was the last skill end searching period
        time.sleep(0.5)
        # TODO this sleep is hacky, but avoids a race condition in
        # case some skill just decides to respond before the others even
        # acknowledge search is starting, this gives more than enough time
        # for self.active_skills to be populated, a better approach should
        # be employed but this works fine for now
        if not self.active_skills and self.searching:
            LOG.info("Received search responses from all skills!")
            self.searching = False
