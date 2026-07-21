""" Package to hold utility functions """

def bytes_to_int(first_byte: int, second_byte: int) -> int:
    """ Convert two bytes to an integer. """

    if not first_byte & 0x80:
        return first_byte << 8 | second_byte
    return - (((first_byte ^ 255) << 8) | (second_byte ^ 255) + 1)


def reset_cause_name() -> str:
    """Return a human-readable name for the MCU's last reset cause.

    Useful for diagnosing why the device restarted (e.g. distinguishing a
    true power-on from a watchdog or soft reset). Returns "unknown" on
    platforms without machine.reset_cause().
    """

    try:
        import machine  # type: ignore
    except ImportError:
        return "unknown"

    try:
        cause = machine.reset_cause()
    except AttributeError:
        return "unknown"

    for attribute_name, label in (
        ("PWRON_RESET", "power-on"),
        ("HARD_RESET", "hard reset"),
        ("WDT_RESET", "watchdog"),
        ("DEEPSLEEP_RESET", "deep-sleep wake"),
        ("SOFT_RESET", "soft reset"),
    ):
        if getattr(machine, attribute_name, None) == cause:
            return label

    return "unknown ({})".format(cause)


def url_decode(encoded_string: str) -> str:
    """Decode a URL-encoded string (replaces %XX sequences and + with space)."""

    decoded = encoded_string.replace("+", " ")
    result = ""
    index = 0

    while index < len(decoded):
        if decoded[index] == "%" and index + 2 < len(decoded):
            try:
                result += chr(int(decoded[index + 1 : index + 3], 16))
                index += 3
            except ValueError:
                result += decoded[index]
                index += 1
        else:
            result += decoded[index]
            index += 1

    return result