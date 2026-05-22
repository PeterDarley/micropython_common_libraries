"""Effect runtime mixin for lighting scene processing."""


class EffectRuntimeMixin:
    """Provides effect resolution and per-tick execution."""

    def _log_effect_start(self, scene_name: str, entry_name: str, effect: dict, tick_number: int) -> None:
        """Print a one-shot debug line when an effect starts running."""

        pattern_name: str = str(effect.get("pattern", "?"))
        target: object = effect.get("target", "all")
        print(
            "lighting: effect start"
            f" scene={scene_name}"
            f" entry={entry_name}"
            f" pattern={pattern_name}"
            f" target={target}"
            f" tick={tick_number}"
        )

    def _log_effect_end(
        self,
        scene_name: str,
        entry_name: str,
        effect: dict,
        tick_number: int,
        reason: str,
    ) -> None:
        """Print a one-shot debug line when an effect ends."""

        pattern_name: str = str(effect.get("pattern", "?"))
        target: object = effect.get("target", "all")
        print(
            "lighting: effect end"
            f" scene={scene_name}"
            f" entry={entry_name}"
            f" pattern={pattern_name}"
            f" target={target}"
            f" reason={reason}"
            f" tick={tick_number}"
        )

    def _log_scene_effect_endings(self, scene_name: str, reason: str, tick_number: int = -1) -> None:
        """Log end lines for any started effects in a scene that have not already ended."""

        scene_data: dict = self.settings.get("scenes", {}).get(scene_name, {})
        for entry_name, scene_entry in scene_data.items():
            state_key: str = scene_name + "::" + entry_name
            state: dict = self.scene_state.get(state_key, {})
            if not state.get("_debug_started") or state.get("_debug_finished"):
                continue

            effect: dict = self._resolve_effect(scene_entry)
            if not effect or "pattern" not in effect:
                continue

            state["_debug_finished"] = True
            self._log_effect_end(scene_name, entry_name, effect, tick_number=tick_number, reason=reason)

    def _is_scene_finished(self, scene_name: str) -> bool:
        """Return True if all cycle-limited effects in the given scene have finished."""

        scene_data = self.settings["scenes"].get(scene_name, {})
        has_any_cycles = False

        for entry_name, scene_entry in scene_data.items():
            effect = self._resolve_effect(scene_entry)
            if not effect or "pattern" not in effect:
                continue

            # Any effect without an explicit cycles limit is ongoing by design,
            # so the scene should never be considered finished automatically.
            if effect.get("cycles") is None:
                return False

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
        """Return True if the scene includes at least one non-terminating effect."""

        scene_data = self.settings["scenes"].get(scene_name, {})
        has_cycle_limited_effect = False

        for name, scene_entry in scene_data.items():
            effect = self._resolve_effect(scene_entry)
            if not effect or "pattern" not in effect:
                continue

            if effect.get("cycles") is None:
                return True

            has_cycle_limited_effect = True

        # Preserve previous behavior for empty/malformed scenes: treat them as ongoing.
        return not has_cycle_limited_effect

    def get_logical_color(self, index: int) -> tuple:
        """Return the pre-scaled logical color for the given LED index."""

        if 0 <= index < len(self.logical_colors):
            return self.logical_colors[index]

        return (0, 0, 0)

    def _resolve_effect(self, scene_entry: dict) -> dict:
        """Resolve a scene entry into a full effect dict with pattern, colors, target, etc."""

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
        """Record that the named effect has completed one cycle."""

        cycles = effect.get("cycles", None)
        if cycles is None:
            return

        if name not in self.scene_state:
            self.scene_state[name] = {"remaining": cycles}

        state = self.scene_state[name]
        state["remaining"] = state.get("remaining", cycles) - 1

        if state["remaining"] <= 0:
            state["finished"] = True
            if not state.get("_debug_finished"):
                state["_debug_finished"] = True
                if "::" in name:
                    scene_name, entry_name = name.split("::", 1)
                else:
                    scene_name, entry_name = "?", name
                tick_number: int = self.animation.tick_number if hasattr(self, "animation") else -1
                self._log_effect_end(scene_name, entry_name, effect, tick_number=tick_number, reason="cycles-complete")

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

                if self.scene_state.get(state_key, {}).get("finished"):
                    continue

                after = scene_entry.get("after")
                if after:
                    after_key = active_scene_name + "::" + after
                    predecessor_state = self.scene_state.get(after_key, {})
                    if not predecessor_state.get("finished"):
                        continue

                    if state_key not in self.scene_state:
                        self.scene_state[state_key] = {"start_tick": tick_number}
                    elif "start_tick" not in self.scene_state[state_key]:
                        self.scene_state[state_key]["start_tick"] = tick_number

                    if scene_entry.get("inherit_target"):
                        passthrough = predecessor_state.get("passthrough", {})
                        if "target" in passthrough:
                            effect = dict(effect)
                            effect["target"] = passthrough["target"]

                start_tick = self.scene_state.get(state_key, {}).get("start_tick", scene_start)
                local_tick = tick_number - start_tick

                state = self.scene_state.get(state_key)
                if state is None:
                    self.scene_state[state_key] = {}
                    state = self.scene_state[state_key]

                if not state.get("_debug_started"):
                    state["_debug_started"] = True
                    self._log_effect_start(active_scene_name, entry_name, effect, tick_number=tick_number)

                pattern_name = "pattern_" + effect["pattern"]
                if hasattr(self, pattern_name):
                    func = getattr(self, pattern_name)
                    result = func(name=state_key, effect=effect, tick_number=local_tick)
                    target_colors = result

                    state = self.scene_state.get(state_key, {})
                    if state.get("finished") and "passthrough" not in state:
                        state["passthrough"] = {"target": effect["target"]}

                    if "filters" in effect:
                        stored_filters = self.settings.get("filters", {})
                        filtered_colors = target_colors

                        for filter_ref in effect["filters"]:
                            if isinstance(filter_ref, str):
                                filter_dict = stored_filters.get(filter_ref)
                                if not filter_dict:
                                    continue

                            else:
                                filter_dict = filter_ref

                            filter_name = "filter_" + filter_dict["filter"]
                            if hasattr(self, filter_name):
                                filter_func = getattr(self, filter_name)
                                transient_target_groups_set = False
                                if filter_dict.get("filter") in ("spike", "dropout"):
                                    filter_dict["_target_groups"] = self._target_component_groups(effect.get("target"))
                                    transient_target_groups_set = True

                                filter_result = filter_func(filter_dict, filtered_colors, tick_number=tick_number)

                                if transient_target_groups_set and "_target_groups" in filter_dict:
                                    del filter_dict["_target_groups"]

                                if filter_result is not None:
                                    filtered_colors = filter_result

                        result = filtered_colors

                    if result:
                        for led_index, color in result:
                            updates[led_index] = color

        for led_index, color in updates.items():
            self.logical_colors[led_index] = color
            self.leds.set(led_index, color)

        for active_scene_name in list(self._active_scenes):
            if self._is_scene_finished(active_scene_name):
                self.remove_scene(active_scene_name)

        try:
            self.leds.show()
        except Exception as e:
            print(f"lighting: leds.show() failed: {e}")
