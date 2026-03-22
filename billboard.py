"""
Billboard — high-level driver for one or more chained MAX7219 8x8 LED matrices.

Depends on max7219.py (must also be in lib/).

Usage:
    import settings
    from billboard import Billboard

    bb = Billboard.from_settings()      # uses settings.BILLBOARD
    bb.scroll_text("Hello World!")
    bb.static_text("Hi!")
    bb.clear()

    # Or construct manually:
    bb = Billboard(mosi=23, sck=18, cs=5, num=1, brightness=5)
"""

import framebuf
import time
from machine import Pin, SPI  # type: ignore

try:
    import _thread
    _THREAD = True
except Exception:
    _THREAD = False

from max7219 import Matrix8x8


class Billboard:

    def __init__(self, *, mosi, sck, cs, num=1, brightness=5, debug=False):
        """
        Parameters
        ----------
        mosi       : GPIO pin number for SPI MOSI (MAX7219 DIN)
        sck        : GPIO pin number for SPI SCK  (MAX7219 CLK)
        cs         : GPIO pin number for chip-select (MAX7219 CS/LOAD)
        num        : number of chained 8x8 modules
        brightness : initial brightness 0-15
        debug      : print debug messages
        """
        self._num   = num
        self._debug = debug

        spi    = SPI(1, baudrate=10_000_000, polarity=1, phase=0,
                     sck=Pin(sck), mosi=Pin(mosi))
        cs_pin = Pin(cs, Pin.OUT, value=1)

        self._matrix = Matrix8x8(spi, cs_pin, num)
        self._matrix.brightness(brightness)
        self._scroll_stop = False
        self._scrolling = False

        if debug:
            print('billboard: init ok,', num, 'module(s), brightness', brightness)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_settings(cls, debug=False):
        """Construct a Billboard using settings.BILLBOARD."""
        import settings
        cfg = settings.BILLBOARD
        return cls(
            mosi=cfg['MOSI'],
            sck=cfg['SCK'],
            cs=cfg['CS'],
            num=cfg['Num'],
            brightness=cfg['Brightness'],
            debug=debug,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def width(self):
        """Display width in pixels."""
        return self._num * 8

    @property
    def matrix(self):
        """Direct access to the underlying Matrix8x8 FrameBuffer."""
        return self._matrix

    # ------------------------------------------------------------------
    # Basic operations
    # ------------------------------------------------------------------

    def clear(self):
        """Clear the display."""
        self._matrix.fill(0)
        self._matrix.show()

    def set_brightness(self, value):
        """Set brightness 0-15."""
        self._matrix.brightness(value)

    def show(self):
        """Push framebuffer to display (use after drawing to self.matrix directly)."""
        self._matrix.show()

    # ------------------------------------------------------------------
    # Text helpers
    # ------------------------------------------------------------------

    def static_text(self, msg, x=0, y=0):
        """Display static text at pixel offset (x, y).  Text is clipped to display width."""
        self._matrix.fill(0)
        self._matrix.text(msg, x, y, 1)
        self._matrix.show()
        if self._debug:
            print('billboard: static_text:', repr(msg))

    def _scroll_text_blocking(self, msg, delay_ms=60, repeat=1):
        """
        Internal: Scroll text from right to left (blocking).
        Use scroll_text() instead, which runs this in the background.
        """
        self._scrolling = True
        self._scroll_stop = False
        try:
            # Each character in MicroPython's built-in font is 8 px wide.
            char_px    = 8
            padding_px = self.width                         # blank run before/after
            text_px    = len(msg) * char_px
            total_px   = padding_px + text_px + padding_px  # always a multiple of 8

            # Build a temp FrameBuffer to hold the full rendered message.
            tmp_buf = bytearray(total_px)                   # MONO_HLSB: total_px/8 * 8 bytes
            fb = framebuf.FrameBuffer(tmp_buf, total_px, 8, framebuf.MONO_HLSB)
            fb.fill(0)
            fb.text(msg, padding_px, 0, 1)

            if self._debug:
                print('billboard: scroll_text:', repr(msg),
                      'total_px:', total_px, 'frames:', total_px - self.width + 1)

            for _ in range(repeat):
                for offset in range(total_px - self.width + 1):
                    if self._scroll_stop:
                        if self._debug:
                            print('billboard: scroll cancelled')
                        return
                    self._matrix.fill(0)
                    # blit at negative x so fb column `offset` aligns with display column 0
                    self._matrix.blit(fb, -offset, 0)
                    self._matrix.show()
                    time.sleep_ms(delay_ms)
        finally:
            self._scrolling = False

    def scroll_text(self, msg, delay_ms=60, repeat=1):
        """
        Scroll text from right to left across the display (non-blocking).

        If a scroll is already in progress it is cancelled and the new
        message starts once the previous thread has exited.

        Parameters
        ----------
        msg      : string to display
        delay_ms : milliseconds between each one-pixel shift
        repeat   : how many times to scroll the message

        Runs in background thread if available; blocks otherwise.
        """
        if _THREAD:
            if self._scrolling:
                if self._debug:
                    print('billboard: cancelling in-flight scroll')
                self._scroll_stop = True
                # Wait for the previous thread to finish before starting a new one.
                while self._scrolling:
                    time.sleep_ms(10)
            _thread.start_new_thread(self._scroll_text_blocking, (msg, delay_ms, repeat))
        else:
            # Fallback: run blocking if threading not available
            self._scroll_text_blocking(msg, delay_ms, repeat)

    # ------------------------------------------------------------------
    # Low-level pixel access
    # ------------------------------------------------------------------

    def set_pixel(self, x, y, val=1):
        """Set a single pixel and push to display."""
        self._matrix.pixel(x, y, val)
        self._matrix.show()

    def fill_pattern(self, pattern):
        """
        Fill from a flat list of bytes organised as:
            [module0_row0, module0_row1, ... module0_row7,
             module1_row0, ..., module(N-1)_row7]

        Total length must be 8 * num_modules.
        Multiplying an 8-byte single-module pattern by num gives the correct layout.
        """
        if len(pattern) != 8 * self._num:
            raise ValueError("pattern must be {} bytes".format(8 * self._num))
        self._matrix.fill(0)
        for m in range(self._num):
            for row in range(8):
                byte_val = pattern[m * 8 + row]
                for bit in range(8):
                    if byte_val & (1 << (7 - bit)):
                        self._matrix.pixel(m * 8 + bit, row, 1)
        self._matrix.show()
