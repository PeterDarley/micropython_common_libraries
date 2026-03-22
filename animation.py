import time

try:
    import _thread

    _THREAD = True
except Exception:
    _THREAD = False


class Animation:
    """Controls animations for LEDs or other things"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        """Return the single shared instance, creating it if necessary."""

        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialised = False

        return cls._instance

    def __init__(self, jobs: dict = None, stop_callbacks: dict = None):
        """Create our animation object."""

        if self._initialised:
            return

        self._initialised = True
        self.stopped = False
        self.running = False
        self.tick_number = 0
        self.frame_interval_ms = 25
        self.jobs = jobs if jobs is not None else {}
        self.jobs_callbacks = stop_callbacks if stop_callbacks is not None else {}

    def add_job(self, name: str, job):
        """Add a job to the animation."""

        if name in self.jobs:
            raise ValueError(f"Job with name '{name}' already exists.")

        self.jobs[name] = job

    def _run(self):
        """Internal thread target: loop calling tick() until stopped."""

        self.stopped = False
        self.running = True

        while not self.stopped:
            self.tick()
            time.sleep_ms(self.frame_interval_ms)

        self.running = False

    def start(self):
        """Start the animation in a background thread."""

        if self.running:
            return

        if _THREAD:
            _thread.start_new_thread(self._run, ())
        else:
            self._run()

    def stop(self):
        """Stop the animation loop."""

        self.stopped = True

        for name, callback in self.jobs_callbacks.items():
            callback()

    def reset(self):
        """Reset the animation to its initial state."""

        self.tick_number = 0

    def tick(self):
        """Perform one frame of the animation. Override in subclasses."""

        self.tick_number += 1

        for name, job in self.jobs.items():
            job(self.tick_number)
