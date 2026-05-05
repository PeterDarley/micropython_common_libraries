import math
import random

from animation import Animation
from leds import LEDs
from storage import PersistentDict

from lighting.colors import colors

# Pattern metadata: defines required and optional parameters for each pattern
PATTERN_METADATA: dict = {
    "solid": {
        "description": "Solid color",
        "required": ["target", "colors"],
        "optional": [],
        "color_count": 1,
    },
    "blink": {
        "description": "Blinking color",
        "required": ["target", "colors"],
        "optional": ["duration"],
        "color_count": 2,
    },
    "pulse": {
        "description": "Pulsing color",
        "required": ["target", "colors"],
        "optional": ["duration", "period"],
        "color_count": 2,
    },
    "fade_in": {
        "description": "Fade between two colors",
        "required": ["target", "colors"],
        "optional": ["duration"],
        "color_count": 2,
    },
    "breathe": {
        "description": "Breathing effect",
        "required": ["target", "colors"],
        "optional": ["duration"],
        "color_count": 2,
    },
    "wave": {
        "description": "Moving wave effect",
        "required": ["target", "colors"],
        "optional": ["duration", "number", "width", "reverse"],
        "color_count": 2,
    },
    "cylon": {
        "description": "Cylon bouncing effect",
        "required": ["target", "colors"],
        "optional": ["duration", "width"],
        "color_count": 2,
    },
    "phaser_strip": {
        "description": "Two waves from each end converging on a random meeting point",
        "required": ["target", "colors"],
        "optional": ["duration", "width"],
        "color_count": 2,
    },
}

# Filter metadata: defines optional parameters for each filter
FILTER_METADATA: dict = {
    "null": {
        "description": "No filter",
        "optional": [],
    },
    "sizzle": {
        "description": "Sizzle filter",
        "optional": ["frequency", "variation", "heat"],
    },
    "scintillate": {
        "description": "Scintillate filter",
        "optional": ["frequency", "variation", "heat"],
    },
}


class Lighting:
    """Main lighting controller class."""

    def __new__(cls, *args, **kwargs):
        """Implement singleton pattern: return existing instance if it exists."""

        if not hasattr(cls, "_instance"):
            cls._instance = super().__new__(cls)

        return cls._instance

    def __init__(self):
        self.settings_object = PersistentDict()

        # Small recursive copier used for migration/copy operations without importing copy.
        def _deepcopy(obj):
            if isinstance(obj, dict):
                return {k: _deepcopy(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_deepcopy(v) for v in obj]
            if isinstance(obj, tuple):
                return tuple(_deepcopy(v) for v in obj)
            return obj

        self._deepcopy = _deepcopy

        # Ensure persistent lighting settings exist as a models container; migrate legacy layout if necessary.
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

        # Load or migrate lighting settings and set `self.settings` to the active model dict.
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

    def _load_lighting_root(self) -> None:
        """Load lighting settings from persistent storage and select the active model.

        If an old single-model layout is detected, wrap it into a models container
        under the name "Model" and persist the change.
        """

        lighting_root = self.settings_object.get("lighting_settings", {})

        # Detect legacy single-model structure (contains scene/effect keys at top-level)
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
            try:
                self.settings_object.store()
            except Exception:
                pass
            lighting_root = new_root

        # Ensure a models container and choose the current model
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
                try:
                    self.settings_object.store()
                except Exception:
                    pass

        self._lighting_root = lighting_root
        self.current_model_name = lighting_root.get("current_model")
        self.settings = self._lighting_root.setdefault("models", {}).setdefault(self.current_model_name, {})

    # diagnostics removed

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
        try:
            self.settings_object.store()
        except Exception:
            pass

    def create_model(self, model_name: str, copy_from_current: bool = False) -> None:
        """Create a new minimal model.

        By default this creates an empty model suitable for UI-managed creation.
        Passing `copy_from_current=True` will copy configuration keys from the
        active model (use sparingly).
        """

        models = self._lighting_root.setdefault("models", {})
        if model_name in models:
            raise ValueError(f"Model '{model_name}' already exists.")

        if copy_from_current:
            # Copy only known configuration keys to avoid copying runtime
            # objects or cyclic references that may exist in the live
            # settings dict.
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
                    try:
                        new_model[key] = self._deepcopy(self.settings[key])
                    except Exception:
                        # Fall back to a shallow copy for this key
                        val = self.settings[key]
                        if isinstance(val, dict):
                            new_model[key] = {k: v for k, v in val.items()}
                        elif isinstance(val, list):
                            new_model[key] = list(val)
                        else:
                            new_model[key] = val
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

        # Persist change
        self.settings_object.store()

    def delete_model(self, model_name: str) -> None:
        """Delete a model (cannot delete the active one)."""

        if model_name == self.current_model_name:
            raise ValueError("Cannot delete the currently active model.")
        models = self._lighting_root.get("models", {})
        if model_name in models:
            del models[model_name]
            try:
                self.settings_object.store()
            except Exception:
                pass

    def rename_model(self, old_name: str, new_name: str) -> None:
        """Rename a model. Updates current_model and active settings if needed.

        Raises ValueError if the old name does not exist or the new name already exists.
        """

        models = self._lighting_root.get("models", {})
        if old_name not in models:
            raise ValueError(f"Model '{old_name}' not found.")
        if new_name in models:
            raise ValueError(f"Model '{new_name}' already exists.")

        models[new_name] = models.pop(old_name)

        # If the renamed model was the current model, update the pointer and settings
        if self.current_model_name == old_name:
            self._lighting_root["current_model"] = new_name
            self.current_model_name = new_name
            self.settings = models[new_name]

        try:
            self.settings_object.store()
        except Exception:
            pass

    def wrap_current_settings_into_model(self, model_name: str = "Model") -> None:
        """Wrap legacy top-level lighting settings into a models container.

        Raises ValueError if lighting_settings already contains models.
        """

        root = self.settings_object.get("lighting_settings", {})
        if "models" in root:
            raise ValueError("lighting_settings already contains models; cannot wrap.")
        old = self._deepcopy(root)
        new_root = {"models": {model_name: old}, "current_model": model_name}
        self.settings_object["lighting_settings"] = new_root
        try:
            self.settings_object.store()
        except Exception:
            pass
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

    def add_colors(self, new_colors: dict[str, tuple[int, int, int]]):
        """Add new named colors to the lighting system."""

        colors.update(new_colors)

    def register_scene_function(self, scene_name: str, func) -> None:
        """Register a callable to be invoked when the named scene is activated.

        The function will be called as ``func(lighting=self, **kwargs)`` whenever
        ``set_scene`` is called with that scene name, where ``kwargs`` are any
        extra keyword arguments forwarded from ``set_scene``.
        """

        self._scene_functions[scene_name] = func

    def set_scene(self, scene_name: str = None, **kwargs) -> None:
        """Replace all active scenes with a single scene and reset state.

        Any additional keyword arguments are stored as ``scene_kwargs`` and
        forwarded to a registered scene function (if one exists for the scene).
        """

        scenes: dict = self.settings.get("scenes", {})

        # Determine which scene to activate. If no scene is provided and there
        # are no defined scenes, make this a no-op to avoid setting a None
        # scene name into the active scene list (which would break joins).
        if scene_name is None:
            default_scene = self.settings.get("default_scene")
            if isinstance(default_scene, str) and default_scene in scenes:
                resolved = default_scene
            else:
                scene_keys = list(scenes.keys())
                if scene_keys:
                    resolved = scene_keys[0]
                else:
                    # No scenes defined: clear active state and return early.
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

        # Skip if already running just this scene with no kwargs and not finished.
        if self._active_scenes == [resolved] and not kwargs and not self.scene_finished:
            return

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
        """Add a scene to the active set without disturbing currently running scenes.

        The scene starts from the current animation tick. Does nothing if the
        scene is already active. If the scene has a ``kills`` list in its
        scene_settings, those scenes are removed before this one is added.
        """

        scenes: dict = self.settings.get("scenes", {})
        if scene_name not in scenes:
            raise ValueError(f"Scene '{scene_name}' not found.")

        if scene_name in self._active_scenes:
            return

        # Remove any scenes listed in this scene's kills setting.
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

        # Remove all state entries belonging to this scene.
        prefix = scene_name + "::"
        for key in list(self.scene_state.keys()):
            if key.startswith(prefix):
                del self.scene_state[key]

        for key in list(self.retained_values.keys()):
            if key.startswith(prefix):
                del self.retained_values[key]

    def stop(self):
        """Runs on animation stop."""

        self.leds.clear()
        self.leds.show()
        self.logical_colors = [(0, 0, 0)] * self.leds.count

    def _is_scene_finished(self, scene_name: str) -> bool:
        """Return True if all cycle-limited effects in the given scene have finished.

        Returns False if the scene has no cycle-limited effects.
        """

        scene_data = self.settings["scenes"].get(scene_name, {})
        has_any_cycles = False

        for entry_name, scene_entry in scene_data.items():
            effect = self._resolve_effect(scene_entry)
            if not effect or "pattern" not in effect:
                continue

            if effect.get("cycles") is not None:
                has_any_cycles = True
                state_key = scene_name + "::" + entry_name
                state = self.scene_state.get(state_key, {})
                if not state.get("finished"):
                    return False

        return has_any_cycles

    @property
    def scene_finished(self) -> bool:
        """Return True if every active scene with cycle-limited effects has finished."""

        if not self._active_scenes:
            return False

        return all(self._is_scene_finished(s) for s in self._active_scenes)

    def is_scene_ongoing(self, scene_name: str) -> bool:
        """Return True if the scene has no cycle-limited effects.

        An ongoing scene never finishes on its own. An immediate scene has at
        least one effect with a ``cycles`` setting and will eventually finish.
        """

        scene_data = self.settings["scenes"].get(scene_name, {})

        for name, scene_entry in scene_data.items():
            effect = self._resolve_effect(scene_entry)
            if not effect or "pattern" not in effect:
                continue

            if effect.get("cycles") is not None:
                return False

        return True

    def get_logical_color(self, index: int) -> tuple:
        """Return the pre-scaled logical color for the given LED index."""

        if 0 <= index < len(self.logical_colors):
            return self.logical_colors[index]

        return (0, 0, 0)

    def _resolve_effect(self, scene_entry: dict) -> dict:
        """Resolve a scene entry into a full effect dict with pattern, colors, target, etc.

        Supports two formats:
        - New format: {"effect": "EffectName", "target": "..."} — looks up the effect by name
        - Legacy format: {"pattern": "solid", "target": "...", "colors": [...]} — used directly

        Returns a merged dict with the target from the scene entry and all other fields from the effect.
        """

        if "effect" in scene_entry:
            effect_name = scene_entry["effect"]
            effects_dict = self.settings.get("effects", {})
            if effect_name not in effects_dict:
                return {}

            resolved = dict(effects_dict[effect_name])
            resolved["target"] = scene_entry.get("target", "all")

            if "cycles" in scene_entry:
                resolved["cycles"] = scene_entry["cycles"]

            return resolved

        return scene_entry

    def _count_cycle(self, name: str, effect: dict) -> None:
        """Record that the named effect has completed one cycle.

        If the effect has a ``cycles`` setting, decrement the remaining counter
        in ``scene_state`` and mark the effect as finished when it reaches zero.
        """

        cycles = effect.get("cycles", None)
        if cycles is None:
            return

        if name not in self.scene_state:
            self.scene_state[name] = {"remaining": cycles}

        state = self.scene_state[name]
        state["remaining"] = state.get("remaining", cycles) - 1

        if state["remaining"] <= 0:
            state["finished"] = True

    def process_tick(self, tick_number: int):
        """Process a single tick of the lighting system."""

        updates = {}

        for active_scene_name in self._active_scenes:
            scene_start = self._scene_start_ticks.get(active_scene_name, 0)

            for entry_name, scene_entry in self.settings["scenes"][active_scene_name].items():
                state_key = active_scene_name + "::" + entry_name
                effect = self._resolve_effect(scene_entry)
                if not effect or "pattern" not in effect:
                    continue

                # Skip effects that have finished their cycles.
                if self.scene_state.get(state_key, {}).get("finished"):
                    continue

                # Skip effects waiting on a predecessor to finish.
                after = scene_entry.get("after")
                if after:
                    after_key = active_scene_name + "::" + after
                    predecessor_state = self.scene_state.get(after_key, {})
                    if not predecessor_state.get("finished"):
                        continue

                    # Record the tick this effect became active.
                    if state_key not in self.scene_state:
                        self.scene_state[state_key] = {"start_tick": tick_number}
                    elif "start_tick" not in self.scene_state[state_key]:
                        self.scene_state[state_key]["start_tick"] = tick_number

                    # Apply inherited target from predecessor's passthrough data.
                    if scene_entry.get("inherit_target"):
                        passthrough = predecessor_state.get("passthrough", {})
                        if "target" in passthrough:
                            effect = dict(effect)
                            effect["target"] = passthrough["target"]

                # Compute a local tick number relative to when this effect started.
                start_tick = self.scene_state.get(state_key, {}).get("start_tick", scene_start)
                local_tick = tick_number - start_tick

                pattern_name = "pattern_" + effect["pattern"]
                if hasattr(self, pattern_name):
                    func = getattr(self, pattern_name)
                    result = func(name=state_key, effect=effect, tick_number=local_tick)
                    target_colors = result

                    # If the effect just finished, store its target as passthrough
                    # for any chained successor. Patterns like phaser_strip may set
                    # a more specific passthrough; this preserves that.
                    state = self.scene_state.get(state_key, {})
                    if state.get("finished") and "passthrough" not in state:
                        state["passthrough"] = {"target": effect["target"]}

                    # Apply filters if present.
                    if "filters" in effect:
                        stored_filters = self.settings.get("filters", {})

                        for filter_ref in effect["filters"]:
                            # Resolve named filter reference to its definition dict.
                            if isinstance(filter_ref, str):
                                filter_dict = stored_filters.get(filter_ref)
                                if not filter_dict:
                                    continue

                            else:
                                filter_dict = filter_ref

                            filter_name = "filter_" + filter_dict["filter"]
                            if hasattr(self, filter_name):
                                filter_func = getattr(self, filter_name)
                                result = filter_func(filter_dict, target_colors, tick_number=tick_number)

                    if result:
                        for led_index, color in result:
                            updates[led_index] = color

        for led_index, color in updates.items():
            self.logical_colors[led_index] = color
            self.leds.set(led_index, color)

        # Remove finished immediate scenes from the active set.
        for active_scene_name in list(self._active_scenes):
            if self._is_scene_finished(active_scene_name):
                self.remove_scene(active_scene_name)

        try:
            self.leds.show()
        except Exception as e:
            print(f"lighting: leds.show() failed: {e}")

    def get_color(self, input: str | tuple | list) -> tuple[int, int, int]:
        """Resolve a color input to an RGB tuple.

        Accepts:
        - ``"name"`` — a standard named color from the colors dict
        - ``"custom:name"`` — a custom color stored in settings
        - ``(r, g, b)`` tuple or list — returned as-is (list wrapped in a list)
        """

        if isinstance(input, str):
            if input.startswith("custom:"):
                color_name = input[7:]
                custom_colors = self.settings.get("custom_colors", {})
                if color_name in custom_colors:
                    rgb = custom_colors[color_name]
                    return (int(rgb[0]), int(rgb[1]), int(rgb[2]))
                return (255, 255, 255)

            return colors.get(input, (255, 255, 255))

        elif isinstance(input, list):
            return [self.get_color(color) for color in input]

        return input

    def get_targets(self, target, _visited=None):
        """Return a list of target indices for the given target specification.

        Supports nested named ranges and will avoid infinite recursion by
        tracking visited named-range names.
        """

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
            # Check for named range reference
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

            # Check for range notation (e.g., "0-14")
            if "-" in target:
                try:
                    start, end = map(int, target.split("-"))
                except Exception:
                    return []
                for i in range(start, end + 1):
                    _append_unique(i)
                return result

            # Check for "all"
            if target == "all":
                for i in range(self.leds.count):
                    _append_unique(i)
                return result

            # Plain integer string (e.g., "10" from a web form)
            try:
                _append_unique(int(target))
                return result
            except Exception:
                return []

        return []

    def _linear_color(self, color_1, color_2, phase):
        """Linearly interpolate between two RGB colors by phase (0.0–1.0)."""

        return (
            int(color_1[0] * (1 - phase) + color_2[0] * phase),
            int(color_1[1] * (1 - phase) + color_2[1] * phase),
            int(color_1[2] * (1 - phase) + color_2[2] * phase),
        )

    def _set_targets(self, targets: list, color: tuple) -> list:
        """Return (led_index, color) pairs for all targets."""

        return [(target, color) for target in targets]

    def filter_null(self, filter_dict: dict, leds: list, tick_number: int) -> list:
        """Null filter: returns the LED list unaltered."""

        return leds

    def filter_sizzle(self, filter_dict: dict, leds: list, tick_number: int) -> list:
        """Sizzle filter: applies a uniform random deviation to all LEDs.

        Generates a single random offset per channel and applies it to every LED's
        target color. The deviation is bounded by ``variation`` and stepped by ``heat``.
        Updates only on ticks divisible by ``40 // frequency``.
        """

        if not leds:
            return leds

        frequency = filter_dict.get("frequency", 40)
        heat = filter_dict.get("heat", 10)

        interval = 40 // frequency

        # Only update on the appropriate ticks.
        if tick_number % interval != 0:
            return leds

        # Generate one random deviation per channel.
        dev_red = random.randint(-heat, heat)
        dev_green = random.randint(-heat, heat)
        dev_blue = random.randint(-heat, heat)

        # Apply the same deviation to all LEDs' target colors.
        result = []
        for led_index, target_color in leds:
            new_red = max(0, min(255, target_color[0] + dev_red))
            new_green = max(0, min(255, target_color[1] + dev_green))
            new_blue = max(0, min(255, target_color[2] + dev_blue))
            result.append((led_index, (new_red, new_green, new_blue)))

        return result

    def filter_scintillate(self, filter_dict: dict, leds: list, tick_number: int) -> list:
        """Scintillate filter: applies independent random deviations to each LED.

        Each LED gets its own random offset per channel, bounded by ``variation``
        and stepped by ``heat``. Updates only on ticks divisible by ``40 // frequency``.
        """

        if not leds:
            return leds

        frequency = filter_dict.get("frequency", 40)
        heat = filter_dict.get("heat", 10)

        interval = 40 // frequency

        # Only update on the appropriate ticks.
        if tick_number % interval != 0:
            return leds

        result = []
        for led_index, target_color in leds:
            dev_red = random.randint(-heat, heat)
            dev_green = random.randint(-heat, heat)
            dev_blue = random.randint(-heat, heat)

            new_red = max(0, min(255, target_color[0] + dev_red))
            new_green = max(0, min(255, target_color[1] + dev_green))
            new_blue = max(0, min(255, target_color[2] + dev_blue))
            result.append((led_index, (new_red, new_green, new_blue)))

        return result

    def _get_effect_colors(self, effect: dict, count: int = 1) -> list:
        """Return a list of resolved RGB tuples for the effect's colors.

        Falls back to white/black if colors are missing or fewer than needed.
        """

        defaults = [(255, 255, 255), (0, 0, 0)]
        raw = effect.get("colors", [])
        resolved = self.get_color(raw) if raw else []

        if not isinstance(resolved, list):
            resolved = [resolved]

        while len(resolved) < count:
            resolved.append(defaults[len(resolved) % len(defaults)])

        return resolved

    def pattern_solid(self, name: str, effect: dict, tick_number: int) -> list:
        """Simple solid color function for a lighting effect."""

        effect_colors = self._get_effect_colors(effect, 1)
        return self._set_targets(self.get_targets(effect["target"]), effect_colors[0])

    def pattern_blink(self, name: str, effect: dict, tick_number: int) -> list:
        """Simple blink function for a lighting effect."""

        half_period = effect.get("duration", 40)
        effect_colors = self._get_effect_colors(effect, 2)

        return self.pattern_periodic(
            name=name,
            effect=effect,
            tick_number=tick_number,
            interval=half_period,
            duration=half_period,
            colors=effect_colors,
            targets=self.get_targets(effect["target"]),
        )

    def pattern_pulse(self, name, effect, tick_number) -> list:
        """Simple pulse function for a lighting effect."""

        on_ticks = effect.get("duration", 10)
        period = effect.get("period", 40)
        interval = max(0, period - on_ticks)
        effect_colors = self._get_effect_colors(effect, 2)

        return self.pattern_periodic(
            name=name,
            effect=effect,
            tick_number=tick_number,
            interval=interval,
            duration=on_ticks,
            colors=effect_colors,
            targets=self.get_targets(effect["target"]),
        )

    def pattern_periodic(self, name, effect, tick_number, interval, duration, colors, targets) -> list:
        """Periodic on/off function for a lighting effect."""

        cycle_length = duration + interval
        phase = tick_number % cycle_length

        if phase == 0 and tick_number > 0:
            self._count_cycle(name, effect)

        color = colors[0] if phase < duration else colors[1]

        return [(target, color) for target in targets]

    def pattern_fade_in(self, name, effect, tick_number) -> list:
        """Simple fade in function for a lighting effect."""

        effect_colors = self._get_effect_colors(effect, 2)
        targets = self.get_targets(effect["target"])
        fade_duration = effect.get("duration", 40)
        phase = min(tick_number / fade_duration, 1.0)

        if tick_number == fade_duration:
            self._count_cycle(name, effect)

        return self._set_targets(targets, self._linear_color(effect_colors[0], effect_colors[1], phase))

    def pattern_breathe(self, name, effect, tick_number) -> list:
        """Breathe function: uses sin() to smoothly modulate between two colors."""

        effect_colors = self._get_effect_colors(effect, 2)
        targets = self.get_targets(effect["target"])
        cycle_ticks = max(1, effect.get("duration", 40))
        phase = (math.sin(2 * math.pi * tick_number / cycle_ticks) + 1) / 2

        if tick_number > 0 and (tick_number - 1) % cycle_ticks == cycle_ticks - 1:
            self._count_cycle(name, effect)

        return self._set_targets(targets, self._linear_color(effect_colors[0], effect_colors[1], phase))

    def _wave_head_position(self, num_leds: int, cycle_ticks: int, phase: int, reverse: bool) -> float:
        """Return the head LED position (as a float) for a wave at the given phase.

        Allows sub-pixel rendering for smoother animation.
        """

        if cycle_ticks > 1:
            position = phase * (num_leds - 1) / (cycle_ticks - 1)
        else:
            position = float(num_leds - 1)

        if reverse:
            position = (num_leds - 1) - position

        return position

    def _render_wave(
        self,
        targets: list,
        head_positions: list,
        width: int,
        ticks_per_led: float,
        color1: tuple,
        color2: tuple,
        reverse: bool = False,
    ) -> list:
        """Render one or more wave comets with sub-pixel smoothing and return (led_index, color) pairs.

        Head positions can be fractional for smooth animation. Colors are interpolated
        across adjacent LEDs at the head boundary. If reverse=True, the tail extends
        forward (higher indices) instead of backward.
        """

        fade_ticks = max(1, width * ticks_per_led)
        step_r = (color2[0] - color1[0]) / fade_ticks
        step_g = (color2[1] - color1[1]) / fade_ticks
        step_b = (color2[2] - color1[2]) / fade_ticks

        lo_r, hi_r = min(color1[0], color2[0]), max(color1[0], color2[0])
        lo_g, hi_g = min(color1[1], color2[1]), max(color1[1], color2[1])
        lo_b, hi_b = min(color1[2], color2[2]), max(color1[2], color2[2])

        updates = {}

        # Phase 1: fade every LED one step toward color1.
        for i, target in enumerate(targets):
            current = self.get_logical_color(target)

            updates[i] = (
                target,
                (
                    int(max(lo_r, min(hi_r, current[0] - step_r))),
                    int(max(lo_g, min(hi_g, current[1] - step_g))),
                    int(max(lo_b, min(hi_b, current[2] - step_b))),
                ),
            )

        # How many LEDs the head can move in a single tick. When > 1, we must
        # fill the gap so no LEDs are skipped.
        fill_count = max(1, math.ceil(1 / ticks_per_led)) if ticks_per_led > 0 else 1

        # Phase 2: place head at full brightness and blend the transition smoothly.
        for head_position in head_positions:
            if not reverse:
                # Forward: head_index is floor(position), sub_position is fractional part
                head_index = int(head_position)
                sub_position = head_position - head_index  # 0.0 to 1.0
            else:
                # Reverse: head_index is ceil(position), sub_position is distance from ceil
                # This ensures smooth blending as position decreases
                head_index = math.ceil(head_position)
                sub_position = head_index - head_position  # 0.0 to 1.0

            if 0 <= head_index < len(targets):
                # Fill head LED and any LEDs skipped since last tick.
                for offset in range(fill_count):
                    fill_idx = head_index - offset if not reverse else head_index + offset

                    if 0 <= fill_idx < len(targets):
                        updates[fill_idx] = (targets[fill_idx], color2)

                # Smooth transition: blend into the next LED for sub-pixel smoothing
                # Direction depends on reverse flag
                if not reverse:
                    # Forward: tail extends forward (higher indices)
                    if head_index + 1 < len(targets) and sub_position > 0:
                        blend_color = self._linear_color(color1, color2, sub_position)
                        updates[head_index + 1] = (targets[head_index + 1], blend_color)

                else:
                    # Reverse: tail extends backward (lower indices)
                    if head_index - 1 >= 0 and sub_position > 0:
                        blend_color = self._linear_color(color1, color2, sub_position)
                        updates[head_index - 1] = (targets[head_index - 1], blend_color)

        return list(updates.values())

    def pattern_wave(self, name, effect, tick_number):
        """Wave function: creates one or more moving comet effects across the LEDs.

        effect["number"] controls how many evenly-spaced peaks travel simultaneously.
        Set effect["reverse"] to True to sweep from last to first.
        """

        effect_colors = self._get_effect_colors(effect, 2)
        targets = self.get_targets(effect["target"])
        width = effect.get("width", 5)
        reverse = effect.get("reverse", False)
        number = effect.get("number", 1)

        num_leds = len(targets)
        cycle_ticks = max(1, effect.get("duration", 40))
        phase = (tick_number - 1) % cycle_ticks
        ticks_per_led = cycle_ticks / max(1, num_leds - 1)

        if phase == 0 and tick_number > 1:
            self._count_cycle(name, effect)
            if self.scene_state.get(name, {}).get("finished"):
                return self._set_targets(targets, effect_colors[0])

        spacing = cycle_ticks // number
        head_positions = [
            self._wave_head_position(num_leds, cycle_ticks, (phase + peak * spacing) % cycle_ticks, reverse)
            for peak in range(number)
        ]

        return self._render_wave(
            targets, head_positions, width, ticks_per_led, effect_colors[0], effect_colors[1], reverse=reverse
        )

    def pattern_cylon(self, name, effect, tick_number) -> list:
        """Cylon function: a comet that bounces back and forth across the LEDs.

        The head sweeps from the first target to the last over 40/frequency ticks,
        then reverses and sweeps back, repeating continuously.
        """

        effect_colors = self._get_effect_colors(effect, 2)
        targets = self.get_targets(effect["target"])
        width = effect.get("width", 5)

        num_leds = len(targets)
        one_way_ticks = max(1, effect.get("duration", 40))
        cycle_ticks = one_way_ticks * 2
        phase = (tick_number - 1) % cycle_ticks
        ticks_per_led = one_way_ticks / max(1, num_leds - 1)

        if phase == 0 and tick_number > 1:
            self._count_cycle(name, effect)
            if self.scene_state.get(name, {}).get("finished"):
                return self._set_targets(targets, effect_colors[0])

        if phase < one_way_ticks:
            head_position = self._wave_head_position(num_leds, one_way_ticks, phase, reverse=False)
            return self._render_wave(
                targets, [head_position], width, ticks_per_led, effect_colors[0], effect_colors[1], reverse=False
            )
        else:
            head_position = self._wave_head_position(num_leds, one_way_ticks, phase - one_way_ticks, reverse=True)
            return self._render_wave(
                targets, [head_position], width, ticks_per_led, effect_colors[0], effect_colors[1], reverse=True
            )

    def pattern_phaser_strip(self, name: str, effect: dict, tick_number: int) -> list:
        """Phaser strip: two waves start at opposite ends of the target range and converge
        on a randomly chosen meeting point, both arriving at the same tick.

        After the waves meet, the effect holds the meeting LED lit while all
        trails fade out, then resets every LED to the background color before
        the cycle is considered complete.

        A new meeting point is picked at the start of each cycle.
        """

        effect_colors = self._get_effect_colors(effect, 2)
        targets = self.get_targets(effect["target"])
        width = effect.get("width", 5)

        num_leds = len(targets)

        if num_leds < 3:
            return []

        cycle_ticks = max(1, effect.get("duration", 40))
        ticks_per_led = cycle_ticks / max(1, num_leds - 1)
        fade_ticks = int(max(1, width * ticks_per_led))
        total_ticks = cycle_ticks + fade_ticks
        phase = (tick_number - 1) % total_ticks

        # Count the cycle at the very last tick of the full sequence.
        if phase == 0 and tick_number > 1:
            self._count_cycle(name, effect)
            if self.scene_state.get(name, {}).get("finished"):
                meet_index = self.retained_values.get(name, 0)
                self.scene_state[name]["passthrough"] = {"target": targets[meet_index]}
                return [(target, effect_colors[0]) for target in targets]

        # Pick a new random meeting point at the start of each full sequence.
        if name not in self.retained_values or phase == 0:
            meet_index = random.randint(1, num_leds - 2)
            self.retained_values[name] = meet_index
        else:
            meet_index = self.retained_values[name]

        # Phase 1: converge — waves travel toward the meeting point.
        if phase < cycle_ticks:
            if cycle_ticks > 1:
                left_pos = phase * meet_index / (cycle_ticks - 1)
                right_pos = (num_leds - 1) - phase * (num_leds - 1 - meet_index) / (cycle_ticks - 1)
            else:
                left_pos = float(meet_index)
                right_pos = float(meet_index)

            left_updates = self._render_wave(
                targets, [left_pos], width, ticks_per_led, effect_colors[0], effect_colors[1], reverse=False
            )
            right_updates = self._render_wave(
                targets, [right_pos], width, ticks_per_led, effect_colors[0], effect_colors[1], reverse=True
            )

            merged = dict(left_updates)

            for led_index, color in right_updates:
                if led_index in merged:
                    if sum(color) > sum(merged[led_index]):
                        merged[led_index] = color
                else:
                    merged[led_index] = color

            return list(merged.items())

        # Phase 2: fade — hold meeting LED, let trails decay naturally.
        fade_phase = phase - cycle_ticks

        if fade_phase < fade_ticks - 1:
            # Render a stationary wave at the meeting point so _render_wave
            # continues to fade all other LEDs toward color1 each tick.
            return self._render_wave(
                targets, [float(meet_index)], width, ticks_per_led, effect_colors[0], effect_colors[1], reverse=False
            )

        # Final tick: reset all LEDs to the background color.
        return [(target, effect_colors[0]) for target in targets]

    def convert_frequencies_to_durations(self) -> int:
        """Convert all frequency-based pattern params to duration-based in persistent storage.

        For each effect or inline scene entry that has a ``frequency`` key, computes
        ``duration = max(1, int(40 // frequency))`` and replaces the key in-place.
        For ``pulse`` effects, the cycle-period ``frequency`` becomes ``period`` instead,
        since ``duration`` is already used for the on-time.

        Returns the total number of values converted. Saves storage if any conversions
        were made.
        """

        count = 0
        lighting_settings = self.settings

        def _convert_entry(entry: dict) -> None:
            """Convert a single effect/entry dict in-place."""

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
