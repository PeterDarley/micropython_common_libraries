"""Persistent dictionary backed by JSON.

Usage:
    from storage import storage
    
    storage['brightness'] = 10
    storage['colors'] = [255, 128, 0]
    storage.store()  # Write to disk
    
    # After reboot:
    from storage import storage
    print(storage['brightness'])  # 10 (restored)
"""

import json


class PersistentDict:
    """A dict wrapper that persists to a JSON file.
    
    Implemented as a singleton per filename: multiple calls with the same
    filename return the same instance.
    """

    _instances = {}

    def __new__(cls, filename='storage.json'):
        """Return existing instance for this filename, or create a new one."""

        if filename not in cls._instances:
            instance = super().__new__(cls)
            cls._instances[filename] = instance
        return cls._instances[filename]

    def __init__(self, filename='storage.json'):
        """
        Initialize the persistent dict.

        Args:
            filename: Path to the JSON file (default 'storage.json').
        """

        if getattr(self, '_initialised', False):
            return
        self._initialised = True
        self.filename = filename
        self._data = {}
        self._load()

    def _load(self):
        """Load data from the JSON file on disk."""

        try:
            with open(self.filename, 'r') as file_handle:
                data = json.load(file_handle)
                if isinstance(data, dict):
                    self._data = data
        except (OSError, ValueError):
            pass

    def store(self):
        """Write the current dict contents to the JSON file."""

        try:
            with open(self.filename, 'w') as file_handle:
                json.dump(self._data, file_handle)
        except Exception as error:
            print(f"storage.store() error: {error}")
            raise

    def __setitem__(self, key, value):
        """Set an item in the dict."""
        self._data[key] = value

    def __getitem__(self, key):
        """Get an item from the dict."""
        return self._data[key]

    def __delitem__(self, key):
        """Delete an item from the dict."""
        del self._data[key]

    def __contains__(self, key):
        """Check if a key exists in the dict."""
        return key in self._data

    def __len__(self):
        """Return the number of items in the dict."""
        return len(self._data)

    def __repr__(self):
        """Return a string representation."""
        return f"PersistentDict({self._data!r})"

    def get(self, key, default=None):
        """Get an item with a default value."""
        return self._data.get(key, default)

    def keys(self):
        """Return the dict keys."""
        return self._data.keys()

    def values(self):
        """Return the dict values."""
        return self._data.values()

    def items(self):
        """Return the dict items."""
        return self._data.items()

    def update(self, *args, **kwargs):
        """Update the dict with new values."""
        self._data.update(*args, **kwargs)

    def pop(self, key, *args):
        """Remove and return a value."""
        return self._data.pop(key, *args)

    def clear(self):
        """Clear all items from the dict."""
        self._data.clear()


storage = PersistentDict('storage.json')
