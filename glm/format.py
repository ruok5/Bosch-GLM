"""Display + clipboard helpers. Pure, no side effects beyond pbcopy."""
import logging
import subprocess

logger = logging.getLogger(__name__)

IN_PER_M = 39.3700787


def format_imperial(meters: float) -> str:
    """Feet-inches rounded to the nearest 1/2 inch."""
    half_inches = round(meters * IN_PER_M * 2)
    feet, rem = divmod(half_inches, 24)
    whole_in, frac = divmod(rem, 2)
    return f"{feet}'-{whole_in} 1/2\"" if frac else f"{feet}'-{whole_in}\""


_QUARTER_FRAC = {0: "", 1: " 1/4", 2: " 1/2", 3: " 3/4"}
_EIGHTH_FRAC = {
    0: "", 1: " 1/8", 2: " 1/4", 3: " 3/8",
    4: " 1/2", 5: " 5/8", 6: " 3/4", 7: " 7/8",
}


def format_imperial_quarter(meters: float) -> str:
    """Feet-inches rounded to the nearest 1/4 inch."""
    quarter_inches = round(meters * IN_PER_M * 4)
    feet, rem = divmod(quarter_inches, 48)
    whole_in, frac = divmod(rem, 4)
    return f"{feet}'-{whole_in}{_QUARTER_FRAC[frac]}\""


def format_imperial_eighth(meters: float) -> str:
    """Feet-inches rounded to the nearest 1/8 inch."""
    eighth_inches = round(meters * IN_PER_M * 8)
    feet, rem = divmod(eighth_inches, 96)
    whole_in, frac = divmod(rem, 8)
    return f"{feet}'-{whole_in}{_EIGHTH_FRAC[frac]}\""


def format_imperial_inch(meters: float) -> str:
    """Feet-inches rounded to the nearest whole inch."""
    inches = round(meters * IN_PER_M)
    feet, whole_in = divmod(inches, 12)
    return f"{feet}'-{whole_in}\""


# Dispatch table keyed on the precision strings used in Preferences.
# Matches PRECISION_VALUES in glm.prefs — a user preference change is just
# a different entry in this dict with no other code paths to update.
_BY_PRECISION = {
    "1":   format_imperial_inch,
    "1/2": format_imperial,
    "1/4": format_imperial_quarter,
    "1/8": format_imperial_eighth,
}


def format_imperial_at(meters: float, precision: str) -> str:
    """Format `meters` as a feet-inches string at the given precision.
    Precision is one of "1", "1/2", "1/4", "1/8". Unknown → 1/2 (default)."""
    return _BY_PRECISION.get(precision, format_imperial)(meters)


def displayed_inches(meters: float) -> float:
    return round(meters * IN_PER_M * 2) / 2


def fractional_inches(meters: float) -> str:
    """Format inches as e.g. `43 1/2"` (whole inches + half-inch, no fraction
    for whole values). Matches the GLM display when set to inch-fraction unit."""
    half_inches = round(meters * IN_PER_M * 2)
    whole, frac = divmod(half_inches, 2)
    return f"{whole} 1/2\"" if frac else f"{whole}\""


BIG_FONT = {
    '0': ["███", "█ █", "█ █", "█ █", "███"],
    '1': [" █ ", "██ ", " █ ", " █ ", "███"],
    '2': ["███", "  █", "███", "█  ", "███"],
    '3': ["███", "  █", "███", "  █", "███"],
    '4': ["█ █", "█ █", "███", "  █", "  █"],
    '5': ["███", "█  ", "███", "  █", "███"],
    '6': ["███", "█  ", "███", "█ █", "███"],
    '7': ["███", "  █", "  █", "  █", "  █"],
    '8': ["███", "█ █", "███", "█ █", "███"],
    '9': ["███", "█ █", "███", "  █", "███"],
    "'": [" █ ", " █ ", "   ", "   ", "   "],
    '"': ["█ █", "█ █", "   ", "   ", "   "],
    '-': ["   ", "   ", "███", "   ", "   "],
    '/': ["  █", "  █", " █ ", "█  ", "█  "],
    ' ': ["   ", "   ", "   ", "   ", "   "],
}


def render_big(text: str) -> str:
    rows = ["", "", "", "", ""]
    for ch in text:
        glyph = BIG_FONT.get(ch, BIG_FONT[' '])
        for i in range(5):
            rows[i] += glyph[i] + " "
    return "\n".join(rows)


def copy_to_clipboard(text: str) -> None:
    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        logger.warning("pbcopy failed: %s", e)
