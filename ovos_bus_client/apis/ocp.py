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
from typing import List

from ovos_utils.gui import is_gui_connected, is_gui_running
from ovos_utils.log import LOG, deprecated

from ovos_bus_client.message import Message
from ovos_bus_client.message import dig_for_message
from ovos_bus_client.util import get_mycroft_bus

try:
    from ovos_utils.ocp import MediaType, PlaybackMode, Playlist, MediaEntry
except ImportError:
    import inspect
    from enum import IntEnum
    from typing import Optional, Union, Tuple
    from dataclasses import dataclass
    LOG.warning("ovos-utils~=0.1 not installed. Patching missing imports")

    import mimetypes
    import orjson

    OCP_ID = "ovos.common_play"


    def find_mime(uri):
        """ Determine mime type. """
        mime = mimetypes.guess_type(uri)
        if mime:
            return mime
        else:
            return None


    class MediaType(IntEnum):
        GENERIC = 0  # nothing else matches
        AUDIO = 1  # things like ambient noises
        MUSIC = 2
        VIDEO = 3  # eg, youtube videos
        AUDIOBOOK = 4
        GAME = 5  # because it shares the verb "play", mostly for disambguation
        PODCAST = 6
        RADIO = 7  # live radio
        NEWS = 8  # news reports
        TV = 9  # live tv stream
        MOVIE = 10
        TRAILER = 11
        AUDIO_DESCRIPTION = 12  # narrated movie for the blind
        VISUAL_STORY = 13  # things like animated comic books
        BEHIND_THE_SCENES = 14
        DOCUMENTARY = 15
        RADIO_THEATRE = 16
        SHORT_FILM = 17  # typically movies under 45 min
        SILENT_MOVIE = 18
        VIDEO_EPISODES = 19  # tv series etc
        BLACK_WHITE_MOVIE = 20
        CARTOON = 21
        ANIME = 22
        ASMR = 23

        ADULT = 69  # for content filtering
        HENTAI = 70  # for content filtering
        ADULT_AUDIO = 71  # for content filtering


    class PlaybackMode(IntEnum):
        AUTO = 0  # play each entry as considered appropriate,
        # ie, make it happen the best way possible
        AUDIO_ONLY = 10  # only consider audio entries
        VIDEO_ONLY = 20  # only consider video entries
        FORCE_AUDIO = 30  # cast video to audio unconditionally
        FORCE_AUDIOSERVICE = 40  ## DEPRECATED - used in ovos 0.0.7
        EVENTS_ONLY = 50  # only emit ocp events, do not display or play anything.
        # allows integration with external interfaces


    class PlaybackType(IntEnum):
        SKILL = 0  # skills handle playback whatever way they see fit,
        # eg spotify / mycroft common play
        VIDEO = 1  # Video results
        AUDIO = 2  # Results should be played audio only
        AUDIO_SERVICE = 3  ## DEPRECATED - used in ovos 0.0.7
        MPRIS = 4  # External MPRIS compliant player
        WEBVIEW = 5  # webview, render a url instead of media player
        UNDEFINED = 100  # data not available, hopefully status will be updated soon..


    class TrackState(IntEnum):
        DISAMBIGUATION = 1  # media result, not queued for playback
        PLAYING_SKILL = 20  # Skill is handling playback internally
        PLAYING_AUDIOSERVICE = 21  ## DEPRECATED - used in ovos 0.0.7
        PLAYING_VIDEO = 22  # Skill forwarded playback to video service
        PLAYING_AUDIO = 23  # Skill forwarded playback to audio service
        PLAYING_MPRIS = 24  # External media player is handling playback
        PLAYING_WEBVIEW = 25  # Media playback handled in browser (eg. javascript)

        QUEUED_SKILL = 30  # Waiting playback to be handled inside skill
        QUEUED_AUDIOSERVICE = 31  ## DEPRECATED - used in ovos 0.0.7
        QUEUED_VIDEO = 32  # Waiting playback in video service
        QUEUED_AUDIO = 33  # Waiting playback in audio service
        QUEUED_WEBVIEW = 34  # Waiting playback in browser service


    @dataclass
    class MediaEntry:
        uri: str = ""
        title: str = ""
        artist: str = ""
        match_confidence: int = 0  # 0 - 100
        skill_id: str = OCP_ID
        playback: PlaybackType = PlaybackType.UNDEFINED
        status: TrackState = TrackState.DISAMBIGUATION
        media_type: MediaType = MediaType.GENERIC
        length: int = 0  # in seconds
        image: str = ""
        skill_icon: str = ""
        javascript: str = ""  # to execute once webview is loaded

        def update(self, entry: dict, skipkeys: list = None, newonly: bool = False):
            """
            Update this MediaEntry object with keys from the provided entry
            @param entry: dict or MediaEntry object to update this object with
            @param skipkeys: list of keys to not change
            @param newonly: if True, only adds new keys; existing keys are unchanged
            """
            skipkeys = skipkeys or []
            if isinstance(entry, MediaEntry):
                entry = entry.as_dict
            entry = entry or {}
            for k, v in entry.items():
                if k not in skipkeys and hasattr(self, k):
                    if newonly and self.__getattribute__(k):
                        # skip, do not replace existing values
                        continue
                    self.__setattr__(k, v)

        @property
        def infocard(self) -> dict:
            """
            Return dict data used for a UI display
            """
            return {
                "duration": self.length,
                "track": self.title,
                "image": self.image,
                "album": self.skill_id,
                "source": self.skill_icon,
                "uri": self.uri
            }

        @property
        def mpris_metadata(self) -> dict:
            """
            Return dict data used by MPRIS
            """
            from dbus_next.service import Variant
            meta = {"xesam:url": Variant('s', self.uri)}
            if self.artist:
                meta['xesam:artist'] = Variant('as', [self.artist])
            if self.title:
                meta['xesam:title'] = Variant('s', self.title)
            if self.image:
                meta['mpris:artUrl'] = Variant('s', self.image)
            if self.length:
                meta['mpris:length'] = Variant('d', self.length)
            return meta

        @property
        def as_dict(self) -> dict:
            """
            Return a dict representation of this MediaEntry
            """
            # orjson handles dataclasses directly
            return orjson.loads(orjson.dumps(self).decode("utf-8"))

        @staticmethod
        def from_dict(track: dict):
            if track.get("playlist"):
                kwargs = {k: v for k, v in track.items()
                          if k in inspect.signature(Playlist).parameters}
                playlist = Playlist(**kwargs)
                for e in track["playlist"]:
                    playlist.add_entry(e)
                return playlist
            else:
                kwargs = {k: v for k, v in track.items()
                          if k in inspect.signature(MediaEntry).parameters}
                return MediaEntry(**kwargs)

        @property
        def mimetype(self) -> Optional[Tuple[Optional[str], Optional[str]]]:
            """
            Get the detected mimetype tuple (type, encoding) if it can be determined
            """
            if self.uri:
                return find_mime(self.uri)

        def __eq__(self, other):
            if isinstance(other, MediaEntry):
                other = other.infocard
            # dict comparison
            return other == self.infocard


    @dataclass
    class Playlist(list):
        title: str = ""
        position: int = 0
        length: int = 0  # in seconds
        image: str = ""
        match_confidence: int = 0  # 0 - 100
        skill_id: str = OCP_ID
        skill_icon: str = ""

        def __init__(self, *args, **kwargs):
            super().__init__(**kwargs)
            list.__init__(self, *args)

        @property
        def infocard(self) -> dict:
            """
            Return dict data used for a UI display
            (model shared with MediaEntry)
            """
            return {
                "duration": self.length,
                "track": self.title,
                "image": self.image,
                "album": self.skill_id,
                "source": self.skill_icon,
                "uri": ""
            }

        @staticmethod
        def from_dict(track: dict):
            return MediaEntry.from_dict(track)

        @property
        def as_dict(self) -> dict:
            """
            Return a dict representation of this MediaEntry
            """
            data = {
                "title": self.title,
                "position": self.position,
                "length": self.length,
                "image": self.image,
                "match_confidence": self.match_confidence,
                "skill_id": self.skill_id,
                "skill_icon": self.skill_icon,
                "playlist": [e.as_dict for e in self.entries]
            }
            return data

        @property
        def entries(self) -> List[MediaEntry]:
            """
            Return a list of MediaEntry objects in the playlist
            """
            entries = []
            for e in self:
                if isinstance(e, dict):
                    e = MediaEntry.from_dict(e)
                if isinstance(e, MediaEntry):
                    entries.append(e)
            return entries

        @property
        def current_track(self) -> Optional[MediaEntry]:
            """
            Return the current MediaEntry or None if the playlist is empty
            """
            if len(self) == 0:
                return None
            self._validate_position()
            track = self[self.position]
            if isinstance(track, dict):
                track = MediaEntry.from_dict(track)
            return track

        @property
        def is_first_track(self) -> bool:
            """
            Return `True` if the current position is the first track or if the
            playlist is empty
            """
            if len(self) == 0:
                return True
            return self.position == 0

        @property
        def is_last_track(self) -> bool:
            """
            Return `True` if the current position is the last track of if the
            playlist is empty
            """
            if len(self) == 0:
                return True
            return self.position == len(self) - 1

        def goto_start(self) -> None:
            """
            Move to the first entry in the playlist
            """
            self.position = 0

        def clear(self) -> None:
            """
            Remove all entries from the Playlist and reset the position
            """
            super().clear()
            self.position = 0

        def sort_by_conf(self):
            """
            Sort the Playlist by `match_confidence` with high confidence first
            """
            self.sort(
                key=lambda k: k.match_confidence if isinstance(k, (MediaEntry, Playlist))
                else k.get("match_confidence", 0), reverse=True)

        def add_entry(self, entry: MediaEntry, index: int = -1) -> None:
            """
            Add an entry at the requested index
            @param entry: MediaEntry to add to playlist
            @param index: index to insert entry at (default -1 to append)
            """
            assert isinstance(index, int)
            if index > len(self):
                raise ValueError(f"Invalid index {index} requested, "
                                 f"playlist only has {len(self)} entries")

            if isinstance(entry, dict):
                entry = MediaEntry.from_dict(entry)

            assert isinstance(entry, (MediaEntry, Playlist))

            if index == -1:
                index = len(self)

            if index < self.position:
                self.set_position(self.position + 1)

            self.insert(index, entry)

        def remove_entry(self, entry: Union[int, dict, MediaEntry]) -> None:
            """
            Remove the requested entry from the playlist or raise a ValueError
            @param entry: index or MediaEntry to remove from the playlist
            """
            if isinstance(entry, int):
                self.pop(entry)
                return
            if isinstance(entry, dict):
                entry = MediaEntry.from_dict(entry)
            assert isinstance(entry, MediaEntry)
            for idx, e in enumerate(self.entries):
                if e == entry:
                    self.pop(idx)
                    break
            else:
                raise ValueError(f"entry not in playlist: {entry}")

        def replace(self, new_list: List[Union[dict, MediaEntry]]) -> None:
            """
            Replace the contents of this Playlist with new_list
            @param new_list: list of MediaEntry or dict objects to set this list to
            """
            self.clear()
            for e in new_list:
                self.add_entry(e)

        def set_position(self, idx: int):
            """
            Set the position in the playlist to a specific index
            @param idx: Index to set position to
            """
            self.position = idx
            self._validate_position()

        def goto_track(self, track: Union[MediaEntry, dict]) -> None:
            """
            Go to the requested track in the playlist
            @param track: MediaEntry to find and go to in the playlist
            """
            if isinstance(track, dict):
                track = MediaEntry.from_dict(track)

            assert isinstance(track, (MediaEntry, Playlist))

            if isinstance(track, MediaEntry):
                requested_uri = track.uri
            else:
                requested_uri = track.title

            for idx, t in enumerate(self):
                if isinstance(t, MediaEntry):
                    pl_entry_uri = t.uri
                else:
                    pl_entry_uri = t.title

                if requested_uri == pl_entry_uri:
                    self.set_position(idx)
                    LOG.debug(f"New playlist position: {self.position}")
                    return
            LOG.error(f"requested track not in the playlist: {track}")

        def next_track(self) -> None:
            """
            Go to the next track in the playlist
            """
            self.set_position(self.position + 1)

        def prev_track(self) -> None:
            """
            Go to the previous track in the playlist
            """
            self.set_position(self.position - 1)

        def _validate_position(self) -> None:
            """
            Make sure the current position is valid; default `position` to 0
            """
            if self.position < 0 or self.position >= len(self):
                LOG.error(f"Playlist pointer is in an invalid position "
                          f"({self.position}! Going to start of playlist")
                self.position = 0

        def __contains__(self, item):
            if isinstance(item, dict):
                item = MediaEntry.from_dict(item)
            if isinstance(item, MediaEntry):
                for e in self.entries:
                    if e.uri == item.uri:
                        return True
            return False


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

    def seek_forward(self, seconds=1):
        """Skip ahead X seconds.

        Args:
            seconds (int): number of seconds to skip
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        self.bus.emit(Message('mycroft.audio.service.seek_forward',
                              {"seconds": seconds}))

    def seek_backward(self, seconds=1):
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
        """ensures a list of tracks contains only MediaEntry or Playlist items"""
        assert isinstance(tracks, list)
        # support Playlist and MediaEntry objects in tracks
        for idx, track in enumerate(tracks):
            if isinstance(track, dict):
                tracks[idx] = MediaEntry.from_dict(track)
            elif isinstance(track, list) and not isinstance(track, Playlist):
                tracks[idx] = OCPInterface.norm_tracks(track)
            elif not isinstance(track, MediaEntry):
                # TODO - support string uris
                # let it fail in next assert
                # log all bad entries before failing
                LOG.error(f"Bad track, invalid type: {track}")
        assert all(isinstance(t, (MediaEntry, Playlist)) for t in tracks)
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

    def seek_forward(self, seconds=1):
        """Skip ahead X seconds.

        Args:
            seconds (int): number of seconds to skip
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        self.bus.emit(Message('ovos.audio.service.seek_forward',
                              {"seconds": seconds}))

    def seek_backward(self, seconds=1):
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

    def seek_forward(self, seconds=1):
        """Skip ahead X seconds.

        Args:
            seconds (int): number of seconds to skip
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        self.bus.emit(Message('ovos.video.service.seek_forward',
                              {"seconds": seconds}))

    def seek_backward(self, seconds=1):
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

    def seek_forward(self, seconds=1):
        """Skip ahead X seconds.

        Args:
            seconds (int): number of seconds to skip
        """
        if isinstance(seconds, timedelta):
            seconds = seconds.total_seconds()
        self.bus.emit(Message('ovos.web.service.seek_forward',
                              {"seconds": seconds}))

    def seek_backward(self, seconds=1):
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
    cast2audio = [
        MediaType.MUSIC,
        MediaType.PODCAST,
        MediaType.AUDIOBOOK,
        MediaType.RADIO,
        MediaType.RADIO_THEATRE,
        MediaType.VISUAL_STORY,
        MediaType.NEWS
    ]

    def __init__(self, query, bus, media_type=MediaType.GENERIC, config=None):
        LOG.debug(f"Created {media_type.name} query: {query}")
        self.query = query
        self.media_type = media_type
        self.bus = bus
        self.config = config or {}
        self.reset()

    def reset(self):
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
