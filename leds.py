"""
Simple WS2812 (NeoPixel) helper for MicroPython.

Usage:
    from leds import LEDs

    strip = LEDs(brightness=0.5)
    strip.fill((255,0,0))
    strip.show()

This wrapper provides convenience helpers and a global brightness scaler.
It intentionally keeps the API small and uses the builtin `neopixel.NeoPixel`.
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
    _instances = {}

    def __new__(
        cls, pin: int | None = None, n: int | None = None, brightness: float = 1.0, pin_inverted: bool = False
    ):
        """Return an existing instance for the given pin, or create a new one."""
        pin_num = pin if pin is not None else (NEOPIXELS["Pin"] if NEOPIXELS else None)

        if pin_num in cls._instances:
            return cls._instances[pin_num]

        instance = super().__new__(cls)
        instance._initialised = False
        cls._instances[pin_num] = instance
        return instance

    def __init__(
        self, pin: int | None = None, count: int | None = None, brightness: float = 1.0, pin_inverted: bool = False
    ):
        """Create a neopixel LED strip controller.

        - pin: integer GPIO pin number or `machine.Pin` instance (defaults to NEOPIXELS['Pin'] from settings)
        - count: number of LEDs (defaults to NEOPIXELS['Num'] from settings)
        - brightness: float 0.0-1.0 to scale colors (default 1.0)
        """
        if self._initialised:
            return

        if neopixel is None:
            raise RuntimeError("neopixel module not available")

        # Use settings defaults if pin or count not provided
        if pin is None:
            if NEOPIXELS is not None:
                pin = NEOPIXELS["Pin"]
            else:
                raise ValueError("pin must be specified or NEOPIXELS must be in settings")

        if count is None:
            if NEOPIXELS is not None:
                count = NEOPIXELS["Num"]
            else:
                raise ValueError("count must be specified or NEOPIXELS must be in settings")

        if isinstance(pin, int):
            pin = Pin(pin)

        self.count = int(count)
        self._np = neopixel.NeoPixel(pin, self.count)
        self._brightness = 1.0
        self.brightness = brightness
        self._initialised = True

    def _scale(self, color: tuple) -> tuple:
        if self._brightness >= 0.999:
            return tuple(int(min(255, max(0, c))) for c in color)
        return tuple(int(min(255, max(0, int(c * self._brightness)))) for c in color)

    def set(self, target: int | list | str, color: tuple) -> None:
        """Set pixel `target` to `color` (r,g,b). Does not write to strip until `show()` is called."""

        if isinstance(target, int):
            if target < 0 or target >= self.count:
                return
            indexes = [target]

        elif isinstance(target, list):
            indexes = [i for i in target if 0 <= i < self.count]

        elif isinstance(target, str) and "-" in target:
            start, end = map(int, target.split("-"))
            indexes = list(range(start, end + 1))

        for index in indexes:
            self._np[index] = self._scale(color)

    def get(self, index: int) -> tuple:
        """Return the raw value currently staged for pixel `index` (after brightness scaling)."""
        if index < 0 or index >= self.count:
            return (0, 0, 0)
        return tuple(self._np[index])

    def fill(self, color: tuple) -> None:
        """Fill the entire strip with `color` (r,g,b)."""
        scaled = self._scale(color)
        for i in range(self.count):
            self._np[i] = scaled

    def range(self, start: int, end: int, color: tuple) -> None:
        """Set pixels from `start` to `end` (exclusive) to `color` (r,g,b)."""
        scaled = self._scale(color)
        for i in range(max(0, start), min(self.count, end)):
            self._np[i] = scaled

    def identify(self, indexes: list | int) -> None:
        """Turn the given LED indexes white and all others black."""

        if isinstance(indexes, int):
            indexes = [indexes]

        target_set = set(indexes)
        white = self._scale((255, 255, 255))
        black = (0, 0, 0)

        for i in range(self.count):
            self._np[i] = white if i in target_set else black

        self.show()

    def clear(self) -> None:
        """Clear the strip (set all pixels to off)."""
        self.fill((0, 0, 0))

    def show(self) -> None:
        """Push the currently staged colors to the LEDs."""
        try:
            self._np.write()
        except Exception:
            # some ports raise on consecutive writes in bad states; ignore
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
