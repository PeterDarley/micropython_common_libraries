"""Holds classes/functions for timing"""

from micropython import schedule, const  # type: ignore
from machine import Timer  # type: ignore
from math import inf

import settings


class TimerManager:
    """Manager for the timers"""

    def __new__(cls, *args, **kwargs):
        """Create a singleton."""

        if not hasattr(cls, "instance"):
            cls.instance: TimerManager = super(TimerManager, cls).__new__(cls)

        return cls.instance

    def __init__(self):
        """Initialize the timer factory."""

        if not hasattr(self, "timers"):
            self.timers: list = [None for timer in range(self.timer_count)]

    @property
    def timer_count(self):
        """Return the number of timers available."""

        timer_counts: dict = {"ESP32": const(4)}

        if settings.BOARD["Type"] in timer_counts:
            return timer_counts[settings.BOARD["Type"]]
        else:
            # Guess at number of timers.  Choosing 4 out of ignorance.
            return 4

    def first_available_timer(self) -> int:
        """Return the first available timer."""

        if len(self) < self.timer_count:
            return min([timer for timer in range(self.timer_count) if self.timers[timer] is None])

        return None

    def __len__(self) -> int:
        """Return the number of timers in use."""
        # return len(self.timers)
        return len([timer for timer in range(self.timer_count) if self.timers[timer] is not None])

    class Timey:
        """Timer class."""

        def __init__(
            self,
            *,
            timer: int,
            callback: callable,
            periods: list[int],
            cycles: int = inf,
            end_callback: callable = None
        ):
            """Initialize the timer."""

            self.callback: callable = callback
            self.periods: list[int] = periods
            self.cycles: int = cycles
            self.current_cycle: int = 0
            self.end_callback: callable = end_callback
            self.timer_index: int = timer

            self.timer: Timer = Timer(self.timer_index)
            TimerManager().timers[self.timer_index] = self

            self.tick(None)

        def tick(self, _):
            """Tick the timer."""

            if self.timer is None:
                return

            self.current_cycle += 1
            if self.current_cycle > self.cycles:
                schedule(self.stop, None)
                return

            period: int = self.periods[self.current_cycle % len(self.periods)]

            self.timer.init(period=period, mode=Timer.ONE_SHOT, callback=self.tick)
            schedule(self.callback, self)

        def stop(self, code: str = None):
            """Stop the timer."""

            if self.timer is None:
                return

            self.timer.deinit()
            TimerManager().timers[self.timer_index] = None
            self.timer = None

            if self.end_callback and code != "kill":
                schedule(self.end_callback, self)

    def get_timer(
        self, *, callback: callable, periods: list[int], cycles: int = inf, end_callback: callable = None
    ) -> Timey:
        """Return a timer."""

        if self.timer_count > 0:
            return self.Timey(
                timer=self.first_available_timer(),
                callback=callback,
                periods=periods,
                cycles=cycles,
                end_callback=end_callback,
            )
        else:
            raise Exception("No timers available.")

    def kill_all_timers(self):
        """Stop all timers."""

        for timer in self.timers:
            if timer:
                timer.stop("kill")
