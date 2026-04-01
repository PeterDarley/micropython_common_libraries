import math
import random

import settings

from animation import Animation
from leds import LEDs
from storage import PersistentDict

colors = {
    # Neutrals
    "white": (255, 255, 255),
    "warm_white": (255, 220, 160),
    "cool_white": (180, 210, 255),
    "dim_white": (64, 64, 64),
    "silver": (180, 180, 200),
    "grey": (128, 128, 128),
    "black": (0, 0, 0),
    # Reds / oranges
    "red": (255, 0, 0),
    "dark_red": (128, 0, 0),
    "orange": (255, 100, 0),
    "amber": (255, 160, 0),
    "gold": (255, 200, 0),
    "yellow": (255, 255, 0),
    # Greens
    "green": (0, 255, 0),
    "dark_green": (0, 128, 0),
    "lime": (128, 255, 0),
    "teal": (0, 180, 128),
    # Blues / purples
    "cyan": (0, 255, 255),
    "ice_blue": (80, 160, 255),
    "blue": (0, 0, 255),
    "dark_blue": (0, 0, 128),
    "indigo": (60, 0, 180),
    "violet": (180, 0, 255),
    "purple": (128, 0, 128),
    "magenta": (255, 0, 255),
    "pink": (255, 80, 150),
    # Specialty / model
    "fire": (255, 40, 0),
    "plasma": (0, 200, 255),
    "engine_glow": (100, 40, 255),
}


class Lighting:
    def __init__(self):
        self.settings_object = PersistentDict()
        self.settings_object["lighting_settings"] = {
            "default_scene": "Engines",
            "scenes": {
                "Test Scene": {
                    "blink_1": {"target": 0, "pattern": "blink", "frequency": 2, "colors": ["white", "black"]},
                    "blink_2": {"target": "1-3", "pattern": "blink", "frequency": 1, "colors": ["red", "blue"]},
                    "pulse_1": {
                        "target": 4,
                        "pattern": "pulse",
                        "frequency": 1.3,
                        "duration": 1,
                        "colors": ["white", "black"],
                    },
                    "fade_in_1": {
                        "target": 5,
                        "pattern": "fade_in",
                        "duration": 120,
                        "colors": ["red", "blue"],
                    },
                    "solid_1": {"pattern": "solid", "target": 6, "colors": ["purple"]},
                    "breath_1": {
                        "target": [7, 8, 10],
                        "pattern": "breathe",
                        "frequency": 0.5,
                        "colors": ["red", "blue"],
                    },
                },
                "Wave": {
                    "wave_1": {
                        "pattern": "wave",
                        "target": "0-14",
                        "frequency": 1,
                        "number": 2,
                        "width": 5,
                        "colors": [(0, 10, 0), "green"],
                    }
                },
                "Cylon": {
                    "cylon_1": {
                        "pattern": "cylon",
                        "target": "0-14",
                        "width": 4,
                        "colors": ["black", "red"],
                    }
                },
                "Dark": {"all_dark": {"pattern": "solid", "target": "all", "colors": ["black"]}},
                "Flood": {
                    "all_flood": {
                        "pattern": "solid",
                        "target": "all",
                        "colors": ["white"],
                        "filters": [{"filter": "sizzle", "frequency": 40, "variation": 30, "heat": 20}],
                    }
                },
                "Engines": {
                    "top": {
                        "pattern": "wave",
                        "target": "0-8",
                        "frequency": 1.5,
                        "number": 1,
                        "width": 7,
                        "colors": [(0, 50, 0), (0, 100, 0)],
                        "reverse": True,
                    },
                    "bottom": {
                        "pattern": "wave",
                        "target": "9-17",
                        "frequency": 1.5,
                        "number": 1,
                        "width": 7,
                        "colors": [(0, 50, 0), (0, 100, 0)],
                        "reverse": True,
                    },
                    "front": {"pattern": "solid", "target": 18, "colors": ["green"]},
                    "fibers": {
                        "pattern": "solid",
                        "target": "19-30",
                        "colors": ["white"],
                    },
                },
                "Engines2": {
                    "startup_1": {
                        "target": "all",
                        "pattern": "fade_in",
                        "duration": 120,
                        "colors": ["black", (50, 50, 50)],
                    },
                    # "startup_2": {
                    #     "target": "all",
                    #     "pattern": "fade_in",
                    #     "duration": 120,
                    #     "colors": [(50, 50, 50), (0, 150, 0)],
                    #     "initial": False,
                    # },
                },
            },
            "named_ranges": {},
        }

        self.settings_object.store()

        self.settings = self.settings_object["lighting_settings"]

        self.scene_name = None
        self.retained_values = {}
        # self.active_jobs = {}

        self.set_scene()
        self.leds = LEDs()

        self.animation = Animation(jobs={"lighting": self.process_tick}, stop_callbacks={"lighting": self.stop})

    def set_scene(self, scene_name: str = None):
        """Set the current lighting scene."""

        current_scene = getattr(self, "scene_name", None)
        if current_scene is not None and current_scene == scene_name:
            return

        scenes = self.settings_object["lighting_settings"]["scenes"]

        if scene_name is None:
            if "default_scene" in self.settings_object["lighting_settings"]:
                self.scene_name = self.settings_object["lighting_settings"]["default_scene"]
            else:
                self.scene_name = list(scenes.keys())[0]

        elif scene_name not in scenes:
            raise ValueError(f"Scene '{scene_name}' not found. Available scenes: {list(scenes.keys())}")

        else:
            self.scene_name = scene_name

        # for name, job in self.settings["scenes"][self.scene_name].items():
        #     if "initial" not in job or job["initial"]:
        #         self.active_jobs[name] = {}

        if hasattr(self, "animation"):
            self.leds.clear()
            self.animation.reset()

    def stop(self):
        """Runs on animation stop"""

        self.leds.clear()
        self.leds.show()

    def process_tick(self, tick_number: int):
        """Process a single tick of the lighting system."""

        updates = {}

        for name, job in self.settings["scenes"][self.scene_name].items():
            # if name not in self.active_jobs:
            #     continue

            pattern_name = "pattern_" + job["pattern"]
            if hasattr(self, pattern_name):
                func = getattr(self, pattern_name)
                result = func(name=name, job=job, tick_number=tick_number)

                # Apply filters if present.
                if "filters" in job:
                    # If no result from the pattern, build one from targets with current colors.
                    if not result:
                        targets = self.get_targets(job["target"])
                        result = [(target, self.leds.get(target)) for target in targets]

                    for filter_dict in job["filters"]:
                        filter_name = "filter_" + filter_dict["filter"]
                        if hasattr(self, filter_name):
                            filter_func = getattr(self, filter_name)
                            result = filter_func(filter_dict, result, tick_number=tick_number)

                if result:
                    for led_index, color in result:
                        updates[led_index] = color

        for led_index, color in updates.items():
            self.leds.set(led_index, color)

        try:
            self.leds.show()
        except Exception as e:
            print(f"lighting: leds.show() failed: {e}")

    def get_color(self, input: str | tuple | list) -> tuple[int, int, int]:
        """Make sure that we have an RGB tuple"""

        if isinstance(input, str):
            return colors.get(input, (255, 255, 255))

        elif isinstance(input, list):
            return [self.get_color(color) for color in input]

        return input

    def get_targets(self, target) -> list[int]:
        """Return a list of target indices for the given target specification."""

        if isinstance(target, int):
            return [target]

        elif isinstance(target, list):
            return target

        elif isinstance(target, str) and "-" in target:
            start, end = map(int, target.split("-"))
            return list(range(start, end + 1))

        elif target == "all":
            return list(range(self.leds.count))

        return []

    def _linear_color(self, color_1, color_2, phase):
        """Linearly interpolate between two RGB colors by phase (0.0–1.0)."""

        return (
            int(color_1[0] * (1 - phase) + color_2[0] * phase),
            int(color_1[1] * (1 - phase) + color_2[1] * phase),
            int(color_1[2] * (1 - phase) + color_2[2] * phase),
        )

    def _set_targets(self, targets: list, color: tuple) -> list:
        """Return (led_index, color) pairs for each target not already at that color."""

        return [(target, color) for target in targets if self.leds.get(target) != color]

    def filter_null(self, filter_dict: dict, leds: list, tick_number: int) -> list:
        """Null filter: returns the LED list unaltered."""

        return leds

    def filter_sizzle(self, filter_dict: dict, leds: list, tick_number: int) -> list:
        """Sizzle filter: generates a random deviation from the first LED's current color
        and applies that same deviation to all LEDs in the list.

        Updates only happen on ticks where tick_number % interval == 0,
        where interval = 40 // frequency. The deviation is computed once based on
        the first LED's difference from its target color, then applied uniformly
        to all LEDs in the list.
        """

        if not leds:
            return leds

        frequency = filter_dict.get("frequency", 40)
        variation = filter_dict.get("variation", 50)
        heat = filter_dict.get("heat", 10)

        interval = 40 // frequency

        # Only update on the appropriate ticks.
        if tick_number % interval != 0:
            return leds

        # Get the first LED's index and its target color (from the pattern).
        first_led_index, first_target_color = leds[0]
        current_red, current_green, current_blue = self.leds.get(first_led_index)

        # Compute deviations based on distance from current to target.
        red_distance = first_target_color[0] - current_red
        green_distance = first_target_color[1] - current_green
        blue_distance = first_target_color[2] - current_blue

        # Red channel
        prob_up_red = max(0.0, min(1.0, 0.5 + red_distance / (2 * variation)))
        if random.random() < prob_up_red:
            dev_red = random.randint(1, max(1, heat))
        else:
            dev_red = -random.randint(1, max(1, heat))

        # Green channel
        prob_up_green = max(0.0, min(1.0, 0.5 + green_distance / (2 * variation)))
        if random.random() < prob_up_green:
            dev_green = random.randint(1, max(1, heat))
        else:
            dev_green = -random.randint(1, max(1, heat))

        # Blue channel
        prob_up_blue = max(0.0, min(1.0, 0.5 + blue_distance / (2 * variation)))
        if random.random() < prob_up_blue:
            dev_blue = random.randint(1, max(1, heat))
        else:
            dev_blue = -random.randint(1, max(1, heat))

        # Apply the same deviation to all LEDs in the list.
        result = []
        for led_index, color in leds:
            new_red = max(0, min(255, color[0] + dev_red))
            new_green = max(0, min(255, color[1] + dev_green))
            new_blue = max(0, min(255, color[2] + dev_blue))
            result.append((led_index, (new_red, new_green, new_blue)))

        return result

    def filter_scintillate(self, filter_dict: dict, leds: list, tick_number: int) -> list:
        """Scintillate filter: generates random deviations for each LED independently.

        Updates only happen on ticks where tick_number % interval == 0,
        where interval = 40 // frequency. Each LED's deviation is computed
        independently based on its own difference from its target color.
        """

        if not leds:
            return leds

        frequency = filter_dict.get("frequency", 40)
        variation = filter_dict.get("variation", 50)
        heat = filter_dict.get("heat", 10)

        interval = 40 // frequency

        # Only update on the appropriate ticks.
        if tick_number % interval != 0:
            return leds

        result = []
        for led_index, target_color in leds:
            current_red, current_green, current_blue = self.leds.get(led_index)

            # Compute deviations based on this LED's distance from its target.
            red_distance = target_color[0] - current_red
            green_distance = target_color[1] - current_green
            blue_distance = target_color[2] - current_blue

            # Red channel
            prob_up_red = max(0.0, min(1.0, 0.5 + red_distance / (2 * variation)))
            if random.random() < prob_up_red:
                dev_red = random.randint(1, max(1, heat))
            else:
                dev_red = -random.randint(1, max(1, heat))

            # Green channel
            prob_up_green = max(0.0, min(1.0, 0.5 + green_distance / (2 * variation)))
            if random.random() < prob_up_green:
                dev_green = random.randint(1, max(1, heat))
            else:
                dev_green = -random.randint(1, max(1, heat))

            # Blue channel
            prob_up_blue = max(0.0, min(1.0, 0.5 + blue_distance / (2 * variation)))
            if random.random() < prob_up_blue:
                dev_blue = random.randint(1, max(1, heat))
            else:
                dev_blue = -random.randint(1, max(1, heat))

            # Apply this LED's individual deviation.
            new_red = max(0, min(255, target_color[0] + dev_red))
            new_green = max(0, min(255, target_color[1] + dev_green))
            new_blue = max(0, min(255, target_color[2] + dev_blue))
            result.append((led_index, (new_red, new_green, new_blue)))

        return result

    def pattern_solid(self, name, job, tick_number) -> list:
        """Simple solid color function for a lighting job."""

        job_colors = self.get_color(job["colors"])
        return self._set_targets(self.get_targets(job["target"]), job_colors[0])

    def pattern_blink(self, name, job, tick_number) -> list:
        """Simple blink function for a lighting job."""

        interval = 40 // job.get("frequency", None)
        duration = interval
        colors = self.get_color(job["colors"])

        return self.pattern_periodic(
            name=name,
            tick_number=tick_number,
            interval=interval,
            duration=duration,
            colors=colors,
            targets=self.get_targets(job["target"]),
        )

    def pattern_pulse(self, name, job, tick_number) -> list:
        """Simple pulse function for a lighting job."""

        duration = job["duration"]
        interval = 40 // job.get("frequency", None) - duration
        colors = self.get_color(job["colors"])

        return self.pattern_periodic(
            name=name,
            tick_number=tick_number,
            interval=interval,
            duration=duration,
            colors=colors,
            targets=self.get_targets(job["target"]),
        )

    def pattern_periodic(self, name, tick_number, interval, duration, colors, targets) -> list:
        """Periodic on/off function for a lighting job."""

        cycle_length = duration + interval
        phase = tick_number % cycle_length
        color = colors[0] if phase < duration else colors[1]

        return [(target, color) for target in targets]

    def pattern_fade_in(self, name, job, tick_number) -> list:
        """Simple fade in function for a lighting job."""

        job_colors = self.get_color(job["colors"])
        targets = self.get_targets(job["target"])
        phase = min(tick_number / job["duration"], 1.0)
        return self._set_targets(targets, self._linear_color(job_colors[0], job_colors[1], phase))

    def pattern_breathe(self, name, job, tick_number) -> list:
        """Breathe function: uses sin() to smoothly modulate between two colors."""

        job_colors = self.get_color(job["colors"])
        targets = self.get_targets(job["target"])
        phase = (math.sin(2 * math.pi * job.get("frequency", 1) * tick_number / 40) + 1) / 2
        return self._set_targets(targets, self._linear_color(job_colors[0], job_colors[1], phase))

    def pattern_sizzle(self, name, job, tick_number) -> list:
        """Sizzle function: fluctuates around a base color with random variations."""

        job_colors = self.get_color(job["colors"])
        targets = self.get_targets(job["target"])
        frequency = job.get("frequency", 40)
        variation = job.get("variation", 50)
        heat = job.get("heat", 10)

        interval = 40 // frequency

        (red, green, blue) = job_colors[0]
        (current_red, current_green, current_blue) = self.leds.get(targets[0])

        if tick_number == 1:
            new_red, new_green, new_blue = red, green, blue

        elif tick_number % interval == 0:
            # Signed probability: 0.5 at target, biased toward target as distance grows
            red_distance = red - current_red
            green_distance = green - current_green
            blue_distance = blue - current_blue

            step = random.randint(1, max(1, heat))
            prob_up_red = max(0.0, min(1.0, 0.5 + red_distance / (2 * variation)))
            if random.random() < prob_up_red:
                new_red = current_red + step
            else:
                new_red = current_red - step

            step = random.randint(1, max(1, heat))
            prob_up_green = max(0.0, min(1.0, 0.5 + green_distance / (2 * variation)))
            if random.random() < prob_up_green:
                new_green = current_green + step
            else:
                new_green = current_green - step

            step = random.randint(1, max(1, heat))
            prob_up_blue = max(0.0, min(1.0, 0.5 + blue_distance / (2 * variation)))
            if random.random() < prob_up_blue:
                new_blue = current_blue + step
            else:
                new_blue = current_blue - step

            new_red = max(0, min(255, new_red))
            new_green = max(0, min(255, new_green))
            new_blue = max(0, min(255, new_blue))

        else:
            return []

        return [(target, (new_red, new_green, new_blue)) for target in targets]

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
        self, targets: list, head_positions: list, width: int, ticks_per_led: float, color1: tuple, color2: tuple
    ) -> list:
        """Render one or more wave comets with sub-pixel smoothing and return (led_index, color) pairs.

        Head positions can be fractional for smooth animation. Colors are interpolated
        across adjacent LEDs at the head boundary.
        """

        fade_ticks = max(1, width * ticks_per_led)
        step_r = (color2[0] - color1[0]) / fade_ticks
        step_g = (color2[1] - color1[1]) / fade_ticks
        step_b = (color2[2] - color1[2]) / fade_ticks

        updates = {}

        # Phase 1: fade every LED one step toward color1.
        for i, target in enumerate(targets):
            current = self.leds.get(target)

            updates[i] = (
                target,
                (
                    int(
                        max(color1[0], min(color2[0], current[0] - step_r))
                        if step_r > 0
                        else max(color2[0], min(color1[0], current[0] - step_r))
                    ),
                    int(
                        max(color1[1], min(color2[1], current[1] - step_g))
                        if step_g > 0
                        else max(color2[1], min(color1[1], current[1] - step_g))
                    ),
                    int(
                        max(color1[2], min(color2[2], current[2] - step_b))
                        if step_b > 0
                        else max(color2[2], min(color1[2], current[2] - step_b))
                    ),
                ),
            )

        # Phase 2: place head at full brightness and blend the transition smoothly.
        for head_position in head_positions:
            head_index = int(head_position)
            sub_position = head_position - head_index  # 0.0 to 1.0

            if head_index < len(targets):
                # Head LED always at full color2 brightness for visibility
                updates[head_index] = (targets[head_index], color2)

                # Smooth transition: blend into the next LED for sub-pixel smoothing
                if head_index + 1 < len(targets) and sub_position > 0:
                    # Next LED is partially still head (at sub_position strength)
                    blend_color = (
                        int(color2[0] * sub_position + color1[0] * (1 - sub_position)),
                        int(color2[1] * sub_position + color1[1] * (1 - sub_position)),
                        int(color2[2] * sub_position + color1[2] * (1 - sub_position)),
                    )
                    updates[head_index + 1] = (targets[head_index + 1], blend_color)

        return list(updates.values())

    def pattern_wave(self, name, job, tick_number):
        """Wave function: creates one or more moving comet effects across the LEDs.

        job["number"] controls how many evenly-spaced peaks travel simultaneously.
        Set job["reverse"] to True to sweep from last to first.
        """

        job_colors = self.get_color(job["colors"])
        targets = self.get_targets(job["target"])
        frequency = job.get("frequency", 1)
        width = job.get("width", 5)
        reverse = job.get("reverse", False)
        number = job.get("number", 1)

        num_leds = len(targets)
        cycle_ticks = max(1, 40 // frequency)
        phase = (tick_number - 1) % cycle_ticks
        ticks_per_led = cycle_ticks / max(1, num_leds - 1)

        spacing = cycle_ticks // number
        head_positions = [
            self._wave_head_position(num_leds, cycle_ticks, (phase + peak * spacing) % cycle_ticks, reverse)
            for peak in range(number)
        ]

        return self._render_wave(targets, head_positions, width, ticks_per_led, job_colors[0], job_colors[1])

    def pattern_cylon(self, name, job, tick_number) -> list:
        """Cylon function: a comet that bounces back and forth across the LEDs.

        The head sweeps from the first target to the last over 40/frequency ticks,
        then reverses and sweeps back, repeating continuously.
        """

        job_colors = self.get_color(job["colors"])
        targets = self.get_targets(job["target"])
        frequency = job.get("frequency", 1)
        width = job.get("width", 5)

        num_leds = len(targets)
        one_way_ticks = max(1, 40 // frequency)
        cycle_ticks = one_way_ticks * 2
        phase = (tick_number - 1) % cycle_ticks
        ticks_per_led = one_way_ticks / max(1, num_leds - 1)

        if phase < one_way_ticks:
            head_position = self._wave_head_position(num_leds, one_way_ticks, phase, reverse=False)
            return self._render_wave(targets, [head_position], width, ticks_per_led, job_colors[0], job_colors[1])
        else:
            head_position = self._wave_head_position(num_leds, one_way_ticks, phase - one_way_ticks, reverse=True)
            return self._render_wave(targets, [head_position], width, ticks_per_led, job_colors[0], job_colors[1])
