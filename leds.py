"""
Simple WS2812 (NeoPixel) helper for MicroPython.

Supports multiple physical LED strips treated as a single logical string.

Usage:
    from leds import LEDs

    strip = LEDs(brightness=0.5)
    strip.fill((255,0,0))
    strip.show()

Settings can define either a single strip or multiple strips:
    # Single strip (legacy)
    NEOPIXELS = {"Pin": 4, "Num": 144}

    # Multiple strips (new)
    NEOPIXELS = [
        {"pin": 4, "count": 144},
        {"pin": 12, "count": 60},
    ]

Indices are mapped contiguously: strip 0 has indices 0-143, strip 1 has 144-203, etc.
This wrapper provides convenience helpers and a global brightness scaler.
"""

try:
    import neopixel
    from machine import Pin
except Exception:
    neopixel = None

try:
    from settings import NEOPIXELS
except Exception:
    NEOPIXELS = None


class LEDs:
    _instance = None

    def __new__(
        cls, pin: int | None = None, count: int | None = None, brightness: float = 1.0, pin_inverted: bool = False
    ):
        """Return the singleton instance (multi-strip support requires single global instance)."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialised = False
        return cls._instance

    def __init__(
        self, pin: int | None = None, count: int | None = None, brightness: float = 1.0, pin_inverted: bool = False
    ):
        """Create a neopixel LED strip controller.

        Supports multiple physical strips via NEOPIXELS list in settings.
        All strips are treated as one logical string with contiguous indices.

        Args:
            pin: Ignored when multiple strips are configured. Single strip GPIO pin (defaults to NEOPIXELS['Pin']).
            count: Ignored when multiple strips are configured. Single strip count (defaults to NEOPIXELS['Num']).
            brightness: float 0.0-1.0 to scale colors (default 1.0)
        """
        if self._initialised:
            return

        if neopixel is None:
            raise RuntimeError("neopixel module not available")

        strips_config = self._parse_neopixels_config()
        if not strips_config:
            raise ValueError("NEOPIXELS must be configured in settings")

        # Initialize all physical strips and track their offsets
        self._strips = []
        self._strip_offsets = []
        total_count = 0

        for config in strips_config:
            pin_num = config["pin"]
            strip_count = config["count"]

            if isinstance(pin_num, int):
                pin_num = Pin(pin_num)

            self._strips.append(neopixel.NeoPixel(pin_num, strip_count))
            self._strip_offsets.append(total_count)
            total_count += strip_count

        self.count = total_count
        self._brightness = 1.0
        self.brightness = brightness
        self._initialised = True

    def _parse_neopixels_config(self) -> list:
        """Parse NEOPIXELS setting into list of {pin, count} dicts.

        Supports both legacy dict format and new list format.
        """
        if NEOPIXELS is None:
            return []

        if isinstance(NEOPIXELS, list):
            # New format: list of {pin, count} dicts
            return NEOPIXELS
        elif isinstance(NEOPIXELS, dict):
            # Legacy format: single {Pin, Num} dict
            return [{"pin": NEOPIXELS["Pin"], "count": NEOPIXELS["Num"]}]

        return []

    def _map_index(self, index: int) -> tuple:
        """Map logical index to (strip_index, physical_index).

        Args:
            index: Logical index (0 to total count-1)

        Returns:
            Tuple of (strip_index, physical_index) or None if out of bounds
        """
        if index < 0 or index >= self.count:
            return None

        for strip_idx in range(len(self._strips)):
            offset = self._strip_offsets[strip_idx]
            if strip_idx == len(self._strips) - 1:  # Last strip
                return (strip_idx, index - offset)
            elif index < self._strip_offsets[strip_idx + 1]:
                return (strip_idx, index - offset)

        return None

    def _get_indexes(self, target: int | list | str) -> list:
        """Convert target specification to list of logical indices."""
        if isinstance(target, int):
            if 0 <= target < self.count:
                return [target]
            return []

        elif isinstance(target, list):
            return [i for i in target if 0 <= i < self.count]

        elif isinstance(target, str):
            if "-" in target:
                start, end = map(int, target.split("-"))
                return list(range(max(0, start), min(self.count, end + 1)))
            elif target == "all":
                return list(range(self.count))

        return []

    def _scale(self, color: tuple) -> tuple:
        """Apply brightness scaling to color."""
        if self._brightness >= 0.999:
            return tuple(int(min(255, max(0, c))) for c in color)
        return tuple(int(min(255, max(0, int(c * self._brightness)))) for c in color)

    def set(self, target: int | list | str, color: tuple) -> None:
        """Set pixels `target` to `color` (r,g,b). Does not write to strip until `show()` is called.

        Args:
            target: int index, list of indices, "0-14" range string, or "all"
            color: RGB tuple (r, g, b)
        """
        scaled = self._scale(color)
        indexes = self._get_indexes(target)

        for logical_index in indexes:
            mapping = self._map_index(logical_index)
            if mapping is not None:
                strip_idx, phys_idx = mapping
                self._strips[strip_idx][phys_idx] = scaled

    def get(self, index: int) -> tuple:
        """Return the raw value currently staged for pixel `index` (after brightness scaling)."""
        mapping = self._map_index(index)
        if mapping is None:
            return (0, 0, 0)

        strip_idx, phys_idx = mapping
        return tuple(self._strips[strip_idx][phys_idx])

    def fill(self, color: tuple) -> None:
        """Fill all strips with `color` (r,g,b)."""
        scaled = self._scale(color)
        for strip in self._strips:
            for i in range(len(strip)):
                strip[i] = scaled

    def range(self, start: int, end: int, color: tuple) -> None:
        """Set pixels from `start` to `end` (exclusive) to `color` (r,g,b)."""
        self.set(list(range(max(0, start), min(self.count, end))), color)

    def identify(self, indexes: list | int) -> None:
        """Turn the given LED indexes white and all others black."""
        if isinstance(indexes, int):
            indexes = [indexes]

        target_set = set(indexes)
        white = self._scale((255, 255, 255))
        black = (0, 0, 0)

        for i in range(self.count):
            color = white if i in target_set else black
            mapping = self._map_index(i)
            if mapping is not None:
                strip_idx, phys_idx = mapping
                self._strips[strip_idx][phys_idx] = color

        self.show()

    def clear(self) -> None:
        """Clear all strips (set all pixels to off)."""
        self.fill((0, 0, 0))

    def show(self) -> None:
        """Push the currently staged colors to all physical strips."""
        for strip in self._strips:
            try:
                strip.write()
            except Exception:
                # Some ports raise on consecutive writes in bad states; ignore
                pass

    @property
    def brightness(self):
        return self._brightness

    @brightness.setter
    def brightness(self, v: float) -> None:
        try:
            f = float(v)
        except Exception:
            f = 1.0
        if f < 0:
            f = 0.0
        if f > 1:
            f = 1.0
        self._brightness = f

    @staticmethod
    def wheel(pos: int) -> tuple:
        """Generate rainbow colors across 0-255."""
        pos = pos % 256
        if pos < 85:
            return (255 - pos * 3, pos * 3, 0)
        if pos < 170:
            pos -= 85
            return (0, 255 - pos * 3, pos * 3)
        pos -= 170
        return (pos * 3, 0, 255 - pos * 3)
