"""Lighting metadata constants for patterns and filters."""

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
        "optional": ["duration", "frequency"],
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
    "brightness": {
        "description": "Brightness filter",
        "optional": ["brightness"],
    },
    "spike": {
        "description": "Spike filter",
        "optional": ["color", "duration", "period", "variation", "heat", "scope"],
    },
    "dropout": {
        "description": "Dropout filter (black spike)",
        "optional": ["duration", "period", "variation", "heat", "scope"],
    },
}
