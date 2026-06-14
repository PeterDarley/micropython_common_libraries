"""Sound management module for playing titled sounds via audio modules.

Manages the mapping of sound titles to file numbers and coordinates playback
across available YX5200 modules.
"""

import sys
from time import sleep
from audio import AudioPlayer, NoPlayersAvailable
from storage import PersistentDict

# Global flag: when True, blocks all sounds except IP announcement
_sound_unavailable: bool = False


def _is_enabled_setting(value: object) -> bool:
    """Return True when a setting value represents an enabled/checked state."""

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value != 0

    if isinstance(value, str):
        normalized: str = value.strip().lower()
        return normalized in ("1", "true", "yes", "on")

    return bool(value)


def _parse_non_negative_int(value: object) -> int:
    """Parse value as a non-negative integer, tolerating numeric strings/floats."""

    try:
        if isinstance(value, bool):
            return int(value)

        if isinstance(value, int):
            return max(0, value)

        if isinstance(value, float):
            return max(0, int(value))

        if isinstance(value, str):
            normalized: str = value.strip()
            if not normalized:
                return 0

            if "." in normalized:
                return max(0, int(float(normalized)))

            return max(0, int(normalized))

        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def set_sound_unavailable(unavailable: bool) -> None:
    """Set the global sound unavailable flag.

    When True, blocks all regular sounds except IP announcement.
    This is used to prevent sound collisions during system announcements.

    Args:
        unavailable: True to block sounds, False to allow them
    """

    global _sound_unavailable
    _sound_unavailable = unavailable
    if unavailable:
        print("sound: sound blocked (unavailable)")
    else:
        print("sound: sound unblocked (available)")


def is_sound_unavailable() -> bool:
    """Check if sound is currently unavailable.

    Returns:
        True if sounds are blocked (during IP announcement), False otherwise
    """

    return _sound_unavailable


class SoundManager:
    """Manages sound playback by title."""

    def __new__(cls):
        """Implement singleton pattern."""

        if not hasattr(cls, "_instance"):
            cls._instance = super().__new__(cls)

        return cls._instance

    def __init__(self) -> None:
        """Initialise sound manager."""

        if getattr(self, "_initialised", False):
            return

        self._initialised: bool = True
        self.audio_player: AudioPlayer = AudioPlayer()
        self._last_health: dict = {}
        self._module_sound_map: dict = {}  # Maps module_idx to (title, loop_count_remaining)
        self._previous_playing_state: dict = {}
        self._active_soundscape: str = None  # Name of currently playing soundscape
        self._soundscape_state: dict = {}  # Tracks progress within soundscape

    def get_last_health(self) -> dict:
        """Return the most recent health-check results."""

        return getattr(self, "_last_health", {})

    def refresh_health(self) -> dict:
        """Run a new health check and store the results."""

        try:
            self._last_health = self.audio_player.check_health()
        except Exception:
            self._last_health = {}

        return self._last_health

    def get_sounds(self) -> dict:
        """Get all configured sounds.

        Returns:
            Dict of {title: {file, high_quality, ...}}
        """

        storage: PersistentDict = PersistentDict()
        # Prefer per-model sounds when available (new layout).
        lighting_root = storage.get("lighting_settings", {})
        models = lighting_root.get("models") if isinstance(lighting_root, dict) else None
        if models:
            current = lighting_root.get("current_model")
            if current and isinstance(models.get(current, {}), dict):
                return models.get(current, {}).get("sounds", {})

        # Fallback to legacy top-level sounds key.
        sounds: dict = storage.get("sounds", {})
        return sounds

    def get_sound_by_title(self, title: str) -> dict:
        """Get a specific sound by title.

        Args:
            title: Sound title

        Returns:
            Sound dict or None if not found
        """

        sounds: dict = self.get_sounds()
        return sounds.get(title)

    def play_sound(self, title: str, loop_count_override: int = -1) -> int:
        """Play a sound by title.

        Args:
            title: Sound title to play
            loop_count_override: Optional loop-count override. Use -1 to keep
                configured loop_count; use 0+ to force an explicit loop count.

        Returns:
            Module index that started playback

        Raises:
            NoPlayersAvailable if no modules available
            ValueError if sound title not found or sound is unavailable
        """

        # Block regular sounds when sound is unavailable (IP announcement in progress)
        if is_sound_unavailable():
            print(f"sound: blocked (unavailable) title='{title}'")
            raise ValueError(f"Sound playback blocked (system announcement in progress)")

        sound: dict | None = self.get_sound_by_title(title)
        if sound is None:
            print(f"sound: not found title='{title}'")
            raise ValueError(f"Sound '{title}' not found")

        file_number: int = int(sound.get("file", 0))
        high_quality: bool = bool(sound.get("high_quality", False))

        # Stop any sounds in the "stops" list before starting
        stops_list: list = sound.get("stops", [])
        if stops_list:
            self._stop_sounds_by_title(stops_list)

        module_idx: int = self.audio_player.play_file(file_number, high_quality_preferred=high_quality)

        configured_loop_count: int = _parse_non_negative_int(sound.get("loop_count", 0))
        loop_count: int = (
            configured_loop_count if loop_count_override < 0 else _parse_non_negative_int(loop_count_override)
        )
        print(
            f"sound: start title='{title}' file={file_number} module={module_idx} hq={high_quality} loop_count={loop_count}"
        )

        # Track which sound is playing on this module, along with remaining loop count
        self._module_sound_map[module_idx] = (title, loop_count)
        return module_idx

    def _stop_sounds_by_title(self, titles: list) -> None:
        """Stop playback of sounds by title.

        Args:
            titles: List of sound titles to stop
        """

        playing: dict = self.get_playing_sounds()
        for module_idx, playing_title in playing.items():
            if playing_title in titles:
                try:
                    self.audio_player.players[module_idx].stop()
                    print(f"sound: stopped title='{playing_title}' module={module_idx}")
                except (AttributeError, IndexError, OSError):
                    pass

    def stop_sounds_by_title(self, titles: list[str]) -> None:
        """Stop playback for all currently playing sounds whose titles match.

        Args:
            titles: Sound titles to stop.
        """

        if not titles:
            return

        normalized_titles: list[str] = []
        for title in titles:
            if isinstance(title, str):
                stripped_title: str = title.strip()
                if stripped_title:
                    normalized_titles.append(stripped_title)

        if not normalized_titles:
            return

        self._stop_sounds_by_title(normalized_titles)

    def stop_sound(self, title: str, module_idx: int | None = None) -> bool:
        """Stop a specific sound reliably, retrying if needed.

        Sends multiple stop commands with drain/delays to overcome UART
        contention. Does NOT optimistically mark state — only marks stopped
        after hardware confirms via status query.

        Args:
            title: Sound title expected to be playing.
            module_idx: Preferred module index to stop.

        Returns:
            True if the sound appears stopped on at least one matching module.
        """

        if not isinstance(title, str) or not title.strip():
            return False

        normalized_title: str = title.strip()
        candidate_modules: list[int] = []

        if isinstance(module_idx, int) and 0 <= module_idx < len(self.audio_player.players):
            candidate_modules.append(module_idx)

        # Also locate modules by current playing title in case the posted index
        # is stale or the sound was reassigned to another module.
        playing_state: dict = self.get_playing_sounds()
        for playing_module_idx, playing_title in playing_state.items():
            if playing_title == normalized_title and playing_module_idx not in candidate_modules:
                candidate_modules.append(playing_module_idx)

        if not candidate_modules:
            return False

        stopped_any: bool = False
        for candidate_module_idx in candidate_modules:
            try:
                player = self.audio_player.players[candidate_module_idx]
            except (AttributeError, IndexError):
                continue

            # Send multiple stop commands with delays. Use update_state=False so
            # we don't prematurely flip cached state — only hardware confirmation
            # should drive state changes.
            for attempt in range(5):
                try:
                    # Drain any pending RX bytes that might be confusing the UART
                    try:
                        if player.uart:
                            player.uart.read()
                    except (AttributeError, OSError):
                        pass

                    player.stop(update_state=False)
                    sleep(0.15)

                    # Temporarily bypass the multi-confirmation requirement by
                    # directly reading hardware status for verification.
                    try:
                        if player.uart:
                            player.uart.read()  # drain
                    except (AttributeError, OSError):
                        pass

                    from audio import _CMD_QUERY_STATUS, _STATUS_PLAYING

                    frame = player._build_frame(_CMD_QUERY_STATUS, 0)
                    player.uart.write(frame)
                    sleep(0.1)
                    resp = player.uart.read()

                    if resp and len(resp) >= 10:
                        parsed = player._find_response_frame(resp, _CMD_QUERY_STATUS)
                        if parsed is not None:
                            status_high, status_low = parsed
                            hw_playing = (status_low == _STATUS_PLAYING) or (status_high == _STATUS_PLAYING)
                            print(
                                f"audio: stop verify uart={player.uart_id} "
                                f"attempt={attempt + 1} hw_playing={hw_playing}"
                            )
                            if not hw_playing:
                                stopped_any = True
                                break
                            # Hardware still playing — loop will retry
                            continue

                    # Inconclusive read — retry
                    print(f"audio: stop verify inconclusive uart={player.uart_id} " f"attempt={attempt + 1}")
                except (AttributeError, OSError, TypeError, ValueError, IndexError) as err:
                    print(f"audio: stop attempt error uart={player.uart_id}: {err}")
                    break

            # Update cached state based on outcome
            if stopped_any:
                player.is_playing = False
                player.current_file = None
                player._pending_stop_confirmations = 0

                # Clear manager-side bookkeeping for that module.
                if candidate_module_idx in self._module_sound_map:
                    del self._module_sound_map[candidate_module_idx]

                # If this was driving a soundscape, stop the soundscape state too.
                if self._active_soundscape:
                    self._active_soundscape = None
                    self._soundscape_state = {}

                print(f"sound: stopped title='{normalized_title}' module={candidate_module_idx}")
            else:
                print(
                    f"sound: stop FAILED title='{normalized_title}' "
                    f"module={candidate_module_idx} (hardware still playing)"
                )

        return stopped_any

    def get_playing_sounds(self) -> dict:
        """Get currently playing sounds.

        Returns:
            Dict mapping module index to (title, remaining_ms) or None if idle
        """

        playing_state: dict = self.audio_player.get_playing_state()
        sounds: dict = self.get_sounds()

        # Build reverse mapping from file number to title
        file_to_title: dict = {v.get("file"): k for k, v in sounds.items()}

        result: dict = {}
        for module_idx, state in playing_state.items():
            if state is not None:
                file_num, _ = state
                title: str = file_to_title.get(file_num, "unknown")
                result[module_idx] = title
            else:
                result[module_idx] = None

        if getattr(self.audio_player, "debug_logging", False):
            print(f"audio-debug: playing_state_raw={playing_state} file_to_title={file_to_title} mapped={result}")

        return result

    def stop_all(self) -> None:
        """Stop playback on all modules."""

        self.audio_player.stop_all()
        self._module_sound_map.clear()
        self._active_soundscape = None
        self._soundscape_state = {}

    def get_soundscapes(self) -> dict:
        """Get all configured soundscapes.

        Returns:
            Dict of {name: {entries}}
        """

        storage: PersistentDict = PersistentDict()
        lighting_root = storage.get("lighting_settings", {})
        models = lighting_root.get("models") if isinstance(lighting_root, dict) else None
        if models:
            current = lighting_root.get("current_model")
            if current and isinstance(models.get(current, {}), dict):
                return models.get(current, {}).get("soundscapes", {})
        return {}

    def get_soundscape(self, name: str) -> dict:
        """Get a soundscape by name.

        Args:
            name: Soundscape name

        Returns:
            Soundscape dict or empty dict if not found
        """

        soundscapes: dict = self.get_soundscapes()
        return soundscapes.get(name, {})

    def play_soundscape(self, name: str) -> bool:
        """Start playing a soundscape.

        Args:
            name: Soundscape name to play

        Returns:
            True if soundscape started, False if not found or error
        """

        soundscape: dict = self.get_soundscape(name)
        if not soundscape:
            print(f"soundscape: not found name='{name}'")
            return False

        self.stop_all()
        self._active_soundscape = name
        self._soundscape_state = {}

        print(f"soundscape: start name='{name}'")
        return self._play_next_soundscape_entry(name)

    def _play_next_soundscape_entry(self, soundscape_name: str) -> bool:
        """Play the next entry in a soundscape.

        Args:
            soundscape_name: Name of the soundscape

        Returns:
            True if an entry was played, False if soundscape is complete
        """

        soundscape: dict = self.get_soundscape(soundscape_name)
        if not soundscape:
            self._active_soundscape = None
            return False

        # Find the next entry that should play
        entries: dict = soundscape if isinstance(soundscape, dict) else {}

        for entry_name, entry_data in entries.items():
            if not isinstance(entry_data, dict):
                continue

            # Get current state for this entry
            entry_state = self._soundscape_state.get(entry_name, {})
            is_started = isinstance(entry_state, dict) and entry_state.get("started", False)

            if not is_started:
                # Check if this entry has an "after" dependency
                after_entry = entry_data.get("after")
                if after_entry:
                    after_state = self._soundscape_state.get(after_entry, {})
                    after_complete = isinstance(after_state, dict) and after_state.get("complete", False)
                    if not after_complete:
                        # Predecessor hasn't finished yet, skip
                        continue

                # Play this entry for the first time
                sound_title = entry_data.get("sound", "")
                repeat_count_raw = entry_data.get("repeat", 0)
                repeat_count: int = _parse_non_negative_int(repeat_count_raw)
                if "repeat_enabled" in entry_data:
                    repeat_enabled: bool = _is_enabled_setting(entry_data.get("repeat_enabled", False))
                else:
                    # Backward compatibility for entries created before repeat_enabled existed.
                    repeat_enabled = repeat_count > 0
                repeat_infinite: bool = repeat_enabled and repeat_count == 0

                if not sound_title:
                    self._soundscape_state[entry_name] = {"started": True, "complete": True}
                    continue

                try:
                    module_idx: int = self.play_sound(sound_title, loop_count_override=0)
                    self._soundscape_state[entry_name] = {
                        "started": True,
                        "title": sound_title,
                        "module_idx": module_idx,
                        "repeat_enabled": repeat_enabled,
                        "repeat_infinite": repeat_infinite,
                        "repeat_count": repeat_count,
                        "repeat_remaining": repeat_count,
                        "complete": False,
                    }
                    if not repeat_enabled:
                        repeat_mode = "off"
                    elif repeat_infinite:
                        repeat_mode = "infinite"
                    else:
                        repeat_mode = str(repeat_count)

                    print(
                        f"soundscape: playing entry={entry_name} sound='{sound_title}' "
                        f"repeat_enabled={repeat_enabled} repeat_mode={repeat_mode}"
                    )
                    return True
                except (ValueError, NoPlayersAvailable) as err:
                    print(f"soundscape: failed to play entry={entry_name} sound='{sound_title}': {err}")
                    sys.print_exception(err)
                    self._soundscape_state[entry_name] = {"started": True, "complete": True}
                    continue

            else:
                # Entry was already started, check if it needs to repeat
                if isinstance(entry_state, dict):
                    repeat_enabled: bool = bool(entry_state.get("repeat_enabled", False))
                    repeat_infinite = bool(entry_state.get("repeat_infinite", False))
                    repeat_remaining = entry_state.get("repeat_remaining", 0)
                    sound_title = entry_state.get("title", "")

                    if not repeat_enabled:
                        entry_state["complete"] = True
                        continue

                    if repeat_infinite:
                        print(f"soundscape: repeating entry={entry_name} sound='{sound_title}' remaining=infinite")
                        try:
                            module_idx = self.play_sound(sound_title, loop_count_override=0)
                            entry_state["module_idx"] = module_idx
                            return True
                        except (ValueError, NoPlayersAvailable) as err:
                            print(f"soundscape: failed to repeat entry={entry_name}: {err}")
                            sys.print_exception(err)
                            entry_state["complete"] = True
                            continue

                    if repeat_remaining > 0:
                        repeat_remaining -= 1
                        print(
                            f"soundscape: repeating entry={entry_name} sound='{sound_title}' remaining={repeat_remaining}"
                        )
                        try:
                            module_idx = self.play_sound(sound_title, loop_count_override=0)
                            entry_state["module_idx"] = module_idx
                            entry_state["repeat_remaining"] = repeat_remaining
                            if repeat_remaining == 0:
                                entry_state["complete"] = True
                            return True
                        except (ValueError, NoPlayersAvailable) as err:
                            print(f"soundscape: failed to repeat entry={entry_name}: {err}")
                            sys.print_exception(err)
                            entry_state["complete"] = True
                            continue
                    else:
                        # This entry is complete, mark it
                        entry_state["complete"] = True

        # No more entries to play
        print(f"soundscape: complete name='{soundscape_name}'")
        self._active_soundscape = None
        self._soundscape_state = {}
        return False

    def get_active_soundscape(self):
        """Return the name of the currently playing soundscape, or None."""

        if self._active_soundscape:
            # Self-heal stale UI/runtime state: if no module is currently
            # playing, advance/complete the soundscape now.
            playing_sounds: dict = self.get_playing_sounds()
            any_module_playing: bool = any(title is not None for title in playing_sounds.values())
            if not any_module_playing:
                soundscape_name: str = self._active_soundscape
                self._play_next_soundscape_entry(soundscape_name)

        return self._active_soundscape

    def set_volume(self, module_index: int, volume: int) -> bool:
        """Set volume for a module.

        Args:
            module_index: Module index
            volume: Volume level 0-30

        Returns:
            True if successful
        """

        return self.audio_player.set_volume(module_index, volume)

    def handle_sound_ended(self, title: str, module_idx: int) -> None:
        """Handle end-of-playback for a sound.

        Checks for loop_count and chain_next config, soundscape advancement, and takes appropriate action.

        Args:
            title: Sound title that ended
            module_idx: Module index where it was playing
        """

        sound: dict | None = self.get_sound_by_title(title)
        if sound is None:
            return

        try:
            # Check if there are remaining loops
            loop_count_remaining: int = 0
            if module_idx in self._module_sound_map:
                map_entry = self._module_sound_map[module_idx]
                if isinstance(map_entry, tuple):
                    _, loop_count_remaining = map_entry

            if loop_count_remaining > 0:
                loop_count_remaining -= 1
                print(f"sound: loop title='{title}' module={module_idx} remaining_loops={loop_count_remaining}")
                file_number: int = int(sound.get("file", 0))
                high_quality: bool = bool(sound.get("high_quality", False))
                self.audio_player.players[module_idx].play_file(file_number)
                self._module_sound_map[module_idx] = (title, loop_count_remaining)
                return

            # Check if we're in a soundscape and only advance when the ended
            # sound matches an active soundscape entry.
            if self._active_soundscape:
                matched_active_entry: bool = False
                for entry_state in self._soundscape_state.values():
                    if not isinstance(entry_state, dict):
                        continue

                    if entry_state.get("complete", False):
                        continue

                    expected_title: str = str(entry_state.get("title", ""))
                    expected_module_idx = entry_state.get("module_idx")

                    if expected_title != title:
                        continue

                    if expected_module_idx is not None and expected_module_idx != module_idx:
                        continue

                    matched_active_entry = True
                    break

                if matched_active_entry:
                    soundscape_name: str = self._active_soundscape
                    self._play_next_soundscape_entry(soundscape_name)
                    return

            # Check if there's a next sound to chain
            chain_next: str | None = sound.get("chain_next")
            if chain_next:
                print(f"sound: chain from '{title}' to '{chain_next}'")
                try:
                    self.play_sound(chain_next)
                except (ValueError, NoPlayersAvailable) as err:
                    print(f"sound: chain failed from '{title}' to '{chain_next}': {err}")
                    sys.print_exception(err)
                return

            # Sound ended without looping or chaining - clear the module map
            if module_idx in self._module_sound_map:
                del self._module_sound_map[module_idx]
            print(f"sound: ended title='{title}' module={module_idx}")

        except (AttributeError, IndexError, OSError) as err:
            print(f"sound: handle_sound_ended error for title='{title}': {err}")
            sys.print_exception(err)

    def check_for_ended_sounds(self) -> None:
        """Check for sounds that have ended since last check.

        This is called by the audio polling system to detect transitions.
        When a sound transitions from playing→stopped, handle loop/chain logic.
        """

        current: dict = self.get_playing_sounds()

        # Detect transitions from playing to stopped
        for module_idx, was_playing_title in self._previous_playing_state.items():
            if was_playing_title is None:
                continue

            is_now_playing_title: str | None = current.get(module_idx)
            if is_now_playing_title is None:
                # Sound ended on this module
                self.handle_sound_ended(was_playing_title, module_idx)
                # After handling, update current state with whatever the handler decided
                # (it might have replayed the sound, chained to next, etc.)
                current = self.get_playing_sounds()

        self._previous_playing_state = current.copy()
