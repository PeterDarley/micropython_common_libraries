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
                # "Wave": {"all_wave": {"pattern": "wave", "target": "0-15", "colors": ["blue", "green"]}},
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

        for name, job in self.settings["scenes"][self.scene_name].items():
            pattern_name = "pattern_" + job["pattern"]
            if hasattr(self, pattern_name):
                func = getattr(self, pattern_name)
                func(job, tick_number)

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

    def _set_targets(self, targets, color):
        """Set each target LED to color, skipping those already at that color."""

        for target in targets:
            if self.leds.get(target) != color:
                self.leds.set(target, color)

    def pattern_solid(self, job, tick):
        """Simple solid color function for a lighting job."""

        job_colors = self.get_color(job["colors"])
        self._set_targets(self.get_targets(job["target"]), job_colors[0])

    def pattern_blink(self, job, tick):
        """Simple blink function for a lighting job."""

        interval = 40 // job.get("frequency", None)
        duration = interval
        colors = self.get_color(job["colors"])

        self.pattern_periodic(
            tick=tick, interval=interval, duration=duration, colors=colors, targets=self.get_targets(job["target"])
        )

    def pattern_pulse(self, job, tick):
        """Simple pulse function for a lighting job."""

        duration = job["duration"]
        interval = 40 // job.get("frequency", None) - duration
        colors = self.get_color(job["colors"])

        self.pattern_periodic(
            tick=tick, interval=interval, duration=duration, colors=colors, targets=self.get_targets(job["target"])
        )

    def pattern_periodic(self, tick, interval, duration, colors, targets):
        """blink function for a lighting job."""

        cycle_length = duration + interval
        phase = tick % cycle_length

        for target in targets:
            if phase < duration:
                self.leds.set(target, colors[0])
            else:
                self.leds.set(target, colors[1])

    def pattern_fade_in(self, job, tick):
        """Simple fade in function for a lighting job."""

        job_colors = self.get_color(job["colors"])
        targets = self.get_targets(job["target"])
        phase = min(tick / job["duration"], 1.0)
        self._set_targets(targets, self._linear_color(job_colors[0], job_colors[1], phase))

    def pattern_breathe(self, job, tick):
        """Breathe function: uses sin() to smoothly modulate between two colors."""

        job_colors = self.get_color(job["colors"])
        targets = self.get_targets(job["target"])
        phase = (math.sin(2 * math.pi * job.get("frequency", 1) * tick / 40) + 1) / 2
        self._set_targets(targets, self._linear_color(job_colors[0], job_colors[1], phase))

    def pattern_sizzle(self, job, tick):
        """Sizzle function: fluctuates around a base color with random variations."""

        job_colors = self.get_color(job["colors"])
        targets = self.get_targets(job["target"])
        frequency = job.get("frequency", 40)
        variation = job.get("variation", 50)
        heat = job.get("heat", 10)

        interval = 40 // frequency

        (red, green, blue) = job_colors[0]
        (current_red, current_green, current_blue) = self.leds.get(targets[0])

        if tick == 1:
            new_red, new_green, new_blue = red, green, blue

            self.leds.set(targets, (new_red, new_green, new_blue))

        elif tick % interval == 0:
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

            self.leds.set(targets, (new_red, new_green, new_blue))
