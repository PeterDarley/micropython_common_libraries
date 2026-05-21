"""Filter mixin for lighting effects."""

import random


class FilterMixin:
    """Provides filter implementations used by the lighting engine."""

    def filter_null(self, filter_dict: dict, leds: list, tick_number: int) -> list:
        """Null filter: returns the LED list unaltered."""

        return leds

    def filter_brightness(self, filter_dict: dict, leds: list, tick_number: int) -> list:
        """Brightness filter: scale each RGB channel by a constant multiplier.

        The result of each channel is clamped to the inclusive range 0..255
        and returned as an integer.
        """

        if not leds:
            return leds

        try:
            brightness_multiplier = float(filter_dict.get("brightness", 1.0))
        except (TypeError, ValueError):
            brightness_multiplier = 1.0

        result = []
        for led_index, target_color in leds:
            new_red = max(0, min(255, int(target_color[0] * brightness_multiplier)))
            new_green = max(0, min(255, int(target_color[1] * brightness_multiplier)))
            new_blue = max(0, min(255, int(target_color[2] * brightness_multiplier)))
            result.append((led_index, (new_red, new_green, new_blue)))

        return result

    def filter_sizzle(self, filter_dict: dict, leds: list, tick_number: int) -> list:
        """Sizzle filter: applies a uniform random deviation to all LEDs.

        Generates a single random offset per channel and applies it to every LED's
        target color. The deviation is bounded by variation and stepped by heat.
        Updates only on ticks divisible by 40 // frequency.
        """

        if not leds:
            return leds

        frequency = filter_dict.get("frequency", 40)
        heat = filter_dict.get("heat", 10)

        interval = 40 // frequency

        if tick_number % interval != 0:
            return leds

        dev_red = random.randint(-heat, heat)
        dev_green = random.randint(-heat, heat)
        dev_blue = random.randint(-heat, heat)

        result = []
        for led_index, target_color in leds:
            new_red = max(0, min(255, target_color[0] + dev_red))
            new_green = max(0, min(255, target_color[1] + dev_green))
            new_blue = max(0, min(255, target_color[2] + dev_blue))
            result.append((led_index, (new_red, new_green, new_blue)))

        return result

    def filter_scintillate(self, filter_dict: dict, leds: list, tick_number: int) -> list:
        """Scintillate filter: applies independent random deviations to each LED."""

        if not leds:
            return leds

        frequency = filter_dict.get("frequency", 40)
        heat = filter_dict.get("heat", 10)

        interval = 40 // frequency

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

    def _find_contiguous_groups(self, leds: list) -> list:
        """Split a list of (led_index, color) pairs into contiguous index groups."""

        if not leds:
            return []

        groups = []
        current_group = [leds[0]]

        for i in range(1, len(leds)):
            if leds[i][0] == leds[i - 1][0] + 1:
                current_group.append(leds[i])
            else:
                groups.append(current_group)
                current_group = [leds[i]]

        groups.append(current_group)

        return groups

    def _target_component_groups(self, target: object) -> list:
        """Return explicit target component groups when the target is an aggregate named range."""

        if not isinstance(target, str) or not target.startswith("named:"):
            return []

        range_name = target[6:]
        named_ranges = self.settings.get("named_ranges", {})
        group_spec = named_ranges.get(range_name)
        if not isinstance(group_spec, list):
            return []

        groups = []
        for item in group_spec:
            group_targets = self.get_targets(item)
            if group_targets:
                groups.append(group_targets)

        return groups

    def _apply_spike_filter(
        self,
        filter_dict: dict,
        leds: list,
        tick_number: int,
        spike_color: tuple,
    ) -> list:
        """Shared implementation for spike and dropout filters."""

        if not leds:
            return leds

        duration = int(filter_dict.get("duration", 5))
        period = int(filter_dict.get("period", 40))
        variation = int(filter_dict.get("variation", 0))
        heat = int(filter_dict.get("heat", 0))
        scope = filter_dict.get("scope", "all")

        if "_state" not in filter_dict:
            filter_dict["_state"] = {}

        state = filter_dict["_state"]

        if scope == "all":
            groups = [leds]
        elif scope == "subranges":
            explicit_groups = filter_dict.get("_target_groups", [])
            if explicit_groups:
                led_lookup = {led_index: (led_index, target_color) for led_index, target_color in leds}
                groups = []
                for target_group in explicit_groups:
                    group = []
                    for led_index in target_group:
                        if led_index in led_lookup:
                            group.append(led_lookup[led_index])
                    if group:
                        groups.append(group)

                if not groups:
                    groups = self._find_contiguous_groups(leds)
            else:
                groups = self._find_contiguous_groups(leds)
        else:
            groups = [[led] for led in leds]

        result = []

        for group_index, group in enumerate(groups):
            group_key = str(group_index)

            if group_key not in state:
                variation_offset = random.randint(-variation, variation) if variation > 0 else 0
                initial_phase_offset = 0
                if scope != "all" and period > 1:
                    initial_phase_offset = random.randint(0, period - 1)
                state[group_key] = {
                    "next_spike": tick_number + period + variation_offset + initial_phase_offset,
                    "spike_end": -1,
                }

            group_state = state[group_key]

            if tick_number >= group_state["next_spike"] and tick_number > group_state["spike_end"]:
                heat_offset = random.randint(-heat, heat) if heat > 0 else 0
                spike_duration = max(1, duration + heat_offset)
                group_state["spike_end"] = tick_number + spike_duration - 1
                variation_offset = random.randint(-variation, variation) if variation > 0 else 0
                group_state["next_spike"] = group_state["spike_end"] + 1 + period + variation_offset

            active = tick_number <= group_state["spike_end"]

            for led_index, target_color in group:
                result.append((led_index, spike_color if active else target_color))

        return result

    def filter_spike(self, filter_dict: dict, leds: list, tick_number: int) -> list:
        """Spike filter: periodically overrides LED color with a configurable spike color."""

        spike_color = self.get_color(filter_dict.get("color", "white"))

        return self._apply_spike_filter(filter_dict, leds, tick_number, spike_color)

    def filter_dropout(self, filter_dict: dict, leds: list, tick_number: int) -> list:
        """Dropout filter: periodically overrides LED color with black."""

        return self._apply_spike_filter(filter_dict, leds, tick_number, (0, 0, 0))
