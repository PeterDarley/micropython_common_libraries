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

        # self.settings_object["lighting_settings"] = {
        #     "default_scene": "Engines",
        #     "scenes": {
        #         "Test Scene": {
        #             "blink_1": {"target": 0, "pattern": "blink", "frequency": 2, "colors": ["white", "black"]},
        #             "blink_2": {"target": "1-3", "pattern": "blink", "frequency": 1, "colors": ["red", "blue"]},
        #             "pulse_1": {
        #                 "target": 4,
        #                 "pattern": "pulse",
        #                 "frequency": 1.3,
        #                 "duration": 1,
        #                 "colors": ["white", "black"],
        #             },
        #             "fade_in_1": {
        #                 "target": 5,
        #                 "pattern": "fade_in",
        #                 "duration": 120,
        #                 "colors": ["red", "blue"],
        #             },
        #             "solid_1": {"pattern": "solid", "target": 6, "colors": ["purple"]},
        #             "breath_1": {
        #                 "target": [7, 8, 10],
        #                 "pattern": "breathe",
        #                 "frequency": 0.5,
        #                 "colors": ["red", "blue"],
        #             },
        #         },
        #         "Wave": {
        #             "wave_1": {
        #                 "pattern": "wave",
        #                 "target": "0-14",
        #                 "frequency": 1,
        #                 "number": 2,
        #                 "width": 5,
        #                 "colors": [(0, 10, 0), "green"],
        #             }
        #         },
        #         "Cylon": {
        #             "cylon_1": {
        #                 "pattern": "cylon",
        #                 "target": "0-14",
        #                 "width": 4,
        #                 "colors": ["black", "red"],
        #             }
        #         },
        #         "Dark": {"all_dark": {"pattern": "solid", "target": "all", "colors": ["black"]}},
        #         "Flood": {
        #             "all_flood": {
        #                 "pattern": "solid",
        #                 "target": "all",
        #                 "colors": ["white"],
        #                 "filters": [{"filter": "scintillate", "frequency": 40, "variation": 30, "heat": 20}],
        #             }
        #         },
        #         "Engines": {
        #             "top": {
        #                 "pattern": "wave",
        #                 "target": "0-8",
        #                 "frequency": 1.5,
        #                 "number": 1,
        #                 "width": 7,
        #                 "colors": [(0, 50, 0), (0, 200, 0)],
        #                 "reverse": True,
        #             },
        #             "bottom": {
        #                 "pattern": "wave",
        #                 "target": "9-17",
        #                 "frequency": 1.5,
        #                 "number": 1,
        #                 "width": 7,
        #                 "colors": [(0, 50, 0), (0, 100, 0)],
        #                 "reverse": True,
        #             },
        #             "front": {"pattern": "solid", "target": 18, "colors": ["green"]},
        #             "fibers": {
        #                 "pattern": "solid",
        #                 "target": "19-30",
        #                 "colors": ["white"],
        #             },
        #         },
        #         "Engines2": {
        #             "startup_1": {
        #                 "target": "all",
        #                 "pattern": "fade_in",
        #                 "duration": 120,
        #                 "colors": ["black", (50, 50, 50)],
        #             },
        #             # "startup_2": {
        #             #     "target": "all",
        #             #     "pattern": "fade_in",
        #             #     "duration": 120,
        #             #     "colors": [(50, 50, 50), (0, 150, 0)],
        #             #     "initial": False,
        #             # },
        #         },
        #     },
        #     "named_ranges": {},
        # }

        # self.settings_object.store()

        self.settings = self.settings_object["lighting_settings"]

        self.scene_name = None
        self.retained_values = {}
        self.scene_kwargs = {}
        self.scene_state = {}
        self._scene_functions = {}

        self.set_scene()
        self.leds = LEDs()
        self.logical_colors = [(0, 0, 0)] * self.leds.count

        self.animation = Animation(jobs={"lighting": self.process_tick}, stop_callbacks={"lighting": self.stop})

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
        """Set the current lighting scene.

        Any additional keyword arguments are stored as ``scene_kwargs`` and
        forwarded to a registered scene function (if one exists for the scene).
        """

        current_scene = getattr(self, "scene_name", None)
        if current_scene is not None and current_scene == scene_name and not kwargs and not self.scene_finished:
            return

        scenes: dict = self.settings_object["lighting_settings"]["scenes"]

        if scene_name is None:
            if "default_scene" in self.settings_object["lighting_settings"]:
                self.scene_name = self.settings_object["lighting_settings"]["default_scene"]
            else:
                self.scene_name = list(scenes.keys())[0]

        elif scene_name not in scenes:
            raise ValueError(f"Scene '{scene_name}' not found. Available scenes: {list(scenes.keys())}")

        else:
            self.scene_name = scene_name

        self.scene_kwargs = kwargs

        # for name, effect in self.settings["scenes"][self.scene_name].items():
        #     if "initial" not in effect or effect["initial"]:
        #         self.active_effects[name] = {}

        self.scene_state = {}

        if hasattr(self, "animation"):
            self.leds.clear()
            if hasattr(self, "logical_colors"):
                self.logical_colors = [(0, 0, 0)] * self.leds.count

            self.animation.reset()

        if self.scene_name in self._scene_functions:
            self._scene_functions[self.scene_name](lighting=self, **kwargs)

    def stop(self):
        """Runs on animation stop."""

        self.leds.clear()
        self.leds.show()
        self.logical_colors = [(0, 0, 0)] * self.leds.count

    @property
    def scene_finished(self) -> bool:
        """Return True if every effect in the current scene that has a cycles
        limit has finished.

        A scene with no cycle-limited effects is never considered finished.
        """

        if not self.scene_state:
            return False

        scene_data = self.settings["scenes"].get(self.scene_name, {})
        has_any_cycles = False

        for name, scene_entry in scene_data.items():
            effect = self._resolve_effect(scene_entry)
            if not effect or "pattern" not in effect:
                continue

            if effect.get("cycles") is not None:
                has_any_cycles = True
                state = self.scene_state.get(name, {})
                if not state.get("finished"):
                    return False

        return has_any_cycles

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

        for name, scene_entry in self.settings["scenes"][self.scene_name].items():
            effect = self._resolve_effect(scene_entry)
            if not effect or "pattern" not in effect:
                continue

            # Skip effects that have finished their cycles.
            if self.scene_state.get(name, {}).get("finished"):
                continue

            # Skip effects waiting on a predecessor to finish.
            after = scene_entry.get("after")
            if after:
                predecessor_state = self.scene_state.get(after, {})
                if not predecessor_state.get("finished"):
                    continue

                # Record the tick this effect became active.
                if name not in self.scene_state:
                    self.scene_state[name] = {"start_tick": tick_number}
                elif "start_tick" not in self.scene_state[name]:
                    self.scene_state[name]["start_tick"] = tick_number

                # Apply inherited target from predecessor's passthrough data.
                if scene_entry.get("inherit_target"):
                    passthrough = self.scene_state.get(after, {}).get("passthrough", {})
                    if "target" in passthrough:
                        effect = dict(effect)
                        effect["target"] = passthrough["target"]

            # Compute a local tick number relative to when this effect started.
            start_tick = self.scene_state.get(name, {}).get("start_tick", 0)
            local_tick = tick_number - start_tick

            pattern_name = "pattern_" + effect["pattern"]
            if hasattr(self, pattern_name):
                func = getattr(self, pattern_name)
                result = func(name=name, effect=effect, tick_number=local_tick)
                target_colors = result

                # If the effect just finished, store its target as passthrough
                # for any chained successor. Patterns like phaser_strip may set
                # a more specific passthrough; this preserves that.
                state = self.scene_state.get(name, {})
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

    def get_targets(self, target) -> list[int]:
        """Return a list of target indices for the given target specification.

        Supports:
        - int: single LED index
        - list: list of indices
        - "0-14": range notation (inclusive)
        - "all": all LEDs
        - "named:range_name": look up range in named_ranges
        """

        if isinstance(target, int):
            return [target]

        elif isinstance(target, list):
            return target

        elif isinstance(target, str):
            # Check for named range reference
            if target.startswith("named:"):
                range_name = target[6:]  # Strip "named:" prefix
                named_ranges = self.settings.get("named_ranges", {})
                if range_name in named_ranges:
                    named_target = named_ranges[range_name]
                    # Recursively resolve the named target (could be a range, list, or "all")
                    return self.get_targets(named_target)
                return []

            # Check for range notation (e.g., "0-14")
            elif "-" in target:
                start, end = map(int, target.split("-"))
                return list(range(start, end + 1))

            # Check for "all"
            elif target == "all":
                return list(range(self.leds.count))

            # Plain integer string (e.g., "10" from a web form)
            else:
                try:
                    return [int(target)]
                except ValueError:
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
        lighting_settings = self.settings_object["lighting_settings"]

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
