"""Persistent TUI preferences. Sits next to the sqlite store so a user's
captured measurements and their UI choices travel together. Keep this
surface small — this is not a general-purpose config system."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from platformdirs import user_data_path

logger = logging.getLogger(__name__)

# The display-precision axis for imperial output. "1" means nearest inch,
# "1/2" means nearest half-inch (the historical default), etc. The order
# here is also the cycle order used by the TUI `P` action.
PRECISION_VALUES: tuple[str, ...] = ("1", "1/2", "1/4", "1/8")
DEFAULT_PRECISION = "1/2"


@dataclass
class Preferences:
    setup_idle_s: float = 20.0
    display_precision: str = DEFAULT_PRECISION
    # None = auto (collapse below 100 cols); True = force collapsed;
    # False = force expanded. The override is for users who want the
    # settings panel always visible even on a narrow pane, or always
    # hidden even on a wide one.
    right_panel_collapsed: bool | None = None

    def cycle_precision(self) -> str:
        try:
            i = PRECISION_VALUES.index(self.display_precision)
        except ValueError:
            i = PRECISION_VALUES.index(DEFAULT_PRECISION) - 1
        self.display_precision = PRECISION_VALUES[(i + 1) % len(PRECISION_VALUES)]
        return self.display_precision


def default_prefs_path() -> Path:
    base = user_data_path("bosch-glm", appauthor=False, ensure_exists=True)
    return base / "prefs.json"


def load(path: Path | None = None) -> Preferences:
    """Read prefs from disk. Missing file or parse errors → defaults, with
    a warning logged. Unknown keys are dropped so older prefs files survive
    a field removal."""
    path = path or default_prefs_path()
    if not path.exists():
        return Preferences()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("prefs: failed to read %s (%s); using defaults", path, e)
        return Preferences()
    known = {f for f in Preferences.__dataclass_fields__}
    cleaned = {k: v for k, v in data.items() if k in known}
    try:
        return Preferences(**cleaned)
    except TypeError as e:
        logger.warning("prefs: bad fields in %s (%s); using defaults", path, e)
        return Preferences()


def save(prefs: Preferences, path: Path | None = None) -> None:
    path = path or default_prefs_path()
    try:
        path.write_text(json.dumps(asdict(prefs), indent=2) + "\n")
    except OSError as e:
        logger.warning("prefs: failed to write %s (%s)", path, e)
