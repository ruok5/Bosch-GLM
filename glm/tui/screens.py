"""Modal screens for the GLM TUI."""
from __future__ import annotations

from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Static

from ..format import format_imperial
from ..station import (
    PIPE_SIZES, PIPE_SIZE_DEFAULT, PRESET_LABELS,
    format_pipe_label, suggest_labels,
)


class PipeSizePicker(ModalScreen[str | None]):
    """Sub-modal: pick a pipe size from PIPE_SIZES. Returns the size string
    or None on cancel."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "select", "Select"),
        Binding("j,down", "next", "Next"),
        Binding("k,up", "prev", "Prev"),
    ]

    DEFAULT_CSS = """
    PipeSizePicker { align: center middle; }
    #pipe-box {
        width: 30;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    .pipe-row { padding: 0 1; }
    .pipe-row.selected { background: $accent; color: $text; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.cursor = PIPE_SIZES.index(PIPE_SIZE_DEFAULT)

    def compose(self) -> ComposeResult:
        with Vertical(id="pipe-box"):
            yield Static("[bold]Pipe size[/bold]")
            for i, size in enumerate(PIPE_SIZES):
                cls = "pipe-row selected" if i == self.cursor else "pipe-row"
                yield Static(size, classes=cls, id=f"pipe-{i}")
            yield Static("[dim]j/k=move  Enter=pick  Esc=cancel[/dim]")

    def _refresh(self) -> None:
        for i in range(len(PIPE_SIZES)):
            row = self.query_one(f"#pipe-{i}", Static)
            row.set_classes("pipe-row selected" if i == self.cursor else "pipe-row")

    def action_next(self) -> None:
        self.cursor = (self.cursor + 1) % len(PIPE_SIZES)
        self._refresh()

    def action_prev(self) -> None:
        self.cursor = (self.cursor - 1) % len(PIPE_SIZES)
        self._refresh()

    def action_select(self) -> None:
        self.dismiss(PIPE_SIZES[self.cursor])

    def action_cancel(self) -> None:
        self.dismiss(None)


class StationReviewScreen(ModalScreen[bool]):
    """Modal for reviewing a station's measurements and assigning labels.

    Returns True if the user confirmed (Enter), False if cancelled (Esc).

    Per-row keys:
      1-6: pick from PRESET_LABELS (6 = pipe → opens PipeSizePicker)
      t: free-form custom label
      x: clear label
      j/k or arrows: navigate

    Footer keys:
      Enter: save + mark confirmed
      s: save drafts (don't confirm)
      Esc: cancel without saving
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Confirm"),
        Binding("s", "save_draft", "Save draft"),
        Binding("j,down", "next", "Next row"),
        Binding("k,up", "prev", "Prev row"),
        Binding("x", "clear", "Clear label"),
        Binding("t", "custom", "Custom"),
        # 1-6 fire pick_label_<n> via on_key
    ]

    DEFAULT_CSS = """
    StationReviewScreen { align: center middle; }
    #review-box {
        width: 100;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    DataTable { height: auto; max-height: 20; }
    #custom-input { dock: bottom; height: 3; }
    """

    def __init__(self, station_id: int, members: list,
                 on_apply: Callable[[dict[int, str | None], bool], None]) -> None:
        """`members` is a list of sqlite3.Row, sorted by result_m ascending.
        `on_apply(labels_by_meas_id, confirmed)` is called when the user saves."""
        super().__init__()
        self.station_id = station_id
        self.members = members
        self.on_apply = on_apply
        self.labels: dict[int, str | None] = {}
        # Pre-fill with existing labels or suggestions
        suggestions = suggest_labels(len(members))
        for i, m in enumerate(members):
            existing = m["station_label"]
            if existing:
                self.labels[m["meas_id"]] = existing
            elif i < len(suggestions):
                self.labels[m["meas_id"]] = suggestions[i]
            else:
                self.labels[m["meas_id"]] = None
        self.cursor_row = 0
        self._custom_active = False

    def compose(self) -> ComposeResult:
        with Vertical(id="review-box"):
            yield Static(f"[bold]Station {self.station_id}[/bold]  —  "
                         f"{len(self.members)} member(s)")
            preset_help = "  ".join(f"[bold]{i+1}[/bold]={lbl.split('-')[-1]}"
                                     for i, lbl in enumerate(PRESET_LABELS))
            yield Static(f"[dim]Per row: {preset_help}  [bold]t[/bold]=custom  "
                         f"[bold]x[/bold]=clear[/dim]")
            yield DataTable(id="review-table", cursor_type="row")
            yield Static("[dim]Enter=confirm & save · s=save draft · Esc=cancel[/dim]")
            yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#review-table", DataTable)
        table.add_columns("Z", "Result", "Imperial", "Label")
        self._render_rows()
        table.move_cursor(row=0)

    def _render_rows(self) -> None:
        table = self.query_one("#review-table", DataTable)
        table.clear()
        for i, m in enumerate(self.members):
            label = self.labels.get(m["meas_id"]) or "[dim]—[/dim]"
            table.add_row(str(i + 1), f"{m['result_m']:.4f} m",
                          format_imperial(m["result_m"]), label)

    def _current_meas_id(self) -> int | None:
        if 0 <= self.cursor_row < len(self.members):
            return self.members[self.cursor_row]["meas_id"]
        return None

    def action_next(self) -> None:
        if self.cursor_row < len(self.members) - 1:
            self.cursor_row += 1
            self.query_one("#review-table", DataTable).move_cursor(row=self.cursor_row)

    def action_prev(self) -> None:
        if self.cursor_row > 0:
            self.cursor_row -= 1
            self.query_one("#review-table", DataTable).move_cursor(row=self.cursor_row)

    def action_clear(self) -> None:
        mid = self._current_meas_id()
        if mid is not None:
            self.labels[mid] = None
            self._render_rows()

    def action_custom(self) -> None:
        if self._custom_active:
            return
        self._custom_active = True
        prompt = Input(placeholder="custom label", id="custom-input")
        self.mount(prompt)
        prompt.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "custom-input":
            return
        mid = self._current_meas_id()
        if mid is not None and event.value.strip():
            self.labels[mid] = event.value.strip()
            self._render_rows()
        event.input.remove()
        self._custom_active = False

    async def on_key(self, event) -> None:
        if self._custom_active:
            return
        if event.key in ("1", "2", "3", "4", "5", "6"):
            idx = int(event.key) - 1
            label = PRESET_LABELS[idx]
            mid = self._current_meas_id()
            if mid is None:
                return
            event.stop()
            if label == "bottom-of-pipe":
                size = await self.app.push_screen_wait(PipeSizePicker())
                if size:
                    self.labels[mid] = format_pipe_label(size)
                    self._render_rows()
            else:
                self.labels[mid] = label
                self._render_rows()
                # Auto-advance for fast labeling
                if self.cursor_row < len(self.members) - 1:
                    self.cursor_row += 1
                    self.query_one("#review-table", DataTable).move_cursor(row=self.cursor_row)

    def action_confirm(self) -> None:
        self.on_apply(self.labels, True)
        self.dismiss(True)

    def action_save_draft(self) -> None:
        self.on_apply(self.labels, False)
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
