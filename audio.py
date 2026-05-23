"""YX5200 MP3 player module driver using DFPlayer protocol.

Supports 0-3 modules connected to separate UARTs. Each module plays from an SD card
with MP3 files in the /MP3/ folder numbered 0001.mp3, 0002.mp3, etc.
"""

from machine import UART  # type: ignore
from time import sleep, time
from storage import PersistentDict

# DFPlayer protocol constants
_BAUD_RATE: int = 9600
_FRAME_START: int = 0x7E
_FRAME_END: int = 0xEF
_CMD_SET_VOLUME: int = 0x06
_CMD_PLAY_FILE: int = 0x12
_CMD_PAUSE: int = 0x0E
_CMD_STOP: int = 0x16
_CMD_QUERY_STATUS: int = 0x42
_STATUS_TRACK_FINISHED: int = 0x3D
_STATUS_PLAYING: int = 0x01
_STATUS_STOPPED: int = 0x00


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
        self.current_file: int | None = None
        self.is_playing: bool = False
        self.start_time: float = 0.0

        try:
            self.uart: UART = UART(uart_id, baudrate=_BAUD_RATE, tx=tx_pin, rx=rx_pin, bits=8, stop=1)
        except (AttributeError, OSError, TypeError, ValueError) as err:
            print(f"YX5200: UART {uart_id} (tx={tx_pin}, rx={rx_pin}) init error: {err}")
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
            # Debug: show frame being written as hex
            try:
                hex_frame = " ".join([f"{b:02X}" for b in frame])
                print(
                    f"YX5200: UART {self.uart_id} (tx={self.tx_pin}, rx={self.rx_pin}) " f"sending frame: {hex_frame}"
                )
            except (TypeError, ValueError):
                pass

            self.uart.write(frame)

            # If feedback requested, try to read a response from the module
            try:
                sleep(0.05)
                resp = self.uart.read()
                if resp:
                    try:
                        hex_resp = " ".join([f"{b:02X}" for b in resp])
                        print(
                            f"YX5200: UART {self.uart_id} (tx={self.tx_pin}, rx={self.rx_pin}) "
                            f"response: {hex_resp}"
                        )
                    except (TypeError, ValueError):
                        print(
                            f"YX5200: UART {self.uart_id} (tx={self.tx_pin}, rx={self.rx_pin}) " "response (raw):",
                            resp,
                        )
                else:
                    print(f"YX5200: UART {self.uart_id} (tx={self.tx_pin}, rx={self.rx_pin}) " "no response received")
            except (AttributeError, OSError) as err:
                # Non-fatal: some UART drivers may not support read() immediately
                print(
                    f"YX5200: UART {self.uart_id} (tx={self.tx_pin}, rx={self.rx_pin}) " f"response read error: {err}"
                )

            return True
        except (AttributeError, OSError, TypeError, ValueError) as err:
            print(f"YX5200: UART {self.uart_id} (tx={self.tx_pin}, rx={self.rx_pin}) " f"write error: {err}")
            return False

    def play_file(self, file_number: int) -> bool:
        """Play a specific file (0001-9999).

        Args:
            file_number: File number to play

        Returns:
            True if command sent successfully
        """

        print(
            f"YX5200: UART {self.uart_id} (tx={self.tx_pin}, rx={self.rx_pin}) " f"play_file request -> {file_number}"
        )
        success: bool = self.send_command(_CMD_PLAY_FILE, file_number)
        if success:
            self.current_file = file_number
            self.is_playing = True
            self.start_time = time()

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
            # Send status query command
            frame: bytes = self._build_frame(_CMD_QUERY_STATUS, 0)
            self.uart.write(frame)

            # Wait for and read response
            sleep(0.05)
            resp = self.uart.read()

            if not resp or len(resp) < 10:
                print(
                    f"YX5200: UART {self.uart_id} (tx={self.tx_pin}, rx={self.rx_pin}) "
                    f"status query: no/incomplete response"
                )
                return False

            # Response format: 7E FF 06 42 XX ... EF
            # The status byte is at index 5 (after 7E FF 06 42)
            # Status: 0x00 = stopped, 0x01 = playing
            status_byte = resp[5] if len(resp) > 5 else 0x00
            is_playing = status_byte == _STATUS_PLAYING

            print(
                f"YX5200: UART {self.uart_id} (tx={self.tx_pin}, rx={self.rx_pin}) "
                f"status query: playing={is_playing} (byte={status_byte:02X})"
            )

            return is_playing

        except (AttributeError, OSError, TypeError, ValueError, IndexError) as err:
            print(f"YX5200: UART {self.uart_id} (tx={self.tx_pin}, rx={self.rx_pin}) " f"status query failed: {err}")
            return False

    def get_state(self) -> tuple | None:
        """Get current playback state using actual hardware status.

        Returns:
            (file_number, high_quality) tuple if playing, None if idle
        """

        # Query actual hardware status instead of relying on is_playing flag
        if self.query_status() and self.current_file is not None:
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

        if not self.players:
            print("AudioPlayer: no modules configured")
        else:
            try:
                infos = [
                    {
                        "index": i,
                        "uart": getattr(p, "uart_id", None),
                        "tx_pin": getattr(p, "tx_pin", None),
                        "rx_pin": getattr(p, "rx_pin", None),
                        "hq": getattr(p, "high_quality", False),
                        "is_playing": getattr(p, "is_playing", False),
                        "current_file": getattr(p, "current_file", None),
                    }
                    for i, p in enumerate(self.players)
                ]
                print("AudioPlayer: configured modules:", infos)
            except (AttributeError, TypeError) as error:
                print(f"AudioPlayer: module info collection failed: {error}")

        # Load master volume from persistent settings
        try:
            system_settings: dict = storage.get("system_settings", {})
            self.master_volume: int = system_settings.get("master_volume", 20)
        except (AttributeError, OSError, TypeError, ValueError) as error:
            print(f"AudioPlayer: reading master volume failed: {error}")
            self.master_volume = 20

        # Perform a quick health check on configured modules at init/boot
        try:
            self.check_health()
        except (AttributeError, OSError, TypeError, ValueError) as error:
            # Non-fatal: ensure init continues even if health checks fail
            print(f"AudioPlayer: initial health check failed: {error}")

        # Apply master volume to all players after health check
        try:
            for player in self.players:
                player.set_volume(self.master_volume)
        except (AttributeError, OSError, TypeError, ValueError) as error:
            # Non-fatal: ensure init continues even if volume setting fails
            print(f"AudioPlayer: setting master volume failed: {error}")

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
                results[i] = {"ok": False, "state": None, "uart": None}

        # summary
        try:
            healthy = sum(1 for v in results.values() if v.get("ok"))
            print(f"AudioPlayer: health check summary {healthy}/{len(self.players)} modules responsive")
        except (AttributeError, TypeError, ValueError) as error:
            print(f"AudioPlayer: health summary failed: {error}")

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

        # Check actual hardware status instead of just is_playing flag
        candidates: list = [i for i, p in enumerate(self.players) if not p.query_status()]  # Use actual status query

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
            print(f"AudioPlayer: set volume={self.master_volume} on module {module_idx}")
        except (AttributeError, OSError, TypeError, ValueError) as error:
            print(f"AudioPlayer: failed to set volume on module {module_idx}: {error}")

        self.players[module_idx].play_file(file_number)
        return module_idx

    def get_playing_state(self) -> dict:
        """Get state of all modules.

        Returns:
            Dict mapping module index to (file_num, hq) tuple or None
        """

        state: dict = {}
        for i, player in enumerate(self.players):
            state[i] = player.get_state()

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
