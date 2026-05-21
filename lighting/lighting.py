from animation import Animation
from leds import LEDs
from storage import PersistentDict

from lighting.colors import colors
from lighting.effects import EffectRuntimeMixin
from lighting.filters import FilterMixin
from lighting.metadata import FILTER_METADATA, PATTERN_METADATA
from lighting.patterns import PatternMixin


class Lighting(EffectRuntimeMixin, PatternMixin, FilterMixin):
    """Main lighting controller class."""

    def __new__(cls, *args, **kwargs):
        """Implement singleton pattern: return existing instance if it exists."""

        if not hasattr(cls, "_instance"):
            cls._instance = super().__new__(cls)

        return cls._instance

    def __init__(self):
        self.settings_object = PersistentDict()

        def _deepcopy(obj):
            if isinstance(obj, dict):
                return {k: _deepcopy(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_deepcopy(v) for v in obj]
            if isinstance(obj, tuple):
                return tuple(_deepcopy(v) for v in obj)
            return obj

        self._deepcopy = _deepcopy

        if "lighting_settings" not in self.settings_object:
            default_model = {
                "default_scene": None,
                "scenes": {},
                "named_ranges": {},
                "effects": {},
                "filters": {},
                "custom_colors": {},
                "scene_settings": {},
            }
            self.settings_object["lighting_settings"] = {
                "models": {"Default": default_model},
                "current_model": "Default",
            }
            try:
                self.settings_object.store()
            except Exception:
                pass

        self._load_lighting_root()

        self._active_scenes: list = []
        self._scene_start_ticks: dict = {}
        self.retained_values = {}
        self.scene_kwargs = {}
        self.scene_state = {}
        self._scene_functions = {}

        self.set_scene()
        self.leds = LEDs()
        self.logical_colors = [(0, 0, 0)] * self.leds.count

        self.animation = Animation(jobs={"lighting": self.process_tick}, stop_callbacks={"lighting": self.stop})

    def _store_settings(self) -> None:
        """Persist lighting settings and log storage failures."""

        try:
            self.settings_object.store()
        except (OSError, ValueError, TypeError) as error:
            print(f"lighting: failed to store settings: {error}")

    def _load_lighting_root(self) -> None:
        """Load lighting settings from persistent storage and select the active model."""

        lighting_root = self.settings_object.get("lighting_settings", {})

        legacy_keys = (
            "scenes",
            "effects",
            "filters",
            "named_ranges",
            "custom_colors",
            "scene_settings",
            "default_scene",
        )
        if "models" not in lighting_root and any(k in lighting_root for k in legacy_keys):
            old = self._deepcopy(lighting_root)
            new_root = {"models": {"Model": old}, "current_model": "Model"}
            self.settings_object["lighting_settings"] = new_root
            self._store_settings()
            lighting_root = new_root

        models = lighting_root.get("models", {})
        current = lighting_root.get("current_model")
        if not current or current not in models:
            names = list(models.keys())
            if names:
                current = names[0]
                lighting_root["current_model"] = current
            else:
                default_model = {
                    "default_scene": None,
                    "scenes": {},
                    "named_ranges": {},
                    "effects": {},
                    "filters": {},
                    "custom_colors": {},
                    "scene_settings": {},
                }
                lighting_root = {"models": {"Default": default_model}, "current_model": "Default"}
                self.settings_object["lighting_settings"] = lighting_root
                self._store_settings()

        self._lighting_root = lighting_root
        self.current_model_name = lighting_root.get("current_model")
        self.settings = self._lighting_root.setdefault("models", {}).setdefault(self.current_model_name, {})

    def get_model_names(self) -> list:
        """Return a sorted list of available model names."""

        return sorted(list(self._lighting_root.get("models", {}).keys()))

    def set_current_model(self, model_name: str) -> None:
        """Set the current model and persist the change."""

        models = self._lighting_root.get("models", {})
        if model_name not in models:
            raise ValueError(f"Model '{model_name}' not found.")
        self._lighting_root["current_model"] = model_name
        self.current_model_name = model_name
        self.settings = models[model_name]
        self._store_settings()

        self.set_scene(None)

    def create_model(self, model_name: str, copy_from_current: bool = False) -> None:
        """Create a new minimal model."""

        models = self._lighting_root.setdefault("models", {})
        if model_name in models:
            raise ValueError(f"Model '{model_name}' already exists.")

        if copy_from_current:
            allowed_keys = [
                "default_scene",
                "scenes",
                "named_ranges",
                "effects",
                "filters",
                "custom_colors",
                "scene_settings",
            ]

            new_model = {}
            for key in allowed_keys:
                if key in self.settings:
                    new_model[key] = self._deepcopy(self.settings[key])
                else:
                    if key == "default_scene":
                        new_model[key] = None
                    else:
                        new_model[key] = {}

            models[model_name] = new_model
        else:
            models[model_name] = {
                "default_scene": None,
                "scenes": {},
                "named_ranges": {},
                "effects": {},
                "filters": {},
                "custom_colors": {},
                "scene_settings": {},
            }

        self._store_settings()

    def delete_model(self, model_name: str) -> None:
        """Delete a model (cannot delete the active one)."""

        if model_name == self.current_model_name:
            raise ValueError("Cannot delete the currently active model.")
        models = self._lighting_root.get("models", {})
        if model_name in models:
            del models[model_name]
            self._store_settings()

    def rename_model(self, old_name: str, new_name: str) -> None:
        """Rename a model. Updates current_model and active settings if needed."""

        models = self._lighting_root.get("models", {})
        if old_name not in models:
            raise ValueError(f"Model '{old_name}' not found.")
        if new_name in models:
            raise ValueError(f"Model '{new_name}' already exists.")

        models[new_name] = models.pop(old_name)

        if self.current_model_name == old_name:
            self._lighting_root["current_model"] = new_name
            self.current_model_name = new_name
            self.settings = models[new_name]

        self._store_settings()

    def wrap_current_settings_into_model(self, model_name: str = "Model") -> None:
        """Wrap legacy top-level lighting settings into a models container."""

        root = self.settings_object.get("lighting_settings", {})
        if "models" in root:
            raise ValueError("lighting_settings already contains models; cannot wrap.")
        old = self._deepcopy(root)
        new_root = {"models": {model_name: old}, "current_model": model_name}
        self.settings_object["lighting_settings"] = new_root
        self._store_settings()
        self._load_lighting_root()

    def __repr__(self) -> str:
        return f"<Lighting current_model={self.current_model_name}>"

    @property
    def scene_name(self) -> str:
        """Return the most recently activated scene name, or None."""

        return self._active_scenes[-1] if self._active_scenes else None

    def get_pattern_metadata(self) -> dict:
        """Return metadata about all available patterns."""

        return PATTERN_METADATA

    def get_filter_metadata(self) -> dict:
        """Return metadata about all available filters."""

        return FILTER_METADATA

    def add_colors(self, new_colors: dict[str, tuple[int, int, int]]) -> None:
        """Add new named colors to the lighting system."""

        colors.update(new_colors)

    def register_scene_function(self, scene_name: str, func: object) -> None:
        """Register a callable to be invoked when the named scene is activated."""

        self._scene_functions[scene_name] = func

    def _clear_runtime_filter_state(self) -> None:
        """Clear transient runtime keys used by filter processing."""

        def _clear_filter_list_runtime(filter_list: object) -> None:
            if not isinstance(filter_list, list):
                return

            for filter_item in filter_list:
                if isinstance(filter_item, dict):
                    filter_item.pop("_state", None)
                    filter_item.pop("_target_groups", None)

        for stored_filter in self.settings.get("filters", {}).values():
            if isinstance(stored_filter, dict):
                stored_filter.pop("_state", None)
                stored_filter.pop("_target_groups", None)

        for effect_dict in self.settings.get("effects", {}).values():
            if isinstance(effect_dict, dict):
                _clear_filter_list_runtime(effect_dict.get("filters"))

        for scene_dict in self.settings.get("scenes", {}).values():
            if not isinstance(scene_dict, dict):
                continue

            for scene_entry in scene_dict.values():
                if isinstance(scene_entry, dict):
                    _clear_filter_list_runtime(scene_entry.get("filters"))

    def set_scene(self, scene_name: str = None, **kwargs) -> None:
        """Replace all active scenes with a single scene and reset state."""

        scenes: dict = self.settings.get("scenes", {})

        if scene_name is None:
            default_scene = self.settings.get("default_scene")
            if isinstance(default_scene, str) and default_scene in scenes:
                resolved = default_scene
            else:
                scene_keys = list(scenes.keys())
                if scene_keys:
                    resolved = scene_keys[0]
                else:
                    self._active_scenes = []
                    self._scene_start_ticks = {}
                    self.scene_kwargs = {}
                    self.scene_state = {}
                    self.retained_values = {}
                    return
        else:
            if scene_name not in scenes:
                raise ValueError(f"Scene '{scene_name}' not found. Available scenes: {list(scenes.keys())}")
            resolved = scene_name

        if self._active_scenes == [resolved] and not kwargs and not self.scene_finished:
            return

        self._clear_runtime_filter_state()

        self._active_scenes = [resolved]
        self._scene_start_ticks = {resolved: 0}
        self.scene_kwargs = kwargs
        self.scene_state = {}
        self.retained_values = {}

        if hasattr(self, "animation"):
            self.leds.clear()
            if hasattr(self, "logical_colors"):
                self.logical_colors = [(0, 0, 0)] * self.leds.count

            self.animation.reset()

        if resolved in self._scene_functions:
            self._scene_functions[resolved](lighting=self, **kwargs)

    def add_scene(self, scene_name: str) -> None:
        """Add a scene to the active set without disturbing currently running scenes."""

        scenes: dict = self.settings.get("scenes", {})
        if scene_name not in scenes:
            raise ValueError(f"Scene '{scene_name}' not found.")

        if scene_name in self._active_scenes:
            return

        scene_meta: dict = self.settings.get("scene_settings", {}).get(scene_name, {})
        for kill_name in scene_meta.get("kills", []):
            self.remove_scene(kill_name)

        self._active_scenes.append(scene_name)
        current_tick = self.animation.tick_number if hasattr(self, "animation") else 0
        self._scene_start_ticks[scene_name] = current_tick

        if scene_name in self._scene_functions:
            self._scene_functions[scene_name](lighting=self)

    def remove_scene(self, scene_name: str) -> None:
        """Remove a scene from the active set and clean up its state."""

        if scene_name not in self._active_scenes:
            return

        self._active_scenes.remove(scene_name)
        self._scene_start_ticks.pop(scene_name, None)

        prefix = scene_name + "::"
        for key in list(self.scene_state.keys()):
            if key.startswith(prefix):
                del self.scene_state[key]

        for key in list(self.retained_values.keys()):
            if key.startswith(prefix):
                del self.retained_values[key]

    def stop(self) -> None:
        """Runs on animation stop."""

        self.leds.clear()
        self.leds.show()
        self.logical_colors = [(0, 0, 0)] * self.leds.count

    def get_color(self, input: str | tuple | list) -> tuple[int, int, int]:
        """Resolve a color input to an RGB tuple."""

        if isinstance(input, str):
            if input.startswith("custom:"):
                color_name = input[7:]
                custom_colors = self.settings.get("custom_colors", {})
                if color_name in custom_colors:
                    rgb = custom_colors[color_name]
                    return (int(rgb[0]), int(rgb[1]), int(rgb[2]))
                return (255, 255, 255)

            return colors.get(input, (255, 255, 255))

        if isinstance(input, list):
            return [self.get_color(color) for color in input]

        return input

    def get_targets(self, target, _visited=None):
        """Return a list of target indices for the given target specification."""

        if _visited is None:
            _visited = set()

        result = []
        seen = set()

        def _append_unique(val):
            if val not in seen:
                seen.add(val)
                result.append(val)

        if isinstance(target, int):
            _append_unique(target)
            return result

        if isinstance(target, list):
            for item in target:
                for t in self.get_targets(item, _visited):
                    _append_unique(t)
            return result

        if isinstance(target, str):
            if target.startswith("named:"):
                range_name = target[6:]
                if range_name in _visited:
                    return []
                named_ranges = self.settings.get("named_ranges", {})
                if range_name in named_ranges:
                    new_visited = set(_visited)
                    new_visited.add(range_name)
                    named_target = named_ranges[range_name]
                    for t in self.get_targets(named_target, new_visited):
                        _append_unique(t)
                    return result
                return []

            if "-" in target:
                try:
                    start, end = map(int, target.split("-"))
                except Exception:
                    return []
                for i in range(start, end + 1):
                    _append_unique(i)
                return result

            if target == "all":
                for i in range(self.leds.count):
                    _append_unique(i)
                return result

            try:
                _append_unique(int(target))
                return result
            except Exception:
                return []

        return []

    def convert_frequencies_to_durations(self) -> int:
        """Convert all frequency-based pattern params to duration-based in persistent storage."""

        count = 0
        lighting_settings = self.settings

        def _convert_entry(entry: dict) -> None:
            nonlocal count
            if "frequency" not in entry:
                return

            new_value = max(1, int(40 // entry["frequency"]))
            del entry["frequency"]

            if entry.get("pattern") == "pulse":
                entry["period"] = new_value
            else:
                entry["duration"] = new_value

            count += 1

        for effect in lighting_settings.get("effects", {}).values():
            _convert_entry(effect)

        for scene in lighting_settings.get("scenes", {}).values():
            for entry in scene.values():
                _convert_entry(entry)

        if count > 0:
            self.settings_object.store()

        return count
