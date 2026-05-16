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
- `debug_logging`: Enable OTA debug logs to `.ota_debug.log` (default `False`)

### Submodule Support

Submodule file tracking is **enabled by default**. The updater uses incremental chunked processing to handle large repository trees without exhausting memory:

1. Fetches the remote tree and saves it as newline-delimited snapshot entries
2. Scans local files and saves them as newline-delimited paths
3. Processes snapshot entries incrementally, comparing SHAs one path at a time
4. Detects changes to individual submodule files (not just the submodule pointer commit)
5. Cleans up temporary files when done

**How it works:**
- **Memory efficient:** Snapshot files are processed line-by-line
- **Stack safer:** Main repo traversal, submodule traversal, and local file discovery are iterative (non-recursive)
- **Socket compatible:** HTTPS response parsing supports SSL sockets with or without `makefile()`
- **Transparent:** Works automatically with no configuration changes needed

When `track_submodules=True`, the updater:
1. Fetches `.gitmodules` to discover submodule repositories
2. Recursively fetches the tree for each submodule at its pinned commit SHA
3. Merges submodule files into the update plan with full paths (e.g., `lib/file.py`)
4. Includes submodule file changes in the update plan

**To disable** submodule tracking (if you want to update them manually), add to persistent storage:
```python
"system_settings": {
    "ota": {
        "track_submodules": False
    }
}
```

### Debug Logging

If update checks crash before returning an error, enable OTA diagnostics:

```python
"system_settings": {
    "ota": {
        "debug_logging": True
    }
}
```

When enabled, the updater writes stage checkpoints and memory hints to `.ota_debug.log` so you can see the last completed phase before a crash.

### File Hashing

Files are compared using git blob SHA-1 hashing (same algorithm used by git itself), ensuring that identical file contents have identical hashes regardless of whitespace or timestamps.
