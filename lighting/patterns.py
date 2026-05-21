"""Pattern mixin for lighting effects."""

import math
import random


class PatternMixin:
    """Provides pattern implementations used by the lighting engine."""

    def _linear_color(self, color_1, color_2, phase):
        """Linearly interpolate between two RGB colors by phase (0.0-1.0)."""

        return (
            int(color_1[0] * (1 - phase) + color_2[0] * phase),
            int(color_1[1] * (1 - phase) + color_2[1] * phase),
            int(color_1[2] * (1 - phase) + color_2[2] * phase),
        )

    def _set_targets(self, targets: list, color: tuple) -> list:
        """Return (led_index, color) pairs for all targets."""

        return [(target, color) for target in targets]

    def _get_effect_colors(self, effect: dict, count: int = 1) -> list:
        """Return a list of resolved RGB tuples for the effect's colors."""

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
        if "frequency" in effect:
            try:
                freq = float(effect.get("frequency", 0))
                if freq > 0:
                    half_period = max(1, int(round(20.0 / freq)))
            except Exception:
                pass
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

    def pattern_pulse(self, name: str, effect: dict, tick_number: int) -> list:
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

    def pattern_periodic(
        self,
        name: str,
        effect: dict,
        tick_number: int,
        interval: int,
        duration: int,
        colors: list,
        targets: list,
    ) -> list:
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
        """Return the head LED position (as a float) for a wave at the given phase."""

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
        """Render one or more wave comets with sub-pixel smoothing."""

        fade_ticks = max(1, width * ticks_per_led)
        step_r = (color2[0] - color1[0]) / fade_ticks
        step_g = (color2[1] - color1[1]) / fade_ticks
        step_b = (color2[2] - color1[2]) / fade_ticks

        lo_r, hi_r = min(color1[0], color2[0]), max(color1[0], color2[0])
        lo_g, hi_g = min(color1[1], color2[1]), max(color1[1], color2[1])
        lo_b, hi_b = min(color1[2], color2[2]), max(color1[2], color2[2])

        updates = {}

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

        fill_count = max(1, math.ceil(1 / ticks_per_led)) if ticks_per_led > 0 else 1

        for head_position in head_positions:
            if not reverse:
                head_index = int(head_position)
                sub_position = head_position - head_index
            else:
                head_index = math.ceil(head_position)
                sub_position = head_index - head_position

            if 0 <= head_index < len(targets):
                for offset in range(fill_count):
                    fill_idx = head_index - offset if not reverse else head_index + offset

                    if 0 <= fill_idx < len(targets):
                        updates[fill_idx] = (targets[fill_idx], color2)

                if not reverse:
                    if head_index + 1 < len(targets) and sub_position > 0:
                        blend_color = self._linear_color(color1, color2, sub_position)
                        updates[head_index + 1] = (targets[head_index + 1], blend_color)

                else:
                    if head_index - 1 >= 0 and sub_position > 0:
                        blend_color = self._linear_color(color1, color2, sub_position)
                        updates[head_index - 1] = (targets[head_index - 1], blend_color)

        return list(updates.values())

    def pattern_wave(self, name: str, effect: dict, tick_number: int) -> list:
        """Wave function: creates one or more moving comet effects across the LEDs."""

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
        """Cylon function: a comet that bounces back and forth across the LEDs."""

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

        head_position = self._wave_head_position(num_leds, one_way_ticks, phase - one_way_ticks, reverse=True)
        return self._render_wave(
            targets, [head_position], width, ticks_per_led, effect_colors[0], effect_colors[1], reverse=True
        )

    def pattern_phaser_strip(self, name: str, effect: dict, tick_number: int) -> list:
        """Phaser strip: two waves converge on a random meeting point."""

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

        if phase == 0 and tick_number > 1:
            self._count_cycle(name, effect)
            if self.scene_state.get(name, {}).get("finished"):
                meet_index = self.retained_values.get(name, 0)
                self.scene_state[name]["passthrough"] = {"target": targets[meet_index]}
                return [(target, effect_colors[0]) for target in targets]

        if name not in self.retained_values or phase == 0:
            meet_index = random.randint(1, num_leds - 2)
            self.retained_values[name] = meet_index
        else:
            meet_index = self.retained_values[name]

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

        fade_phase = phase - cycle_ticks

        if fade_phase < fade_ticks - 1:
            return self._render_wave(
                targets, [float(meet_index)], width, ticks_per_led, effect_colors[0], effect_colors[1], reverse=False
            )

        return [(target, effect_colors[0]) for target in targets]
