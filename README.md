# lightmotron — shared MicroPython libraries

Common libraries used by the lightmotron project.

## Default pin assignments

| Setting | GPIO | Notes |
|---|---|---|
| NeoPixel data | 4 | Configurable via System Settings |
| Onboard RGB LED | 48 | Fixed — ESP32-S3-DevKitC-1 built-in NeoPixel |
| MAX7219 MOSI (DIN) | 23 | Billboard module (unused by default) |
| MAX7219 SCK (CLK) | 18 | Billboard module (unused by default) |
| MAX7219 CS | 5 | Billboard module (unused by default) |
| I2C SCL | 22 | Unused by default |
| I2C SDA | 21 | Unused by default |
| Onboard button | 0 | Boot button on DevKitC-1 |

## Modules

| Module | Purpose |
|---|---|
| `animation.py` | Animation loop and timing engine |
| `billboard.py` | MAX7219 LED matrix driver |
| `comms.py` | WiFi and I2C connection management |
| `control.py` | Button/input handling |
| `leds.py` | NeoPixel strip driver (single and multi-strip) |
| `max7219.py` | Low-level MAX7219 SPI driver |
| `storage.py` | Lazy-loaded persistent JSON storage |
| `timing.py` | Timer and scheduling utilities |
| `utils.py` | General helpers |
| `webserver.py` | Minimal HTTP server and template engine |
| `lighting/` | Full lighting system (patterns, filters, scenes) |

> **Note:** This directory is a shared submodule. Do not remove modules that appear unused in this project — they may be used by other projects.
