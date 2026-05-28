"""YX5200 MP3 player module driver using DFPlayer protocol.

Supports 0-3 modules connected to separate UARTs. Each module plays from an SD card
with MP3 files in the /MP3/ folder numbered 0001.mp3, 0002.mp3, etc.
"""

import sys
from machine import UART  # type: ignore
from time import sleep, time
from storage import PersistentDict
from timing import TimerManager

# DFPlayer protocol constants
_BAUD_RATE: int = 9600
_FRAME_START: int = 0x7E
_FRAME_END: int = 0xEF
_CMD_SET_VOLUME: int = 0x06
_CMD_RESET: int = 0x0C
_CMD_PLAY_FILE: int = 0x12
_CMD_PAUSE: int = 0x0E
_CMD_STOP: int = 0x16
_CMD_QUERY_STATUS: int = 0x42
_STATUS_PLAYING: int = 0x01


class NoPlayersAvailable(Exception):
    """Raised when no audio modules are available to play a sound."""

    pass


class YX5200Player:
    """Controller for a single YX5200 MP3 player module via UART."""

    def __init__(self, uart_id: int, tx_pin: int, rx_pin: int, high_quality: bool = False) -> None:
        """Initialise a player on the given UART.

        Args:
            uart_id: UART number (0, 1, or 2)
            tx_pin: TX pin GPIO number
            rx_pin: RX pin GPIO number
            high_quality: Whether this module is marked for high-quality playback
        """

        self.uart_id: int = uart_id
        self.tx_pin: int = tx_pin
        self.rx_pin: int = rx_pin
        self.high_quality: bool = high_quality
        self.debug_logging: bool = False
        self.current_file: int | None = None
        self.is_playing: bool = False
        self.start_time: float = 0.0
        self._pending_stop_confirmations: int = 0
        self.last_polled: float = 0.0

        try:
            self.uart: UART = UART(uart_id, baudrate=_BAUD_RATE, tx=tx_pin, rx=rx_pin, bits=8, stop=1)
        except (AttributeError, OSError, TypeError, ValueError) as err:
            print(f"YX5200: UART {uart_id} (tx={tx_pin}, rx={rx_pin}) init error: {err}")
            sys.print_exception(err)
            self.uart = None

    def _build_frame(self, cmd: int, param: int = 0) -> bytes:
        """Build a DFPlayer protocol frame (10 bytes).

        Frame format: 0x7E 0xFF 0x06 CMD ACK PARAM_H PARAM_L CHK_H CHK_L 0xEF
        Checksum is the negated sum of bytes 1-6 (version through param_low).
        """

        version: int = 0xFF
        length: int = 0x06
        feedback: int = 0x01
        param_high: int = (param >> 8) & 0xFF
        param_low: int = param & 0xFF

        # Checksum covers bytes 1-6: version, length, cmd, feedback, param_h, param_l
        checksum: int = -(version + length + cmd + feedback + param_high + param_low) & 0xFFFF
        checksum_high: int = (checksum >> 8) & 0xFF
        checksum_low: int = checksum & 0xFF

        return bytes(
            [
                _FRAME_START,
                version,
                length,
                cmd,
                feedback,
                param_high,
                param_low,
                checksum_high,
                checksum_low,
                _FRAME_END,
            ]
        )

    def _debug(self, message: str) -> None:
        """Print audio debug logs when enabled for this player."""

        if self.debug_logging:
            print(f"audio-debug: uart={self.uart_id} tx={self.tx_pin} rx={self.rx_pin} {message}")

    def send_command(self, cmd: int, param: int = 0) -> bool:
        """Send a command to the player.

        Args:
            cmd: Command byte
            param: Parameter word (0-65535)

        Returns:
            True if command sent successfully, False if UART error
        """

        if self.uart is None:
            print(
                f"YX5200: UART {self.uart_id} (tx={self.tx_pin}, rx={self.rx_pin}) "
                "send_command - uart not initialised"
            )
            return False

        try:
            frame: bytes = self._build_frame(cmd, param)
            self._debug(f"send cmd=0x{cmd:02X} param={param}")
            self.uart.write(frame)

            # Attempt a short response read to keep UART buffers tidy.
            try:
                sleep(0.05)
                self.uart.read()
            except (AttributeError, OSError):
                pass

            return True
        except (AttributeError, OSError, TypeError, ValueError) as err:
            print(f"YX5200: UART {self.uart_id} (tx={self.tx_pin}, rx={self.rx_pin}) " f"write error: {err}")
            sys.print_exception(err)
            return False

    def play_file(self, file_number: int) -> bool:
        """Play a specific file (0001-9999).

        Args:
            file_number: File number to play

        Returns:
            True if command sent successfully
        """

        success: bool = self.send_command(_CMD_PLAY_FILE, file_number)
        if success:
            self.current_file = file_number
            self.is_playing = True
            self.start_time = time()
            self._pending_stop_confirmations = 0
            self.last_polled = time()

        return success

    def pause(self) -> bool:
        """Pause playback."""

        return self.send_command(_CMD_PAUSE)

    def stop(self) -> bool:
        """Stop playback."""

        success: bool = self.send_command(_CMD_STOP)
        if success:
            self.is_playing = False
            self.current_file = None
            self._pending_stop_confirmations = 0

        return success

    def set_volume(self, volume: int) -> bool:
        """Set volume (0-30).

        Args:
            volume: Volume level 0-30

        Returns:
            True if command sent successfully
        """

        clamped: int = max(0, min(30, volume))
        return self.send_command(_CMD_SET_VOLUME, clamped)

    def reset_module(self) -> bool:
        """Issue a UART soft reset to the YX5200 module.

        Returns:
            True if reset command sent successfully.
        """

        success: bool = self.send_command(_CMD_RESET, 0)
        if success:
            # Module firmware needs a short settle period after reset.
            sleep(0.7)
            self.is_playing = False
            self.current_file = None
            self._pending_stop_confirmations = 0

        return success

    def _find_response_frame(self, data: bytes, expected_cmd: int) -> tuple | None:
        """Search raw UART bytes for a valid 10-byte DFPlayer response frame.

        A valid response frame is: 7E FF 06 <cmd> 00 <param_h> <param_l> <chk_h> <chk_l> EF
        The feedback byte (index 4) is 0x00 in responses (vs 0x01 in sent commands).

        Returns:
            (param_high, param_low) tuple if a valid frame is found, None otherwise.
        """

        for i in range(len(data) - 9):
            if data[i] != _FRAME_START:
                continue
            if data[i + 9] != _FRAME_END:
                continue
            # Check version and length bytes
            if data[i + 1] != 0xFF or data[i + 2] != 0x06:
                continue
            # Check command byte matches expected response
            if data[i + 3] != expected_cmd:
                continue
            # Response frames have feedback=0x00; sent frames have 0x01.
            # Accept 0x00 to distinguish from echoed commands.
            if data[i + 4] != 0x00:
                continue
            return (data[i + 5], data[i + 6])

        return None

    def query_status(self) -> bool:
        """Query whether the player is currently playing.

        Sends a status query command and reads the response to determine
        actual playback state instead of relying on the is_playing flag.

        Returns:
            True if the module reports it is currently playing, False otherwise
        """

        if self.uart is None:
            return False

        try:
            if self.current_file is not None or self.is_playing:
                print(
                    f"audio: status check begin uart={self.uart_id} "
                    f"file={self.current_file} cached_playing={self.is_playing}"
                )
            self._debug(f"status check begin cached_playing={self.is_playing} current_file={self.current_file}")

            # Drain any stale bytes in the RX buffer before sending query.
            try:
                self.uart.read()
            except (AttributeError, OSError):
                pass

            # Send status query command
            frame: bytes = self._build_frame(_CMD_QUERY_STATUS, 0)
            self.uart.write(frame)

            # Wait for response — 100ms gives the module time to reply.
            sleep(0.1)
            resp = self.uart.read()

            if not resp or len(resp) < 10:
                # Keep previous state when status read is inconclusive.
                if self.current_file is not None or self.is_playing:
                    print(
                        f"audio: status inconclusive uart={self.uart_id} "
                        f"file={self.current_file} cached_playing={self.is_playing}"
                    )
                self._debug(f"status inconclusive resp={resp} keep_cached={self.is_playing}")
                return bool(self.is_playing)

            # Search for a valid response frame in the buffer. The buffer may
            # contain the echoed command frame followed by the actual response.
            parsed = self._find_response_frame(resp, _CMD_QUERY_STATUS)

            if parsed is None:
                # No valid response frame found — treat as inconclusive.
                try:
                    resp_hex = " ".join([f"{b:02X}" for b in resp])
                except Exception:
                    resp_hex = str(resp)
                if self.current_file is not None or self.is_playing:
                    print(
                        f"audio: status no valid frame uart={self.uart_id} "
                        f"file={self.current_file} resp={resp_hex}"
                    )
                self._debug(f"status no valid frame resp={resp_hex} keep_cached={self.is_playing}")
                return bool(self.is_playing)

            status_high, status_low = parsed
            status_param: int = ((status_high & 0xFF) << 8) | (status_low & 0xFF)
            is_playing = (status_low == _STATUS_PLAYING) or (status_high == _STATUS_PLAYING)

            try:
                resp_hex = " ".join([f"{b:02X}" for b in resp])
            except Exception:
                resp_hex = str(resp)
            self._debug(
                f"status resp={resp_hex} status_high=0x{status_high:02X} "
                f"status_low=0x{status_low:02X} status_param=0x{status_param:04X} "
                f"parsed_playing={is_playing} cached_before={self.is_playing}"
            )
            if self.current_file is not None or self.is_playing or is_playing:
                print(
                    f"audio: status result uart={self.uart_id} "
                    f"file={self.current_file} parsed_playing={is_playing} "
                    f"status_high=0x{status_high:02X} status_low=0x{status_low:02X} "
                    f"status_param=0x{status_param:04X}"
                )

            # Some modules briefly report a non-playing state right after play
            # starts or while transitioning tracks. Require two consecutive
            # stopped observations before clearing active playback state.
            if not is_playing and self.is_playing:
                self._pending_stop_confirmations += 1
                print(
                    f"audio: status stop pending uart={self.uart_id} " f"confirm={self._pending_stop_confirmations}/2"
                )
                if self._pending_stop_confirmations < 2:
                    return True
            else:
                self._pending_stop_confirmations = 0

            # Keep cached state in sync with hardware-reported status.
            self.is_playing = is_playing
            if not is_playing:
                self.current_file = None

            return is_playing

        except (AttributeError, OSError, TypeError, ValueError, IndexError) as error:
            # Treat UART errors as unknown and preserve cached state.
            if self.current_file is not None or self.is_playing:
                print(
                    f"audio: status query error uart={self.uart_id} "
                    f"file={self.current_file} cached_playing={self.is_playing} error={error}"
                )
            self._debug(f"status query error keep_cached={self.is_playing}")
            return bool(self.is_playing)

    def get_state(self) -> tuple | None:
        """Get current playback state using actual hardware status.

        Returns:
            (file_number, high_quality) tuple if playing, None if idle
        """

        # Query actual hardware status instead of relying on is_playing flag
        self._debug(f"get_state check begin cached_playing={self.is_playing} current_file={self.current_file}")
        is_currently_playing: bool = self.query_status()
        self._debug(
            "get_state check result " f"queried_playing={is_currently_playing} current_file={self.current_file}"
        )

        if is_currently_playing and self.current_file is not None:
            return (self.current_file, self.high_quality)

        return None


class AudioPlayer:
    """Manages 0-3 YX5200 MP3 player modules."""

    def __new__(cls) -> "AudioPlayer":
        """Implement singleton pattern."""

        if not hasattr(cls, "_instance"):
            cls._instance = super().__new__(cls)

        return cls._instance

    def __init__(self) -> None:
        """Initialise from persistent settings."""

        if getattr(self, "_initialised", False):
            return

        self._initialised: bool = True
        self.players: list = []

        storage: PersistentDict = PersistentDict()
        audio_config: list = storage.get("system_settings", {}).get("audio_players", [])

        for config in audio_config:
            if not isinstance(config, dict):
                continue

            uart_id: int | None = config.get("uart")
            tx_pin: int | None = config.get("tx_pin")
            rx_pin: int | None = config.get("rx_pin")
            high_quality: bool = bool(config.get("high_quality", False))

            if uart_id is not None and tx_pin is not None and rx_pin is not None:
                player: YX5200Player = YX5200Player(uart_id, tx_pin, rx_pin, high_quality)
                self.players.append(player)

        # Load master volume from persistent settings
        try:
            system_settings: dict = storage.get("system_settings", {})
            self.master_volume: int = system_settings.get("master_volume", 20)
            self.reset_on_boot: bool = bool(system_settings.get("audio_reset_on_boot", True))
            self.debug_logging: bool = bool(system_settings.get("audio_debug_logging", False))
        except (AttributeError, OSError, TypeError, ValueError) as error:
            print(f"AudioPlayer: reading master volume failed: {error}")
            sys.print_exception(error)
            self.master_volume = 20
            self.reset_on_boot = True
            self.debug_logging = False

        for player in self.players:
            player.debug_logging = self.debug_logging

        # Optional module reset at boot to recover players that miss power-on init.
        if self.reset_on_boot:
            for i, player in enumerate(self.players):
                try:
                    reset_ok = player.reset_module()
                    print(f"AudioPlayer: boot reset module {i} ok={reset_ok}")
                except (AttributeError, OSError, TypeError, ValueError) as error:
                    print(f"AudioPlayer: boot reset failed for module {i}: {error}")
                    sys.print_exception(error)

        # Perform a quick health check on configured modules at init/boot
        try:
            self.check_health()
        except (AttributeError, OSError, TypeError, ValueError) as error:
            # Non-fatal: ensure init continues even if health checks fail
            print(f"AudioPlayer: initial health check failed: {error}")
            sys.print_exception(error)

        # Initialize continuous polling state
        self._active_players: list = []
        self._current_poll_index: int = 0
        self._polling_timer_id = None

    def check_health(self) -> dict:
        """Check basic responsiveness of all configured players.

        Sends a volume set (with feedback) to each module and reports whether
        the module responded and its reported playback state.

        Returns a dict mapping module index to a dict with keys:
            - "ok": bool (whether the volume command succeeded)
            - "state": playback state from `get_state()` or None
            - "uart": uart id if available
        """

        results: dict = {}
        for i, player in enumerate(self.players):
            try:
                uart_id = getattr(player, "uart_id", None)
                tx_pin = getattr(player, "tx_pin", None)
                rx_pin = getattr(player, "rx_pin", None)
                # Use set_volume which issues a command and (via send_command)
                # attempts to read a short response from the module.
                ok = False
                try:
                    ok = player.set_volume(getattr(self, "master_volume", 20))
                except (AttributeError, OSError, TypeError, ValueError):
                    ok = False

                # Small pause to let module update internal state
                sleep(0.05)

                state = player.get_state()
                results[i] = {
                    "ok": bool(ok),
                    "state": state,
                    "uart": uart_id,
                    "tx_pin": tx_pin,
                    "rx_pin": rx_pin,
                }
                print(
                    f"AudioPlayer: health module {i} uart={uart_id} tx={tx_pin} rx={rx_pin} " f"ok={ok} state={state}"
                )
            except (AttributeError, OSError, TypeError, ValueError) as err:
                print(f"AudioPlayer: health check failed for module {i}: {err}")
                sys.print_exception(err)
                results[i] = {"ok": False, "state": None, "uart": None}

        healthy = sum(1 for v in results.values() if v.get("ok"))
        print(f"AudioPlayer: health check summary {healthy}/{len(self.players)} modules responsive")

        return results

    def play_file(self, file_number: int, high_quality_preferred: bool = False) -> int:
        """Play a file on an available module.

        Args:
            file_number: File number to play (1-9999)
            high_quality_preferred: Prefer high-quality modules if available

        Returns:
            Index of module that started playback.

        Raises:
            NoPlayersAvailable if all modules are busy or none configured.
        """

        if not self.players:
            raise NoPlayersAvailable("No audio modules configured")

        # Use cached is_playing as fast path; only query hardware for modules
        # that think they are playing (to confirm they haven't stopped).
        candidates: list = []
        for i, player in enumerate(self.players):
            if not player.is_playing:
                candidates.append(i)
            elif not player.query_status():
                candidates.append(i)

        if not candidates:
            raise NoPlayersAvailable(f"All {len(self.players)} modules busy")

        if high_quality_preferred:
            hq_candidates: list = [i for i in candidates if self.players[i].high_quality]
            module_idx: int = hq_candidates[0] if hq_candidates else candidates[0]
        else:
            module_idx = candidates[0]

        # Ensure volume is set correctly before playing
        try:
            self.set_volume(module_idx, self.master_volume)
        except (AttributeError, OSError, TypeError, ValueError):
            pass

        self.players[module_idx].play_file(file_number)
        return module_idx

    def get_playing_state(self) -> dict:
        """Get current playback state (cached, not queried).

        Returns the current state based on continuous background polling.
        Does NOT perform UART queries (use query_status() if you need fresh data).

        Returns:
            Dict mapping module index to (file_num, hq) tuple or None
        """

        state: dict = {}
        for i, player in enumerate(self.players):
            if player.is_playing and player.current_file is not None:
                state[i] = (player.current_file, player.high_quality)
            else:
                state[i] = None

        if getattr(self, "debug_logging", False):
            print(f"audio-debug: get_playing_state={state}")

        return state

    def set_volume(self, module_index: int, volume: int) -> bool:
        """Set volume for a specific module.

        Args:
            module_index: Module index (0-2)
            volume: Volume level 0-30

        Returns:
            True if successful, False if module index out of range
        """

        if 0 <= module_index < len(self.players):
            return self.players[module_index].set_volume(volume)

        return False

    def stop_all(self) -> None:
        """Stop playback on all modules."""

        for player in self.players:
            player.stop()

    def start_continuous_polling(self) -> None:
        """Start background polling of active players every 100ms.

        Maintains _active_players list of indices currently playing. Only polls
        players that are actively playing; when a player stops, it's automatically
        removed from the active list. Queries are staggered so only one player
        is polled per 100ms tick.
        """

        if self._polling_timer_id is not None:
            return

        try:
            from math import inf

            timer_mgr: TimerManager = TimerManager()
            timer = timer_mgr.get_timer(callback=self._poll_tick, periods=[100], cycles=inf)
            self._polling_timer_id = timer
            print("AudioPlayer: continuous polling started (100ms interval)")
        except (AttributeError, OSError, TypeError, ValueError) as error:
            print(f"AudioPlayer: failed to start continuous polling: {error}")
            sys.print_exception(error)
            raise

    def _poll_tick(self, _timer=None) -> None:
        """Poll one active player per 100ms tick, staggered across all active players.

        This method is called by the background timer every 100ms. It maintains
        the active_players list and queries exactly one player per tick to avoid
        blocking. When a player reports stopped, it's removed from active list.
        """

        try:
            # Rebuild active_players list: only include players currently playing
            self._active_players = [i for i in range(len(self.players)) if self.players[i].is_playing]

            if not self._active_players:
                return

            # Stagger queries: poll one active player per tick
            if self._current_poll_index >= len(self._active_players):
                self._current_poll_index = 0

            player_idx: int = self._active_players[self._current_poll_index]
            self.players[player_idx].query_status()
            self._current_poll_index += 1

            # Check for sounds that ended and handle loop/chain logic
            try:
                from sounds import SoundManager

                SoundManager().check_for_ended_sounds()
            except (AttributeError, ImportError, OSError):
                pass

        except (AttributeError, OSError, TypeError, ValueError, IndexError) as error:
            print(f"AudioPlayer: polling tick error: {error}")
            sys.print_exception(error)
            if self._polling_timer_id is not None:
                self._polling_timer_id.stop("kill")
                self._polling_timer_id = None
            raise
