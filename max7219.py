"""
Minimal MAX7219 8x8 LED matrix driver for MicroPython.

Supports one or more chained modules.  Extends framebuf.FrameBuffer so all
standard drawing primitives (text, line, rect, pixel, blit …) work directly.

Wiring (SPI, write-only — no MISO needed):
    MAX7219 DIN  → ESP32 MOSI
    MAX7219 CLK  → ESP32 SCK
    MAX7219 CS   → any GPIO (driven here as chip-select)
    MAX7219 VCC  → 5 V
    MAX7219 GND  → GND
"""

import framebuf
from micropython import const  # type: ignore

_REG_NOOP        = const(0x00)
_REG_DIGIT0      = const(0x01)   # rows 1-8 are registers 1-8
_REG_DECODEMODE  = const(0x09)
_REG_INTENSITY   = const(0x0A)
_REG_SCANLIMIT   = const(0x0B)
_REG_SHUTDOWN    = const(0x0C)
_REG_DISPLAYTEST = const(0x0F)


class Matrix8x8(framebuf.FrameBuffer):
    """
    One or more chained MAX7219 8x8 matrix modules as a single FrameBuffer.

    The display is `num * 8` pixels wide and 8 pixels tall.
    Matrix 0 is the left-most display; matrix `num-1` is the right-most.
    """

    def __init__(self, spi, cs, num: int = 1):
        """Create the chained-matrix framebuffer and initialize the hardware.

        Args:
            spi: Configured write-only SPI bus instance (machine.SPI).
            cs: Chip-select pin instance (machine.Pin), driven low during writes.
            num: Number of chained 8x8 modules.
        """

        self._spi = spi
        self._cs  = cs
        self._num = num
        # MONO_HLSB: horizontal bytes, MSbit = leftmost pixel.
        # stride = num bytes per row; total = num * 8 bytes.
        self._buffer = bytearray(num * 8)
        super().__init__(self._buffer, num * 8, 8, framebuf.MONO_HLSB)
        self._cs.value(1)
        self._init_display()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_all(self, reg: int, data: int) -> None:
        """Write the same (reg, data) pair to every module in the chain."""

        self._cs.value(0)
        for _ in range(self._num):
            self._spi.write(bytes([reg, data]))
        self._cs.value(1)

    def _init_display(self) -> None:
        """Run the shutdown-configure-enable register sequence and clear the display."""

        for reg, val in (
            (_REG_SHUTDOWN,    0),   # enter shutdown to configure safely
            (_REG_DISPLAYTEST, 0),   # disable test mode
            (_REG_SCANLIMIT,   7),   # scan all 8 rows
            (_REG_DECODEMODE,  0),   # raw LED control (no BCD decode)
            (_REG_INTENSITY,   5),   # medium brightness
            (_REG_SHUTDOWN,    1),   # normal operation
        ):
            self._write_all(reg, val)
        self.fill(0)
        self.show()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def brightness(self, value: int) -> None:
        """Set display brightness.  value must be 0 (min) … 15 (max)."""

        if not 0 <= value <= 15:
            raise ValueError("brightness must be 0-15")
        self._write_all(_REG_INTENSITY, value)

    def show(self) -> None:
        """Push the current framebuffer contents to the physical display."""

        # _buffer layout: row `r`, matrix `m`  →  byte  r * num + m
        for row in range(8):
            self._cs.value(0)
            # Write left-to-right (module 0 first shifts through to the far end).
            for m in range(self._num):
                self._spi.write(bytes([_REG_DIGIT0 + row,
                                       self._buffer[row * self._num + m]]))
            self._cs.value(1)
