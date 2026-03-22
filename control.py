"""Hold the decision making logic for the bot"""

from machine import WDT  # type: ignore
from comms import WIFIManager  # , I2CManager


class ThinkTank:
    """ThinkTank is the decision making logic for the bot"""

    def __new__(cls):
        """Return the singleton instance."""

        if not hasattr(cls, "instance"):
            cls.instance: ThinkTank = super(ThinkTank, cls).__new__(cls)

        return cls.instance

    def __init__(self):
        """Initialize the ThinkTank."""

        pass

    def panic(self):
        """Panic!"""

        pass

    def failsafe(self):
        """Things that need to happen if the bot loses connection."""

        pass


class Orientation:
    """Orientation is a singleton that manages the orientation of the bot."""

    def __new__(cls):
        """Return the singleton instance."""

        if not hasattr(cls, "instance"):
            cls.instance: Orientation = super(Orientation, cls).__new__(cls)

        return cls.instance

    def __init__(self):
        """Initialize the Orientation."""

        pass

    def get_orientation(self):
        """Get the current orientation of the bot."""

        pass

    def set_orientation(self):
        """Set the current orientation of the bot."""

        pass
