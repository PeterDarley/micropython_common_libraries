"""IP address announcement module for audio playback on device IP change.

Plays back the device's IP address using audio when it differs from stored IP,
without blocking the rest of the system.
"""

import sys
from time import sleep
from random import randint
from comms import WIFIManager  # type: ignore
from storage import PersistentDict
from audio import AudioPlayer, NoPlayersAvailable
from sounds import set_sound_unavailable
import _thread


def _get_stored_ip() -> str:
    """Get the stored IP address from persistent settings.

    Returns:
        Stored IP address as string, empty string if not found
    """

    storage: PersistentDict = PersistentDict()
    system_settings: dict = storage.get("system_settings", {})
    return str(system_settings.get("stored_ip_address", "")).strip()


def _store_ip_address(ip_address: str) -> None:
    """Store the current IP address in persistent settings.

    Args:
        ip_address: IP address to store
    """

    try:
        storage: PersistentDict = PersistentDict()
        system_settings: dict = storage.get("system_settings", {})
        system_settings["stored_ip_address"] = ip_address
        storage["system_settings"] = system_settings
        storage.store()
        print(f"ip_announcement: stored IP address: {ip_address}")
    except Exception as err:
        print(f"ip_announcement: failed to store IP address: {err}")
        sys.print_exception(err)


def _announce_ip_address_background(ip_address: str, voice: int) -> None:
    """Background thread function to announce IP address via audio.

    Plays "my IP address is", then each octet's digits with 3-second pauses
    between octets. Repeats the full announcement once after 15 seconds.
    Leading zeros within an octet are skipped (e.g. 086 is read as "8 6").
    Plays at maximum volume and restores the previous volume when done.

    Args:
        ip_address: IP address to announce (e.g. "192.168.1.100")
        voice: Voice to use (8 or 9)
    """

    try:
        set_sound_unavailable(True)
        audio_player: AudioPlayer = AudioPlayer()

        # Validate voice
        if voice not in (8, 9):
            voice = 8

        # File numbers for voices 8 and 9
        # "my ip address is" = 9908.mp3 or 9909.mp3
        intro_file: int = 9900 + voice

        # Parse IP octets
        octets: list = []
        try:
            parts = ip_address.split(".")
            octets = [int(p) for p in parts if p]
        except (ValueError, AttributeError):
            print(f"ip_announcement: invalid IP address: {ip_address}")
            set_sound_unavailable(False)
            return

        if len(octets) != 4:
            print(f"ip_announcement: IP address has {len(octets)} parts, expected 4")
            set_sound_unavailable(False)
            return

        # Override master_volume to max so play_file uses it; restore afterwards
        original_volume: int = getattr(audio_player, "master_volume", 20)
        audio_player.master_volume = 30

        def _play_with_retry(file_num: int, max_wait_s: int = 10) -> bool:
            """Play file_num, retrying until a module is free or timeout.

            Each retry calls play_file which internally calls query_status,
            advancing the stop-confirmation counter (6 needed at 100ms each).

            Args:
                file_num: File number to play
                max_wait_s: Maximum seconds to wait for a free module

            Returns:
                True if playback started, False if timed out
            """

            attempts: int = max_wait_s * 10
            for _ in range(attempts):
                try:
                    audio_player.play_file(file_num)
                    return True
                except NoPlayersAvailable:
                    sleep(0.1)

            print(f"ip_announcement: timed out waiting to play file {file_num}")
            return False

        # Announce twice (now and after 15 second pause)
        for announcement_pass in range(2):
            if announcement_pass > 0:
                sleep(15)

            try:
                # Play intro "my ip address is" — retry until module is free
                if not _play_with_retry(intro_file):
                    break

                # Play each octet; retry loop waits for each file to finish
                for octet_idx, octet in enumerate(octets):
                    octet_int: int = int(octet)

                    # Build digit list, skipping leading zeros
                    digits: list = [int(c) for c in str(octet_int)]

                    for digit in digits:
                        digit_file: int = 9900 + (voice * 10) + digit
                        _play_with_retry(digit_file)

                    # 2-second pause between octets for clarity
                    if octet_idx < len(octets) - 1:
                        sleep(3)

            except Exception as err:
                print(f"ip_announcement: playback error: {err}")
                sys.print_exception(err)
                break

    except Exception as err:
        print(f"ip_announcement: background thread error: {err}")
        sys.print_exception(err)
    finally:
        # Restore original volume
        try:
            audio_player.master_volume = original_volume
        except Exception:
            pass

        set_sound_unavailable(False)


def check_and_announce_ip() -> None:
    """Check if IP has changed and announce it if audio is available.

    This function checks if the current IP differs from the stored IP,
    and if so, launches a background thread to announce the new address
    without blocking the main system.

    If no audio players are configured, does nothing silently.
    """

    try:
        # Check if audio players are configured
        storage: PersistentDict = PersistentDict()
        system_settings: dict = storage.get("system_settings", {})
        audio_players: list = system_settings.get("audio_players", [])

        if not audio_players or len(audio_players) == 0:
            # No audio players configured, skip announcement
            return

        # Get current IP address
        wifi_mgr: object = WIFIManager()
        current_ip: str = str(getattr(wifi_mgr, "ip", "")).strip()

        if not current_ip or current_ip == "N/A":
            print("ip_announcement: no IP address available")
            return

        # Get stored IP
        stored_ip: str = _get_stored_ip()

        # If IPs match, skip announcement
        if current_ip == stored_ip:
            return

        print(f"ip_announcement: IP changed from '{stored_ip}' to '{current_ip}'")

        # Randomly choose voice (8 or 9)
        voice: int = randint(8, 9)

        # Launch background thread to announce IP without blocking
        _thread.start_new_thread(_announce_ip_address_background, (current_ip, voice))

    except Exception as err:
        print(f"ip_announcement: check_and_announce_ip error: {err}")
        sys.print_exception(err)


def set_ip_announced(ip_address: str) -> None:
    """Mark an IP address as announced so it won't be announced again.

    This can be called via the UI to store the current IP when the user
    confirms they've heard/recorded it.

    Args:
        ip_address: IP address to mark as announced
    """

    try:
        _store_ip_address(ip_address)
        print(f"ip_announcement: marked IP {ip_address} as announced")
    except Exception as err:
        print(f"ip_announcement: set_ip_announced error: {err}")
        sys.print_exception(err)
