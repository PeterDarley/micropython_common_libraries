# Shared MicroPython Libraries

Reusable libraries shared across MicroPython projects.

## Pin Assignments

Pin defaults are intentionally defined by the host project, typically via
its settings module or persistent storage. Shared library code should avoid
hardcoding project-specific pin maps.

## Modules

| Module | Purpose |
|---|---|
| `animation.py` | Animation loop and timing engine |
| `billboard.py` | MAX7219 LED matrix driver |
| `comms.py` | WiFi and I2C connection management |
| `control.py` | Button/input handling |
| `leds.py` | NeoPixel strip driver (single and multi-strip) |
| `max7219.py` | Low-level MAX7219 SPI driver |
| `ota_update.py` | Generic OTA updater engine for GitHub-backed file sync |
| `storage.py` | Lazy-loaded persistent JSON storage |
| `timing.py` | Timer and scheduling utilities |
| `utils.py` | General helpers |
| `webserver.py` | Minimal HTTP server and template engine |
| `lighting/` | Full lighting system (patterns, filters, scenes) |

> **Note:** This directory is a shared submodule. Do not remove modules that appear unused in one project, since they may be used by others.
