"""Choices for archived-history imports; other Web operations retain their defaults."""

WAIT_SECONDS_OPTIONS = (30, 60, 90, 120, 180, 300)
PARALLEL_COUNT_OPTIONS = tuple(range(1, 11))
DEFAULT_WAIT_SECONDS = 60
DEFAULT_PARALLEL_COUNT = 3
WAIT_SECONDS_SETTING = "historical_archive_wait_seconds"
PARALLEL_COUNT_SETTING = "historical_archive_parallel_count"


def saved_choice(value: str, choices: tuple[int, ...], default: int) -> int:
    """Restore a supported choice, including settings from older installations."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number in choices else default
