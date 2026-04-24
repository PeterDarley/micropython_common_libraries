"""Sound management module for playing titled sounds via audio modules.

Manages the mapping of sound titles to file numbers and coordinates playback
across available YX5200 modules.
"""

from audio import AudioPlayer, NoPlayersAvailable
from storage import PersistentDict


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

    def get_sounds(self) -> dict:
        """Get all configured sounds.

        Returns:
            Dict of {title: {file, duration_ms, high_quality}}
        """

        storage: PersistentDict = PersistentDict()
        sounds: dict = storage.get("sounds", {})
        return sounds

    def get_sound_by_title(self, title: str) -> dict | None:
        """Get a specific sound by title.

        Args:
            title: Sound title

        Returns:
            Sound dict or None if not found
        """

        sounds: dict = self.get_sounds()
        return sounds.get(title)

    def play_sound(self, title: str) -> int:
        """Play a sound by title.

        Args:
            title: Sound title to play

        Returns:
            Module index that started playback

        Raises:
            NoPlayersAvailable if no modules available
            ValueError if sound title not found
        """

        sound: dict | None = self.get_sound_by_title(title)
        if sound is None:
            raise ValueError(f"Sound '{title}' not found")

        file_number: int = sound.get("file", 0)
        high_quality: bool = bool(sound.get("high_quality", False))

        module_idx: int = self.audio_player.play_file(file_number, high_quality_preferred=high_quality)
        return module_idx

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

        return result

    def stop_all(self) -> None:
        """Stop playback on all modules."""

        self.audio_player.stop_all()

    def set_volume(self, module_index: int, volume: int) -> bool:
        """Set volume for a module.

        Args:
            module_index: Module index
            volume: Volume level 0-30

        Returns:
            True if successful
        """

        return self.audio_player.set_volume(module_index, volume)
