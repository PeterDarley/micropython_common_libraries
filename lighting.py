import math
import random

import settings

from animation import Animation
from leds import LEDs
from storage import PersistentDict

colors = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "purple": (128, 0, 128),
}


class Lighting:
    def __init__(self):
        self.settings_object = PersistentDict()
        self.settings_object["lighting_settings"] = {
            "default_scene": "Test Scene",
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
                    "sizzle_1": {
                        "target": "11-13",
                        "pattern": "sizzle",
                        "frequency": 40,
                        "variation": 20,
                        # "colors": [(100, 0, 0)],
                        "colors": ["red"],
                        "heat": 5,
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
                "Flood": {"all_flood": {"pattern": "solid", "target": "all", "colors": ["white"]}},
            },
            "named_ranges": {},
        }

        self.settings_object.store()

        self.settings = self.settings_object["lighting_settings"]
        self.set_scene()
        self.leds = LEDs()
        self.animation = Animation(jobs={"lighting": self.process_tick}, stop_callbacks={"lighting": self.stop})
        self.retained_values = {}

    def set_scene(self, scene_name: str = None):
        """Set the current lighting scene."""

        current_scene = getattr(self, "scene_name", None)
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

        if self.scene_name != current_scene and hasattr(self, "animation"):
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
            pattern_name = "pattern_" + job["pattern"]
            if hasattr(self, pattern_name):
                func = getattr(self, pattern_name)
                result = func(name=name, job=job, tick_number=tick_number)
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

    def _wave_head_index(self, num_leds: int, cycle_ticks: int, phase: int, reverse: bool) -> int:
        """Return the head LED index for a wave at the given phase."""

        if cycle_ticks > 1:
            index = phase * (num_leds - 1) // (cycle_ticks - 1)
        else:
            index = num_leds - 1

        if reverse:
            index = (num_leds - 1) - index

        return index

    def _render_wave(
        self, targets: list, head_indices: list, width: int, ticks_per_led: float, color1: tuple, color2: tuple
    ) -> list:
        """Render one or more wave comets and return (led_index, color) pairs.

        Phase 1: compute a faded color for every LED one fixed linear step toward
        color1. Phase 2: overwrite head LEDs with color2. Returns all updates as
        a list so process_tick applies them to the LEDs object.
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

        # Phase 2: stamp heads at full color2.
        for head_index in head_indices:
            updates[head_index] = (targets[head_index], color2)

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
        head_indices = [
            self._wave_head_index(num_leds, cycle_ticks, (phase + peak * spacing) % cycle_ticks, reverse)
            for peak in range(number)
        ]

        return self._render_wave(targets, head_indices, width, ticks_per_led, job_colors[0], job_colors[1])

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
            head_index = self._wave_head_index(num_leds, one_way_ticks, phase, reverse=False)
            return self._render_wave(targets, [head_index], width, ticks_per_led, job_colors[0], job_colors[1])
        else:
            head_index = self._wave_head_index(num_leds, one_way_ticks, phase - one_way_ticks, reverse=True)
            return self._render_wave(targets, [head_index], width, ticks_per_led, job_colors[0], job_colors[1])
