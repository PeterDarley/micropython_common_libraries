"""Persistent dictionary backed by JSON with lazy loading.

Usage:
    from storage import PersistentDict

    storage = PersistentDict('storage.json')
    storage['brightness'] = 10
    storage['colors'] = [255, 128, 0]
    storage.store()  # Write to disk

    # After reboot:
    storage = PersistentDict('storage.json')
    # Data is loaded from disk only when first accessed
    print(storage['brightness'])  # 10 (loaded from disk on first access)
"""

import json


class PersistentDict:
    """A dict wrapper that persists to a JSON file with lazy loading.

    Data is only loaded from disk on first access, reducing memory usage at startup.
    After first access, data is cached in memory like a normal dict.

    Implemented as a singleton per filename: multiple calls with the same
    filename return the same instance.
    """

    _instances = {}

    def __new__(cls, filename="storage.json"):
        """Return existing instance for this filename, or create a new one."""

        if filename not in cls._instances:
            instance = super().__new__(cls)
            cls._instances[filename] = instance
        return cls._instances[filename]

    def __init__(self, filename="storage.json"):
        """
        Initialize the lazy persistent dict.

        Args:
            filename: Path to the JSON file (default 'storage.json').
        """

        if getattr(self, "_initialised", False):
            return
        self._initialised = True
        self.filename = filename
        self._data = None  # None means not yet loaded from disk
        self._loaded = False

    def _ensure_loaded(self):
        """Load data from disk if not already loaded."""

        if self._loaded:
            return

        if self._data is None:
            self._data = {}

        try:
            with open(self.filename, "r") as file_handle:
                data = json.load(file_handle)
                if isinstance(data, dict):
                    self._data = data
        except (OSError, ValueError):
            # File doesn't exist or is invalid JSON; use empty dict
            self._data = {}

        self._loaded = True

    def store(self):
        """Write the current dict contents to the JSON file."""

        try:
            with open(self.filename, "w") as file_handle:
                json.dump(self._data if self._data is not None else {}, file_handle)
        except Exception as error:
            print(f"storage.store() error: {error}")
            raise

    def __setitem__(self, key, value):
        """Set an item in the dict."""
        self._ensure_loaded()
        self._data[key] = value

    def __getitem__(self, key):
        """Get an item from the dict."""
        self._ensure_loaded()
        return self._data[key]

    def __delitem__(self, key):
        """Delete an item from the dict."""
        self._ensure_loaded()
        del self._data[key]

    def __contains__(self, key):
        """Check if a key exists in the dict."""
        self._ensure_loaded()
        return key in self._data

    def __len__(self):
        """Return the number of items in the dict."""
        self._ensure_loaded()
        return len(self._data)

    def __repr__(self):
        """Return a string representation."""
        status = "unloaded" if not self._loaded else "loaded"
        return f"PersistentDict({status}, {self._data!r})"

    def get(self, key, default=None):
        """Get an item with a default value."""
        self._ensure_loaded()
        return self._data.get(key, default)

    def keys(self):
        """Return the dict keys."""
        self._ensure_loaded()
        return self._data.keys()

    def values(self):
        """Return the dict values."""
        self._ensure_loaded()
        return self._data.values()

    def items(self):
        """Return the dict items."""
        self._ensure_loaded()
        return self._data.items()

    def update(self, *args, **kwargs):
        """Update the dict with new values."""
        self._ensure_loaded()
        self._data.update(*args, **kwargs)

    def pop(self, key, *args):
        """Remove and return a value."""
        self._ensure_loaded()
        return self._data.pop(key, *args)

    def clear(self):
        """Clear all items from the dict."""
        self._ensure_loaded()
        self._data.clear()
        self._data.clear()


storage = PersistentDict("storage.json")
