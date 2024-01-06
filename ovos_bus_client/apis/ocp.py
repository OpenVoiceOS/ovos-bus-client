# Copyright 2017 Mycroft AI Inc.
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
import time
from datetime import timedelta
from os.path import abspath
from threading import Lock
from typing import List

from ovos_bus_client.message import Message, dig_for_message
from ovos_bus_client.util import get_mycroft_bus
from ovos_utils.gui import is_gui_connected, is_gui_running
from ovos_utils.log import LOG
from ovos_utils.messagebus import Message
from ovos_utils.ocp import MediaType, PlaybackType, PlaybackMode, available_extractors


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
    """AudioService class for interacting with the audio subsystem

    Audio is managed by OCP in the default implementation,
    usually this class should not be directly used, see OCPInterface instead

    Args:
        bus: Mycroft messagebus connection
    """

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
        bus: Mycroft messagebus connection
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
    def queue(self, tracks):
        """Queue up a track to OCP playing playlist.

        Args:
            tracks: track dict or list of track dicts (OCP result style)
        """

        assert isinstance(tracks, list)
        assert all(isinstance(t, dict) for t in tracks)

        msg = self._format_msg('ovos.common_play.playlist.queue',
                               {'tracks': tracks})
        self.bus.emit(msg)

    def play(self, tracks, utterance=None):
        """Start playback.
        Args:
            tracks: track dict or list of track dicts (OCP result style)
            utterance: forward utterance for further processing by OCP
        """
        assert isinstance(tracks, list)
        assert all(isinstance(t, dict) for t in tracks)

        utterance = utterance or ''

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

    def send(self):
        self.query_replies = []
        self.query_timeouts = self.config.get("min_timeout", 5)
        self.search_start = time.time()
        self.searching = True
        self.register_events()
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
        return [s for s in self.query_replies
                if s.get("results")]

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
                    LOG.warning(
                        f"{message.data['skill_id']} is not answering fast "
                        "enough!")

                # populate search playlist
                results = message.data.get("results", [])
                for idx, res in enumerate(results):
                    if self.media_type not in [MediaType.ADULT, MediaType.HENTAI]:
                        # skip adult content results unless explicitly enabled
                        if not self.config.get("adult_content", False) and \
                                res.get("media_type", MediaType.GENERIC) in \
                                [MediaType.ADULT, MediaType.HENTAI]:
                            continue

                    # filter uris we can play, usually files and http streams, but some
                    # skills might return results that depend on additional packages,
                    # eg. soundcloud, rss, youtube, deezer....
                    uri = res.get("uri", "")
                    if res.get("playlist") and not uri:
                        res["playlist"] = [
                            r for r in res["playlist"]
                            if r.get("uri") and any(r.get("uri").startswith(e)
                                                    for e in
                                                    available_extractors())]
                        if not len(res["playlist"]):
                            results[idx] = None  # can't play this search result!
                            LOG.error(f"Empty playlist for {res}")
                            continue
                    elif uri and res.get("playback") not in [
                        PlaybackType.SKILL, PlaybackType.UNDEFINED] and \
                            not any(
                                uri.startswith(e) for e in available_extractors()):
                        results[idx] = None  # can't play this search result!
                        LOG.error(f"stream handler not available for {res}")
                        continue

                    # filter video results if GUI not connected
                    if not self.has_gui:
                        # force allowed stream types to be played audio only
                        if res.get("media_type", "") in self.cast2audio:
                            LOG.debug("unable to use GUI, "
                                      "forcing result to play audio only")
                            res["playback"] = PlaybackType.AUDIO
                            res["match_confidence"] -= 10
                            results[idx] = res

                # remove filtered results
                message.data["results"] = [r for r in results if r is not None]
                LOG.debug(f'got {len(message.data["results"])} results from {skill_id}')
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
