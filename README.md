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

## OTA Update Engine

The `ota_update.py` module provides a generic, project-agnostic OTA (over-the-air) update engine that synchronizes files from a GitHub repository. All project-specific behavior is configured at runtime, making the module reusable across different MicroPython projects.

### Configuration Parameters

When instantiating `OTAUpdater`, pass all project-specific rules as constructor arguments:

- `repo_owner`, `repo_name`: GitHub repository location
- `tracked_root_prefixes`: Path prefixes to include (e.g., `("lib/", "www/", ...)`)
- `tracked_root_files`: Root-level files to include by exact path
- `excluded_path_prefixes`: Remote/local path prefixes to skip (e.g., `(".git/", "copilot_working/", ...)`)
- `excluded_paths`: Exact remote/local paths to skip (e.g., upload scripts)
- `excluded_local_dirs`: Directory names to ignore during local scans (added to defaults like `.git`, `__pycache__`)
- `candidate_branches`: Branch names to try in order (defaults to `("main", "master")`)
- `user_agent`: HTTP user agent string
- `track_submodules`: Enable recursive fetching of git submodule files (default `False`)

### Submodule Support

When `track_submodules=True`, the updater:

1. Detects submodule entries (type `"commit"`) in the repository tree
2. Fetches `.gitmodules` to map submodule paths to their repository URLs
3. Recursively fetches the tree for each submodule at its pinned commit SHA
4. Merges submodule files into the update plan with full paths (e.g., `lib/file.py`)
5. Compares local file SHAs against remote submodule files for change detection

**Example:** If your project has `lib/` as a git submodule and you enable `track_submodules=True`, changes to any file in the submodule will appear as updateable changes when you run `check_for_updates()`.

### File Hashing

Files are compared using git blob SHA-1 hashing (same algorithm used by git itself), ensuring that identical file contents have identical hashes regardless of whitespace or timestamps.
